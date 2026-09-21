"""Resultado da empresa: PnL por competência, PnL de caixa, payback e VPL.

Três fontes, cada uma dona de uma parte:

  - Shopify: receita e peças vendidas. Em divergência, ela manda.
  - Pagar.me: taxas, estornos e a data em que o dinheiro cai.
  - Extrato da conta Stone (extrato.py): todo custo pago desde 25/03/2025,
    a venda física (maquininha e Pix direto) e os aportes. É a verdade do
    caixa.
  - Planilha legada (2025): fichas técnicas com o custo por peça, e as
    compras pagas do bolso antes de a conta existir (jan a mar/2025).

Competência e caixa divergem justamente no estoque: tecido e facção pagos
são saída de caixa no mês do pagamento, mas só viram custo (CMV) quando a
peça é vendida. Na moda é isso que quebra empresa lucrativa, então as duas
visões andam lado a lado.

Valores em reais (float), datas em horário de Brasília.
"""

import os
import re
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

import db
from memo import memo

LEGADO = os.path.join(os.path.dirname(__file__), "legado", "planilha_2025.xlsx")

# Alíquota efetiva do Simples informada pelo Pedro em 20/09/2026. A planilha
# de precificação usava 2,85%.
IMPOSTO_PADRAO = 0.0264
TAXA_DESCONTO_MENSAL_PADRAO = 0.010

# A planilha chama as peças pelo nome de desenvolvimento; a loja, pelo nome
# de coleção. Regras em ordem, a primeira que casar vence. Marcadas "a
# confirmar" porque foram deduzidas por tecido e preço, não informadas.
MAPA_FICHAS = [
    (r"^Top .*Giverny", "Top Alça Peplum"),
    (r"^Saia .*Giverny", "Saia Midi Reta"),
    (r"Monet", "Vestido"),
    (r"^Bolsa de Jacquard Rel", "Bolsa Jacquard"),
    (r"Loulou", "Vestido Jacquard Loulou"),
    (r"^Vestido .*Relic[aá]rio", "Vestido Jacquard Relicário"),
    (r"^Corset .*Joan", "Top estruturado"),
    (r"^Cal[çc]a .*Joan", "Calça marrom"),
    (r"^Top de Linho .*Bloom", "Top Balonê Linho"),
    (r"Blazer", "Blazer"),
    (r"^Colete .*Klint", "Colete Jacquard"),
    (r"Provence", "Vestido Midi Cinto"),
    (r"^Cinto", "Cinto"),
    (r"^Cal[çc]a .*Trama", "Calça Trama"),
    (r"^Colete .*Trama", "Colete Trama"),
    (r"Heran[çc]a", "Camisa Herança"),
]

# Investimento em Meta Ads por mês, dos relatórios mensais da agência (PROADZ,
# depois V60), repassados pelo Pedro em 20/09/2026. É pago no cartão das
# sócias e reembolsado pela conta, então no extrato aparece diluído em
# "Pagas no cartão das sócias"; aqui entra pelo mês em que rodou. Setembro
# de 2026 é a meta do mês (R$ 2.500) proporcional aos 20 dias lidos.
META_ADS = {
    "2025-09": 992.97, "2025-10": 978.17, "2025-11": 1454.17, "2025-12": 1318.25,
    "2026-01": 1388.27, "2026-02": 1180.67, "2026-03": 1571.90, "2026-04": 1387.21,
    "2026-05": 1617.17, "2026-06": 1836.82, "2026-07": 1626.43, "2026-08": 2339.92,
    "2026-09": 2500.00 * 20 / 30,
}
CATEGORIA_CARTAO = "Pagas no cartão das sócias (anúncios, aluguel, outras)"


@memo()
def meta_ads() -> dict:
    """Meta por mês: Supabase, com o dicionário acima como reserva local."""
    import dados_fin
    nuvem_meta = dados_fin.ler_meta_ads() if dados_fin.disponivel() else {}
    return nuvem_meta or dict(META_ADS)

_TAMANHO = re.compile(r"\s*-\s*(PP|P|M|G|GG|U|\d{2})$")
_ITEM = re.compile(r"^(\d+)x\s+(.+?)(?:\s+\(([^)]*)\))?$")


# ─── Planilha legada ────────────────────────────────────────────────────────

def legado_disponivel() -> bool:
    import dados_fin
    return (dados_fin.disponivel() and not dados_fin.ler_fichas().empty) or os.path.exists(LEGADO)


@memo()
def carregar_legado() -> dict:
    """Fichas e lançamentos pré-conta. Supabase primeiro; sem ele, a planilha
    em legado/. Cache: nada disso muda."""
    import dados_fin
    vazio = {"lancamentos": pd.DataFrame(), "fichas": pd.DataFrame(),
             "custo_pecas": pd.DataFrame(), "insumos": pd.DataFrame()}
    if dados_fin.disponivel():
        fichas_nuvem = dados_fin.ler_fichas()
        if not fichas_nuvem.empty:
            return {"lancamentos": dados_fin.ler_lancamentos_legado(), "fichas": pd.DataFrame(),
                    "custo_pecas": fichas_nuvem, "insumos": pd.DataFrame()}
    if not os.path.exists(LEGADO):
        return vazio
    return ler_planilha_legada()


def ler_planilha_legada() -> dict:
    """Lê a planilha de 2025 direto do xlsx. É o que a carga inicial usa."""
    lanc = pd.read_excel(LEGADO, sheet_name="Lançamento", usecols="A:L")
    lanc = lanc.dropna(subset=["Valor"]).copy()
    lanc.columns = ["codigo", "descricao", "data", "setor", "atividade", "tipo",
                    "categoria", "terceiro", "parcelas", "forma", "valor", "status"]
    lanc["data"] = pd.to_datetime(lanc["data"], errors="coerce")
    lanc["parcelas"] = lanc["parcelas"].fillna(1).astype(int).clip(lower=1)
    lanc["valor"] = lanc["valor"].astype(float)
    for c in ("descricao", "setor", "atividade", "tipo", "categoria", "terceiro"):
        lanc[c] = lanc[c].fillna("").astype(str).str.strip()

    fichas = pd.read_excel(LEGADO, sheet_name="Precificacão", usecols="A:J")
    fichas = fichas.dropna(subset=["Nome da Peça"]).copy()
    fichas.columns = ["peca", "tipo_custo", "tipo_insumo", "referencia", "descricao",
                      "cor", "unidade", "preco_unitario", "quantidade", "total"]
    fichas["peca"] = fichas["peca"].str.strip()
    fichas["total"] = pd.to_numeric(fichas["total"], errors="coerce").fillna(0.0)
    fichas["quantidade"] = pd.to_numeric(fichas["quantidade"], errors="coerce")

    resumo = pd.read_excel(LEGADO, sheet_name="Precificacão", usecols="L:S")
    resumo.columns = ["peca", "custo", "taxa_venda", "imposto", "cmv_final",
                      "preco", "margem", "margem_pct"]
    resumo = resumo.dropna(subset=["peca"]).copy()
    resumo["peca"] = resumo["peca"].astype(str).str.strip()
    # Ficha com insumo sem quantidade é ficha incompleta: o custo sai menor do
    # que é e a margem vira ficção. Melhor avisar do que fingir.
    incompletas = set(fichas[(fichas.tipo_custo == "Insumo") & fichas.quantidade.isna()]["peca"])
    resumo["incompleta"] = resumo["peca"].isin(incompletas)

    insumos = pd.read_excel(LEGADO, sheet_name="Estoque_Insumo")
    insumos = insumos.dropna(subset=["Descrição"]).copy()

    return {"lancamentos": lanc, "fichas": fichas, "custo_pecas": resumo, "insumos": insumos}


@memo()
def aportes() -> pd.DataFrame:
    """Aportes das sócias: o que entrou na conta da empresa vindo delas (ou do
    Pedro, em nome da Ana), mais o que foi pago do bolso antes de a conta
    existir. Os aportes anotados na planilha legada não entram: os de
    jan/fev são o mesmo dinheiro que pagou as compras pré-conta, e o de
    21/03 é o mesmo que caiu na conta em 25/03."""
    import extrato
    ex = extrato.carregar()
    da_conta = ex[ex.natureza == "aporte"][["data", "categoria", "valor", "confianca"]].rename(columns={"categoria": "quem"})
    pre = saidas_pre_conta()
    em_especie = pd.DataFrame({
        "data": pre.data, "quem": "Ana e Isa, pagas do bolso", "valor": pre.valor, "confianca": "ok",
    }) if not pre.empty else pd.DataFrame(columns=da_conta.columns)
    return pd.concat([em_especie, da_conta], ignore_index=True).sort_values("data").reset_index(drop=True)


def saidas_pre_conta() -> pd.DataFrame:
    """Parcelas da planilha legada vencidas antes de a conta existir. O que
    vence depois já aparece no extrato e entraria duas vezes."""
    import extrato
    p = parcelas_legado()
    if p.empty:
        return p
    return p[p.data < extrato.INICIO_DA_CONTA].reset_index(drop=True)


@memo()
def ledger_saidas() -> pd.DataFrame:
    """Toda saída de dinheiro da empresa, uma fonte por período:
    planilha até 24/03/2025, extrato da conta dali em diante."""
    import extrato
    pre = saidas_pre_conta()
    pre = pd.DataFrame({
        "data": pre.data, "valor": pre.valor, "contraparte": pre.descricao,
        "natureza": pre.natureza, "categoria": pre.categoria, "confianca": "ok", "fonte": "planilha",
    }) if not pre.empty else pd.DataFrame()
    ex = extrato.carregar()
    ex = ex[(ex.sentido == "saida") & (ex.natureza != "interna")]
    ex = pd.DataFrame({
        "data": ex.data, "valor": ex.valor_abs, "contraparte": ex.contraparte,
        "natureza": ex.natureza, "categoria": ex.categoria, "confianca": ex.confianca, "fonte": "extrato",
    })
    t = pd.concat([pre, ex], ignore_index=True).sort_values("data").reset_index(drop=True)
    t["mes"] = t.data.dt.strftime("%Y-%m")
    return t


def fim_dos_custos():
    """Última data com saída registrada. Depois dela o PnL está cego."""
    import extrato
    ex = extrato.carregar()
    return ex.data.max() if not ex.empty else None


def _classificar_saida(setor: str, atividade: str) -> str:
    """Onde cada saída cai no resultado.

    - estoque: tecido, aviamento e facção. Vira CMV quando a peça vende
    - capex: estrutura da loja, cabides e decoração
    - despesa: tudo do administrativo, inclusive marca e consultoria
    """
    if setor == "Produção":
        return "estoque"
    if setor == "Loja":
        return "capex"
    return "despesa"


def saidas_legado() -> pd.DataFrame:
    lanc = carregar_legado()["lancamentos"]
    if lanc.empty:
        return pd.DataFrame(columns=["data", "descricao", "categoria", "natureza", "valor", "parcelas"])
    s = lanc[lanc.tipo == "Saída"].copy()
    s["natureza"] = [_classificar_saida(a, b) for a, b in zip(s.setor, s.atividade)]
    return s[["data", "descricao", "categoria", "natureza", "valor", "parcelas"]].reset_index(drop=True)


def parcelas_legado() -> pd.DataFrame:
    """Saídas abertas em parcelas mensais, para a visão de caixa.

    A aba Contas a Pagar da planilha fazia isso, mas deixou duas compras de
    fora e ficou R$ 2.200 abaixo do Lançamento. Refazer aqui a partir do
    Lançamento garante que as duas visões partem do mesmo total.
    """
    s = saidas_legado()
    linhas = []
    for r in s.itertuples():
        n = int(r.parcelas)
        for i in range(n):
            linhas.append({
                "data": r.data + pd.DateOffset(months=i),
                "descricao": r.descricao,
                "categoria": r.categoria,
                "natureza": r.natureza,
                "valor": r.valor / n,
                "parcela": f"{i + 1} de {n}",
            })
    return pd.DataFrame(linhas)



# ─── Custo por peça ─────────────────────────────────────────────────────────

def ficha_da_peca(titulo: str):
    for padrao, ficha in MAPA_FICHAS:
        if re.search(padrao, titulo, flags=re.I):
            return ficha
    return None


def custo_por_ficha() -> dict:
    r = carregar_legado()["custo_pecas"]
    return dict(zip(r.peca, r.custo)) if not r.empty else {}


def separar_itens(texto: str) -> list:
    """'1x Top de Jacquard Rosé - Giverny (P), 2x Cinto Pingente - U' vira
    [(1, 'Top de Jacquard Rosé - Giverny'), (2, 'Cinto Pingente')]."""
    itens = []
    for parte in (texto or "").split(", "):
        m = _ITEM.match(parte.strip())
        if not m:
            continue
        titulo = _TAMANHO.sub("", m.group(2)).strip()
        itens.append((int(m.group(1)), titulo))
    return itens


# ─── Pedidos e Pagar.me ─────────────────────────────────────────────────────

def _mes_brt(iso: str) -> str:
    t = datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=3)
    return t.strftime("%Y-%m")


@memo()
def pedidos_pagos() -> pd.DataFrame:
    """Um pedido por linha, nuvem e local juntos, só os pagos, cada um casado
    com a cobrança da Pagar.me que o pagou (quando existe)."""
    import nuvem
    vistos, linhas = set(), []
    for p in list(nuvem.ler_pedidos()) + db.pedidos_para_nuvem():
        if p["numero"] in vistos or str(p.get("situacao", "")).lower() != "paid":
            continue
        vistos.add(p["numero"])
        linhas.append({
            "numero": p["numero"],
            "quando": pd.Timestamp(str(p["criado_em"])[:19]),
            "mes": _mes_brt(p["criado_em"]),
            "total": (p.get("total") or 0) / 100,
            "itens": p.get("itens") or "",
            "cupom": p.get("cupom") or "",
        })
    ped = pd.DataFrame(linhas)
    return _casar_com_cobrancas(ped) if not ped.empty else ped


def _casar_com_cobrancas(ped: pd.DataFrame) -> pd.DataFrame:
    """Liga cada pedido à cobrança que o pagou.

    O total da Shopify é o preço antes do desconto do Pix, que o app de
    pagamento aplica por fora: pedido de R$ 878 vira cobrança de R$ 834,10.
    Por isso o casamento aceita valor igual ou até 6% menor, dentro de dois
    dias. A diferença é o desconto Pix, que a receita precisa descontar.
    """
    ch = _sql("""
        SELECT id, created_at, amount / 100.0 AS valor, payment_method AS metodo, status
          FROM charges
         WHERE (status = 'paid') OR (status = 'canceled' AND paid_at != '')
    """)
    ch["quando"] = pd.to_datetime(ch.created_at.str[:19])
    usados = set()
    cobrado, metodo, estornada = [], [], []
    for r in ped.sort_values("quando").itertuples():
        janela = ch[(~ch.id.isin(usados)) & ((ch.quando - r.quando).abs() <= pd.Timedelta(days=2))]
        exato = janela[(janela.valor - r.total).abs() < 0.01]
        if exato.empty:
            perto = janela[(janela.valor <= r.total + 0.01) & (janela.valor >= r.total * 0.94)]
            exato = perto.assign(dif=(perto.valor - r.total).abs()).sort_values("dif")
        if exato.empty:
            cobrado.append(None); metodo.append(""); estornada.append(False)
            continue
        c = exato.iloc[0]
        usados.add(c.id)
        cobrado.append(float(c.valor)); metodo.append(c.metodo); estornada.append(c.status == "canceled")
    ped = ped.sort_values("quando").copy()
    ped["cobrado"] = cobrado
    ped["metodo"] = metodo
    ped["estornada"] = estornada
    ped["desconto_pix"] = [(t - c) if (c is not None and c < t - 0.01) else 0.0
                           for t, c in zip(ped.total, ped.cobrado)]
    return ped.reset_index(drop=True)


def cmv_dos_pedidos(pedidos: pd.DataFrame) -> tuple:
    """CMV por mês e a lista de peças sem custo conhecido."""
    custos = custo_por_ficha()
    por_mes, sem_custo, pecas_total, pecas_sem = {}, {}, 0, 0
    for r in pedidos.itertuples():
        for qtd, titulo in separar_itens(r.itens):
            pecas_total += qtd
            ficha = ficha_da_peca(titulo)
            custo = custos.get(ficha) if ficha else None
            if custo is None:
                pecas_sem += qtd
                sem_custo[titulo] = sem_custo.get(titulo, 0) + qtd
                continue
            por_mes[r.mes] = por_mes.get(r.mes, 0.0) + custo * qtd
    cobertura = 1 - pecas_sem / pecas_total if pecas_total else 0.0
    return pd.Series(por_mes, name="cmv"), sem_custo, cobertura


def _sql(consulta: str) -> pd.DataFrame:
    con = db._conn()
    try:
        return pd.read_sql_query(consulta, con)
    finally:
        con.close()


def taxas_por_mes_da_venda() -> pd.DataFrame:
    """MDR e antecipação amarrados ao mês da venda, não ao da liquidação."""
    return _sql("""
        SELECT strftime('%Y-%m', datetime(c.created_at, '-3 hours')) AS mes,
               SUM(p.fee) / 100.0 AS mdr,
               SUM(COALESCE(p.anticipation_fee, 0)) / 100.0 AS antecipacao
          FROM payables p JOIN charges c ON c.id = p.charge_id
         WHERE c.status = 'paid' AND p.type = 'credit'
         GROUP BY mes
    """).set_index("mes")


def estornos_por_mes() -> pd.DataFrame:
    """Estornos pelo mês em que foram feitos, com a taxa que a Pagar.me devolve."""
    return _sql("""
        SELECT strftime('%Y-%m', datetime(created_at, '-3 hours')) AS mes,
               -SUM(amount) / 100.0 AS estornos,
               -SUM(fee) / 100.0 AS taxa_devolvida
          FROM payables
         WHERE type = 'refund'
         GROUP BY mes
    """).set_index("mes")


def caixa_pagarme_por_mes() -> pd.DataFrame:
    """O que caiu na conta, líquido de taxas, pelo mês da liquidação.
    Estornos já entram negativos. `previsto` é o que ainda vai cair."""
    return _sql("""
        SELECT strftime('%Y-%m', datetime(payment_date, '-3 hours')) AS mes,
               SUM(CASE WHEN status = 'paid'
                        THEN amount - fee - COALESCE(anticipation_fee, 0) ELSE 0 END) / 100.0 AS recebido,
               SUM(CASE WHEN status != 'paid'
                        THEN amount - fee - COALESCE(anticipation_fee, 0) ELSE 0 END) / 100.0 AS previsto
          FROM payables
         GROUP BY mes
    """).set_index("mes")

# ─── PnL ────────────────────────────────────────────────────────────────────

def _eixo_meses(*series) -> list:
    meses = set()
    for s in series:
        meses.update(str(m) for m in s)
    if not meses:
        return []
    ini, fim = min(meses), max(meses)
    return [str(p) for p in pd.period_range(ini, fim, freq="M")]


def receita_fisica_por_mes() -> pd.DataFrame:
    """Venda fora do site: maquininha (líquida de MDR) e Pix direto de cliente."""
    import extrato
    ex = extrato.carregar()
    v = ex[ex.natureza == "venda_fisica"]
    if v.empty:
        return pd.DataFrame(columns=["maquininha", "pix"])
    t = v.pivot_table(index="mes", columns="categoria", values="valor", aggfunc="sum").fillna(0.0)
    out = pd.DataFrame(index=t.index)
    out["maquininha"] = t.get("Maquininha (líquido)", 0.0)
    out["pix"] = t.get("Pix direto", 0.0) + t.get("Link de pagamento", 0.0)
    return out


@memo()
def pnl_competencia(imposto: float = IMPOSTO_PADRAO) -> dict:
    ped = pedidos_pagos()
    receita = ped.groupby("mes")["total"].sum() if not ped.empty else pd.Series(dtype=float)
    pedidos_n = ped.groupby("mes")["numero"].count() if not ped.empty else pd.Series(dtype=float)
    cmv, sem_custo, cobertura = cmv_dos_pedidos(ped)
    taxas = taxas_por_mes_da_venda()
    est = estornos_por_mes()
    fis = receita_fisica_por_mes()

    led = ledger_saidas()
    desp = led[led.natureza == "despesa"]
    desp_cat = desp.groupby(["mes", "categoria"])["valor"].sum().unstack(fill_value=0.0)
    # Meta entra pelo relatório da agência e sai, até onde der, do reembolso
    # às sócias do mesmo mês, que é por onde ela foi paga. O que sobrar no
    # reembolso é aluguel e outras despesas no cartão.
    meta = pd.Series(meta_ads(), name="Meta Ads")
    desp_cat = desp_cat.reindex(sorted(set(desp_cat.index) | set(meta.index))).fillna(0.0)
    # A fatura Porto também é pagamento de Meta já contada pelo relatório.
    fatura = desp_cat.pop("Fatura do cartão de anúncios") if "Fatura do cartão de anúncios" in desp_cat else 0.0
    cartao = (desp_cat[CATEGORIA_CARTAO] if CATEGORIA_CARTAO in desp_cat else pd.Series(0.0, index=desp_cat.index)) + fatura
    meta = meta.reindex(desp_cat.index).fillna(0.0)
    desp_cat["Meta Ads"] = meta
    desp_cat[CATEGORIA_CARTAO] = (cartao - meta).clip(lower=0.0)
    desp_cat = desp_cat.rename(columns={CATEGORIA_CARTAO: "Outras no cartão das sócias"})
    desp_mes = desp_cat.sum(axis=1)
    marketing_mes = desp_cat["Meta Ads"] + (desp_cat["Ads e agência"] if "Ads e agência" in desp_cat else 0.0)

    meses = _eixo_meses(receita.index, desp_mes.index, fis.index, [datetime.now().strftime("%Y-%m")])
    t = pd.DataFrame(index=meses)
    t.index.name = "mes"
    z = pd.Series(0.0, index=meses)
    t["pedidos"] = pedidos_n.reindex(meses).fillna(0).astype(int)
    t["receita_site"] = receita.reindex(meses).fillna(0.0)
    desc = ped.groupby("mes")["desconto_pix"].sum() if not ped.empty else pd.Series(dtype=float)
    t["desconto_pix"] = desc.reindex(meses).fillna(0.0)
    t["estornos"] = est["estornos"].reindex(meses).fillna(0.0) if not est.empty else z
    t["receita_site_liquida"] = t.receita_site - t.desconto_pix - t.estornos
    t["receita_maquininha"] = fis["maquininha"].reindex(meses).fillna(0.0) if not fis.empty else z
    t["receita_pix_direto"] = fis["pix"].reindex(meses).fillna(0.0) if not fis.empty else z
    t["receita_fisica"] = t.receita_maquininha + t.receita_pix_direto
    t["receita_liquida"] = t.receita_site_liquida + t.receita_fisica
    t["taxas"] = (taxas["mdr"].reindex(meses).fillna(0.0) + taxas["antecipacao"].reindex(meses).fillna(0.0)) if not taxas.empty else z
    if not est.empty:
        t["taxas"] = t["taxas"] - est["taxa_devolvida"].reindex(meses).fillna(0.0)
    t["imposto"] = t.receita_liquida * imposto
    t["cmv_site"] = cmv.reindex(meses).fillna(0.0)
    # Fora do site não se sabe a peça. O CMV é estimado com a proporção do
    # site no mesmo mês; sem venda no site no mês, com a proporção do total.
    razao_geral = t.cmv_site.sum() / t.receita_site_liquida.sum() if t.receita_site_liquida.sum() else 0.0
    razao_mes = (t.cmv_site / t.receita_site_liquida.replace(0, pd.NA)).fillna(razao_geral).astype(float)
    t["cmv_fisico_estimado"] = t.receita_fisica * razao_mes
    t["cmv"] = t.cmv_site + t.cmv_fisico_estimado
    t["margem_bruta"] = t.receita_liquida - t.taxas - t.imposto - t.cmv
    t["despesas"] = desp_mes.reindex(meses).fillna(0.0)
    t["marketing"] = marketing_mes.reindex(meses).fillna(0.0)
    t["despesas_outras"] = t.despesas - t.marketing
    t["resultado"] = t.margem_bruta - t.despesas
    t["resultado_acumulado"] = t.resultado.cumsum()

    fim = fim_dos_custos()
    return {
        "tabela": t.reset_index(),
        "despesas_por_categoria": desp_cat.reindex(meses).fillna(0.0),
        "pecas_sem_custo": sem_custo,
        "cobertura_cmv": cobertura,
        "razao_cmv": razao_geral,
        "fim_dos_custos": fim,
        "pedidos_sem_cobranca": ped[ped.cobrado.isna()] if not ped.empty else ped,
    }


@memo()
def fluxo_de_caixa() -> pd.DataFrame:
    """Mês a mês, pelo extrato: o que entrou, o que saiu, aportes e acumulado.

    `saldo_operacional` ignora aportes: é o que a empresa gerou ou queimou.
    `caixa` soma os aportes e deve bater com o saldo da conta."""
    import extrato
    ex = extrato.carregar()
    ent = ex[(ex.sentido == "entrada") & (ex.natureza != "interna")]
    ent_nat = ent.pivot_table(index="mes", columns="natureza", values="valor", aggfunc="sum").fillna(0.0) if not ent.empty else pd.DataFrame()
    led = ledger_saidas()
    sai_nat = led.pivot_table(index="mes", columns="natureza", values="valor", aggfunc="sum").fillna(0.0) if not led.empty else pd.DataFrame()
    ap = aportes()
    ap_mes = ap.groupby(ap.data.dt.strftime("%Y-%m"))["valor"].sum() if not ap.empty else pd.Series(dtype=float)
    pag = caixa_pagarme_por_mes()

    hoje = datetime.now().strftime("%Y-%m")
    meses = _eixo_meses(ent_nat.index, sai_nat.index, ap_mes.index, pag.index if not pag.empty else [], [hoje])
    t = pd.DataFrame(index=meses)
    t.index.name = "mes"
    z = pd.Series(0.0, index=meses)

    def col(df, nome):
        return df[nome].reindex(meses).fillna(0.0) if (not df.empty and nome in df) else z

    t["recebido_site"] = col(ent_nat, "liquidacao_online")
    t["recebido_fisico"] = col(ent_nat, "venda_fisica")
    t["devolucoes"] = col(ent_nat, "devolucao")
    t["recebido"] = t.recebido_site + t.recebido_fisico + t.devolucoes
    t["a_receber"] = col(pag, "previsto")
    for nat in ("estoque", "despesa", "capex", "imposto"):
        t[f"saida_{nat}"] = col(sai_nat, nat)
    t["saidas"] = t.saida_estoque + t.saida_despesa + t.saida_capex + t.saida_imposto
    t["aportes"] = ap_mes.reindex(meses).fillna(0.0)
    t["saldo_operacional"] = t.recebido - t.saidas
    t["acumulado_operacional"] = t.saldo_operacional.cumsum()
    t["caixa"] = (t.saldo_operacional + t.aportes).cumsum()
    t["futuro"] = [m > hoje for m in meses]
    return t.reset_index()


# ─── Payback e VPL ──────────────────────────────────────────────────────────

def vpl(fluxos, taxa_mensal: float) -> float:
    return float(sum(f / (1 + taxa_mensal) ** i for i, f in enumerate(fluxos)))


def payback(fluxo: pd.DataFrame):
    """Primeiro mês em que o acumulado operacional deixa de ser negativo.
    None se ainda não aconteceu."""
    realizado = fluxo[~fluxo.futuro]
    positivos = realizado[realizado.acumulado_operacional >= 0]
    if positivos.empty:
        return None
    # Só conta se ficou positivo de vez: um mês isolado acima de zero antes
    # do grande investimento de maio/2025 não é payback.
    ultimo_negativo = realizado[realizado.acumulado_operacional < 0]
    if ultimo_negativo.empty:
        return positivos.iloc[0]["mes"]
    depois = positivos[positivos.mes > ultimo_negativo.iloc[-1]["mes"]]
    return depois.iloc[0]["mes"] if not depois.empty else None


@memo()
def estoque_a_custo() -> float:
    """Produção paga menos CMV consumido: o que está na arara e no rolo, a custo."""
    led = ledger_saidas()
    t = pnl_competencia()["tabela"]
    return float(led[led.natureza == "estoque"].valor.sum() - t.cmv.sum())


def projetar(fluxo: pd.DataFrame, meses: int, receita_liquida_mensal: float,
             crescimento_mensal: float, cmv_pct: float, ads_pct: float,
             fixos_mensais: float, taxa_desconto: float, estoque_custo: float = 0.0) -> dict:
    """Continua o acumulado operacional para a frente.

    Empresa em andamento: o estoque já pago vira receita sem nova saída de
    CMV. Só quando ele acaba é que cada venda volta a exigir compra de
    tecido e facção. Ads escalam com a receita; fixos não.

    Receita líquida já descontadas taxas e imposto. Devolve a série
    projetada, o mês em que o estoque acaba, o payback dentro do horizonte
    e o VPL do realizado + projetado.
    """
    realizado = fluxo[~fluxo.futuro]
    base = float(realizado.acumulado_operacional.iloc[-1]) if not realizado.empty else 0.0
    ultimo = pd.Period(realizado.mes.iloc[-1], "M") if not realizado.empty else pd.Period(datetime.now(), "M")

    linhas, acumulado, receita, estoque = [], base, receita_liquida_mensal, max(estoque_custo, 0.0)
    mes_payback, mes_fim_estoque = payback(fluxo), None
    for i in range(1, meses + 1):
        cmv_necessario = receita * cmv_pct
        do_estoque = min(estoque, cmv_necessario)
        estoque -= do_estoque
        if estoque <= 0 and mes_fim_estoque is None and do_estoque > 0:
            mes_fim_estoque = str(ultimo + i)
        cmv_caixa = cmv_necessario - do_estoque
        saldo = receita - cmv_caixa - receita * ads_pct - fixos_mensais
        acumulado += saldo
        m = str(ultimo + i)
        if mes_payback is None and acumulado >= 0:
            mes_payback = m
        linhas.append({"mes": m, "receita_liquida": receita, "cmv_caixa": cmv_caixa,
                       "estoque_restante": estoque, "saldo": saldo, "acumulado": acumulado})
        receita *= 1 + crescimento_mensal

    proj = pd.DataFrame(linhas)
    fluxos = list(realizado.saldo_operacional) + list(proj.saldo)
    return {
        "projecao": proj,
        "payback": mes_payback,
        "fim_do_estoque": mes_fim_estoque,
        "vpl": vpl(fluxos, taxa_desconto),
        "vpl_realizado": vpl(list(realizado.saldo_operacional), taxa_desconto),
    }
