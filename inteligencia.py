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


def _marca_visitas(ev: pd.DataFrame) -> pd.DataFrame:
    ev = ev.sort_values(["visitante", "em"]).copy()
    novo = (ev.visitante != ev.visitante.shift()) | (ev.em - ev.em.shift() > PAUSA)
    ev["visita"] = novo.cumsum()
    return ev


def buscas(ev: pd.DataFrame) -> tuple:
    """Buscas feitas navegando, e à parte as que abriram a visita.

    Um link que já cai na página de busca (o anúncio da arara com Relicário
    abre /search?q=jacquard relic) dispara o mesmo evento de busca sem a
    pessoa digitar nada; em 21/10 foram 91 "buscas" assim, que pareciam
    interesse e eram só o destino do anúncio. Busca na primeira página da
    visita conta como entrada por link, não como busca.
    """
    vazio = pd.DataFrame(columns=["Busca", "Vezes"])
    if ev.empty:
        return vazio, vazio
    ev = _marca_visitas(ev)
    primeira_pagina = ev[ev.evento == "page_viewed"].groupby("visita").em.min()
    b = ev[ev.evento == "search_submitted"].copy()
    b["termo"] = b.extra.fillna("").str.strip().str.lower()
    b = b[b.termo != ""]
    # Na primeira página da visita, ou com marca de link de anúncio no
    # endereço (quem clica no anúncio de novo na mesma visita): quem digita
    # na busca do site não traz utm nem fbclid.
    de_link = b.busca.fillna("").str.contains(r"utm_|fbclid", case=False)
    na_entrada = de_link | (b.em <= b.visita.map(primeira_pagina) + pd.Timedelta(seconds=2))
    contar = lambda x: x.termo.value_counts().rename_axis("Busca").reset_index(name="Vezes")
    return contar(b[~na_entrada]), contar(b[na_entrada])


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


# ─── Clarity ────────────────────────────────────────────────────────────────
# Números por página da API de exportação do Clarity (site/clarity.sql). O
# mapa de calor não exporta: cada página leva um link para ele no Clarity.

CLARITY_PROJETO = "z0rp8ouj62"


def _caminho(url: str) -> str:
    from urllib.parse import urlparse
    try:
        p = urlparse(url).path or "/"
    except Exception:
        return url or ""
    return p.rstrip("/") or "/"


def link_mapa_de_calor(caminho: str) -> str:
    """Abre o mapa de calor já na página: o Clarity só seleciona a página
    quando o endereço leva também o filtro URL (regra 2;6 = expressão, com ou
    sem query string), no formato que ele mesmo gera ao clicar na lista."""
    import re
    from urllib.parse import quote
    completa = "https://annis.store" + caminho
    regra = "2;6;^" + re.sub(r"([.?+*()\[\]{}|^$])", r"\\\1", completa) + r"(\?.*)?$"
    return (f"https://clarity.microsoft.com/projects/view/{CLARITY_PROJETO}/heatmaps"
            f"?date=Last%203%20days&heatmapDeviceType=0&heatmapType=0"
            f"&url={quote(completa, safe='')}&URL={quote(regra, safe='')}")


def clarity_paginas(coletas: list, minimo: int = 3) -> pd.DataFrame:
    """Uma linha por página, somando as coletas diárias do período.

    Cada coleta cobre as 24 horas anteriores. Uma coleta feita menos de 20
    horas antes da seguinte cobre o mesmo pedaço de tempo e sai da conta (foi
    o caso da coleta manual da instalação, em 20/10).
    """
    coletas = sorted(coletas, key=lambda c: c["coletado_em"])
    validas = [c for i, c in enumerate(coletas)
               if i == len(coletas) - 1
               or (coletas[i + 1]["coletado_em"] - c["coletado_em"]).total_seconds() >= 20 * 3600]
    linhas = []
    for c in validas:
        for m in c["dados"] or []:
            nome = m.get("metricName")
            for info in m.get("information") or []:
                linhas.append({"pagina": _caminho(info.get("Url", "")), "metrica": nome, **info})
    if not linhas:
        return pd.DataFrame()
    d = pd.DataFrame(linhas)
    d = d[~d.pagina.str.startswith("/checkouts")]
    tr = d[d.metrica == "Traffic"].groupby("pagina").totalSessionCount.sum()
    saida = pd.DataFrame({"visitas": tr})

    def media_pesada(metrica, campo):
        x = d[d.metrica == metrica][["pagina", campo]].merge(
            d[d.metrica == "Traffic"][["pagina", "totalSessionCount"]].groupby("pagina").sum(),
            left_on="pagina", right_index=True, how="left")
        x["peso"] = x.totalSessionCount.fillna(0)
        x["v"] = pd.to_numeric(x[campo], errors="coerce") * x.peso
        g = x.groupby("pagina")
        return g.v.sum() / g.peso.sum().replace(0, pd.NA)

    def taxa(metrica):
        x = d[d.metrica == metrica].copy()
        x["com"] = pd.to_numeric(x.sessionsCount, errors="coerce") * pd.to_numeric(
            x.sessionsWithMetricPercentage, errors="coerce") / 100
        g = x.groupby("pagina")
        return g.com.sum() / pd.to_numeric(g.sessionsCount.sum(), errors="coerce").replace(0, pd.NA)

    saida["rolagem"] = media_pesada("ScrollDepth", "averageScrollDepth")
    saida["ativo"] = media_pesada("EngagementTime", "activeTime")
    saida["volta"] = taxa("QuickbackClick")
    saida["raiva"] = taxa("RageClickCount")
    saida["morto"] = taxa("DeadClickCount")
    saida = saida[saida.visitas >= minimo].sort_values("visitas", ascending=False)

    def pct(x):
        return "" if pd.isna(x) else f"{x:.0%}"

    return pd.DataFrame({
        "Página": saida.index,
        "Visitas": saida.visitas.astype(int).values,
        "Rolagem média": [("" if pd.isna(x) else f"{x:.0f}%") for x in saida.rolagem],
        "Tempo ativo": [("" if pd.isna(x) else (f"{int(x // 60)}min{int(x % 60):02d}s" if x >= 60 else f"{int(x)}s"))
                        for x in saida.ativo],
        "Volta rápida": [pct(x) for x in saida.volta],
        "Clique de raiva": [pct(x) for x in saida.raiva],
        "Clique morto": [pct(x) for x in saida.morto],
        "Mapa de calor": [link_mapa_de_calor(p) for p in saida.index],
    })
