"""PDF do dia para imprimir: todas as etiquetas e notas num arquivo só, 09/10/2026.

Combinado com a loja: em dia útil, por volta de 12h05, chega no grupo do
Telegram um único PDF com os pedidos pagos até as 12h que ainda não foram
impressos, seguido do status dos pedidos de hoje (o que tem de ser postado
hoje, o que está atrasado e o que está em produção). Primeiro as etiquetas, 4 por folha (layout padrão dos Correios),
depois as DANFEs na mesma ordem, para casar etiqueta e nota na hora de
embalar. Pedido pago depois das 12h fica para o dia útil seguinte; fim de
semana e feriado nacional não têm PDF.

A baixa na Shopify (pedido marcado como enviado, com rastreio e sem e-mail
para a cliente) não acontece aqui: só depois que os Correios registram a
postagem, em "python avisos/envios.py --baixa", algumas vezes por dia.

Quem chama é o workflow envios.yml, disparado pelo Supabase às 12h05 de
segunda a sexta (avisos.sql). Pedido impresso fica anotado como
"pdfdia:<número>" em avisos.enviados; rodar de novo não repete.

    python avisos/envios.py            PDF do dia (respeita fim de semana e feriado)
    python avisos/envios.py --agora    gera mesmo fora de dia útil (teste)
    python avisos/envios.py --baixa    dá baixa na Shopify do que já foi postado
"""

import datetime as dt
import os
import sys
import tempfile
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402
from sqlalchemy import text  # noqa: E402

from avisos import danfe, etiqueta  # noqa: E402

FUSO = zoneinfo.ZoneInfo("America/Sao_Paulo")
CORTE = dt.time(12, 0)

# Feriados nacionais (Correios e loja parados). Carnaval, Sexta-feira Santa e
# Corpus Christi mudam de data todo ano. Municipais de Londrina não entram.
FERIADOS = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-04-03", "2026-04-21", "2026-05-01",
    "2026-06-04", "2026-09-07", "2026-10-12", "2026-11-02", "2026-11-15", "2026-11-20",
    "2026-12-25",
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-26", "2027-04-21", "2027-05-01",
    "2027-05-27", "2027-09-07", "2027-10-12", "2027-11-02", "2027-11-15", "2027-11-20",
    "2027-12-25",
}


def dia_util(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in FERIADOS


def pendentes() -> list:
    """Pedidos com etiqueta automática e ainda fora de um PDF do dia."""
    with nuvem._conectar().connect() as con:
        rows = con.execute(text("""
            select substr(e.id, 5), e.tipo from avisos.enviados e
            where e.id like 'etq:%' and e.tipo like 'etiqueta % % %'
              and not exists (select 1 from avisos.enviados p where p.id = 'pdfdia:' || substr(e.id, 5))
        """)).fetchall()
    saida = []
    for numero, tipo in rows:
        _, codigo, id_pre, id_nota = tipo.split()[:4]
        saida.append({"numero": numero, "codigo": codigo, "id_pre": id_pre, "id_nota": id_nota})
    return sorted(saida, key=lambda p: int(p["numero"]))


def pago_ate(pedido: dict, limite: dt.datetime) -> bool:
    criado = dt.datetime.fromisoformat(pedido["createdAt"].replace("Z", "+00:00"))
    return criado <= limite


def etiquetas_juntas(ids: list, destino: str) -> None:
    """Rótulo dos Correios com vários ids: 4 etiquetas por folha, como o Meu Correios."""
    import base64
    import time
    recibo = etiqueta._correios("POST", "/v1/prepostagens/rotulo/assincrono/pdf", json={
        "idsPrePostagem": ids, "numeroCartaoPostagem": os.environ["CORREIOS_CARTAO"],
        "tipoRotulo": "P", "formatoRotulo": "ET", "imprimeRemetente": "S", "layoutImpressao": "PADRAO",
    })["idRecibo"]
    for _ in range(36):
        time.sleep(5)
        r = etiqueta._correios("GET", f"/v1/prepostagens/rotulo/download/assincrono/{recibo}")
        if r.get("dados"):
            with open(destino, "wb") as f:
                f.write(base64.b64decode(r["dados"]))
            return
    raise RuntimeError(f"Correios: rótulo do recibo {recibo} não ficou pronto em 3 minutos")


def status_de_hoje(dia: dt.date) -> str:
    """Status dos pedidos com o dia de hoje como alvo de postagem: o que tem de
    sair hoje, o que já está atrasado e o que está em produção. A regra é a do
    fechamento das 23h (avisos.status_pedidos, no Supabase); aqui só muda o
    dia que a mensagem olha (pedido do Pedro, 12/10/2026)."""
    try:
        with nuvem._conectar().connect() as con:
            return con.execute(text("select avisos.status_pedidos(:d)"), {"d": dia}).scalar() or ""
    except Exception as erro:
        return f"Status dos pedidos: não consegui montar ({str(erro)[:120]})"


def anotar(numero: str) -> None:
    with nuvem._conectar().begin() as con:
        con.execute(text("insert into avisos.enviados (id, tipo) values (:id, 'pdf do dia') on conflict do nothing"),
                    {"id": f"pdfdia:{numero}"})


def main(forcar: bool = False) -> None:
    agora = dt.datetime.now(FUSO)
    if not etiqueta.configurado():
        print("etiqueta automática desligada")
        return
    if not forcar and not dia_util(agora.date()):
        print("não é dia útil")
        return
    limite = dt.datetime.combine(agora.date(), CORTE, FUSO)

    lista = []
    for p in pendentes():
        pedido = etiqueta.pedido_shopify(p["numero"])
        if pago_ate(pedido, limite):
            lista.append({**p, "pedido": pedido})
    if not lista:
        danfe.mensagem(f"Envios de {agora:%d/%m}: nenhum pedido novo para imprimir.\n\n"
                       + status_de_hoje(agora.date()))
        return

    from pypdf import PdfWriter
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as pasta, sync_playwright() as pw:
        partes = []
        etq = os.path.join(pasta, "etiquetas.pdf")
        etiquetas_juntas([p["id_pre"] for p in lista], etq)
        partes.append(etq)
        navegador = pw.chromium.launch()
        for p in lista:
            link = danfe.olist("nota.fiscal.obter.link", id=p["id_nota"])["link_nfe"]
            arq = os.path.join(pasta, f"nf_{p['numero']}.pdf")
            danfe.pdf_da_danfe(navegador, link, arq)
            partes.append(arq)
        navegador.close()

        junto = PdfWriter()
        for arq in partes:
            junto.append(arq)
        nome = f"Envios {agora:%d-%m-%Y}.pdf"
        final = os.path.join(pasta, nome)
        with open(final, "wb") as f:
            junto.write(f)

        # Lista de tudo que está no PDF, na ordem das etiquetas. A legenda do
        # Telegram aceita até 1.024 caracteres; passando disso, vai em mensagem.
        linhas = [f"Envios de {agora:%d/%m} · {len(lista)} pedido(s)",
                  "Etiquetas nas primeiras folhas, notas na mesma ordem:"]
        for i, p in enumerate(lista, 1):
            nome_cli = ((p["pedido"].get("shippingAddress") or {}).get("name") or "").strip().title()
            servico = ((p["pedido"].get("shippingLines") or {}).get("nodes") or [{}])[0].get("title", "")
            linhas.append(f"{i}. #{p['numero']} · {nome_cli} · {servico} {p['codigo']}")
        texto = "\n".join(linhas)
        if len(texto) <= 1000:
            danfe.telegram(final, nome, texto)
        else:
            danfe.telegram(final, nome, linhas[0])
            danfe.mensagem(texto)

    for p in lista:
        anotar(p["numero"])
    danfe.mensagem(status_de_hoje(agora.date()))
    print(f"PDF com {len(lista)} pedidos")


def baixa() -> None:
    """Marca como enviado na Shopify só o que os Correios já receberam.

    O painel de pedidos da Shopify é onde a loja vê o que já saiu e o que
    falta. Marcar às 12h, junto do PDF, diria "enviado" antes da coleta; por
    isso a baixa espera a pré-postagem virar POSTADO (status 3) nos Correios
    (pedido do Pedro, 09/10/2026). Sem e-mail para a cliente. Etiqueta que
    vence sem postagem (status 4) é avisada uma vez no grupo.
    """
    with nuvem._conectar().connect() as con:
        rows = con.execute(text("""
            select substr(e.id, 5), e.tipo from avisos.enviados e
            where e.id like 'etq:%' and e.tipo like 'etiqueta % % %'
              and not exists (select 1 from avisos.enviados b where b.id = 'baixa:' || substr(e.id, 5))
        """)).fetchall()
    erros = []
    for numero, tipo in rows:
        _, codigo, id_pre, _nota = tipo.split()[:4]
        itens = etiqueta._correios("GET", "/v2/prepostagens", params={"id": id_pre}).get("itens") or [{}]
        status = itens[0].get("statusAtual")
        if status == 3:
            try:
                etiqueta.rastreio_na_shopify(etiqueta.pedido_shopify(numero), codigo)
            except Exception as erro:
                erros.append(f"#{numero}: {str(erro)[:150]}")
                continue
            marca = "enviado (postado nos Correios)"
        elif status in (4, 5):
            if status == 4:
                danfe.mensagem(f"A etiqueta {codigo} do pedido #{numero} venceu sem ser postada. "
                               "Gerar outra antes de mandar.")
            marca = "etiqueta vencida" if status == 4 else "etiqueta cancelada"
        else:
            continue
        with nuvem._conectar().begin() as con:
            con.execute(text("insert into avisos.enviados (id, tipo) values (:id, :t) on conflict do nothing"),
                        {"id": f"baixa:{numero}", "t": marca})
        print(f"#{numero}: {marca}")
    erros += baixa_etiquetas_manuais()
    if erros:
        danfe.mensagem("Postados nos Correios, mas sem baixa na Shopify:\n" + "\n".join(erros))


def _digitos(t) -> str:
    return "".join(ch for ch in str(t or "") if ch.isdigit())


def baixa_etiquetas_manuais() -> list:
    """Etiqueta emitida à mão no Meu Correios também dá baixa, 20/10/2026.

    Pedido sem etiqueta do sistema (anterior à automação, ou feito à mão) não
    tinha baixa nenhuma: a Nicole (#1140) e a Gisela (#1130) foram postadas e
    seguiam "não processado" na Shopify. Aqui a baixa olha as pré-postagens
    POSTADAS do contrato nos últimos 20 dias e casa com pedido pago ainda não
    enviado pelo CEP e pelo primeiro nome do destinatário. Só marca quando o
    casamento é único dos dois lados. Devolve os erros para o aviso no grupo.
    """
    import datetime as _dt
    hoje = _dt.date.today()
    r = etiqueta._correios("GET", "/v2/prepostagens", params={
        "status": "POSTADO", "dataInicial": str(hoje - _dt.timedelta(days=20)), "dataFinal": str(hoje),
        "page": 0, "size": 100})
    with nuvem._conectar().connect() as con:
        usados = {t.split()[1] for (t,) in con.execute(text(
            "select tipo from avisos.enviados where id like 'etq:%' and tipo like 'etiqueta % % %'"))}
    postados = []
    for x in r.get("itens") or []:
        d = x.get("destinatario") or {}
        cep = _digitos((d.get("endereco") or {}).get("cep"))
        if x.get("codigoObjeto") in usados or cep == "86050450":   # etiqueta do sistema ou reversa para a loja
            continue
        postados.append({"codigo": x["codigoObjeto"], "cep": cep,
                         "nome": (d.get("nome") or "").strip().lower().split(" ")[0]})
    if not postados:
        return []
    pedidos = shopify_client._graphql("""{ orders(first: 100, sortKey: CREATED_AT, reverse: true,
        query: "fulfillment_status:unfulfilled financial_status:paid status:open") {
        nodes { name shippingAddress { name zip } } } }""")["orders"]["nodes"]
    abertos = [{"numero": p["name"].lstrip("#"), "cep": _digitos((p.get("shippingAddress") or {}).get("zip")),
                "nome": ((p.get("shippingAddress") or {}).get("name") or "").strip().lower().split(" ")[0]}
               for p in pedidos]
    erros = []
    for e in postados:
        cand = [p for p in abertos if p["cep"] == e["cep"] and p["nome"] == e["nome"]]
        rival = [x for x in postados if x["cep"] == e["cep"] and x["nome"] == e["nome"]]
        if len(cand) != 1 or len(rival) != 1:
            continue
        numero = cand[0]["numero"]
        try:
            etiqueta.rastreio_na_shopify(etiqueta.pedido_shopify(numero), e["codigo"])
        except Exception as erro:
            erros.append(f"#{numero} ({e['codigo']}, etiqueta à mão): {str(erro)[:150]}")
            continue
        with nuvem._conectar().begin() as con:
            con.execute(text("insert into avisos.enviados (id, tipo) values (:id, :t) on conflict do nothing"),
                        {"id": f"baixa:{numero}", "t": f"enviado (etiqueta à mão {e['codigo']})"})
        print(f"#{numero}: enviado, etiqueta à mão {e['codigo']}")
    return erros


if __name__ == "__main__":
    if "--baixa" in sys.argv:
        baixa()
    else:
        main(forcar="--agora" in sys.argv)
