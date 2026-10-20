"""Inteligência do site, 20/10/2026: o que o pixel próprio grava (site.eventos,
ver site/pixel.js) transformado em visitas, funil, origens, páginas e checkouts
que pararam.

Visita = mesmo navegador (visitante) sem pausa de mais de 30 minutos, como o
Google Analytics conta sessão. Tempo numa página = até a próxima página da
mesma visita; a última página de cada visita não tem fim conhecido e fica de
fora da média.
"""

from urllib.parse import parse_qs

import pandas as pd

PAUSA = pd.Timedelta(minutes=30)

# Etapas na ordem do funil, com o nome que aparece no portal.
ETAPAS = [
    ("visita", "Entrou no site"),
    ("product_viewed", "Viu uma peça"),
    ("product_added_to_cart", "Pôs no carrinho"),
    ("checkout_started", "Abriu o checkout"),
    ("checkout_contact_info_submitted", "Preencheu o contato"),
    ("checkout_address_info_submitted", "Preencheu o endereço"),
    ("checkout_shipping_info_submitted", "Escolheu o frete"),
    ("payment_info_submitted", "Preencheu o pagamento"),
    ("checkout_completed", "Comprou"),
]
ETAPAS_CHECKOUT = [e for e, _ in ETAPAS[3:8]]
NOME_ETAPA = dict(ETAPAS)


def _utm(busca: str) -> dict:
    try:
        q = parse_qs((busca or "").lstrip("?"))
        return {k: (v[0] or "").lower() for k, v in q.items()}
    except Exception:
        return {}


def classificar_origem(origem: str, busca: str, pagina: str) -> str:
    """De onde veio a visita, pela primeira página dela."""
    u = _utm(busca)
    fonte, meio = u.get("utm_source", ""), u.get("utm_medium", "")
    o = (origem or "").lower()
    if "fbads" in fonte or meio in ("paid", "cpc", "ads", "paid_social") or "fbads" in meio:
        return "Anúncio Meta"
    if "n" in u and (pagina or "").startswith("/pages/entretempos"):
        return "Campanha WhatsApp"
    if "whatsapp" in fonte or "whatsapp" in o or o.startswith("wa.me") or o.endswith("wa.me"):
        return "WhatsApp"
    if fonte in ("ig", "instagram") or "instagram" in o:
        return "Instagram"
    if fonte in ("fb", "facebook") or "facebook" in o:
        return "Facebook"
    if "google" in fonte or "google" in o:
        return "Google"
    if fonte:
        return fonte.capitalize()
    if o:
        return o
    return "Direto"


def visitas(ev: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por visita, com origem, entrada, páginas, tempo e até onde foi."""
    if ev.empty:
        return pd.DataFrame()
    ev = ev.sort_values(["visitante", "em"]).copy()
    novo = (ev.visitante != ev.visitante.shift()) | (ev.em - ev.em.shift() > PAUSA)
    ev["visita"] = novo.cumsum()
    paginas = ev[ev.evento == "page_viewed"].copy()
    paginas["dur"] = (paginas.groupby("visita").em.shift(-1) - paginas.em).dt.total_seconds()
    paginas.loc[paginas.dur > PAUSA.total_seconds(), "dur"] = None
    primeira = ev.groupby("visita").first()
    v = pd.DataFrame({
        "visitante": primeira.visitante,
        "inicio": primeira.em,
        "fim": ev.groupby("visita").em.max(),
        "aparelho": primeira.aparelho,
        "entrada": primeira.pagina,
        "origem": [classificar_origem(o, b, p) for o, b, p in
                   zip(primeira.origem, primeira.busca, primeira.pagina)],
        "paginas": paginas.groupby("visita").size(),
        "tempo_s": paginas.groupby("visita").dur.sum(min_count=1),
    })
    v["paginas"] = v.paginas.fillna(0).astype(int)
    for etapa, _ in ETAPAS[1:]:
        v[etapa] = ev[ev.evento == etapa].groupby("visita").size().reindex(v.index).fillna(0).gt(0)
    v["valor"] = ev[ev.evento.isin(ETAPAS_CHECKOUT)].groupby("visita").valor.max().reindex(v.index)
    v["pedido"] = ev[ev.evento == "checkout_completed"].groupby("visita").pedido.first().reindex(v.index)
    return v.reset_index()


def funil(v: pd.DataFrame) -> pd.DataFrame:
    linhas, antes = [], None
    for etapa, nome in ETAPAS:
        n = len(v) if etapa == "visita" else int(v[etapa].sum())
        linhas.append({"Etapa": nome, "Visitas": n,
                       "Da etapa anterior": "" if antes is None else (f"{n / antes:.0%}" if antes else "—"),
                       "Do total": f"{n / len(v):.1%}" if len(v) else "—"})
        antes = n
    return pd.DataFrame(linhas)


def por_origem(v: pd.DataFrame) -> pd.DataFrame:
    g = v.groupby("origem").agg(
        Visitas=("visita", "size"), Carrinho=("product_added_to_cart", "sum"),
        Checkout=("checkout_started", "sum"), Compras=("checkout_completed", "sum"),
        Paginas=("paginas", "mean"))
    g["Conversão"] = (g.Compras / g.Visitas).map(lambda x: f"{x:.1%}")
    g["Páginas por visita"] = g.pop("Paginas").map(lambda x: f"{x:.1f}".replace(".", ","))
    return g.sort_values("Visitas", ascending=False).reset_index().rename(columns={"origem": "Origem"})


def paginas_de_entrada(v: pd.DataFrame, minimo: int = 3) -> pd.DataFrame:
    g = v.groupby("entrada").agg(Visitas=("visita", "size"),
                                 so_uma=("paginas", lambda p: (p <= 1).mean()),
                                 carrinho=("product_added_to_cart", "mean"),
                                 compras=("checkout_completed", "sum"))
    g = g[g.Visitas >= minimo].sort_values("Visitas", ascending=False)
    g["Saiu sem ver outra página"] = g.pop("so_uma").map(lambda x: f"{x:.0%}")
    g["Pôs no carrinho"] = g.pop("carrinho").map(lambda x: f"{x:.0%}")
    g = g.rename(columns={"compras": "Compras"})
    return g.reset_index().rename(columns={"entrada": "Página de entrada"})


def tempo_por_pagina(ev: pd.DataFrame, minimo: int = 3) -> pd.DataFrame:
    if ev.empty:
        return pd.DataFrame()
    p = ev[ev.evento == "page_viewed"].sort_values(["visitante", "em"]).copy()
    prox = p.groupby("visitante").em.shift(-1)
    p["dur"] = (prox - p.em).dt.total_seconds()
    p = p[p.dur.notna() & (p.dur <= PAUSA.total_seconds())]
    p = p[~p.pagina.fillna("").str.startswith("/checkouts")]
    g = p.groupby("pagina").dur.agg(["size", "median"])
    g = g[g["size"] >= minimo].sort_values("size", ascending=False)
    return pd.DataFrame({"Página": g.index, "Vezes vista": g["size"].values,
                         "Tempo típico": [f"{int(s // 60)}min{int(s % 60):02d}s" if s >= 60 else f"{int(s)}s"
                                          for s in g["median"].values]})


def pecas(ev: pd.DataFrame) -> pd.DataFrame:
    vistas = ev[ev.evento == "product_viewed"].groupby("produto").visitante.nunique()
    carrinho = ev[ev.evento == "product_added_to_cart"].groupby("produto").visitante.nunique()
    g = pd.DataFrame({"Pessoas que viram": vistas, "Puseram no carrinho": carrinho}).fillna(0).astype(int)
    g = g[g["Pessoas que viram"] > 0].sort_values("Pessoas que viram", ascending=False)
    g["Carrinho / vistas"] = (g["Puseram no carrinho"] / g["Pessoas que viram"]).map(lambda x: f"{x:.0%}")
    return g.reset_index().rename(columns={"produto": "Peça", "index": "Peça"})


def buscas(ev: pd.DataFrame) -> pd.DataFrame:
    b = ev[ev.evento == "search_submitted"].extra.fillna("").str.strip().str.lower()
    b = b[b != ""]
    return b.value_counts().rename_axis("Busca").reset_index(name="Vezes")


def checkouts_parados(v: pd.DataFrame) -> pd.DataFrame:
    p = v[v.checkout_started & ~v.checkout_completed].copy()
    if p.empty:
        return pd.DataFrame()

    def ultima(r):
        feitas = [e for e in ETAPAS_CHECKOUT if r[e]]
        return NOME_ETAPA[feitas[-1]] if feitas else NOME_ETAPA["checkout_started"]

    p["Parou em"] = p.apply(ultima, axis=1)
    p = p.sort_values("inicio", ascending=False)
    return pd.DataFrame({
        "Quando": p.inicio.dt.tz_convert("America/Sao_Paulo").dt.strftime("%d/%m %Hh%M"),
        "Origem": p.origem, "Aparelho": p.aparelho, "Parou em": p["Parou em"],
        "Valor": p.valor, "Páginas": p.paginas,
    })
