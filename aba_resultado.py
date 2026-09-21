"""Aba Financeiro › Resultado.

Também roda sozinha, para revisão local: `streamlit run aba_resultado.py`.

A página conta uma história em três tempos, nesta ordem, e cada bloco
responde uma pergunta só:

  1. O que aconteceu      PnL e caixa, mês a mês
  2. Onde estamos hoje    caixa, estoque, contas a pagar
  3. Para onde vai        um modelo só de projeção, com gráficos

Tabela é detalhe: fica em expansor. O que aparece aberto é número grande e
gráfico.
"""

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

import extrato
import financeiro as fin
import plano


COR = {"receita": "#1f5fa8", "margem": "#7fb3e6", "resultado": "#c0392b", "caixa": "#2e7d32",
       "estoque": "#b07d2b", "producao": "#6d4c41", "neutro": "#888", "aporte": "#c0392b"}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def brl(v, casas=0) -> str:
    if v is None or pd.isna(v):
        return ""
    s = f"{abs(v):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if v < 0 else "") + "R$ " + s


def mil(v) -> str:
    """R$ 26,6 mil, para número grande."""
    if v is None or pd.isna(v):
        return ""
    return ("-" if v < 0 else "") + f"R$ {abs(v) / 1000:.1f} mil".replace(".", ",")


def md(texto: str) -> str:
    """Escapa o cifrão, em markdown o Streamlit trata `$...$` como LaTeX."""
    return texto.replace("$", r"\$")


def pct(x, casas=1) -> str:
    return f"{x * 100:.{casas}f}%".replace(".", ",")


def mes_curto(m) -> str:
    if not m:
        return ""
    a, mm = str(m).split("-")[:2]
    return f"{MESES[int(mm) - 1]}/{a[2:]}"


def grafico_linhas(df, cores: dict, titulo_y="R$ mil", altura=260, zero=True):
    """Linhas por mês em R$ mil, com régua no zero. `cores` = {coluna: (rótulo, cor)}."""
    g = df[["mes"] + list(cores)].melt("mes", var_name="linha", value_name="valor")
    g["mes"] = g.mes.map(mes_curto)
    g["valor"] = g.valor / 1000
    g["linha"] = g.linha.map({k: v[0] for k, v in cores.items()})
    escala = alt.Scale(domain=[v[0] for v in cores.values()], range=[v[1] for v in cores.values()])
    ch = alt.Chart(g).mark_line(point=True).encode(
        x=alt.X("mes:N", sort=None, title=""),
        y=alt.Y("valor:Q", title=titulo_y),
        color=alt.Color("linha:N", title="", scale=escala, legend=alt.Legend(orient="top")),
        tooltip=["mes", "linha", alt.Tooltip("valor:Q", format=".1f", title="R$ mil")],
    ).properties(height=altura)
    if zero:
        ch = ch + alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=COR["neutro"]).encode(y="y")
    return ch


def grafico_barras_sinal(df, coluna, rotulo, altura=220):
    """Barras por mês, verde acima de zero e vermelho abaixo."""
    g = df[["mes", coluna]].rename(columns={coluna: "valor"}).copy()
    g["mes"] = g.mes.map(mes_curto)
    g["valor"] = g.valor / 1000
    g["sinal"] = g.valor.map(lambda v: "positivo" if v >= 0 else "negativo")
    return alt.Chart(g).mark_bar().encode(
        x=alt.X("mes:N", sort=None, title=""),
        y=alt.Y("valor:Q", title=f"{rotulo} (R$ mil)"),
        color=alt.Color("sinal:N", legend=None, scale=alt.Scale(domain=["positivo", "negativo"], range=[COR["caixa"], COR["aporte"]])),
        tooltip=["mes", alt.Tooltip("valor:Q", format=".1f", title="R$ mil")],
    ).properties(height=altura)


@st.cache_data(ttl=3600, show_spinner="Montando o resultado…")
def _dados():
    """Tudo que é pesado, uma vez por hora. Carga nova no Supabase aparece na
    próxima hora ou depois de um reboot."""
    pnl = fin.pnl_competencia()
    return {
        "pnl": pnl, "fluxo": fin.fluxo_de_caixa(), "extrato": extrato.carregar(),
        "aportes": fin.aportes(), "ledger": fin.ledger_saidas(), "estoque_custo": fin.estoque_a_custo(),
        "contas_a_pagar": plano.contas_a_pagar_total(), "pedidos": fin.pedidos_pagos(),
        "fichas": fin.carregar_legado()["custo_pecas"], "a_classificar": extrato.a_classificar(),
    }


def render():
    if not fin.legado_disponivel() or not extrato.disponivel():
        st.error("Sem extrato e fichas: carregue com `carga.py` no Supabase, ou deixe os arquivos em `legado/`.")
        return
    hoje = datetime.now().strftime("%Y-%m")
    d = _dados()
    pnl = d["pnl"]
    t = pnl["tabela"]
    fluxo = d["fluxo"]
    fx = fluxo[fluxo.mes <= hoje].copy()
    ex = d["extrato"]
    ap = d["aportes"]
    led = d["ledger"]
    estoque_custo = d["estoque_custo"]
    contas_a_pagar = d["contas_a_pagar"]
    acumulado = float(fx.acumulado_operacional.iloc[-1])

    # ─── Cabeçalho: quatro números que resumem tudo ─────────────────────────────
    st.title("Resultado")
    st.caption(md(f"Shopify na receita do site, Pagar.me nas taxas, extrato da conta Stone em custos, venda física e aportes "
                  f"(lido até {ex.data.max():%d/%m/%Y}), planilha de 2025 nas fichas técnicas."))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Aportes das sócias", mil(ap.valor.sum()), help=f"Ana {brl(ap[ap.quem == 'Ana'].valor.sum())} · Isa {brl(ap[ap.quem == 'Isa'].valor.sum())} · pagas do bolso antes da conta {brl(ap[ap.quem.str.contains('bolso')].valor.sum())}")
    k2.metric("Receita líquida desde o início", mil(t.receita_liquida.sum()), help="Site líquido de desconto Pix e estornos, mais maquininha e Pix direto.")
    k3.metric("Caixa hoje", brl(fx.caixa.iloc[-1]), help="Aportes mais tudo que entrou, menos tudo que saiu. Bate com o saldo da conta.")
    k4.metric("Acumulado operacional", mil(acumulado), help="Tudo que a operação gerou menos tudo que gastou, sem aportes. Quando chegar a zero, o capital voltou.")

    # ═══════════════════════════════════════════════════════════════════════════
    st.header("1. O que aconteceu")
    st.caption("Mês a mês desde o lançamento. Competência é a venda no mês em que aconteceu; caixa é o dinheiro quando entrou e saiu.")

    operacao = t[(t.receita_liquida > 0) | (t.despesas > 0)]
    meses_pnl = [m for m in operacao.mes if m <= hoje]
    janela = st.select_slider("Período", options=[6, 12, 18, len(meses_pnl)], value=min(12, len(meses_pnl)),
                              format_func=lambda n: "tudo" if n == len(meses_pnl) else f"últimos {n} meses")
    meses_vis = meses_pnl[-janela:]
    base = t.set_index("mes").loc[meses_vis].reset_index()
    fx_vis = fx[fx.mes.isin(meses_vis)]

    e1, e2 = st.columns(2)
    with e1:
        st.subheader("Receita, margem e resultado")
        st.altair_chart(grafico_linhas(base, {
            "receita_liquida": ("Receita líquida", COR["receita"]),
            "margem_bruta": ("Margem bruta", COR["margem"]),
            "resultado": ("Resultado", COR["resultado"]),
        }), use_container_width=True)
        mb = base.margem_bruta.sum() / base.receita_liquida.sum() if base.receita_liquida.sum() else 0
        st.caption(md(f"No período: receita {brl(base.receita_liquida.sum())}, margem bruta {pct(mb)}, resultado **{brl(base.resultado.sum())}**. "
                      f"Fora do site (maquininha e Pix direto) foi {pct(base.receita_fisica.sum() / base.receita_liquida.sum() if base.receita_liquida.sum() else 0)} da receita."))
    with e2:
        st.subheader("Caixa: o que entrou e o que saiu")
        cx = fx_vis[["mes", "recebido", "saidas", "saldo_operacional"]].copy()
        cx["saidas"] = -cx.saidas
        st.altair_chart(grafico_linhas(cx, {
            "recebido": ("Entrou", COR["caixa"]),
            "saidas": ("Saiu", COR["resultado"]),
            "saldo_operacional": ("Saldo do mês", COR["neutro"]),
        }), use_container_width=True)
        st.caption(md(f"No período entrou {brl(fx_vis.recebido.sum())} e saiu {brl(fx_vis.saidas.sum())}: "
                      f"estoque {brl(fx_vis.saida_estoque.sum())}, despesas {brl(fx_vis.saida_despesa.sum())}. "
                      f"Aportes no período: {brl(fx_vis.aportes.sum())}."))

    LINHAS_PNL = [
        ("Pedidos no site", "pedidos", False),
        ("Receita do site (Shopify)", "receita_site", True),
        ("(-) Desconto Pix", "desconto_pix", True),
        ("(-) Estornos", "estornos", True),
        ("Maquininha (líquido de MDR)", "receita_maquininha", True),
        ("Pix direto e link", "receita_pix_direto", True),
        ("Receita líquida", "receita_liquida", True),
        ("(-) Taxas Pagar.me", "taxas", True),
        ("(-) Imposto", "imposto", True),
        ("(-) CMV do site", "cmv_site", True),
        ("(-) CMV fora do site (estimado)", "cmv_fisico_estimado", True),
        ("Margem bruta", "margem_bruta", True),
        ("(-) Meta e agência", "marketing", True),
        ("(-) Demais despesas", "despesas_outras", True),
        ("Resultado", "resultado", True),
    ]

    with st.expander("Tabela: PnL por competência"):
        b = base.set_index("mes")
        linhas = {}
        for rotulo, col, dinheiro in LINHAS_PNL:
            tot = b[col].sum()
            linhas[rotulo] = [brl(tot) if dinheiro else str(int(tot))] + [brl(v) if dinheiro else str(int(v)) for v in b[col]]
        wide = pd.DataFrame(linhas, index=["Total"] + [mes_curto(m) for m in b.index]).T
        wide.loc["Margem bruta %"] = [pct(mb)] + [pct(a / r) if r else "" for a, r in zip(b.margem_bruta, b.receita_liquida)]
        st.dataframe(wide, width="stretch", height=(len(LINHAS_PNL) + 2) * 35 + 40)
        if pnl["cobertura_cmv"] < 0.999:
            faltam = ", ".join(f"{k} ({v})" for k, v in pnl["pecas_sem_custo"].items())
            st.caption(md(f"CMV do site cobre {pct(pnl['cobertura_cmv'])} das peças; sem ficha: {faltam}. "
                          f"Fora do site o CMV é estimado com a proporção do site, {pct(pnl['razao_cmv'])}."))

    with st.expander("Tabela: caixa mês a mês"):
        st.dataframe(pd.DataFrame({
            "Mês": fx_vis.mes.map(mes_curto),
            "Site": fx_vis.recebido_site.map(brl),
            "Fora do site": fx_vis.recebido_fisico.map(brl),
            "Estoque": fx_vis.saida_estoque.map(lambda v: brl(-v) if v else ""),
            "Despesas": fx_vis.saida_despesa.map(lambda v: brl(-v) if v else ""),
            "Imposto": fx_vis.saida_imposto.map(lambda v: brl(-v) if v else ""),
            "Estrutura": fx_vis.saida_capex.map(lambda v: brl(-v) if v else ""),
            "Saldo do mês": fx_vis.saldo_operacional.map(brl),
            "Aportes": fx_vis.aportes.map(lambda v: brl(v) if v else ""),
            "Caixa": fx_vis.caixa.map(brl),
        }), width="stretch", hide_index=True, height=min(38 * (len(fx_vis) + 1), 520))

    with st.expander("Tabela: despesas por categoria"):
        dc = pnl["despesas_por_categoria"]
        dc = dc.loc[[m for m in dc.index if m in meses_vis and dc.loc[m].sum() > 0]]
        dc.index = [mes_curto(m) for m in dc.index]
        dc = dc.loc[:, (dc != 0).any()]
        st.dataframe(dc.map(lambda v: brl(v) if v else ""), width="stretch")

    # ═══════════════════════════════════════════════════════════════════════════
    st.header("2. Onde estamos hoje")
    st.caption(md(f"Foto de {ex.data.max():%d/%m/%Y}. O que tem para vender, o que tem para pagar."))

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Estoque a preço de venda", mil(164440), help="290 peças ativas na Shopify vezes o preço de etiqueta, sem os 15 Loulou sob encomenda. A coleção nova está com 16 e 17 por variante: conferir se é contagem.")
    h2.metric("Estoque a custo", mil(estoque_custo), help="Produção paga menos custo das peças já vendidas. Bate com a Shopify a custo de ficha (R$ 54,5 mil).")
    h3.metric("Contas contratadas até dez", mil(contas_a_pagar), help="Fornecedores de produção, fotos e anúncios já contratados e não pagos. Aluguel, contador e sistema não estão aqui, estão nos fixos da projeção.")
    h4.metric("A receber da Pagar.me", mil(fluxo[fluxo.mes >= hoje].a_receber.sum()))
    st.caption(md("Leitura direta: o estoque a preço de venda cobre as contas contratadas 5,7 vezes. O que ele não cobre sozinho é o tempo: aluguel e fixos correm enquanto ele não gira."))

    # ═══════════════════════════════════════════════════════════════════════════
    st.header("3. Para onde vai")
    st.caption("Um modelo só. Produz todo mês para vender dois meses depois, sem parar. Receita cresce pela taxa dos últimos 6 meses, "
               "amortecida para não virar exponencial. Com o estoque alto se produz menos, e a produção volta ao ritmo cheio conforme ele desce. "
               "Parte do estoque envelhece a cada mês.")

    with st.expander("Premissas", expanded=False):
        st.caption("Arraste e a página inteira recalcula. Os valores iniciais vêm dos últimos 6 meses.")
        q1, q2, q3, q4 = st.columns(4)
        pr_receita = float(q1.slider("Receita líquida em out/26 (R$)", 5000, 40000, 20000, 500,
                                     help="Ponto de partida escolhido pelo Pedro: o patamar de agosto."))
        pr_g = q2.slider("Crescimento ao mês (%)", -10.0, 30.0, 10.5, 0.5, help="Ajuste log-linear de abr a set/26.") / 100
        pr_fator = q3.slider("Fator de redução do crescimento", 0.0, 1.0, 0.8, 0.05,
                             help="g no mês t = g × fator^t. Com 0,8 a receita converge para 1,7x a inicial; com 0,9, para 2,6x.")
        pr_cobertura = float(q4.slider("Estoque alvo (meses de venda)", 1.0, 8.0, 3.0, 0.5,
                                       help="Quanto se quer ter em estoque, a custo, em meses de CMV. Hoje são uns 8. Acima do alvo produz-se menos, na proporção do excesso."))
        q5, q6, q7, q8 = st.columns(4)
        pr_cmv = q5.slider("CMV médio (% da receita)", 20.0, 70.0, 41.4, 0.5) / 100
        pr_ads = q6.slider("Meta (% da receita)", 0.0, 40.0, 12.5, 0.5,
                           help="Só a mídia, que escala com a venda. Relatórios da agência: R$ 10,5 mil nos últimos 6 meses para R$ 83,6 mil de receita, 12,5%. A agência é fixa e está nos fixos.") / 100
        pr_fixos = float(q7.slider("Fixos por mês (R$)", 0, 15000, 7600, 100,
                                   help="Aluguel 2,8 mil, agência PROADZ 1,8 mil, pessoas 1,3 mil, foto 0,7 mil, frete, contador, sistemas."))
        pr_imposto = q8.slider("Taxas e imposto (% da receita)", 0.0, 20.0, 7.4, 0.1, help="Pagar.me mais Simples a 2,64%.") / 100
        q9, _, _, _ = st.columns(4)
        pr_envelh = q9.slider("Estoque que envelhece por mês (%)", 0.0, 5.0, 2.0, 0.5,
                              help="Peça que deixa de vender a preço cheio. Sai do estoque sem virar receita.") / 100

    prem = plano.Premissas(receita_base=pr_receita, crescimento=pr_g, fator_reducao=pr_fator, cobertura_alvo=pr_cobertura,
                           envelhecimento=pr_envelh, cmv=pr_cmv, ads=pr_ads, fixos=pr_fixos, taxas=pr_imposto,
                           estoque_custo=estoque_custo, acumulado_historico=acumulado)
    res = plano.simular(prem)
    pt = res["tabela"]
    fim_horizonte = mes_curto(pt.mes.iloc[-1])
    contrib = 1 - prem.taxas - prem.cmv - prem.ads
    breakeven_lucro = prem.fixos / contrib if contrib > 0 else float("inf")


    def quando(m):
        return mes_curto(m) if m else f"não até {fim_horizonte}"


    # O filme todo: realizado e projetado no mesmo eixo, quatro leituras
    st.subheader("O filme todo")
    hist = t[(t.mes >= "2025-06") & (t.mes <= hoje)][["mes", "receita_liquida", "resultado"]].rename(columns={"receita_liquida": "receita"})
    hist = hist.merge(fx[["mes", "saldo_operacional", "acumulado_operacional"]], on="mes", how="left").rename(columns={"saldo_operacional": "fluxo"}).assign(fase="Realizado")
    fut = pt[["mes", "receita", "resultado", "fluxo", "acumulado_total"]].rename(columns={"acumulado_total": "acumulado_operacional"}).assign(fase="Projetado")
    # O último ponto realizado entra também na série projetada, só para as linhas
    # não ficarem partidas entre as duas cores. Sem barras, para não duplicar.
    emenda = hist.tail(1).assign(fase="Projetado", receita=float("nan"), resultado=float("nan"), fluxo=float("nan"))
    filme = pd.concat([hist, emenda, fut], ignore_index=True)
    filme["rotulo"] = filme.mes.map(mes_curto)
    for c in ("receita", "resultado", "fluxo", "acumulado_operacional"):
        filme[c] = filme[c] / 1000
    filme["lucro_acumulado"] = filme.resultado.fillna(0).cumsum()
    filme.loc[filme.fase == "Projetado", "lucro_acumulado"] = filme.loc[filme.fase == "Projetado", "lucro_acumulado"]
    ordem = list(dict.fromkeys(filme.rotulo))
    corte = mes_curto(hoje)
    eixo_x = alt.X("rotulo:N", sort=ordem, scale=alt.Scale(domain=ordem), title="")
    regua_hoje = alt.Chart(pd.DataFrame({"rotulo": [corte]})).mark_rule(color=COR["neutro"], strokeDash=[4, 4]).encode(x=eixo_x)
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=COR["neutro"]).encode(y="y")
    legenda_fase = alt.Legend(orient="top")


    def barras_sinal_filme(coluna, titulo):
        """Barras verde/vermelho; o projetado sai mais claro para não se confundir."""
        g = filme.dropna(subset=[coluna]).copy()
        g["classe"] = [("+" if v >= 0 else "-") + ("R" if f == "Realizado" else "P") for v, f in zip(g[coluna], g.fase)]
        escala = alt.Scale(domain=["+R", "-R", "+P", "-P"], range=[COR["caixa"], COR["resultado"], "#9ccc9c", "#e8a09a"])
        return alt.Chart(g).mark_bar().encode(
            x=eixo_x, y=alt.Y(f"{coluna}:Q", title=f"{titulo} (R$ mil)"),
            color=alt.Color("classe:N", scale=escala, legend=None),
            tooltip=["rotulo", "fase", alt.Tooltip(f"{coluna}:Q", format=".1f", title="R$ mil")],
        )


    f1, f2 = st.columns(2)
    with f1:
        st.markdown("**Receita líquida**")
        barras = alt.Chart(filme.dropna(subset=["receita"])).mark_bar().encode(
            x=eixo_x, y=alt.Y("receita:Q", title="R$ mil"),
            color=alt.Color("fase:N", title="", scale=alt.Scale(domain=["Realizado", "Projetado"], range=[COR["receita"], COR["margem"]]), legend=legenda_fase),
            tooltip=["rotulo", "fase", alt.Tooltip("receita:Q", format=".1f", title="R$ mil")])
        be = alt.Chart(pd.DataFrame({"y": [breakeven_lucro / 1000]})).mark_rule(color=COR["resultado"], strokeDash=[6, 4]).encode(y="y")
        st.altair_chart((barras + be + regua_hoje).properties(height=230), use_container_width=True)
        st.caption(md(f"Tracejado vermelho: {brl(breakeven_lucro)}/mês, onde o lucro começa."))
    with f2:
        st.markdown("**Lucro do mês (competência)**")
        st.altair_chart((barras_sinal_filme("resultado", "Lucro") + zero + regua_hoje).properties(height=230), use_container_width=True)
        st.caption(md("Receita menos taxas, CMV cheio, Meta, agência e fixos. Tom mais claro é projeção."))

    f3, f4 = st.columns(2)
    with f3:
        st.markdown("**Fluxo de caixa do mês**")
        st.altair_chart((barras_sinal_filme("fluxo", "Fluxo") + zero + regua_hoje).properties(height=230), use_container_width=True)
        st.caption(md("O que entrou menos o que saiu no mês, sem aportes. Vermelho depois de hoje é mês que pede aporte."))
    with f4:
        st.markdown("**Acumulados: caixa e lucro**")
        g = filme[["rotulo", "fase", "acumulado_operacional", "lucro_acumulado"]].melt(["rotulo", "fase"], var_name="serie", value_name="valor")
        g["serie"] = g.serie.map({"acumulado_operacional": "Caixa acumulado", "lucro_acumulado": "Lucro acumulado"})
        g["traco"] = g.fase
        linhas = alt.Chart(g).mark_line(point=True).encode(
            x=eixo_x, y=alt.Y("valor:Q", title="R$ mil"),
            color=alt.Color("serie:N", title="", scale=alt.Scale(domain=["Caixa acumulado", "Lucro acumulado"], range=[COR["resultado"], COR["receita"]]), legend=legenda_fase),
            strokeDash=alt.StrokeDash("traco:N", title="", scale=alt.Scale(domain=["Realizado", "Projetado"], range=[[1, 0], [6, 4]]), legend=legenda_fase),
            detail="fase:N",
            tooltip=["rotulo", "serie", "fase", alt.Tooltip("valor:Q", format=".1f", title="R$ mil")],
        )
        st.altair_chart((linhas + zero + regua_hoje).properties(height=230), use_container_width=True)
        st.caption(md("Caixa acumulado é tudo que a operação gerou menos gastou desde o início, sem aportes: cruzou zero, o capital voltou. "
                      "Lucro acumulado é a soma do resultado de competência desde jun/25. A distância entre os dois é o que está em estoque mais o que foi gasto antes do lançamento."))

    # As três respostas, uma coluna cada
    r1, r2, r3 = st.columns(3)
    with r1:
        st.subheader("Quando roda sozinha")
        st.metric("Aporte ainda necessário", mil(res["total_aportes"]),
                  help="Meses em que o caixa fecharia negativo. Quase tudo são as contas já contratadas de outubro caindo num caixa zerado.")
        st.metric("Último mês com aporte", mes_curto(res["ultimo_aporte"]) if res["ultimo_aporte"] else "nenhum")
        st.metric("Caixa positivo e fica", quando(res["breakeven"]),
                  help="Primeiro mês com fluxo positivo sem nenhum negativo depois.")
    with r2:
        st.subheader("Quando o dinheiro volta")
        st.metric("O que for posto agora", quando(res["payback_novo"]))
        st.metric(f"Os {mil(-acumulado)} desde o começo", quando(res["payback_total"]))
        st.metric("Produção volta ao ritmo cheio em", quando(res["fim_estoque"]), help="Mês em que o estoque chega ao alvo e se produz 95% ou mais da demanda.")
    with r3:
        st.subheader("Quanto dá de lucro")
        pa = res["por_ano"]
        st.metric("Primeiro mês no lucro", quando(res["primeiro_lucro"]))
        st.metric("Resultado 2027", mil(pa.loc["2027", "resultado"]) if "2027" in pa.index else "")
        st.metric("Resultado 2028", mil(pa.loc["2028", "resultado"]) if "2028" in pa.index else "")

    st.info(md(f"Cada real de receita deixa **{pct(contrib)}** depois de CMV, taxas e Meta. Com {brl(prem.fixos)} de fixos (agência dentro), "
               f"o lucro começa em **{brl(breakeven_lucro)}/mês** de receita. A projeção converge para **{brl(res['patamar'])}/mês**."))

    # Gráfico da projeção que o filme não cobre
    if True:
        st.subheader("Produção, venda e estoque")
        g = pt[["mes", "producao", "cmv_competencia", "estoque_restante"]].copy()
        g["mes"] = g.mes.map(mes_curto)
        for c in ("producao", "cmv_competencia", "estoque_restante"):
            g[c] = g[c] / 1000
        eixo = alt.X("mes:N", sort=None, title="")
        area = alt.Chart(g).mark_area(color=COR["estoque"], opacity=0.25).encode(
            x=eixo, y=alt.Y("estoque_restante:Q", title="R$ mil, a custo"),
            tooltip=["mes", alt.Tooltip("estoque_restante:Q", format=".1f", title="Estoque (R$ mil)")])
        barras = alt.Chart(g).mark_bar(color=COR["producao"]).encode(
            x=eixo, y="producao:Q", tooltip=["mes", alt.Tooltip("producao:Q", format=".1f", title="Produção paga (R$ mil)")])
        linha = alt.Chart(g).mark_line(color=COR["receita"], point=True).encode(
            x=eixo, y="cmv_competencia:Q", tooltip=["mes", alt.Tooltip("cmv_competencia:Q", format=".1f", title="Custo do vendido (R$ mil)")])
        st.altair_chart((area + barras + linha).properties(height=260), use_container_width=True)
        st.caption(md("Área: estoque a custo. Barras: produção paga no mês. Linha: custo do que foi vendido. "
                      "A produção começa abaixo da venda e sobe até encostar nela, e o estoque desce até o alvo e para de cair."))

    with st.expander("Tabela: projeção mês a mês"):
        st.dataframe(pd.DataFrame({
            "Mês": pt.mes.map(mes_curto),
            "Receita": pt.receita.map(brl),
            "Recebido": pt.recebido.map(brl),
            "Ads": pt.ads.map(lambda v: brl(-v)),
            "Fixos": pt.fixos.map(lambda v: brl(-v)),
            "Produção p/ mês seguinte": pt.producao.map(lambda v: brl(-v)),
            "Contas contratadas": pt.compromissos.map(lambda v: brl(-v) if v else ""),
            "Fluxo do mês": pt.fluxo.map(brl),
            "Aporte": pt.aporte.map(lambda v: brl(v) if v else ""),
            "Caixa": pt.caixa.map(brl),
            "Estoque (custo)": pt.estoque_restante.map(brl),
            "Produção vs demanda": pt.fracao_producao.map(lambda v: pct(v, 0)),
            "Resultado": pt.resultado.map(brl),
        }), width="stretch", hide_index=True, height=min(38 * (len(pt) + 1), 560))

    with st.expander("Cenários: o que muda se mexer numa alavanca"):
        cenarios = [
            ("Base", {}),
            ("Meta a 8%", {"ads": 0.08}),
            ("Meta a 16%", {"ads": 0.16}),
            ("Sem agência (fixos R$ 5,8 mil)", {"fixos": 5800.0}),
            ("Crescimento amortece menos (0,9)", {"fator_reducao": 0.9}),
            ("Crescimento amortece mais (0,7)", {"fator_reducao": 0.7}),
            ("Sem crescimento", {"crescimento": 0.0}),
            ("Estoque alvo de 2 meses", {"cobertura_alvo": 2.0}),
            ("Nada envelhece", {"envelhecimento": 0.0}),
        ]
        linhas = []
        for nome, ajuste in cenarios:
            p2 = plano.Premissas(**{**prem.__dict__, **ajuste})
            r2_ = plano.simular(p2)
            pa2 = r2_["por_ano"]
            linhas.append({
                "Cenário": nome,
                "Aporte ainda": brl(r2_["total_aportes"]),
                "Caixa positivo e fica": quando(r2_["breakeven"]),
                "Ritmo cheio em": quando(r2_["fim_estoque"]),
                "Capital volta": quando(r2_["payback_total"]),
                "Resultado 2027": brl(pa2.loc["2027", "resultado"]) if "2027" in pa2.index else "",
                "Resultado 2028": brl(pa2.loc["2028", "resultado"]) if "2028" in pa2.index else "",
                "Patamar/mês": brl(r2_["patamar"]),
            })
        st.dataframe(pd.DataFrame(linhas), width="stretch", hide_index=True)

    # ═══════════════════════════════════════════════════════════════════════════
    st.header("Bastidores")
    with st.expander("Custo por peça e o casamento com a loja (a confirmar)"):
        fichas = d["fichas"].copy()
        usadas = {}
        for qtd, titulo in [it for r in d["pedidos"].itertuples() for it in fin.separar_itens(r.itens)]:
            f = fin.ficha_da_peca(titulo)
            if f:
                usadas.setdefault(f, set()).add(titulo.split(" - ")[-1] if " - " in titulo else titulo)
        st.dataframe(pd.DataFrame({
            "Ficha (planilha)": fichas.peca,
            "Vendido como (Shopify)": fichas.peca.map(lambda p: ", ".join(sorted(usadas.get(p, []))) or ""),
            "Custo": fichas.custo.map(lambda v: brl(v, 2)),
            "Preço na ficha": fichas.preco.map(lambda v: brl(v, 0)),
            "Margem na ficha": fichas.margem_pct.map(lambda v: pct(v)),
            "Situação": fichas.incompleta.map(lambda x: "incompleta" if x else "ok"),
        }), width="stretch", hide_index=True)

    with st.expander("Extrato: o que foi classificado por dedução e espera confirmação"):
        pend = d["a_classificar"].reset_index()
        pend["sum"] = pend["sum"].map(lambda v: brl(v, 2))
        pend.columns = ["Sentido", "Natureza", "Categoria", "Contraparte", "Movimentações", "Total"]
        st.dataframe(pend, width="stretch", hide_index=True, height=min(38 * (len(pend) + 1), 600))

    with st.expander("Extrato: saídas por categoria, desde o começo"):
        cat = led.groupby(["natureza", "categoria"])["valor"].agg(["count", "sum"]).reset_index().sort_values("sum", ascending=False)
        cat["sum"] = cat["sum"].map(brl)
        cat.columns = ["Natureza", "Categoria", "Movimentações", "Total"]
        st.dataframe(cat, width="stretch", hide_index=True, height=min(38 * (len(cat) + 1), 700))

    with st.expander("Conciliação Shopify × Pagar.me"):
        ped = d["pedidos"]
        st.write(md(f"{len(ped)} pedidos pagos na Shopify, {brl(ped.total.sum())}. Cobrado de fato {brl(ped.cobrado.sum())}, "
                    f"sendo {brl(ped.desconto_pix.sum())} de desconto Pix e {brl(ped[ped.estornada].cobrado.sum())} estornados depois."))
        orfaos = pnl["pedidos_sem_cobranca"]
        if not orfaos.empty:
            st.dataframe(orfaos[["numero", "quando", "total", "cupom", "itens"]], hide_index=True, width="stretch")


if __name__ == "__main__":
    st.set_page_config(page_title="Resultado · ANNIS", page_icon="📒", layout="wide")
    render()
