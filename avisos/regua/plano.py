"""Quando cada e-mail da régua sai, para um pedido (13/10/2026).

    montar_pedido("#1160", pedido, pedido, cobrancas) -> [(quando, "e2_pronta", dados), ...]

Devolve a sequência completa do pedido, do 0 ao 9, com a hora em que cada
e-mail vence e os dados para modelos.montar. O motor (motor.py) manda o que já
venceu e ainda não saiu; o backtest (fora do repositório) manda tudo para a
contato@. As regras:

- 0: confirmação da Shopify, na criação do pedido ("pago" se já nasce pago).
- 1: Pix pendente há 15 minutos.
- 2: pagamento aprovado. Pronta entrega posta em D+1 útil; sob encomenda em
  compra + dias de produção (descrição do produto).
- 3: metade da produção (sob encomenda), se não postou.
- 4: alerta interno no Telegram, 1 dia útil antes do prazo, só sob encomenda.
- 5: atraso. Pronta entrega: não postou até D+2 útil; sob encomenda: passou do prazo.
- 6: postado (primeiro evento PO dos Correios).
- 7: tentativa sem sucesso, aguardando retirada, entrega atrasada (passou da
  data prevista dos Correios).
- Em devolução ao remetente: sem e-mail, só aviso no Telegram ("devolucao").
- 8: entregue. 9: avaliação, 6 dias depois da entrega.
- Cancelado: nada depois do cancelamento.

"pedido" é o nó do pedido da Shopify (ver motor.CONSULTA) com "rastro" por
código de rastreio ({"prevista", "eventos"}); "cobrancas" é {payment_id: {qr,
expira, parcelas}} da Pagar.me.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import sys
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import nuvem  # noqa: E402
from avisos import etiqueta  # noqa: E402
from avisos.regua import modelos  # noqa: E402
from sqlalchemy import text  # noqa: E402

TZ = ZoneInfo("America/Sao_Paulo")
CEP_ORIGEM = "86050450"
SERVICO = {"SEDEX": "03220", "PAC": "03298"}
DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
FEMININO = {"bolsa", "saia", "calça", "blusa", "camisa", "jaqueta", "regata", "bermuda"}

_con = None


def _db():
    global _con
    if _con is None:
        _con = nuvem._conectar().connect()
    return _con


# Relógio da régua: o motor usa a hora real; o backtest pode fixar outra.
AGORA = None


def agora():
    return AGORA or dt.datetime.now(TZ)


# Links das páginas do site. O motor troca por tokens de verdade, criados no
# envio (avisos.regua_links); o backtest usa marcadores.
def link_pagina(tipo: str, pedido: str) -> str:
    return f"https://annis.store/pages/{'pix' if tipo == 'pix' else 'sua-opiniao'}?p=backtest-{pedido.lstrip('#')}"


def soma(d, n):
    return _db().execute(text("select avisos.somar_dias_uteis(:d, :n)"), {"d": d, "n": n}).scalar()


def du(d):
    return _db().execute(text("select avisos.dia_util(:d)"), {"d": d}).scalar()


def util_antes(d):
    x = d - dt.timedelta(days=1)
    while not du(x):
        x -= dt.timedelta(days=1)
    return x


def local(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)


def corr(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=TZ) if s else None


def brl(v):
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dia(d):
    return f"{DIAS[d.weekday()]}, {d:%d/%m}"


def material(t):
    t = t.lower()
    return "linho" if "linho" in t else ("jacquard" if "jacquard" in t else ("veludo" if "veludo" in t else ""))


def dias_producao(li):
    h = (li.get("product") or {}).get("descriptionHtml") or ""
    t = re.sub(r"<[^>]+>", " ", h).replace("&nbsp;", " ").replace("\xa0", " ")
    m = re.search(r"produ[^.]{0,80}?([0-9]+)\s*dias?\s+úteis", t, re.I)
    return int(m.group(1)) if m else 0


_tk = None


def prazo_correios(servico, cep, postagem):
    global _tk
    _tk = _tk or etiqueta._token_correios()
    r = requests.get(f"https://api.correios.com.br/prazo/v1/nacional/{SERVICO[servico]}",
                     params={"cepOrigem": CEP_ORIGEM, "cepDestino": cep, "dtEvento": postagem.strftime("%d-%m-%Y")},
                     headers={"Authorization": "Bearer " + _tk}, timeout=30)
    return dt.date.fromisoformat(r.json()["dataMaxima"][:10])


def montar_pedido(nome, completo, detalhe, pagarme):
    o, d = completo, detalhe
    criado = local(o["createdAt"])
    pix = any("PIX" in g for g in o["paymentGatewayNames"])
    pagos = [t for t in o["transactions"] if t["status"] == "SUCCESS" and t["kind"] in ("SALE", "CAPTURE")]
    pago = local(pagos[0]["processedAt"]) if pagos else None
    pid = next((t["payment_id"] for t in d["transactions"] if t.get("payment_id")), None)
    cob = pagarme.get(pid, {})
    end = d["shippingAddress"] or {}
    primeiro = (d.get("customer") or {}).get("firstName") or end.get("firstName") or ""
    primeiro = primeiro.strip().split(" ")[0].capitalize()
    lis = d["lineItems"]["nodes"]
    lis_c = o["lineItems"]["nodes"]
    itens = []
    for li, lc in zip(lis, lis_c):
        img = (li.get("image") or {}).get("url") or ((li.get("product") or {}).get("featuredImage") or {}).get("url") or ""
        if img:
            img += ("&" if "?" in img else "?") + "width=300&height=450&crop=center"
        dp = dias_producao(lc)
        var = " · ".join(x for x in [li.get("variantTitle") or "", "sob encomenda" if dp else "", f"{li['quantity']} un."] if x)
        itens.append({"img": img, "nome": li["title"].replace(" — ", " - "), "variante": var,
                      "preco": brl(li["originalTotalSet"]["shopMoney"]["amount"]), "material": material(li["title"]), "dp": dp,
                      "cuidados": modelos.cuidados_da_descricao((lc.get("product") or {}).get("descriptionHtml"))})
    principal = max(itens, key=lambda i: modelos._valor(i["preco"]))
    palavra = principal["nome"].split(" ")[0].lower()
    genero = "f" if palavra in FEMININO else "m"
    modelo = re.split(r"\s[-—]\s", principal["nome"])
    peca = palavra if palavra != "bolsa" else "bolsa " + modelo[0].split(" ")[-1]
    dps = [i["dp"] for i in itens]
    dp = max(dps)
    misto = dp > 0 and min(dps) == 0
    frete = (o["shippingLines"]["nodes"][0]["title"] if o["shippingLines"]["nodes"] else "").upper()
    sub = float(d["subtotalPriceSet"]["shopMoney"]["amount"])
    desc = float(d["totalDiscountsSet"]["shopMoney"]["amount"])
    somas = [("Subtotal", brl(sub + desc if desc else sub)), (f"Frete · {frete}", brl(d["totalShippingPriceSet"]["shopMoney"]["amount"]))]
    if desc:
        somas.append(("Desconto Pix (5%)" if pix else "Desconto", "-" + brl(desc)))
    total = brl(d["totalPriceSet"]["shopMoney"]["amount"])
    pagamento = "Pix" if pix else ("Cartão de crédito" + (f", {cob['parcelas']}x" if cob.get("parcelas") and cob["parcelas"] > 1 else ""))
    base = {
        "nome": primeiro, "pedido": nome, "genero": genero, "peca": peca, "peca_nome": principal["nome"],
        "itens": itens, "pagamento": pagamento, "servico": frete, "dias_producao": dp, "misto": misto,
        "endereco": [end.get("name", ""), ", ".join(x for x in [end.get("address1"), end.get("address2")] if x),
                     f"{end.get('city', '')}, {end.get('provinceCode', '')} · {end.get('zip', '')}"],
        "roupa": any(not i["nome"].lower().startswith(("bolsa", "cinto")) for i in itens),
        "pesquisa_link": link_pagina("pesquisa", nome), "nota": "",
    }
    saida = []  # (quando, id do e-mail, dados extras)

    def ev(quando, qual, **extra):
        saida.append((quando, qual, {**base, **extra}))

    # Cartão aprova junto com o pedido: o 0 já sai na versão "pagamento aprovado".
    ev(criado, "e0", somas=somas + [("Total", total)], pago=bool(pago and (pago - criado).total_seconds() < 60))
    if pix and (not pago or (pago - criado).total_seconds() > 15 * 60):
        exp = local(cob["expira"]) if cob.get("expira") else criado + dt.timedelta(hours=1)
        ev(criado + dt.timedelta(minutes=15), "e1", somas=somas + [("Total a pagar", total)], pagamento="Pix, aguardando",
           hora_limite=f"{exp:%Hh%M}",
           pix={"valor": total, "codigo": cob.get("qr") or "", "link_copiar": link_pagina("pix", nome)})
    if not pago:
        return saida
    saida_final = _depois_do_pago(o, criado, pago, base, saida, ev, somas, total, pix, dp, misto, frete, end, itens)
    cancel = local(o["cancelledAt"]) if o.get("cancelledAt") else None
    if cancel:  # cancelado depois de pago: a régua para no cancelamento
        saida_final = [x for x in saida_final if x[0] <= cancel] + [(cancel, "cancelado", base)]
    return sorted(saida_final, key=lambda x: x[0])


def _depois_do_pago(o, criado, pago, base, saida, ev, somas, total, pix, dp, misto, frete, end, itens):
    b = criado.date()
    prazo, limite5 = (soma(b, 1), soma(b, 2)) if dp == 0 else (soma(b, dp), soma(b, dp))
    ev_rastro = []
    prevista, codigo = None, None
    for cd, rr in o["rastro"].items():
        if rr and rr.get("eventos"):
            ev_rastro += rr["eventos"]
            prevista = corr(rr.get("prevista")) or prevista
            codigo = cd
    po = sorted(corr(e["quando"]) for e in ev_rastro if e["codigo"] == "PO")
    postado = po[0] if po else None
    entrega_prev = prazo_correios(frete, end.get("zip", "").replace("-", ""), prazo)
    fase = "Produção" if dp else "Separação"
    # Cartão aprova no mesmo segundo do pedido: o 2 sai logo depois do 0.
    ev(max(pago, criado + dt.timedelta(seconds=1)), "e2_encomenda" if dp else "e2_pronta", somas=somas + [("Total", total)],
       pagamento=base["pagamento"] + (f", aprovado em {pago:%d/%m}" if pix else ""),
       data_postagem=dia(prazo), data_entrega=f"até {entrega_prev:%d/%m}",
       passos=[("feito", f"Pago<br>{pago:%d/%m}"), ("agora", f"{fase}<br>até {prazo:%d/%m}"), ("", "Postado"), ("", "Entregue")])
    meio_dia = lambda d_: dt.datetime.combine(d_, dt.time(12, 0), TZ)  # noqa: E731
    if dp:
        meio = soma(b, math.ceil(dp / 2))
        if (postado is None or postado > meio_dia(meio)) and meio_dia(meio) <= agora():
            ev(meio_dia(meio), "e3", data_postagem=dia(prazo), data_entrega=f"até {entrega_prev:%d/%m}",
               passos=[("feito", f"Pago<br>{pago:%d/%m}"), ("agora", f"Produção<br>até {prazo:%d/%m}"), ("", "Postado"), ("", "Entregue")])
        d4 = util_antes(prazo)
        if d4 > b and (postado is None or postado > meio_dia(d4)) and meio_dia(d4) <= agora():
            ev(meio_dia(d4), "e4", prazo=dia(prazo))
    dia5 = soma(limite5, 1)
    # Só sai se, na hora do envio (12h do dia seguinte ao limite), ainda não postou.
    if (postado is None or postado > meio_dia(dia5)) and meio_dia(dia5) <= agora():
        ev(meio_dia(dia5), "e5" if dp else "e5_pronta")
    if not postado:
        return saida
    link = f"https://rastreamento.correios.com.br/app/index.php?objetos={codigo}"
    rast = {"codigo": codigo, "link": link}
    prev_txt = dia(prevista.date()) if prevista else ""
    ev(postado, "e6", somas=somas + [("Total pago", total)], rastreio=rast, previsao_correios=prev_txt,
       data_postado=dia(postado.date()),
       passos=[("feito", f"Pago<br>{pago:%d/%m}"), ("feito", fase), ("agora", f"Postado<br>{postado:%d/%m}"), ("", "Entregue")])
    entregue = sorted(corr(e["quando"]) for e in ev_rastro if e["codigo"] in ("BDE", "BDI", "BDR") and e["tipo"] in ("01", "12", "23"))
    entregue = entregue[0] if entregue else None
    for e in sorted(ev_rastro, key=lambda e: e["quando"]):
        if e["codigo"] in ("BDE", "BDI", "BDR") and e["tipo"] == "46":
            q = corr(e["quando"])
            ev(q, "e7_tentativa", rastreio=rast, data_evento=f"{dia(q.date())} às {q:%Hh%M}",
               previsao_correios=prev_txt if prevista and prevista >= q else "")
            break
    for e in sorted(ev_rastro, key=lambda e: e["quando"]):
        # Em devolução ao remetente: sem e-mail; a loja chama a cliente (só Telegram).
        if "devolu" in (e["desc"] or "").lower():
            ev(corr(e["quando"]), "devolucao", rastreio=rast, evento=e["desc"])
            break
    for e in sorted(ev_rastro, key=lambda e: e["quando"]):
        if "aguardando retirada" in (e["desc"] or "").lower():
            q = corr(e["quando"])
            ev(q, "e7_retirada", rastreio=rast, agencia=[e.get("detalhe") or "Agência dos Correios indicada no rastreio"],
               prazo_retirada=dia((q + dt.timedelta(days=7)).date()))
            break
    if prevista and ((entregue and entregue > prevista) or (not entregue and prevista < agora())):
        ev(prevista + dt.timedelta(seconds=1), "e7_atrasada", rastreio=rast, previsao_correios=prev_txt)
    if entregue:
        ev(entregue, "e8")
        # O 9 sai 6 dias depois da entrega; se ainda não chegou o dia, vai marcado como futuro.
        ev(meio_dia((entregue + dt.timedelta(days=6)).date()), "e9")
    return saida
