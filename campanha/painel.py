"""Números do Novembro Annis para a aba do portal (Trabalho > Novembro Annis), 30/10/2026.

Cinco blocos, cada um uma função que devolve dados prontos para a tela:

  vendas()    pedidos do site desde 01/11, peça a peça, separando sale e fora
              da sale (variante em campanha.sale_precos), vendas da loja física
              (omni.py) e outubro inteiro para comparar
  trafego()   visitas do site e da coleção da sale, por dia, por origem e por
              anúncio, e quem abriu ou pôs no carrinho cada peça (site.eventos)
  sale()      peças da sale: vendidas, desconto dado e estoque que resta
  cashback()  emitido, usado, em aberto, cancelado e vencido (campanha.cashback)
  emails()    por e-mail de divulgação: enviados, abriram, clicaram, compraram
              depois de receber e saíram da lista

Pedido cancelado ou de teste não entra; devolução entra pelo que ficou
(currentQuantity). Pix leva 5% do app de pagamento que a Shopify não enxerga,
então a receita do Pix aparece cheia.
"""

import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inteligencia  # noqa: E402
import nuvem  # noqa: E402
import omni  # noqa: E402
import pandas as pd  # noqa: E402
import shopify_client  # noqa: E402
from sqlalchemy import text  # noqa: E402

BRT = timezone(timedelta(hours=-3))
INICIO = datetime(2026, 11, 1, tzinfo=BRT)
FIM = datetime(2026, 12, 1, tzinfo=BRT)
COLECAO = "/collections/novembro-annis"
ANUNCIO_NOVEMBRO = "Anúncios do Novembro Annis"
ANUNCIO_OUTROS = "Outros anúncios"

_PEDIDOS = """query($c:String, $q:String!){orders(first:100, after:$c, query:$q){pageInfo{hasNextPage endCursor}
  nodes{name createdAt email sourceName cancelledAt test discountCodes
    currentTotalPriceSet{shopMoney{amount}}
    lineItems(first:50){nodes{quantity currentQuantity variant{id}
      originalUnitPriceSet{shopMoney{amount}}
      discountedUnitPriceAfterAllDiscountsSet{shopMoney{amount}}}}}}}"""


def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pedidos(de: datetime, ate: datetime) -> list:
    """Pedidos do site entre as datas, sem cancelados e testes."""
    q = f"created_at:>='{_utc(de)}' created_at:<'{_utc(ate)}'"
    saida, c = [], None
    while True:
        d = shopify_client._graphql(_PEDIDOS, {"c": c, "q": q})["orders"]
        saida += [o for o in d["nodes"] if o["sourceName"] == "web" and not o["cancelledAt"] and not o["test"]]
        if not d["pageInfo"]["hasNextPage"]:
            return saida
        c = d["pageInfo"]["endCursor"]


def _sale_precos() -> dict:
    with nuvem._conectar().connect() as con:
        return {r["variante_id"]: dict(r) for r in con.execute(text(
            "select variante_id, produto_id, produto, tamanho, preco_antes, preco_sale, riscado_sale, desconto"
            " from campanha.sale_precos")).mappings()}


def vendas(agora: datetime = None) -> dict:
    """pedidos: um por pedido (data, total, sale, fora, cupons, e-mail).
    dias: receita por dia e por tipo. itens: cada peça vendida.
    dias_corridos: dias desde o início da campanha, com fração do dia de hoje.
    fisico: pagamentos da loja (maquininha, link e Pix); as peças da loja vêm das
            vendas registradas (financeiro.pecas_loja).
    fisico_ate: último dia que a loja está lida (conciliação da Stone e extrato).
    outubro: site e loja do mês inteiro, régua da média por dia."""
    agora = agora or datetime.now(BRT)
    sp = _sale_precos()
    pedidos, itens = [], []
    for o in _pedidos(INICIO, min(agora + timedelta(minutes=1), FIM)):
        dia = datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(BRT)
        na_sale = fora = 0.0
        for li in o["lineItems"]["nodes"]:
            q = li["currentQuantity"] or 0
            if q <= 0 or not li["variant"]:
                continue
            vid = li["variant"]["id"]
            unit = float(li["discountedUnitPriceAfterAllDiscountsSet"]["shopMoney"]["amount"])
            if vid in sp:
                na_sale += unit * q
            else:
                fora += unit * q
            itens.append({"pedido": o["name"], "dia": dia, "variante_id": vid, "qtd": q, "valor": unit * q,
                          "sale": vid in sp})
        pedidos.append({"pedido": o["name"], "dia": dia, "email": (o["email"] or "").lower(),
                        "total": float(o["currentTotalPriceSet"]["shopMoney"]["amount"]),
                        "sale": na_sale, "fora": fora, "cupons": o["discountCodes"]})
    # Outubro inteiro: comparar dia a dia oscila com o pico do lançamento (04/10).
    outubro = _pedidos(datetime(2026, 10, 1, tzinfo=BRT), INICIO)
    fis_out = omni.vendas_fisicas("2026-10-01", "2026-10-31")
    fis = omni.vendas_fisicas("2026-11-01", min(agora, FIM - timedelta(days=1)).date())
    lidos = [d.date() if isinstance(d, datetime) else d for d in (omni.ultimo_extrato(), omni.stone_conciliada_ate()) if d]
    return {
        "pedidos": pedidos, "itens": itens,
        "fisico": [{"dia": q, "valor": b / 100} for q, b in zip(fis.quando, fis.bruto)],
        "fisico_ate": min(lidos) if lidos else None,
        "dias_corridos": max(min(agora, FIM) - INICIO, timedelta(0)) / timedelta(days=1),
        "outubro": {"receita": sum(float(o["currentTotalPriceSet"]["shopMoney"]["amount"]) for o in outubro),
                    "pedidos": len(outubro), "dias": 31,
                    "pecas": sum(li["currentQuantity"] or 0 for o in outubro for li in o["lineItems"]["nodes"]),
                    "fisico": float(fis_out.bruto.sum()) / 100, "fisico_vendas": len(fis_out)},
    }


def _origem(origem: str, busca: str, pagina: str) -> str:
    """Origem da visita, separando os anúncios da campanha dos demais."""
    o = inteligencia.classificar_origem(origem, busca, pagina)
    if o != "Anúncio Meta":
        return o
    u = inteligencia._utm(busca)
    return ANUNCIO_NOVEMBRO if "novembro-annis" in u.get("utm_content", "") + u.get("utm_medium", "") else ANUNCIO_OUTROS


def _anuncio(busca: str) -> str:
    """Nome curto do anúncio: ad_auto_novembro-annis1_categoria_imagem_31-10 vira novembro-annis1."""
    c = inteligencia._utm(busca).get("utm_content", "").replace(" ", "+")
    if not c.startswith("ad_auto_") or c.count("_") < 3:
        return "Outros"
    return c.split("_")[2]


def trafego(agora: datetime = None) -> dict:
    """Visita = mesmo navegador sem pausa de 30 minutos, como na aba Inteligência.
    O e-mail de 01/11 saiu sem marcação no link e a visita dele cai em Direto;
    os seguintes aparecem como E-mail."""
    agora = agora or datetime.now(BRT)
    ate = min(agora, FIM)
    antes = INICIO - timedelta(days=7)
    with nuvem._conectar().connect() as con:
        ev = pd.read_sql_query(text(
            "select em, visitante, evento, pagina, busca, origem, produto from site.eventos"
            " where em >= :de and em < :ate"), con, params={"de": antes, "ate": ate}, parse_dates=["em"])
    da_sale = {s["produto"] for s in _sale_precos().values()}
    vazio = {"visitas": 0, "pessoas": 0, "por_dia_antes": 0.0, "por_dia": None, "colecao": 0, "peca_sale": 0,
             "carrinho_sale": 0, "carrinho": 0, "compras": 0, "dias": [], "origens": [], "anuncios": [], "pecas": {}}
    if ev.empty:
        return vazio
    ev = inteligencia._marca_visitas(ev)
    ev["sale"] = ev.produto.isin(da_sale)
    ev["na_colecao"] = ev.pagina.fillna("").str.startswith(COLECAO)
    prim = ev.groupby("visita").first()
    v = pd.DataFrame({
        "visitante": prim.visitante, "inicio": prim.em,
        "origem": [_origem(o, b, p) for o, b, p in zip(prim.origem, prim.busca, prim.pagina)],
        "anuncio": [_anuncio(b) for b in prim.busca],
    })
    grupo = lambda m: ev[m].groupby("visita").size().reindex(v.index).fillna(0).gt(0)
    v["colecao"] = grupo(ev.na_colecao)
    v["peca_sale"] = grupo((ev.evento == "product_viewed") & ev.sale)
    v["carrinho_sale"] = grupo((ev.evento == "product_added_to_cart") & ev.sale)
    v["carrinho"] = grupo(ev.evento == "product_added_to_cart")
    v["comprou"] = grupo(ev.evento == "checkout_completed")
    v["dia"] = v.inicio.dt.tz_convert(BRT).dt.date

    inicio = pd.Timestamp(INICIO)
    previas = v[v.inicio < inicio]
    v = v[v.inicio >= inicio]
    camp = ev[ev.em >= inicio]

    def _resumo(g) -> dict:
        return {"visitas": len(g), "colecao": int(g.colecao.sum()), "peca_sale": int(g.peca_sale.sum()),
                "carrinho_sale": int(g.carrinho_sale.sum()), "carrinho": int(g.carrinho.sum()),
                "compras": int(g.comprou.sum())}

    anuncios = v[v.origem.isin([ANUNCIO_NOVEMBRO, ANUNCIO_OUTROS])]
    # Cada dia da campanha aparece, mesmo sem visita, para o gráfico não pular dia.
    hoje = agora.astimezone(BRT).date()
    n_dias = (min(hoje, (FIM - timedelta(seconds=1)).date()) - INICIO.date()).days + 1
    dias = [{"dia": d, **_resumo(v[v.dia == d])}
            for d in (INICIO.date() + timedelta(days=i) for i in range(max(n_dias, 0)))]
    pecas = {}
    for evento, campo in (("product_viewed", "viram"), ("product_added_to_cart", "carrinho")):
        for prod, n in camp[(camp.evento == evento) & camp.sale].groupby("produto").visitante.nunique().items():
            pecas.setdefault(prod, {"viram": 0, "carrinho": 0})[campo] = int(n)
    fechados = [d["visitas"] for d in dias if d["dia"] < hoje]
    return {
        **_resumo(v), "pessoas": int(v.visitante.nunique()), "dias": dias, "por_dia_antes": len(previas) / 7,
        "por_dia": sum(fechados) / len(fechados) if fechados else None,
        "origens": sorted(({"origem": o, **_resumo(g)} for o, g in v.groupby("origem")), key=lambda x: -x["visitas"]),
        "anuncios": sorted(({"anuncio": a, "campanha": g.origem.iloc[0] == ANUNCIO_NOVEMBRO, **_resumo(g)}
                            for a, g in anuncios.groupby("anuncio")), key=lambda x: -x["visitas"]),
        "pecas": pecas,
    }


_ESTOQUE = """query($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id inventoryQuantity}}}"""


def sale(itens: list) -> list:
    """Uma linha por peça da sale: vendidas, receita, desconto dado (sobre o
    preço de antes da campanha) e tamanhos que ainda têm estoque."""
    sp = _sale_precos()
    estoque = {}
    ids = list(sp)
    for i in range(0, len(ids), 100):
        for n in shopify_client._graphql(_ESTOQUE, {"ids": ids[i:i + 100]})["nodes"]:
            if n:
                estoque[n["id"]] = max(0, n["inventoryQuantity"] or 0)
    vendidas = Counter()
    receita = defaultdict(float)
    for it in itens:
        if it["sale"]:
            vendidas[it["variante_id"]] += it["qtd"]
            receita[it["variante_id"]] += it["valor"]
    por_peca = {}
    for vid, s in sp.items():
        p = por_peca.setdefault(s["produto"], {"peca": s["produto"], "desconto": 0.0, "vendidas": 0, "receita": 0.0,
                                               "desconto_dado": 0.0, "estoque": 0, "restam": []})
        p["desconto"] = max(p["desconto"], float(s["desconto"]))
        p["vendidas"] += vendidas[vid]
        p["receita"] += receita[vid]
        p["desconto_dado"] += max(0.0, float(s["preco_antes"]) - float(s["preco_sale"])) * vendidas[vid]
        p["estoque"] += estoque.get(vid, 0)
        if estoque.get(vid, 0) > 0:
            p["restam"].append(f"{s['tamanho']} ({estoque[vid]})")
    return sorted(por_peca.values(), key=lambda p: (-p["vendidas"], -p["desconto"], p["peca"]))


def cashback(pedidos: list) -> dict:
    from campanha.cashback import credito_de
    with nuvem._conectar().connect() as con:
        linhas = [dict(r) for r in con.execute(text(
            "select pedido, base, valor, situacao, valor_usado, pedido_uso, emitido_em from campanha.cashback")).mappings()]
    raiz = [l for l in linhas if "-" not in l["pedido"]]
    soma = lambda ls, f: sum(float(f(l) or 0) for l in ls)
    usados = [l for l in linhas if l["situacao"] == "usado"]
    total_de = {p["pedido"]: p["total"] for p in pedidos}
    return {
        "emitidos": len(raiz),
        "emitido": soma(raiz, lambda l: credito_de(float(l["base"]))),
        "usados": len(usados),
        "usado": soma(usados, lambda l: l["valor_usado"] if l["valor_usado"] is not None else l["valor"]),
        "vendas_com_cashback": sum(total_de.get(l["pedido_uso"], 0) for l in usados),
        "abertos": len([l for l in linhas if l["situacao"] == "ativo"]),
        "aberto": soma([l for l in linhas if l["situacao"] == "ativo"], lambda l: l["valor"]),
        "cancelados": len([l for l in raiz if l["situacao"] == "cancelado"]),
        "cancelado": soma([l for l in raiz if l["situacao"] == "cancelado"], lambda l: credito_de(float(l["base"]))),
        "vencido": soma([l for l in linhas if l["situacao"] == "vencido"], lambda l: l["valor"]),
    }


NOMES_EMAIL = {"d1": "1 · Começou (01/11)", "d2a": "2a · Lembrete, não abriu (04/11)",
               "d2b": "2b · Lembrete, abriu sem clicar (04/11)", "d3": "3 · Lançamento", "d4": "4 · Últimos dias (26/11)"}


def emails(pedidos: list) -> list:
    """Por e-mail: enviados, abriram, clicaram, compraram depois de receber, saíram."""
    with nuvem._conectar().connect() as con:
        envios = [dict(r) for r in con.execute(text(
            "select e.token, e.email, e.grupo, e.qual, e.enviado_em,"
            " exists(select 1 from campanha.divulgacao_aberturas a where a.token = e.token) as abriu,"
            " exists(select 1 from avisos.regua_cliques c where c.pedido = 'div:' || e.token) as clicou,"
            " exists(select 1 from campanha.descadastro d where d.email = e.email and d.em >= e.enviado_em) as saiu"
            " from campanha.divulgacao_envios e")).mappings()]
    compras = defaultdict(list)
    for p in pedidos:
        if p["email"]:
            compras[p["email"]].append(p)
    saida = []
    for qual in NOMES_EMAIL:
        es = [e for e in envios if e["qual"] == qual]
        if not es:
            continue
        compraram = [e for e in es if any(p["dia"] >= e["enviado_em"] for p in compras.get(e["email"], []))]
        saida.append({
            "qual": qual, "email": NOMES_EMAIL[qual], "enviados": len(es),
            "clientes": len([e for e in es if e["grupo"] == "cliente"]),
            "abriram": len([e for e in es if e["abriu"]]), "clicaram": len([e for e in es if e["clicou"]]),
            "compraram": len(compraram),
            "receita": sum(p["total"] for e in compraram for p in compras[e["email"]] if p["dia"] >= e["enviado_em"]),
            "sairam": len([e for e in es if e["saiu"]]),
        })
    return saida


if __name__ == "__main__":
    agora = datetime(2026, 10, 30, 22, tzinfo=BRT) if "--outubro" in sys.argv else None
    v = vendas(agora)
    print(len(v["pedidos"]), "pedidos;", v["outubro"])
    t = trafego()
    print({k: t[k] for k in ("visitas", "pessoas", "por_dia_antes", "colecao", "peca_sale", "carrinho_sale")})
    print(t["origens"], t["anuncios"][:5])
    print(sale(v["itens"])[:3])
    print(cashback(v["pedidos"]))
    print(emails(v["pedidos"]))
