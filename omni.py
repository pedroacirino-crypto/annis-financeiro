"""Visão omni do financeiro: o site (digital) e a loja (físico) no mesmo portal.

Até 12/10/2026 só a aba Resultado enxergava a loja; as outras eram a conta da
Pagar.me, que é só o site. O Pedro pediu o portal inteiro omni, com um
seletor no canto superior direito: consolidado (padrão), só digital ou só
físico, valendo para todas as abas do financeiro.

De onde vem cada canal:

  Digital  cobranças pagas da Pagar.me (o checkout do site).
  Físico   maquininha e link no cartão pela API de Conciliação da Stone
           (fin_vendas_stone, com bruto e líquido de cada venda) e Pix
           direto e link Pix pelo extrato da conta Stone, que é o único lugar
           onde eles aparecem. O Pix só chega até o último extrato carregado.

Valores em centavos, como o resto do app (fmt_brl recebe centavos).
"""

import pandas as pd

CONSOLIDADO, DIGITAL, FISICO = "Consolidado", "Digital", "Físico"
VISOES = [CONSOLIDADO, DIGITAL, FISICO]

MEIO_PAGARME = {"credit_card": "Crédito", "debit_card": "Débito", "pix": "Pix", "boleto": "Boleto"}


def ver_digital(visao: str) -> bool:
    return visao in (CONSOLIDADO, DIGITAL)


def ver_fisico(visao: str) -> bool:
    return visao in (CONSOLIDADO, FISICO)


def _janela(df: pd.DataFrame, ini, fim) -> pd.DataFrame:
    if df.empty:
        return df
    q = df["quando"]
    if ini is not None:
        df = df[q >= pd.Timestamp(ini)]
        q = df["quando"]
    if fim is not None:
        df = df[q < pd.Timestamp(fim) + pd.Timedelta(days=1)]
    return df


COLUNAS = ["quando", "canal", "origem", "meio", "parcelas", "bruto", "liquido", "cliente", "id"]


def vendas_digitais(cobrancas: list, para_brt) -> pd.DataFrame:
    """Cobranças pagas da Pagar.me no formato comum. `cobrancas` vem de
    db.query_charges, já recortado no período."""
    df = pd.DataFrame(cobrancas)
    if df.empty:
        return pd.DataFrame(columns=COLUNAS)
    df = df[df["status"] == "paid"].copy()
    if df.empty:
        return pd.DataFrame(columns=COLUNAS)
    quando = para_brt(pd.to_datetime(df["created_at"], errors="coerce", utc=True)).dt.tz_localize(None)
    return pd.DataFrame({
        "quando": quando,
        "canal": DIGITAL,
        "origem": "Site",
        "meio": df["payment_method"].map(lambda m: MEIO_PAGARME.get(m, m)),
        "parcelas": pd.to_numeric(df.get("installments"), errors="coerce").fillna(1).astype(int),
        "bruto": df["amount"].astype(int),
        "liquido": pd.NA,
        "cliente": df.get("customer_name", pd.Series("", index=df.index)).fillna(""),
        "id": df["id"],
    })


def vendas_fisicas(ini=None, fim=None) -> pd.DataFrame:
    """Maquininha, link no cartão e Pix direto, uma linha por venda."""
    import dados_fin
    import extrato
    partes = []
    try:
        vs = dados_fin.ler_vendas_stone()
    except Exception:
        vs = pd.DataFrame()
    if not vs.empty:
        ok = vs[(vs.status == "Aprovada") & vs.captura.isin(["POS", "E-commerce"])]
        partes.append(pd.DataFrame({
            "quando": pd.to_datetime(ok.data),
            "canal": FISICO,
            "origem": ok.captura.map({"POS": "Maquininha", "E-commerce": "Link no cartão"}),
            "meio": ok.produto.map({"Credito": "Crédito", "Debito": "Débito"}).fillna(ok.produto),
            "parcelas": pd.to_numeric(ok.parcelas, errors="coerce").fillna(1).astype(int),
            "bruto": (ok.bruto * 100).round().astype(int),
            "liquido": (ok.liquido * 100).round().astype(int),
            "cliente": ok.cartao.fillna("").map(lambda c: f"cartão {c}" if c else ""),
            "id": ok.stone_id,
        }))
    try:
        ex = extrato.carregar()
    except Exception:
        ex = pd.DataFrame()
    if not ex.empty:
        px = ex[(ex.natureza == "venda_fisica") & ex.categoria.isin(["Pix direto", "Link de pagamento"])]
        partes.append(pd.DataFrame({
            "quando": pd.to_datetime(px.data),
            "canal": FISICO,
            "origem": px.categoria.map({"Pix direto": "Pix direto", "Link de pagamento": "Link Pix"}),
            "meio": "Pix",
            "parcelas": 1,
            "bruto": (px.valor * 100).round().astype(int),
            "liquido": (px.valor * 100).round().astype(int),
            "cliente": px.contraparte.fillna("").str.title(),
            "id": "pix-" + px.data.astype(str) + "-" + px.valor.astype(str),
        }))
    if not partes:
        return pd.DataFrame(columns=COLUNAS)
    df = pd.concat(partes, ignore_index=True)
    return _janela(df, ini, fim).sort_values("quando").reset_index(drop=True)


def vendas(visao: str, cobrancas: list, para_brt, ini=None, fim=None) -> pd.DataFrame:
    partes = []
    if ver_digital(visao):
        partes.append(vendas_digitais(cobrancas, para_brt))
    if ver_fisico(visao):
        partes.append(vendas_fisicas(ini, fim))
    partes = [p for p in partes if not p.empty]
    if not partes:
        return pd.DataFrame(columns=COLUNAS)
    return pd.concat(partes, ignore_index=True).sort_values("quando").reset_index(drop=True)


def ultimo_extrato():
    """Data do último movimento do extrato da Stone carregado, ou None."""
    import extrato
    try:
        ex = extrato.carregar()
    except Exception:
        return None
    return None if ex.empty else pd.to_datetime(ex.data.max())


def _dia_util_seguinte(d: pd.Timestamp) -> pd.Timestamp:
    from avisos.envios import FERIADOS
    d = d.normalize() + pd.Timedelta(days=1)
    while d.weekday() >= 5 or d.strftime("%Y-%m-%d") in FERIADOS:
        d += pd.Timedelta(days=1)
    return d


def a_receber_fisico() -> pd.DataFrame:
    """Venda da Stone que ainda não apareceu como repasse no extrato.

    A Stone repassa a maquininha no dia útil seguinte, já líquida (inclusive
    o link parcelado, que cai de uma vez), e cada repasse da madrugada cobre
    a venda do dia anterior. Então o que falta receber é a venda feita a
    partir do dia do último repasse visto no extrato. Como o extrato chega
    por carga manual, isso é previsão: o dinheiro pode já ter caído e só não
    estar no extrato ainda.
    """
    import extrato
    vf = vendas_fisicas()
    if vf.empty:
        return pd.DataFrame(columns=["dia", "liquido", "vendas", "vendido_em"])
    stone = vf[vf.origem.isin(["Maquininha", "Link no cartão"])]
    try:
        ex = extrato.carregar()
        rep = ex[(ex.natureza == "venda_fisica") & (ex.categoria == "Maquininha (líquido)")]
        corte = pd.to_datetime(rep.data.max()).normalize() if not rep.empty else None
    except Exception:
        corte = None
    pend = stone if corte is None else stone[stone.quando >= corte]
    if pend.empty:
        return pd.DataFrame(columns=["dia", "liquido", "vendas", "vendido_em"])
    pend = pend.assign(dia=pend.quando.map(_dia_util_seguinte))
    return (pend.groupby("dia")
            .agg(liquido=("liquido", "sum"), vendas=("id", "count"), vendido_em=("quando", "min"))
            .reset_index())


def conciliacao_fisica(ini=None, fim=None) -> pd.DataFrame:
    """Cada dia de venda na Stone contra os repasses que caíram na conta.

    Repasse não traz o id da venda e o link parcelado chega em mais de um
    lançamento, então a conferência é por dia: o líquido vendido num dia tem
    que aparecer, somado, nos repasses seguintes, na ordem.
    """
    import extrato
    vf = vendas_fisicas()
    stone = vf[vf.origem.isin(["Maquininha", "Link no cartão"])] if not vf.empty else vf
    if stone.empty:
        return pd.DataFrame()
    dias = (stone.assign(dia=stone.quando.dt.normalize())
            .groupby("dia").agg(bruto=("bruto", "sum"), liquido=("liquido", "sum"), vendas=("id", "count"))
            .reset_index().sort_values("dia"))
    try:
        ex = extrato.carregar()
        rep = ex[(ex.natureza == "venda_fisica") & (ex.categoria == "Maquininha (líquido)")]
        rep = rep.assign(centavos=(rep.valor * 100).round().astype(int)).sort_values("data")
        fim_ex = pd.to_datetime(ex.data.max())
    except Exception:
        rep, fim_ex = pd.DataFrame(columns=["data", "centavos"]), None
    creditos = list(zip(pd.to_datetime(rep.data), rep.centavos))
    linhas, i = [], 0
    for r in dias.itertuples(index=False):
        while i < len(creditos) and creditos[i][0].normalize() <= r.dia:
            i += 1      # repasse anterior à venda é de outro dia
        soma, usados, quando = 0, 0, None
        j = i
        while j < len(creditos) and soma < r.liquido - 100:
            soma += creditos[j][1]
            quando = creditos[j][0]
            j += 1
            usados += 1
        if usados and abs(soma - r.liquido) <= 100:
            situacao, i = "Repassado", j
        elif fim_ex is not None and _dia_util_seguinte(r.dia) > fim_ex.normalize():
            situacao, soma, quando = "Pendente", 0, None
        else:
            situacao, soma, quando = "Sem repasse no extrato", 0, None
        linhas.append({"vendido_em": r.dia, "vendas": r.vendas, "bruto": r.bruto, "liquido": r.liquido,
                       "repassado": soma, "repassado_em": quando, "situacao": situacao})
    df = pd.DataFrame(linhas)
    if ini is not None:
        df = df[df.vendido_em >= pd.Timestamp(ini)]
    if fim is not None:
        df = df[df.vendido_em <= pd.Timestamp(fim)]
    return df


def extrato_stone(ini=None, fim=None, so_fisico: bool = False) -> pd.DataFrame:
    """Conta da Stone, para onde vai todo o dinheiro: o repasse do site, a
    maquininha, o Pix direto e todas as saídas. Com `so_fisico`, só as
    entradas de venda física."""
    import dados_fin
    import extrato
    try:
        cru = dados_fin.ler_extrato()
        ex = extrato.carregar()
    except Exception:
        return pd.DataFrame()
    if cru.empty:
        return pd.DataFrame()
    df = cru.copy()
    df["quando"] = pd.to_datetime(df.data)
    cls = ex[["data", "valor", "contraparte", "natureza", "categoria"]].copy()
    cls["quando"] = pd.to_datetime(cls.data)
    df["valor"] = df.valor.astype(float)
    df = df.merge(cls.drop(columns="data"), on=["quando", "valor", "contraparte"], how="left")
    df = df.drop_duplicates(subset=["quando", "valor", "contraparte", "saldo_depois"])
    if so_fisico:
        df = df[df.natureza == "venda_fisica"]
    return _janela(df.sort_values("quando"), ini, fim).reset_index(drop=True)
