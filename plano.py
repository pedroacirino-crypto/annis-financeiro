"""Plano para a frente: produzir em M para vender em M+1, sem parar.

Responde três perguntas do Pedro (20/09/2026):

  1. Quando a operação paga a produção do mês seguinte sem aporte (breakeven
     de caixa, sustentado).
  2. Quando o dinheiro volta: o que for posto daqui para a frente, e o total
     desde o começo.
  3. Quanto dá de lucro, e quando.

Receita: run rate com a taxa de crescimento dos últimos 6 meses, amortecida
por um fator para não virar exponencial (g_t = g · fator^t, converge para
um patamar). CMV médio histórico.

Estoque é um estoque só, a custo: a produção entra, a venda sai, e uma
fração envelhece por mês (peça que não vai mais vender a preço cheio). A
produção de cada mês é para a venda de M+2 e é reduzida na proporção do
excesso de estoque sobre a cobertura alvo: com o estoque alto se produz
pouco, e conforme ele desce a produção volta ao ritmo cheio. É um fade, não
um degrau, porque é assim que a operação vai funcionar (Pedro, 20/09/2026).

Tudo em reais nominais, sem desconto. Mês de competência = mês da venda.
"""

from dataclasses import dataclass, field

import pandas as pd

from memo import memo


# Contas já contratadas e ainda não pagas em 20/09/2026, fora de aluguel,
# contador, sistema, Correios e Simples, que já estão em fixos e taxas.
# Fonte: planilha "contas a pagar até dezembro" do Pedro.
COMPROMISSOS_LOCAL = {
    "2026-10": 361.00 + 126.55 + 1120.00 + 1632.07 + 1492.96 + 523.78 + 867.00   # sobra de setembro
              + 258.00 + 920.00 + 644.75 + 1222.35 + 1799.19 + 867.00 + 1118.00 + 1800.00 + 867.00,
    "2026-11": 258.00 + 1799.19,
    "2026-12": 258.00,
}


# O que já está em fixos ou taxas e não pode entrar de novo como compromisso.
_FIXOS_NA_PLANILHA = r"aluguel|condom|koyashiki|contabil|olist|correios|simples"


@memo()
def compromissos_pendentes() -> dict:
    """Contas contratadas e não pagas, por mês, fora do que já é fixo.
    Supabase primeiro; sem ele, o número local de 20/09/2026."""
    import re
    import dados_fin
    df = dados_fin.ler_contas_a_pagar() if dados_fin.disponivel() else pd.DataFrame()
    if df.empty:
        return dict(COMPROMISSOS_LOCAL)
    pend = df[(df.situacao.fillna("").str.upper() != "PAGO")
              & ~df.descricao.str.contains(_FIXOS_NA_PLANILHA, case=False, regex=True)]
    return pend.groupby(pend.data.dt.strftime("%Y-%m"))["valor"].sum().to_dict()


def contas_a_pagar_total() -> float:
    return float(sum(compromissos_pendentes().values()))


@dataclass
class Premissas:
    inicio: str = "2026-10"
    meses: int = 27
    receita_base: float = 20000.0        # receita líquida do 1º mês (out/26), escolha do Pedro
    crescimento: float = 0.105           # ao mês, ajuste log-linear abr a set/26
    fator_reducao: float = 0.8           # g_t = crescimento · fator^t
    cmv: float = 0.414                   # CMV médio sobre receita líquida
    taxas: float = 0.074                 # taxas Pagar.me + Simples
    ads: float = 0.125                   # Meta, % da receita líquida total, últimos 6 meses (relatórios da agência)
    fixos: float = 7600.0                # aluguel 2,8, agência 1,8, pessoas 1,3, foto 0,7, frete, contador, sistemas
    estoque_custo: float = 53700.0       # já pago, a custo
    cobertura_alvo: float = 3.0          # meses de CMV que se quer ter em estoque
    envelhecimento: float = 0.02         # fração do estoque que deixa de ser vendável por mês
    antecedencia: int = 2                # produz em M para vender em M+antecedencia
    prazo_recebimento: float = 0.3       # fração da receita que só cai no mês seguinte (cartão)
    a_receber_inicial: float = 3300.0    # recebíveis da Pagar.me em 20/09
    caixa_inicial: float = 0.0
    acumulado_historico: float = -94574.0
    compromissos: dict = field(default_factory=compromissos_pendentes)


def simular(p: Premissas) -> dict:
    ini = pd.Period(p.inicio, "M")
    meses = [ini + i for i in range(p.meses)]

    # Demanda com crescimento amortecido, com meses a mais porque a produção
    # de M olha para a venda de M+antecedencia.
    demanda = []
    nivel = p.receita_base
    for i in range(p.meses + p.antecedencia + 1):
        demanda.append(nivel)
        nivel *= 1 + p.crescimento * (p.fator_reducao ** i)

    # Conta vencida antes do primeiro mês do plano (a sobra de setembro, por
    # exemplo) não some: cai no primeiro mês.
    compromissos = dict(p.compromissos)
    atrasados = sum(v for m, v in compromissos.items() if m < str(ini))
    compromissos = {m: v for m, v in compromissos.items() if m >= str(ini)}
    compromissos[str(ini)] = compromissos.get(str(ini), 0.0) + atrasados

    linhas, estoque, caixa, receita_anterior = [], p.estoque_custo, p.caixa_inicial, 0.0
    total_aportes = 0.0
    for i, m in enumerate(meses):
        d = demanda[i]
        recebido = d * (1 - p.prazo_recebimento) + receita_anterior * p.prazo_recebimento
        if i == 0:
            recebido += p.a_receber_inicial
        taxas = d * p.taxas
        ads = d * p.ads
        cmv_total = d * p.cmv
        # A venda sai do estoque; o que não tem em estoque é produção do mês.
        do_estoque = min(estoque, cmv_total)
        estoque -= do_estoque
        envelhecido = estoque * p.envelhecimento
        estoque -= envelhecido

        # Produção para M+antecedencia, reduzida na proporção do excesso de
        # estoque sobre a cobertura alvo. Fração 1 = ritmo cheio.
        alvo = p.cobertura_alvo * demanda[i + 1] * p.cmv
        fracao = 1.0 if estoque <= 0 else min(1.0, alvo / estoque)
        producao = demanda[i + p.antecedencia] * p.cmv * fracao + (cmv_total - do_estoque)
        estoque += demanda[i + p.antecedencia] * p.cmv * fracao

        compromisso_mes = compromissos.get(str(m), 0.0)
        fluxo = recebido - taxas - ads - p.fixos - producao - compromisso_mes
        caixa += fluxo
        aporte = 0.0
        if caixa < 0:
            aporte = -caixa
            caixa = 0.0
        total_aportes += aporte

        resultado = d - taxas - cmv_total - ads - p.fixos
        linhas.append({
            "mes": str(m), "receita": d, "recebido": recebido, "taxas": taxas, "ads": ads,
            "fixos": p.fixos, "producao": producao, "compromissos": compromisso_mes,
            "cmv_competencia": cmv_total, "do_estoque": do_estoque, "estoque_restante": estoque,
            "envelhecido": envelhecido, "fracao_producao": fracao,
            "fluxo": fluxo, "aporte": aporte, "caixa": caixa, "resultado": resultado,
        })
        receita_anterior = d

    t = pd.DataFrame(linhas)
    t["fluxo_acumulado"] = t.fluxo.cumsum()
    t["resultado_acumulado"] = t.resultado.cumsum()
    t["aportes_acumulados"] = t.aporte.cumsum()
    t["acumulado_total"] = p.acumulado_historico + t.fluxo_acumulado

    # 1. Breakeven de caixa sustentado: primeiro mês positivo sem nenhum
    # negativo depois dele.
    breakeven = None
    for i in range(len(t)):
        if (t.fluxo.iloc[i:] >= 0).all():
            breakeven = t.mes.iloc[i]
            break
    ultimo_aporte = t[t.aporte > 0].mes.iloc[-1] if (t.aporte > 0).any() else None

    # 2. Quando o dinheiro volta.
    novo = t[(t.fluxo_acumulado >= t.aportes_acumulados.iloc[-1]) & (t.mes > (ultimo_aporte or ""))]
    payback_novo = novo.mes.iloc[0] if not novo.empty and total_aportes > 0 else (t.mes.iloc[0] if total_aportes == 0 else None)
    tot = t[t.acumulado_total >= 0]
    payback_total = tot.mes.iloc[0] if not tot.empty else None

    # 3. Lucro.
    lucro_mes = t[t.resultado > 0]
    primeiro_lucro = lucro_mes.mes.iloc[0] if not lucro_mes.empty else None
    por_ano = t.groupby(t.mes.str[:4])[["receita", "resultado", "fluxo", "aporte"]].sum()
    # Quando a produção volta ao ritmo cheio (95% da demanda ou mais).
    cheia = t[t.fracao_producao >= 0.95]
    fim_estoque = cheia.mes.iloc[0] if not cheia.empty else None

    return {
        "tabela": t, "breakeven": breakeven, "ultimo_aporte": ultimo_aporte,
        "total_aportes": total_aportes, "payback_novo": payback_novo,
        "payback_total": payback_total, "primeiro_lucro": primeiro_lucro,
        "por_ano": por_ano, "fim_estoque": fim_estoque,
        "patamar": demanda[-1],
    }
