"""PDF do dia para imprimir: todas as etiquetas e notas num arquivo só, 09/10/2026.

Combinado com a loja: em dia útil, por volta de 12h05, chega no grupo do
Telegram um único PDF com os pedidos pagos até as 12h que ainda não foram
impressos. Primeiro as etiquetas, 4 por folha (layout padrão dos Correios),
depois as DANFEs na mesma ordem, para casar etiqueta e nota na hora de
embalar. Pedido pago depois das 12h fica para o dia útil seguinte; fim de
semana e feriado nacional não têm PDF.

Junto, cada pedido do PDF é marcado como enviado na Shopify com o rastreio,
sem e-mail para a cliente (a jornada pós-compra ainda vai ser desenhada).

Quem chama é o workflow envios.yml, disparado pelo Supabase às 12h05 de
segunda a sexta (avisos.sql). Pedido impresso fica anotado como
"pdfdia:<número>" em avisos.enviados; rodar de novo não repete.

    python avisos/envios.py            PDF do dia (respeita fim de semana e feriado)
    python avisos/envios.py --agora    gera mesmo fora de dia útil (teste)
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
        danfe.mensagem(f"Envios de {agora:%d/%m}: nenhum pedido novo para imprimir.")
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
        try:
            etiqueta.rastreio_na_shopify(p["pedido"], p["codigo"])
        except Exception as erro:
            danfe.mensagem(f"Pedido #{p['numero']} foi para o PDF, mas não foi marcado como enviado na Shopify: "
                           f"{str(erro)[:200]}")
    print(f"PDF com {len(lista)} pedidos")


if __name__ == "__main__":
    main(forcar="--agora" in sys.argv)
