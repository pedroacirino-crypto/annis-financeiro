"""Motor da régua pós-compra: manda os e-mails que venceram, 13/10/2026.

Roda a cada 10 minutos (workflow regua.yml, disparado pelo Supabase). Para cada
pedido criado depois de REGUA_INICIO, monta a sequência (plano.py), e manda o
que já venceu e ainda não saiu. Pedido de antes de REGUA_INICIO (até 60 dias)
recebe só do 6 em diante, e só o que acontece depois de REGUA_INICIO. Cada envio fica anotado em avisos.enviados como
"regua:<e-mail>:<número>", então rodar de novo nunca repete.

    python -m avisos.regua.motor              manda o que venceu
    python -m avisos.regua.motor --simular    só mostra o que mandaria

Variáveis:
    REGUA_INICIO   data e hora (ISO) a partir da qual os pedidos entram na régua
    REGUA_MODO     "no_ar" (padrão) ou "sombra": em sombra, tudo vai só para a
                   contato@, com [sombra] no assunto, e não conta como enviado
    REGUA_CCO      cópia oculta, separada por vírgula (padrão: contato@annis.store; no Gmail
                   da contato@, um filtro põe essas cópias no marcador Régua/Enviados)
    GMAIL_SENHA_APP, PAGARME_SECRET_KEY, SHOPIFY_*, CORREIOS_*, TELEGRAM_*, SUPABASE_URL_BANCO

Regras de envio, além das do plano:
- o 0 é da Shopify e não sai daqui;
- 3, 5 e 9 só entre 9h e 20h; o 4 (Telegram) também;
- se um e-mail venceu mas outro posterior do mesmo pedido também já venceu
  (o motor ficou parado), manda só o mais recente, sem atropelar a cliente;
- o 1 não sai se o Pix já foi pago;
- erro num pedido não trava os outros e vai para o Telegram (uma vez por erro).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import sys
import traceback

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import nuvem  # noqa: E402
import pagarme_client  # noqa: E402
import shopify_client  # noqa: E402
from avisos import danfe, etiqueta, gmail  # noqa: E402
from avisos.regua import modelos, plano  # noqa: E402
from sqlalchemy import text  # noqa: E402

TZ = plano.TZ
CONTATO = "contato@annis.store"
HORARIO = {"e3", "e4", "e5", "e5_pronta", "e7_atrasada", "e9"}  # só em horário comercial
# Pedidos de antes da virada (Pedro, 14/10): do 6 em diante, e só eventos que
# acontecem depois da virada (o 6 só para quem for postado de 14/10 em diante).
# Do 0 ao 5 é só para pedido novo.
LEGADO = {"e6", "e7_atrasada", "e7_tentativa", "e7_retirada", "e8", "e9", "devolucao"}
TOKEN = "__TOKEN__"

CONSULTA = '''query($c:String, $q:String){orders(first:50, after:$c, sortKey:CREATED_AT, query:$q){pageInfo{hasNextPage endCursor}
 nodes{name createdAt cancelledAt test email displayFinancialStatus paymentGatewayNames
  customer{firstName email} shippingAddress{firstName name address1 address2 city provinceCode zip}
  subtotalPriceSet{shopMoney{amount}} totalShippingPriceSet{shopMoney{amount}} totalDiscountsSet{shopMoney{amount}} totalPriceSet{shopMoney{amount}}
  shippingLines(first:2){nodes{title}}
  lineItems(first:20){nodes{title variantTitle quantity originalTotalSet{shopMoney{amount}} image{url} product{featuredImage{url} descriptionHtml}}}
  fulfillments{createdAt trackingInfo{number}} refunds{createdAt}
  transactions(first:10){kind status processedAt gateway receiptJson}}}}'''


def _con():
    return nuvem._conectar()


def inicio() -> dt.datetime:
    v = os.environ.get("REGUA_INICIO")
    if not v:
        raise SystemExit("REGUA_INICIO não definido: a régua não liga sem data de início")
    d = dt.datetime.fromisoformat(v)
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def pedidos() -> list:
    """Pedidos dos últimos 60 dias (os de antes da virada entram só do 7 em diante)."""
    desde = plano.agora() - dt.timedelta(days=60)
    q = f"created_at:>={desde.astimezone(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
    nodes, c = [], None
    while True:
        r = shopify_client._graphql(CONSULTA, {"c": c, "q": q})["orders"]
        nodes += r["nodes"]
        if not r["pageInfo"]["hasNextPage"]:
            break
        c = r["pageInfo"]["endCursor"]
    for o in nodes:
        for t in o["transactions"]:
            try:
                rec = json.loads(t.get("receiptJson") or "{}")
            except ValueError:
                rec = {}
            t["payment_id"] = rec.get("payment_id") or rec.get("id")
            t.pop("receiptJson", None)
        # Reembolso total conta como cancelado: a régua para.
        if o["displayFinancialStatus"] == "REFUNDED" and o["refunds"] and not o["cancelledAt"]:
            o["cancelledAt"] = o["refunds"][-1]["createdAt"]
    return [o for o in nodes if not o["test"] and plano.local(o["createdAt"]) >= desde]


def enviados(con) -> dict:
    return {r[0]: r[1] for r in con.execute(text("select id, tipo from avisos.enviados where id like 'regua:%'"))}


def etiquetas(con) -> dict:
    return {"#" + r[0]: r[1] for r in con.execute(text(
        "select substr(id, 5), split_part(tipo, ' ', 2) from avisos.enviados where id like 'etq:%'"))}


def rastro(con, codigo: str, entregue_ja: bool) -> dict:
    """Eventos dos Correios, guardados por 1 hora (para sempre depois de entregue)."""
    chave = "regua_rastro:" + codigo
    r = con.execute(text("select valor from avisos.estado where chave = :c and expira > now()"), {"c": chave}).scalar()
    if r:
        return json.loads(r)
    resp = requests.get(f"https://api.correios.com.br/srorastro/v1/objetos/{codigo}", params={"resultado": "T"},
                        headers={"Authorization": "Bearer " + etiqueta._token_correios()}, timeout=40)
    if not resp.ok:
        return {}
    o = (resp.json().get("objetos") or [{}])[0]
    dados = {"prevista": o.get("dtPrevista"),
             "eventos": [{"codigo": x.get("codigo"), "tipo": x.get("tipo"), "desc": x.get("descricao"),
                          "detalhe": x.get("detalhe") or _agencia(x), "quando": x.get("dtHrCriado")}
                         for x in o.get("eventos") or []]}
    entregue = any(e["codigo"] in ("BDE", "BDI", "BDR") and e["tipo"] in ("01", "12", "23") for e in dados["eventos"])
    validade = "3650 days" if entregue else "1 hour"
    con.execute(text(f"insert into avisos.estado (chave, valor, expira, em) values (:c, :v, now() + interval '{validade}', now()) "
                     "on conflict (chave) do update set valor = excluded.valor, expira = excluded.expira, em = now()"),
                {"c": chave, "v": json.dumps(dados, ensure_ascii=False)})
    return dados


def _agencia(ev: dict) -> str:
    """Para "aguardando retirada": nome e endereço da agência, quando vierem no evento."""
    u = ev.get("unidade") or {}
    e = u.get("endereco") or {}
    partes = [u.get("nome") or u.get("tipo"), ", ".join(x for x in [e.get("logradouro"), e.get("numero")] if x),
              e.get("bairro"), "/".join(x for x in [e.get("cidade"), e.get("uf")] if x)]
    return ", ".join(p for p in partes if p) if "retirada" in (ev.get("descricao") or "").lower() else ""


def cobranca_pix(payment_id: str) -> dict:
    # A listagem de pedidos não traz a cobrança; a de cobranças, pelo mesmo código, traz.
    d = pagarme_client._get("/charges", {"code": payment_id}).get("data") or []
    lt = (d[0].get("last_transaction") or {}) if d else {}
    return {"qr": lt.get("qr_code"), "expira": lt.get("expires_at"), "parcelas": lt.get("installments")}


def criar_link(con, tipo: str, pedido: str, dados: dict) -> str:
    token = secrets.token_urlsafe(12)
    con.execute(text("insert into avisos.regua_links (token, pedido, tipo, dados) values (:t, :p, :tipo, cast(:d as jsonb))"),
                {"t": token, "p": pedido, "tipo": tipo, "d": json.dumps(dados, ensure_ascii=False)})
    return token


def telegram(texto: str) -> None:
    try:
        danfe.mensagem(texto)
    except Exception:  # aviso não pode derrubar o envio
        traceback.print_exc()


def vencidos(seq: list, ja: dict, numero: str, agora: dt.datetime) -> list:
    """O que mandar agora: venceu, não saiu e não foi atropelado por um posterior."""
    venc = [(q, qual, d) for q, qual, d in seq if q <= agora and qual != "e0"]
    if not venc:
        return []
    ultimo = max(q for q, _, _ in venc)
    saida = []
    for q, qual, d in venc:
        if f"regua:{qual}:{numero}" in ja:
            continue
        # Atropelado: um e-mail posterior também já venceu há mais de 1 hora.
        if any(q2 > q and qual2 not in ("e4", "cancelado", "devolucao") and q2 <= agora - dt.timedelta(hours=1) for q2, qual2, _ in venc) \
                and q < ultimo:
            continue
        saida.append((q, qual, d))
    return saida


def main(simular: bool = False) -> None:
    modo = os.environ.get("REGUA_MODO") or "no_ar"
    cco = [x.strip() for x in (os.environ.get("REGUA_CCO") or CONTATO).split(",") if x.strip()]
    agora = plano.agora()
    plano.link_pagina = lambda tipo, pedido: f"https://annis.store/pages/{'pix' if tipo == 'pix' else 'sua-opiniao'}?p={TOKEN}"
    eng = _con()
    with eng.connect() as con:
        ja, etq = enviados(con), etiquetas(con)
    erros = []
    virada = inicio()
    for o in pedidos():
        numero = o["name"].lstrip("#")
        legado = plano.local(o["createdAt"]) < virada
        try:
            with eng.begin() as con:
                cods = [t["number"].strip().upper() for f in o["fulfillments"] for t in f["trackingInfo"] if t.get("number")]
                if o["name"] in etq and etq[o["name"]] not in cods:
                    cods.append(etq[o["name"]])
                entregue_ja = f"regua:e8:{numero}" in ja
                o["rastro"] = {cd: rastro(con, cd, entregue_ja) for cd in cods[:1]}
                if legado and not cods:  # sem pacote, nada do 7 em diante para mandar
                    continue
                pix_pendente = (not legado and any("PIX" in g for g in o["paymentGatewayNames"])
                                and o["displayFinancialStatus"] == "PENDING")
                pid = next((t["payment_id"] for t in o["transactions"] if t.get("payment_id")), None)
                cobrancas = {pid: cobranca_pix(pid)} if (pix_pendente and pid) else {}
                seq = plano.montar_pedido(o["name"], o, o, cobrancas)
                if legado:
                    seq = [x for x in seq if x[1] in LEGADO and x[0] >= virada]
                for quando, qual, dados in vencidos(seq, ja, numero, agora):
                    if qual in HORARIO and not (9 <= agora.hour < 20):
                        continue
                    if qual == "e1" and not pix_pendente:
                        continue
                    if qual == "e1" and not dados["pix"]["codigo"]:
                        raise RuntimeError("Pix pendente sem código na Pagar.me: e-mail 1 não saiu")
                    _enviar(con, o, numero, qual, dados, modo, cco, simular)
                    ja[f"regua:{qual}:{numero}"] = "agora"
        except Exception as e:
            erros.append(f"{o['name']}: {str(e)[:200]}")
            traceback.print_exc()
    if erros and not simular:
        texto = "Régua pós-compra, erro:\n" + "\n".join(erros)
        with eng.begin() as con:
            antes = con.execute(text("select valor from avisos.estado where chave = 'regua_erro'")).scalar()
            if antes != texto:
                telegram(texto)
                con.execute(text("insert into avisos.estado (chave, valor, em) values ('regua_erro', :v, now()) "
                                 "on conflict (chave) do update set valor = excluded.valor, em = now()"), {"v": texto})


def _enviar(con, o, numero, qual, dados, modo, cco, simular):
    email = o.get("email") or (o.get("customer") or {}).get("email")
    if qual == "e4":
        texto = (f"Vence amanhã e ainda não foi postado\n{o['name']} {dados['endereco'][0]} · {dados['peca_nome']} · "
                 f"{dados['servico']}\nPrazo de postagem: {dados['prazo']}")
        print(f"{o['name']} e4 (Telegram)")
        if not simular:
            telegram(texto)
            con.execute(text("insert into avisos.enviados (id, tipo) values (:i, 'regua telegram') on conflict do nothing"),
                        {"i": f"regua:e4:{numero}"})
        return
    if qual == "devolucao":
        print(f"{o['name']} devolução (Telegram)")
        if not simular:
            telegram(f"{o['name']}: {dados['evento']} ({dados['rastreio']['codigo']}). Sem e-mail automático: "
                     "chamar a cliente para combinar o reenvio.")
            con.execute(text("insert into avisos.enviados (id, tipo) values (:i, 'regua telegram') on conflict do nothing"),
                        {"i": f"regua:devolucao:{numero}"})
        return
    if qual == "cancelado":
        if not simular:
            con.execute(text("insert into avisos.enviados (id, tipo) values (:i, 'regua parada') on conflict do nothing"),
                        {"i": f"regua:cancelado:{numero}"})
        return
    anexos = []
    if qual == "e6":  # nota anexada, guardada por avisos/danfe.py
        nota = con.execute(text("select numero, pdf, xml from avisos.regua_notas where pedido = :p"), {"p": numero}).first()
        if nota and nota.pdf:
            dados = {**dados, "nota": nota.numero}
            anexos.append((f"NF ANNIS_{nota.numero}.pdf", bytes(nota.pdf), "application/pdf"))
            if nota.xml:
                anexos.append((f"NF ANNIS_{nota.numero}.xml", nota.xml.encode("utf-8"), "application/xml"))
    m = modelos.montar(qual, dados)
    html, texto, assunto = m["html"], m["texto"], m["assunto"]
    if TOKEN in html:
        if qual == "e1":
            token = "simulado" if simular else criar_link(con, "pix", o["name"], {
                "codigo": dados["pix"]["codigo"], "valor": dados["pix"]["valor"], "expira": dados["pix"].get("expira") or ""})
        else:
            unica_roupa = dados.get("roupa", True) and len(dados["itens"]) == 1
            principal = max(dados["itens"], key=lambda i: modelos._valor(i["preco"]))
            g = modelos.GENERO[dados.get("genero", "m")]
            token = "simulado" if simular else criar_link(con, "pesquisa", o["name"], {
                "nome": dados["nome"], "peca": dados["peca"], "peca_nome": principal["nome"], "img": principal["img"],
                "o_seu": g["o_seu"], "roupa": unica_roupa})
        html, texto = html.replace(TOKEN, token), texto.replace(TOKEN, token)
    para = email
    if modo == "sombra":
        para, assunto, cco = CONTATO, f"[sombra {o['name']}] {assunto}", []
    # Nunca o e-mail da cliente no log: o repositório é público e o log do Actions também.
    print(f"{o['name']} {qual} -> {'(simulado) ' if simular else ''}{'contato@' if para == CONTATO else 'cliente'}: {assunto}")
    if simular or not para:
        return
    mid = gmail.enviar(para, assunto, html, texto, imagens=m["imagens"], cco=cco, anexos=anexos)
    prefixo = "regua_sombra" if modo == "sombra" else "regua"
    con.execute(text("insert into avisos.enviados (id, tipo) values (:i, :t) on conflict do nothing"),
                {"i": f"{prefixo}:{qual}:{numero}", "t": f"regua {mid}"})
    if qual.startswith("e7_"):
        telegram(f"{o['name']}: {assunto} (e-mail enviado para a cliente)")


if __name__ == "__main__":
    main(simular="--simular" in sys.argv)
