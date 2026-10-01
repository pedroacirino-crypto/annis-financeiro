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
CATEGORIA_FATURA = "Fatura do cartão de anúncios"


@memo()
def meta_ads() -> dict:
    """Meta por mês: Supabase, com o dicionário acima como reserva local."""
    import dados_fin
    nuvem_meta = dados_fin.ler_meta_ads() if dados_fin.disponivel() else {}
    return nuvem_meta or dict(META_ADS)

# Peças vendidas sob encomenda: o cadastro tem estoque para a página não
# dizer esgotado, mas elas não existem na arara.
SOB_ENCOMENDA = r"Loulou"

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
    ex = ex[ex.sentido == "saida"]
    ex = pd.DataFrame({
        "data": ex.data, "valor": ex.valor_abs, "contraparte": ex.contraparte,
        "natureza": ex.natureza, "categoria": ex.categoria, "confianca": ex.confianca, "fonte": "extrato",
    })
    t = pd.concat([pre, ex], ignore_index=True).sort_values("data").reset_index(drop=True)
    t["mes"] = t.data.dt.strftime("%Y-%m")
    return _aplicar_rateios(t)


@memo()
def _rateios() -> pd.DataFrame:
    """Pagamento que é duas coisas: o boleto do aluguel de 15/09/2026 saiu
    R$ 2.615,43 e tinha R$ 435,38 de seguro incêndio dentro, pontual. Sem
    separar, a leitura vira "o aluguel subiu 23%", que foi a conclusão
    errada que eu tirei em 30/09/2026 e o Pedro corrigiu."""
    import os
    try:
        import dados_fin
        if dados_fin.disponivel():
            df = dados_fin.ler_rateios()
            if not df.empty:
                return df
    except Exception:
        pass
    caminho = os.path.join("legado", "rateios.csv")
    if not os.path.exists(caminho):
        return pd.DataFrame()
    df = pd.read_csv(caminho)
    df["data"] = pd.to_datetime(df["data"])
    return df


def _aplicar_rateios(t: pd.DataFrame) -> pd.DataFrame:
    """Separa a parte em linha própria e desconta do original.

    O total não muda, só a categoria de um pedaço, por isso o caixa e todas
    as conferências continuam batendo.
    """
    r = _rateios()
    if r.empty or t.empty:
        return t
    novas = []
    for p in r.itertuples(index=False):
        alvo = t[(t.data.dt.normalize() == pd.Timestamp(p.data).normalize())
                 & t.contraparte.str.contains(p.contraparte, case=False, na=False)
                 & (t.valor.round(2) == round(float(p.valor_total), 2))]
        if alvo.empty:
            continue
        i = alvo.index[0]
        t.loc[i, "valor"] = round(t.loc[i, "valor"] - float(p.valor_parte), 2)
        linha = t.loc[i].to_dict()
        linha.update({"valor": float(p.valor_parte), "categoria": p.categoria_parte,
                      "natureza": p.natureza_parte})
        novas.append(linha)
    if not novas:
        return t
    return pd.concat([t, pd.DataFrame(novas)], ignore_index=True).sort_values("data").reset_index(drop=True)


# Fornecedor de contrato mensal: a despesa é do mês do serviço, não do mês em
# que o Pix saiu. Sem isso, conta que atrasa deixa um mês zerado e dobra o
# seguinte. Aconteceu duas vezes: a agência foi paga em 23/07/2026 e só de
# novo em 01/09, então agosto ficou sem agência nenhuma e setembro apareceu
# com R$ 3.600; e o sistema Olist pulou setembro de 2025 e pagou duas vezes
# em outubro. Cada um desses buracos estraga a margem dos dois meses.
_CONTRATOS_MENSAIS = [
    ("agência",    r"PROADZ|V60 ANUNCIOS"),
    ("aluguel",    r"PJBANK|SUPERLOGICA"),
    ("condomínio", r"CONDOMINIO"),
    ("contador",   r"KOYASHIKI"),
    ("sistema",    r"OLIST"),
]


def _contrato(nome: str) -> str:
    """Qual contrato mensal é este fornecedor, ou vazio se nenhum.

    Casa por padrão porque o mesmo contrato troca de nome no extrato:
    PROADZ virou V60, Koyashiki aparece como 'KOYASHIKI & CIA' e como
    'Grupo Koyashiki', e o condomínio ora vem com sublinhado ora com espaço.
    """
    import re
    for chave, padrao in _CONTRATOS_MENSAIS:
        if re.search(padrao, nome or "", re.I):
            return chave
    return ""


def _meses_entre(de: str, ate: str) -> list:
    saida, m = [], de
    while m <= ate:
        saida.append(m)
        ano, mm = int(m[:4]), int(m[5:7])
        m = f"{ano + 1}-01" if mm == 12 else f"{ano}-{mm + 1:02d}"
    return saida


def _competencia_dos_contratos(pagamentos: pd.DataFrame) -> pd.DataFrame:
    """Move cada mensalidade para o mês a que ela se refere.

    Uma mensalidade por mês: do primeiro ao último mês do contrato, o
    pagamento mais antigo cobre o mês mais antigo. Assim o Pix de 01/09 vira
    a mensalidade de agosto, que tinha ficado vazia, e o de 28/09 fica em
    setembro.

    Conserva o total: nenhum real é criado nem sumido, só muda de mês. Se o
    contrato tiver mais ou menos pagamentos do que meses, o que significa
    mês pulado de verdade ou cobrança dobrada, fica pelo mês do pagamento e
    nada é inventado. O que não é contrato mensal passa direto.
    """
    pagamentos = pagamentos.copy()
    pagamentos["contrato"] = pagamentos.contraparte.map(_contrato)
    linhas = [pagamentos[pagamentos.contrato == ""]]
    for _, grupo in pagamentos[pagamentos.contrato != ""].groupby("contrato"):
        grupo = grupo.sort_values("data")
        meses = _meses_entre(grupo.mes.iloc[0], grupo.mes.iloc[-1])
        if len(meses) == len(grupo):
            grupo = grupo.assign(mes=meses)
        linhas.append(grupo)
    return pd.concat(linhas, ignore_index=True).drop(columns=["contrato"])


def _abater_devolucoes(pagamentos: pd.DataFrame) -> pd.DataFrame:
    """Devolução de fornecedor abate a despesa dele, não vira receita.

    São R$ 3.226,62 desde o começo, o maior deles a duplicata de R$ 1.800
    da agência em 08/09/2025, paga duas vezes e estornada no mesmo dia. Sem
    abater, setembro de 2025 aparecia com R$ 3.600 de agência. No fluxo de
    caixa a devolução já entra como entrada, por isso o caixa sempre fechou.
    """
    import extrato
    ex = extrato.carregar()
    dev = ex[(ex.natureza == "devolucao") & (ex.categoria == "Devolução de fornecedor")]
    if dev.empty:
        return pagamentos
    pagamentos = pagamentos.reset_index(drop=True)
    cancelar = set()
    for d in dev.itertuples(index=False):
        iguais = pagamentos[(~pagamentos.index.isin(cancelar))
                            & (pagamentos.valor.round(2) == round(d.valor_abs, 2))
                            & (pagamentos.data <= d.data)
                            & (pagamentos.contraparte.str.slice(0, 6)
                               == (d.contraparte or "")[:6])]
        if not iguais.empty:
            cancelar.add(iguais.index[-1])      # o pagamento mais próximo
    return pagamentos.drop(index=cancelar)


# Fatura do cartão das sócias: o extrato da Stone só mostra a transferência
# para a sócia, e ela não é a despesa. Em 11/08/2026 a fatura da Ana era de
# R$ 1.999,85; a empresa mandou R$ 730 para ela e a Isa depositou R$ 1.270,
# cada uma bancando metade. O painel registrava R$ 800 de "cartão das
# sócias" em agosto quando a despesa real tinha sido R$ 1.999,85, no mês
# errado e sem categoria. Confirmado pelo Pedro em 30/09/2026.
#
# A conversão roda só aqui, no PnL. A classificação do extrato fica como
# está: no caixa a transferência é saída de verdade e o depósito da sócia é
# aporte de verdade, e mexer nisso quebraria a conferência com o saldo da
# conta e o total de aportes.

_TITULARES = {"Ana": r"ANA CAROLINE", "Isa": r"ISABELA"}


@memo()
def faturas_cartao() -> pd.DataFrame:
    """Linhas das faturas, do Supabase ou do arquivo local."""
    import os
    try:
        import dados_fin
        if dados_fin.disponivel():
            df = dados_fin.ler_fatura_cartao()
            if not df.empty:
                return df
    except Exception:
        pass
    caminho = os.path.join("legado", "faturas_cartao.csv")
    if not os.path.exists(caminho):
        return pd.DataFrame()
    df = pd.read_csv(caminho)
    df["data"] = pd.to_datetime(df["data"])
    return df


@memo()
def compras_no_cartao() -> pd.DataFrame:
    """Uma linha por compra, não por parcela, pelo mês em que foi comprada.

    Parcelado é uma compra só: o aviamento de 25/08 em 3x de R$ 372,68 é
    R$ 1.118,04 de estoque em agosto, não três pedaços em três meses. A
    parcela que falta chegar não muda nada, porque o valor cheio já sai de
    'n/m' vezes o valor da parcela; por isso a linha é contada uma vez só,
    na primeira parcela que aparecer.
    """
    fat = faturas_cartao()
    if fat.empty:
        return pd.DataFrame(columns=["mes", "titular", "estabelecimento", "valor", "natureza", "categoria"])
    fat = fat[fat.natureza.isin(["despesa", "estoque"])].copy()
    fat["parcelas"] = (fat.parcela.fillna("").astype(str)
                       .str.extract(r"/(\d+)")[0].astype(float).fillna(1.0))
    fat["ordem"] = (fat.parcela.fillna("").astype(str)
                    .str.extract(r"^(\d+)/")[0].astype(float).fillna(1.0))
    fat = fat[fat.ordem == 1]                      # a compra conta uma vez
    fat["valor"] = fat.valor * fat.parcelas        # valor cheio da compra
    fat["mes"] = fat.data.dt.strftime("%Y-%m")
    return fat[["mes", "titular", "estabelecimento", "valor", "natureza", "categoria"]]


@memo()
def meses_com_fatura() -> dict:
    """{titular: meses de vencimento cujas faturas já foram transcritas}.

    O recorte é por vencimento porque é ele que casa com o reembolso: a
    fatura que venceu em agosto foi a que a empresa ajudou a pagar em
    agosto. Mês sem fatura carregada continua no modelo antigo.
    """
    fat = faturas_cartao()
    if fat.empty:
        return {}
    return {t: set(g.fatura.unique()) for t, g in fat.groupby("titular")}


def _trocar_reembolso_por_fatura(desp: pd.DataFrame) -> pd.DataFrame:
    """Onde a fatura foi transcrita, a despesa é a linha dela.

    Sai a transferência para a sócia naquele mês de vencimento, que é
    reembolso e não despesa, e entram as compras da fatura pelo mês em que
    foram feitas. Mês sem fatura carregada não é tocado: continua com a
    estimativa antiga, senão o resultado melhoraria por falta de dado.

    Meta não entra por aqui: a fonte dele continua sendo o relatório da
    agência, que cobre o histórico inteiro, enquanto a fatura cobre só os
    meses transcritos. Entrasse pelos dois, contaria duas vezes.
    """
    cobertos = meses_com_fatura()
    if not cobertos:
        return desp
    fora = pd.Series(False, index=desp.index)
    for titular, meses in cobertos.items():
        padrao = _TITULARES.get(titular)
        if not padrao:
            continue
        fora |= ((desp.categoria == CATEGORIA_CARTAO)
                 & desp.contraparte.str.contains(padrao, case=False, na=False)
                 & desp.mes.isin(meses))
    # A fatura às vezes a empresa paga direto, sem passar pela sócia: em
    # 11/09/2026 saíram R$ 3.793,16 para a Portoseg, que é exatamente a soma
    # dos dois cartões daquela fatura (374,25 + 3.418,91). Pagando direto, a
    # empresa cobriu também R$ 572,30 de coisa pessoal. Onde a fatura está
    # transcrita, a despesa vem dela e esse pagamento vira caixa.
    meses_todos = set().union(*cobertos.values())
    fora |= (desp.categoria == CATEGORIA_FATURA) & desp.mes.isin(meses_todos)
    desp = desp[~fora]
    compras = compras_no_cartao()
    novas = compras[(compras.natureza == "despesa") & (compras.categoria != "Meta Ads")]
    if novas.empty:
        return desp
    return pd.concat([desp, pd.DataFrame({
        "data": pd.NaT, "valor": novas.valor.values, "contraparte": novas.estabelecimento.values,
        "natureza": "despesa", "categoria": novas.categoria.values, "confianca": "ok",
        "fonte": "fatura", "mes": novas.mes.values,
    })], ignore_index=True)


@memo()
def conferir_contas_pagas(dias: int = 7) -> pd.DataFrame:
    """Cada linha marcada PAGO na planilha saiu mesmo da conta?

    A planilha de contas a pagar era usada só para o futuro, e as linhas
    já pagas passavam sem conferência nenhuma. Foi o Pedro quem apontou, em
    30/09/2026: se um valor está errado na planilha e ninguém compara com o
    extrato, o erro atravessa o painel inteiro sem fazer barulho.

    Casa por valor exato dentro de uma janela de dias, porque a data da
    planilha é o vencimento e o Pix sai perto dele, não nele.
    """
    import dados_fin
    cp = dados_fin.ler_contas_a_pagar() if dados_fin.disponivel() else pd.DataFrame()
    if cp.empty:
        return pd.DataFrame()
    pagas = cp[cp.situacao.fillna("").str.upper() == "PAGO"].copy()
    if pagas.empty:
        return pd.DataFrame()
    ex = dados_fin.ler_extrato()
    saidas = ex[ex.valor < 0].copy() if not ex.empty else pd.DataFrame(columns=["data", "valor", "contraparte"])
    saidas["abs"] = saidas.valor.abs().round(2)
    saidas["dia"] = saidas.data.dt.normalize()
    janela = pd.Timedelta(days=dias)
    linhas, usados = [], set()
    for r in pagas.itertuples(index=False):
        alvo = round(float(r.valor), 2)
        perto = saidas[(saidas.dia >= r.data.normalize() - janela)
                       & (saidas.dia <= r.data.normalize() + janela)
                       & (~saidas.index.isin(usados))]
        # Uma linha da planilha consome um pagamento só: sem o head(1), a
        # Violet de 1.074,80 de 01/09 e a de 04/09 casavam com a mesma linha
        # e sobrava a outra sem par.
        casou = perto[perto["abs"] == alvo].head(1)
        # A planilha traz uma linha onde a conta teve duas: o boleto do Eco
        # Simple de 08/09 saiu em dois pagamentos à Nika, 1.222,36 e 644,76,
        # que somam o 1.867,12 da planilha.
        if casou.empty:
            for dia, g in perto.groupby("dia"):
                if len(g) > 1 and round(g["abs"].sum(), 2) == alvo:
                    casou = g
                    break
        achou = not casou.empty
        if achou:
            usados.update(casou.index)
        linhas.append({
            "data": r.data, "descricao": r.descricao, "valor": alvo,
            "saiu_da_conta": achou,
            "quando_saiu": casou.dia.iloc[0] if achou else pd.NaT,
            "para_quem": " + ".join(casou.contraparte.astype(str).str[:28]) if achou else "",
        })
    return pd.DataFrame(linhas)


# A projeção anda: hoje ela começa no mês que vem, e quando esse mês chegar
# ela passa a começar no seguinte. Para o mês corrente ter contra o que ser
# comparado, a projeção dele é congelada antes de começar. As linhas abaixo
# são as que o plano produz; as que ele não produz (receita por canal, CMV
# separado por site e físico) ficam vazias em vez de inventadas.
@memo()
def mes_fechado() -> str:
    """Último mês com dado até o fim. Mês só fecha quando o extrato alcança
    o último dia dele; antes disso a receita ainda está pela metade e não
    serve de base para nada."""
    import calendar
    fim = fim_dos_custos()
    if fim is None:
        return datetime.now().strftime("%Y-%m")
    ultimo = calendar.monthrange(fim.year, fim.month)[1]
    return fim.strftime("%Y-%m") if fim.day >= ultimo else \
        (fim - pd.offsets.MonthBegin(1)).strftime("%Y-%m")


@memo()
def mes_corrente() -> str:
    """O mês que está acontecendo, que é o seguinte ao último fechado."""
    return str(pd.Period(mes_fechado(), "M") + 1)


@memo()
def receita_base() -> float:
    """Receita líquida do último mês fechado: a âncora da projeção."""
    t = pnl_competencia()["tabela"]
    linha = t[t.mes == mes_fechado()]
    return float(linha.receita_liquida.iloc[0]) if not linha.empty else 20000.0


# Toda premissa que sai de dado vive aqui e é medida a cada leitura. Antes
# disso metade delas era constante digitada em 20/09/2026 e a outra metade
# vinha do banco, no mesmo painel, e ninguém via a diferença: o crescimento
# ainda dizia 10,5% quando o mesmo ajuste já dava 13,6%, e o CMV dizia 41,4%
# quando o medido era 43,8%. O Pedro perguntou o que fazer para eu não
# esquecer de atualizar; a resposta é não deixar número para esquecer.
#
# `regra` diz de onde cada uma sai, e existem só duas:
#   aparada  média dos 6 meses fechados sem o maior e o menor, para o que
#            oscila sem direção
#   M-1      último mês fechado, para o que tem trajetória ou mudou de
#            regime, onde média apaga o movimento
# Categorias de despesa e a regra de cada uma. Contrato mensal vai pelo mês
# fechado, porque é o valor vigente; o que escala com volume vai por pedido;
# o resto vai por média aparada. O que é pontual de verdade, como cartório e
# seguro incêndio, cai para zero na apara, e por isso existe a linha de
# pontuais: sem ela a projeção esqueceria uma classe inteira de gasto.
_DESP_CONTRATO = ("Ads e agência", "Aluguel e condomínio", "Contador", "Sistemas", "Maquininha")
_DESP_VOLUME = ("Frete e entrega",)
_MIN_RECORRENTE = 4 / 6   # aparece em pelo menos 4 dos 6 meses para valer como recorrente


@memo()
def despesas_projetadas() -> dict:
    """Projeção de despesa por categoria. Meta fica de fora, vem do plano.

    Três coisas que o Pedro corrigiu em 30/09/2026 e que valem para sempre:

    1. **Mês sem lançamento não é custo zero.** A matriz preenche vazio com
       zero, e a média tratava "não veio a cobrança" como "não custou". O
       aluguel da maquininha, R$ 109 fixos, virava R$ 82 porque dois meses
       sem cobrança entraram como zero na conta.
    2. **Contrato não se calcula por média**: vale o último valor visto.
    3. **Compromisso contratado se aloca na categoria dele**, não vira
       linha separada nem média de pontuais. O que não está contratado e
       não é recorrente simplesmente não existe na projeção.
    """
    dc = pnl_competencia()["despesas_por_categoria"]
    fim = mes_fechado()
    meses = [m for m in dc.index if str(pd.Period(fim, "M") - 5) <= m <= fim]
    u = dc.loc[meses]
    if u.empty:
        return {}
    t = pnl_competencia()["tabela"]
    ult = t[t.mes == fim]
    ped_ult = float(ult.pedidos.iloc[0]) if not ult.empty else 0.0
    ped_proj = (_abrir_receita(receita_partida(), 0.0) or {}).get("Pedidos no site", ped_ult)

    saida = {}
    for c in u.columns:
        if c == "Meta Ads":
            continue
        s = u[c]
        presentes = s[s != 0]
        if len(presentes) / len(s) < _MIN_RECORRENTE:
            continue            # ocasional: só existe se estiver contratado
        if c in _DESP_CONTRATO:
            v = float(presentes.iloc[-1])
        elif c in _DESP_VOLUME:
            v = float(s.iloc[-1]) / ped_ult * ped_proj if ped_ult else float(s.iloc[-1])
        else:
            v = _aparada(presentes)     # só os meses em que houve, sem os vazios
        if round(v, 2) > 0:
            saida[c] = v

    # Compromisso contratado manda na categoria dele: se existe boleto para
    # o mês, ele é o número, não a estimativa.
    for categoria, valor in compromissos_por_categoria(mes_corrente()).items():
        saida[categoria] = valor
    return saida


@memo()
def compromissos_por_categoria(mes: str) -> dict:
    """Contas contratadas do mês, já classificadas na categoria de despesa.

    Usa as regras de planilha, que ficam no Supabase porque a planilha
    chama fornecedor por apelido. O que for produção ou agência não entra:
    já está no CMV e nos fixos.
    """
    import re
    import dados_fin
    df = dados_fin.ler_contas_a_pagar() if dados_fin.disponivel() else pd.DataFrame()
    if df.empty:
        return {}
    import extrato
    regras = [r for r in extrato.regras_externas() if r.get("sentido") == "planilha"]
    pend = df[(df.situacao.fillna("").str.upper() != "PAGO")
              & (df.data.dt.strftime("%Y-%m") == mes)]
    saida = {}
    for r in pend.itertuples(index=False):
        for regra in regras:
            if re.search(regra["padrao"], str(r.descricao), re.I):
                if regra["natureza"] == "estoque" or regra["categoria"] == "Ads e agência":
                    break       # já modelado no CMV ou nos fixos
                saida[regra["categoria"]] = saida.get(regra["categoria"], 0.0) + float(r.valor)
                break
    return saida


def _premissas_do_painel():
    """As premissas exatamente como o painel as mostra.

    Os campos eram barras com passo de arredondamento, e por causa disso o
    gráfico dizia lucro de R$ 276 em out/26 enquanto a coluna do orçado
    dizia R$ 314. Viraram campo de digitar, escolha do Pedro em 30/09/2026,
    e aqui não se arredonda mais nada: os dois leem o mesmo número.
    """
    import plano
    m = premissas_medidas()
    if not m:
        return None
    return plano.Premissas(
        inicio=mes_corrente(),
        receita_base=m["receita_base"][0],
        crescimento=m["crescimento"][0],
        cmv=m["cmv"][0],
        imposto=m["imposto"][0],
        taxa_site=m["taxa_site"][0],
        ads=m["ads"][0],
        fixos=m["fixos"][0],
        share_fisica=m["share_fisica"][0],
        prazo_recebimento=m["prazo_recebimento"][0],
        ads_a_pagar=float(meta_ads().get(mes_fechado(), 0.0)),
        estoque_custo=estoque_a_custo(),
        caixa_inicial=max(_caixa_hoje(), 0.0))


@memo()
def caixa_orcado(mes: str) -> dict:
    """A linha do fluxo de caixa projetado, nas colunas da tabela de caixa.

    A taxa da Pagar.me não é saída: ela já vem descontada do que liquida.
    Por isso Site entra líquido dela, como no realizado, e o imposto vai na
    coluna própria. A identidade fecha: Site mais fora do site, menos
    estoque, despesa e imposto, dá o saldo do mês do plano.
    """
    import plano
    p = _premissas_do_painel()
    if p is None:
        return {}
    sim = plano.simular(p)
    linha = sim["tabela"][sim["tabela"].mes == mes]
    if linha.empty:
        return {}
    r = linha.iloc[0]
    fisica = float(r.receita) * p.share_fisica
    imposto = float(r.receita) * IMPOSTO_PADRAO
    return {
        "Site": float(r.recebido) - fisica - (float(r.taxas) - imposto),
        "Fora do site": fisica,
        "Estoque": -float(r.producao),
        "Despesas": -(float(r.ads_pago) + float(r.fixos) + float(r.compromissos)),
        "Imposto": -imposto,
        "Saldo do mês": float(r.fluxo),
        "Aportes": float(r.aporte),
        "Caixa": float(r.caixa),
    }


def _caixa_hoje() -> float:
    fx = fluxo_de_caixa()
    real = fx[~fx.futuro]
    return float(real.caixa.iloc[-1]) if not real.empty else 0.0


@memo()
def premissas_medidas() -> dict:
    """Todas as premissas que saem de dado, com a regra de cada uma."""
    import numpy as np
    t = pnl_competencia()["tabela"]
    fim = mes_fechado()
    u = t[(t.mes >= str(pd.Period(fim, "M") - 5)) & (t.mes <= fim)]
    prop = _proporcoes()
    if u.empty:
        return {}
    crescimento = float(np.polyfit(range(len(u)), np.log(u.receita_liquida.replace(0, pd.NA).ffill().values), 1)[0])
    return {
        "receita_base": (receita_partida(), "M-1 crescido", "receita do primeiro mês projetado"),
        "crescimento": (crescimento, "ajuste 6m", "crescimento log-linear da receita líquida"),
        "cmv": (_aparada(u.cmv / u.receita_liquida), "aparada", "CMV sobre receita líquida"),
        "taxa_site": (_aparada(u.taxas / u.receita_site_liquida.replace(0, pd.NA)), "aparada",
                      "Pagar.me sobre a receita líquida do site"),
        "imposto": (IMPOSTO_PADRAO, "alíquota", "Simples sobre toda a receita"),
        "ads": (meta_por_site(), "M-1", "Meta sobre receita líquida do site"),
        "share_fisica": (prop.get("fisica", 0.0), "aparada", "maquininha e Pix direto na receita"),
        "ticket": (prop.get("ticket", 0.0), "M-1", "ticket médio do site"),
        "maq_na_fisica": (prop.get("maq_na_fisica", 0.0), "M-1", "maquininha dentro da venda física"),
        "desconto": (prop.get("desconto", 0.0), "M-1", "desconto de Pix sobre a receita do site"),
        "estornos": (prop.get("estornos", 0.0), "aparada", "estornos sobre a receita do site"),
        "cmv_site": (prop.get("cmv_site", 0.0), "aparada", "fatia do site no CMV"),
        "roas": (_roas(), "M-1", "receita do site por real de Meta"),
        "fixos": (sum(despesas_projetadas().values()), "por categoria",
                  "despesas fora Meta, somadas da projeção por categoria"),
        "prazo_recebimento": (_prazo_recebimento() * (1 - prop.get("fisica", 0.0)), "aparada",
                              "fatia da receita que só cai no mês seguinte"),
    }


@memo()
def _roas() -> float:
    """Receita do site por real investido em Meta, no último mês fechado.

    Fica como informação medida, não entra na projeção: o plano cresce a
    receita por uma taxa e trata o Meta como custo. Ligar os dois viraria
    circular, porque o Meta é indexado à receita. A decisão de quanto
    investir se toma olhando este número, não simulando na tela. Pedido do
    Pedro em 30/09/2026.

    Vem do mês fechado porque tem trajetória clara: 3,6x em fevereiro,
    6,8x em setembro.
    """
    meta = meta_ads().get(mes_fechado())
    t = pnl_competencia()["tabela"]
    linha = t[t.mes == mes_fechado()]
    if not meta or linha.empty:
        return 0.0
    return float(linha.receita_site.iloc[0]) / float(meta)


@memo()
def _prazo_recebimento() -> float:
    """Fatia do líquido da Pagar.me que cai depois do mês da venda.

    A premissa do plano dizia 30%. Medido nos recebíveis contra a data da
    venda, os seis meses fechados dão 18,6%: com antecipação automática
    quase tudo liquida em sete dias, e só a última semana do mês atravessa.
    """
    import db
    try:
        linhas = db.recebimento_atrasado()
    except Exception:
        return 0.186
    fim = mes_fechado()
    u = [r for r in linhas if str(pd.Period(fim, "M") - 5) <= r["mes"] <= fim and r["liquido"]]
    if not u:
        return 0.186
    return _aparada(pd.Series([r["cai_depois"] / r["liquido"] for r in u]))


@memo()
def meta_por_site() -> float:
    """Meta como fração da receita do site, no último mês fechado.

    Anúncio puxa venda de site, e a eficiência vem melhorando com
    trajetória clara: 27,8%, 28,2%, 21,2%, 14,4%, 15,4%, 18,8%, 14,2% e
    9,4% em setembro sobre a receita cheia. Média sobre trajetória é o erro
    que já cometi no ticket e no desconto de Pix, então aqui também vale o
    mês fechado.
    """
    meta = meta_ads().get(mes_fechado())
    t = pnl_competencia()["tabela"]
    linha = t[t.mes == mes_fechado()]
    if meta is None or linha.empty:
        return 0.125
    # Sobre a receita do site já líquida de desconto e estorno, que é como o
    # plano trabalha. Medir sobre a bruta e aplicar sobre a líquida, como eu
    # fiz na primeira tentativa, encolhe o investimento sem querer.
    base = float(linha.receita_site_liquida.iloc[0])
    return float(meta) / base if base else 0.125


@memo()
def receita_partida() -> float:
    """Receita do primeiro mês projetado: a âncora já crescida.

    `plano.simular` usa `receita_base` como o próprio primeiro mês da
    projeção, sem crescimento nenhum. Ancorando no último mês fechado, isso
    fazia outubro sair idêntico a setembro na receita líquida, centavo por
    centavo, enquanto todas as outras linhas mudavam. Com a base antiga de
    R$ 20 mil no braço o defeito existia igual e não aparecia, porque
    R$ 20 mil não era mês nenhum. O Pedro viu em 30/09/2026.
    """
    import plano
    return receita_base() * (1 + plano.Premissas().crescimento)


def orcamento_do_mes(mes: str, premissas=None) -> dict:
    """Projeção de um mês nas linhas do PnL, para congelar.

    O plano trabalha com receita líquida, CMV, mídia e fixos. A separação
    entre taxa da Pagar.me e Simples sai da própria premissa: os 7,4% são
    4,76% de taxa mais 2,64% de imposto. A agência de R$ 1.800 está dentro
    dos fixos do plano e aqui sai de lá para a linha de marketing, que é
    onde ela aparece no realizado.
    """
    import plano
    p = premissas or _premissas_do_painel()
    if p is None:
        return {}
    sim = plano.simular(p)
    linha = sim["tabela"][sim["tabela"].mes == mes]
    if linha.empty:
        return {}
    r = linha.iloc[0]
    receita = float(r.receita)
    imposto = receita * p.imposto
    taxas = float(r.taxas) - imposto
    cmv = float(r.cmv_competencia)
    agencia = 1800.0
    marketing = float(r.ads) + agencia
    outras = float(r.fixos) - agencia + float(r.compromissos)
    linhas = {
        "Receita líquida": receita,
        "(-) Taxas Pagar.me": taxas,
        "(-) Imposto": imposto,
        "(-) CMV": cmv,
        "Margem bruta": receita - taxas - imposto - cmv,
        "(-) Meta e agência": marketing,
        "(-) Demais despesas": outras,
        "Resultado": receita - taxas - imposto - cmv - marketing - outras,
    }
    linhas.update(_abrir_receita(receita, cmv))
    return linhas


@memo()
def _proporcoes(meses: int = 6) -> dict:
    """Como a receita se reparte, medido nos últimos meses fechados.

    O plano projeta um número só de receita líquida e um de CMV. Para o
    orçado não ficar com metade das linhas em branco, o resto sai daqui:
    proporção medida, não chute, e recalculada a cada mês que fecha.
    """
    t = pnl_competencia()["tabela"]
    fim = mes_fechado()
    u = t[(t.mes >= str(pd.Period(fim, "M") - (meses - 1))) & (t.mes <= fim)]
    if u.empty or not u.receita_liquida.sum():
        return {}
    cmv_total = u.cmv_site + u.cmv_fisico_estimado
    fisica = u.receita_maquininha + u.receita_pix_direto
    # A venda fora do site é projetada somada, e só depois repartida entre
    # maquininha e Pix direto. Separadas elas oscilam muito e sem sentido
    # econômico: em setembro a maquininha caiu para 12,3% da receita e o Pix
    # direto subiu para 20%, com a física total no mesmo lugar. O que mudou
    # foi a forma de pagar, não a venda. O rateio usa o mês fechado mais
    # recente, que é a foto mais próxima do hábito de agora. Decisão do
    # Pedro, 30/09/2026.
    ult = u[u.mes == mes_fechado()]
    fis_ult = float((ult.receita_maquininha + ult.receita_pix_direto).iloc[0]) if not ult.empty else 0.0
    maq_na_fisica = float(ult.receita_maquininha.iloc[0]) / fis_ult if fis_ult else 0.5
    return {
        "fisica": _aparada(fisica / u.receita_liquida),
        "maq_na_fisica": maq_na_fisica,
        # Desconto de Pix pelo mês fechado, não pela média: até agosto todo
        # pedido Pix levava 5% e em setembro só 1 de 9 levou. A política
        # mudou, o cupom virou FRETEGRATIS, e a média de 6 meses projetava
        # R$ 234 onde setembro gastou R$ 19. Média sobre mudança de regime
        # é o mesmo erro do ticket.
        "desconto": (float(ult.desconto_pix.iloc[0]) / float(ult.receita_site.iloc[0])
                     if not ult.empty and float(ult.receita_site.iloc[0]) else 0.0),
        "estornos": _aparada(u.estornos / u.receita_site),
        "cmv_site": _aparada(u.cmv_site / cmv_total.replace(0, pd.NA)),
        # Ticket pelo mês fechado mais recente, não pela média: ele vem
        # caindo (1.324, 867, 865, 771) e a média aparada dava R$ 921, o que
        # fazia a projeção vender menos peças e faturar mais sem nada que
        # sustentasse a alta. O Pedro viu em 30/09/2026. Com o ticket de
        # M-1, o crescimento da receita aparece como mais pedido, que é o
        # que a operação vem fazendo.
        "ticket": (float(ult.receita_site.iloc[0]) / float(ult.pedidos.iloc[0])
                   if not ult.empty and float(ult.pedidos.iloc[0]) else 0.0),
        "meses": int(len(u)),
    }


def _aparada(s: pd.Series) -> float:
    """Média sem o maior e o menor. Escolha do Pedro em 30/09/2026.

    A média agregada era puxada pelos meses grandes: setembro vendeu muito
    e teve pouca maquininha, e isso sozinho derrubava a proporção do canal.
    Com poucos meses de histórico, um mês fora da curva move demais.
    """
    s = s.replace([float("inf"), float("-inf")], pd.NA).dropna().astype(float).sort_values()
    if len(s) >= 4:
        s = s.iloc[1:-1]
    return float(s.mean()) if len(s) else 0.0


def _abrir_receita(receita: float, cmv: float) -> dict:
    """Quebra a receita líquida projetada em canal, desconto e estorno."""
    p = _proporcoes()
    if not p:
        return {}
    fisica = receita * p["fisica"]
    maq = fisica * p["maq_na_fisica"]
    pix = fisica - maq
    site_liq = receita - fisica
    # Desconto e estorno são só do site, e são medidos sobre a receita cheia
    # dele: site_liq = site_bruta × (1 − desconto − estorno).
    fator = 1 - p["desconto"] - p["estornos"]
    site_bruta = site_liq / fator if fator > 0 else site_liq
    cmv_site = cmv * p["cmv_site"]
    return {
        "Receita bruta": site_bruta + maq + pix,
        "Receita do site (Shopify)": site_bruta,
        "Maquininha (líquido de MDR)": maq,
        "Pix direto e link": pix,
        "(-) Desconto Pix": site_bruta * p["desconto"],
        "(-) Estornos": site_bruta * p["estornos"],
        "(-) CMV do site": cmv_site,
        "(-) CMV fora do site (estimado)": cmv - cmv_site,
        "Pedidos no site": site_bruta / p["ticket"] if p["ticket"] else 0.0,
    }


@memo()
def orcamentos() -> dict:
    """{mes: {linha: valor}} do que já foi congelado."""
    try:
        import dados_fin
        if not dados_fin.disponivel():
            return {}
        df = dados_fin.ler_orcamento()
    except Exception:
        return {}
    if df.empty:
        return {}
    return {m: dict(zip(g.linha, g.valor)) for m, g in df.groupby("mes")}


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
    # Competência, não caixa: mensalidade no mês do serviço e devolução de
    # fornecedor abatendo a despesa dele. O fluxo de caixa continua pelo
    # extrato cru, que é o que fecha com o saldo da conta.
    desp = _trocar_reembolso_por_fatura(_competencia_dos_contratos(_abater_devolucoes(desp)))
    # `_abater_devolucoes` roda aqui e em estoque_a_custo, nunca dentro de
    # ledger_saidas: no fluxo de caixa a devolução entra como entrada, e
    # abater dos dois lados quebraria a conferência com o saldo da conta.
    desp_cat = desp.groupby(["mes", "categoria"])["valor"].sum().unstack(fill_value=0.0)
    # Meta entra pelo relatório da agência e sai, até onde der, do reembolso
    # às sócias do mesmo mês, que é por onde ela foi paga. O que sobrar no
    # reembolso é aluguel e outras despesas no cartão.
    meta = pd.Series(meta_ads(), name="Meta Ads")
    desp_cat = desp_cat.reindex(sorted(set(desp_cat.index) | set(meta.index))).fillna(0.0)
    # A fatura Porto também é pagamento de Meta já contada pelo relatório.
    fatura = desp_cat.pop(CATEGORIA_FATURA) if CATEGORIA_FATURA in desp_cat else 0.0
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
    # Bruta é tudo que foi vendido antes de tirar desconto de Pix e estorno.
    # A maquininha já entra líquida de MDR porque é assim que o dinheiro
    # aparece no extrato: não existe a venda cheia dela em lugar nenhum.
    t["receita_bruta"] = t.receita_site + t.receita_fisica
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
    ent = ex[ex.sentido == "entrada"]
    ent_nat = ent.pivot_table(index="mes", columns="natureza", values="valor", aggfunc="sum").fillna(0.0) if not ent.empty else pd.DataFrame()
    led = ledger_saidas()
    sai_nat = led.pivot_table(index="mes", columns="natureza", values="valor", aggfunc="sum").fillna(0.0) if not led.empty else pd.DataFrame()
    ap = aportes()
    ap_mes = ap.groupby(ap.data.dt.strftime("%Y-%m"))["valor"].sum() if not ap.empty else pd.Series(dtype=float)
    pag = caixa_pagarme_por_mes()

    # O mês corrente sai do dado, não do relógio: o servidor roda em UTC e
    # no fim do dia 30 ele já acha que virou o mês, o que fazia o gráfico
    # projetar a partir de novembro e deixar outubro sem barra nenhuma.
    # O Pedro viu em 30/09/2026 às 21h, que é 01/10 em UTC.
    hoje = mes_fechado()
    meses = _eixo_meses(ent_nat.index, sai_nat.index, ap_mes.index, pag.index if not pag.empty else [], [hoje])
    t = pd.DataFrame(index=meses)
    t.index.name = "mes"
    z = pd.Series(0.0, index=meses)

    def col(df, nome):
        return df[nome].reindex(meses).fillna(0.0) if (not df.empty and nome in df) else z

    t["recebido_site"] = col(ent_nat, "liquidacao_online")
    t["recebido_fisico"] = col(ent_nat, "venda_fisica")
    t["devolucoes"] = col(ent_nat, "devolucao")
    # "interna" é transferência para a outra conta Stone da própria empresa e
    # os créditos de teste de centavos: não são resultado, mas saem e entram
    # de verdade, e sem eles o caixa não fecha com o saldo da conta.
    t["internas"] = col(ent_nat, "interna")
    t["recebido"] = t.recebido_site + t.recebido_fisico + t.devolucoes + t.internas
    t["a_receber"] = col(pag, "previsto")
    # a_classificar entra no caixa como qualquer outra saída: o dinheiro saiu
    # mesmo sem regra. Fora do caixa ela continua aparecendo em Bastidores até
    # ganhar categoria.
    for nat in ("estoque", "despesa", "capex", "imposto", "interna", "a_classificar"):
        t[f"saida_{nat}"] = col(sai_nat, nat)
    t["saidas"] = (t.saida_estoque + t.saida_despesa + t.saida_capex + t.saida_imposto
                   + t.saida_interna + t.saida_a_classificar)
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
@memo()
def estoque_na_loja() -> dict:
    """Estoque ativo na Shopify: peças, valor a preço de etiqueta e a custo
    de ficha. Os "sob encomenda" (Loulou) saem da conta, porque não existem
    na arara: o cadastro tem número só para a página não dizer esgotado."""
    import shopify_client
    if not shopify_client.configurado():
        return {"pecas": 0, "venda": 0.0, "custo": 0.0, "sob_encomenda": 0}
    consulta = """query($cursor: String) { productVariants(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { inventoryQuantity price product { title status } } } }"""
    custos, cursor = custo_por_ficha(), None
    pecas = venda = custo = 0.0
    encomenda = 0
    try:
        while True:
            bloco = shopify_client._graphql(consulta, {"cursor": cursor})["productVariants"]
            for v in bloco["nodes"]:
                prod = v.get("product") or {}
                if prod.get("status") != "ACTIVE":
                    continue
                q = max(v.get("inventoryQuantity") or 0, 0)
                titulo = prod.get("title", "")
                if re.search(SOB_ENCOMENDA, titulo, flags=re.I):
                    encomenda += q
                    continue
                pecas += q
                venda += q * float(v.get("price") or 0)
                ficha = ficha_da_peca(titulo)
                custo += q * (custos.get(ficha) or 0.0)
            if not bloco["pageInfo"]["hasNextPage"]:
                break
            cursor = bloco["pageInfo"]["endCursor"]
    except Exception:
        return {"pecas": 0, "venda": 0.0, "custo": 0.0, "sob_encomenda": 0}
    return {"pecas": int(pecas), "venda": venda, "custo": custo, "sob_encomenda": encomenda}


def estoque_a_custo() -> float:
    """Produção paga menos CMV consumido: o que está na arara e no rolo, a custo.

    Devolução de fornecedor abate a compra: tecido devolvido não está no
    rolo. São R$ 886,91 desde o começo, entre Mikkonos, Bonor, Paulistana e
    SHPP. No caixa a devolução já entra como entrada, por isso ela não pode
    ser abatida no `ledger_saidas`, que é o que fecha com o saldo da conta."""
    led = _abater_devolucoes(ledger_saidas())
    # Tecido e aviamento comprados no cartão da sócia não passam pelo
    # extrato da Stone, só pela fatura. São estoque igual ao resto.
    compras = compras_no_cartao()
    no_cartao = float(compras[compras.natureza == "estoque"].valor.sum()) if not compras.empty else 0.0
    t = pnl_competencia()["tabela"]
    return float(led[led.natureza == "estoque"].valor.sum() + no_cartao - t.cmv.sum())


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
