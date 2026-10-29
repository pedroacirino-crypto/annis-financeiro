"""Carrega no Supabase o que o Pedro manda na conversa.

    python carga.py extrato  caminho/Extrato.csv       exportação da conta Stone (csv ou xlsx)
    python carga.py contas   caminho/contas.xlsx       planilha de contas a pagar (uma aba por mês)
    python carga.py meta     2026-09 2500              investimento em Meta de um mês
    python carga.py legado                             fichas e lançamentos pré-conta, da planilha de 2025
    python carga.py regras                             regras de classificação com nome de pessoa (legado/regras.json)
    python carga.py fatura   legado/faturas_cartao.csv linhas das faturas dos cartões das sócias
    python carga.py rateio   legado/rateios.csv        pagamento que é duas coisas (aluguel com seguro dentro)
    python carga.py stone    caminho/vendas.csv        relatório de vendas da Stone, com o cartão de cada venda
    python carga.py plano    legado/plano.json         plano de ação contínuo, que aparece em Trabalho > Plano
    python carga.py orcamento 2026-10                  congela a projeção do mês, para comparar com o realizado
    python carga.py orcamento 2026-10 3000 6.82        congela com decisão de Meta e o ROAS assumido
    python carga.py pedido-link legado/link_michelle.json  pedido na Shopify de venda pelo link da Stone
    python carga.py resumo                             o que tem lá e até quando

Roda na máquina do Pedro, com o .env apontando para o banco. Nada disso vai
para o Git: o repositório é público e aqui tem fornecedor, valor e custo.
"""

import sys

import pandas as pd

import dados_fin
import extrato
import financeiro


def carregar_extrato(caminho: str) -> None:
    cru = extrato.ler_arquivo(caminho)
    n = dados_fin.salvar_extrato(cru)
    print(f"extrato: {n} movimentações gravadas, de {cru.data.min():%d/%m/%Y} a {cru.data.max():%d/%m/%Y}")
    df = extrato.classificar(cru)
    pend = df[(df.confianca != "ok") & (df.natureza != "interna")]
    print(f"  a classificar ou confirmar: {len(pend)} linhas, R$ {pend.valor_abs.sum():,.2f}")
    novos = pend[pend.natureza == "a_classificar"].groupby("contraparte").valor.sum().sort_values()
    if not novos.empty:
        print("  sem regra nenhuma:")
        for nome, v in novos.items():
            print(f"    {nome[:50]:50s} R$ {v:,.2f}")
    _avisar_fatura_sem_transcricao(df)


def _avisar_fatura_sem_transcricao(df: pd.DataFrame) -> None:
    """Pagamento de fatura de cartão (Portoseg) num mês cuja fatura não foi
    transcrita em legado/faturas_cartao.csv vai inteiro para anúncio, e não
    é: a fatura da Ana mistura Meta com tecido, aviamento, embalagem e coisa
    pessoal. Aconteceu em setembro e de novo em outubro/2026."""
    import financeiro
    cobertos = set().union(*financeiro.meses_com_fatura().values()) if financeiro.meses_com_fatura() else set()
    pag = df[df.categoria == financeiro.CATEGORIA_FATURA].copy()
    if pag.empty:
        return
    pag["mes"] = pd.to_datetime(pag.data).dt.strftime("%Y-%m")
    falta = pag[~pag.mes.isin(cobertos)]
    for r in falta.itertuples(index=False):
        print(f"  ATENÇÃO: pagamento de fatura de R$ {abs(r.valor):,.2f} em {pd.to_datetime(r.data):%d/%m} sem a fatura"
              f" transcrita ({r.mes}). Transcrever em legado/faturas_cartao.csv e rodar carga.py fatura.")


def carregar_contas(caminho: str) -> None:
    """Aceita o xlsx exportado (uma aba por mês) ou um csv com mes,dia,...

    Depois de gravar, confere: toda linha marcada PAGO tem que ter saído da
    conta pelo mesmo valor. Antes de 30/09/2026 isso não era conferido, e o
    Pedro perguntou, com razão, como eu usava o valor da planilha sem olhar
    se batia com o banco.
    """
    if caminho.lower().endswith(".csv"):
        df = pd.read_csv(caminho)
        todas = pd.DataFrame({
            "data": pd.to_datetime(df.mes + "-" + df.dia.astype(str).str.zfill(2)),
            "descricao": df.descricao.astype(str).str.strip(),
            "valor": df.valor.astype(float),
            "situacao": df.situacao.fillna("").astype(str).str.strip().str.upper(),
            "observacao": df.observacao.fillna("").astype(str),
        })
        _gravar_contas(todas)
        return
    xls = pd.ExcelFile(caminho)
    partes = []
    meses_pt = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
                "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}

    def valor(v):
        # Célula digitada como número (960, 99.76) vem como float; como texto
        # vem "R$ 1.040,00". Tratar as duas como texto fazia 99,76 virar 9.976.
        if isinstance(v, (int, float)):
            return float(v)
        return float(str(v).replace("R$", "").replace(".", "").replace(",", ".").strip())

    for aba in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=aba)
        df.columns = [str(c).strip().upper() for c in df.columns]
        if "VALOR" not in df or "DATA" not in df:
            continue
        df = df.dropna(subset=["VALOR"])
        df = df[df["VALOR"].astype(str).str.strip() != ""]
        datas = pd.to_datetime(df["DATA"])
        # A aba é o mês. Data de outro mês dentro dela é erro de digitação
        # (em 12/10/2026 a agência de novembro estava com 20/10 e duplicava a
        # de outubro), e linha sem data ("PROGRAMAR") cai no fim do mês da aba.
        mes_aba = meses_pt.get(aba.strip().lower())
        if mes_aba:
            ref = datas.dropna()
            ano = int(ref.dt.year.mode().iloc[0]) if not ref.empty else pd.Timestamp.now().year
            fim = pd.Timestamp(ano, mes_aba, 1) + pd.offsets.MonthEnd(0)
            datas = datas.map(lambda d: fim if pd.isna(d) else
                              d if d.month == mes_aba else pd.Timestamp(ano, mes_aba, min(d.day, fim.day)))
        else:
            df, datas = df[datas.notna()], datas[datas.notna()]
        partes.append(pd.DataFrame({
            "data": datas,
            "descricao": df["DESCRIÇÃO"].astype(str).str.strip(),
            "valor": df["VALOR"].map(valor),
            "situacao": df["SITUAÇÃO"].fillna("").astype(str).str.strip().str.upper() if "SITUAÇÃO" in df else "",
            "observacao": df.iloc[:, 4].fillna("").astype(str) if df.shape[1] > 4 else "",
        }))
    _gravar_contas(pd.concat(partes, ignore_index=True))


def _gravar_contas(todas: pd.DataFrame) -> None:
    import financeiro
    n = dados_fin.salvar_contas_a_pagar(todas, substituir=True)
    pend = todas[todas.situacao != "PAGO"]
    print(f"contas a pagar: {n} linhas gravadas (substituindo as anteriores). Pendentes: {len(pend)}, R$ {pend.valor.sum():,.2f}")
    for m, v in pend.groupby(pend.data.dt.strftime("%Y-%m")).valor.sum().items():
        print(f"  {m}: R$ {v:,.2f}")

    import memo
    memo.limpar_tudo()
    conf = financeiro.conferir_contas_pagas()
    if conf.empty:
        return
    faltando = conf[~conf.saiu_da_conta]
    print(f"\n  conferência: {len(conf) - len(faltando)} de {len(conf)} linhas PAGO casaram com o extrato")
    if not faltando.empty:
        print(f"  sem saída correspondente na conta ({len(faltando)} linhas, R$ {faltando.valor.sum():,.2f}):")
        for r in faltando.itertuples(index=False):
            print(f"    {r.data:%d/%m/%Y}  {str(r.descricao)[:36]:36s} R$ {r.valor:>10,.2f}")


def carregar_meta(mes: str, valor: str) -> None:
    dados_fin.salvar_meta_ads({mes: float(valor.replace(".", "").replace(",", "."))})
    print(f"meta ads: {mes} = R$ {float(valor.replace('.', '').replace(',', '.')):,.2f}")


def carregar_legado() -> None:
    leg = financeiro.ler_planilha_legada()
    fichas = leg["custo_pecas"][["peca", "custo", "preco", "margem_pct", "incompleta"]]
    print("fichas:", dados_fin.salvar_fichas(fichas))
    lanc = leg["lancamentos"]
    lanc = lanc[lanc.data < extrato.INICIO_DA_CONTA]
    print("lançamentos pré-conta:", dados_fin.salvar_lancamentos_legado(lanc))
    print("meta ads (histórico da agência):", dados_fin.salvar_meta_ads(financeiro.META_ADS))


def carregar_fatura(caminho: str) -> None:
    """A fatura diz no que o reembolso à sócia foi gasto. O extrato da Stone
    só mostra a transferência, e sem isso tudo vira 'outras no cartão'."""
    df = pd.read_csv(caminho)
    n = dados_fin.salvar_fatura_cartao(df)
    print(f"faturas: {n} linhas gravadas, {df.fatura.nunique()} faturas")
    for fat, g in df.groupby("fatura"):
        annis = g[g.natureza.isin(["despesa", "estoque"])]
        print(f"  {fat}: Annis R$ {annis.valor.sum():,.2f}"
              f" | fora R$ {g[g.natureza == 'pessoal'].valor.sum():,.2f}")
    annis = df[df.natureza.isin(["despesa", "estoque"])]
    for cat, v in annis.groupby("categoria").valor.sum().sort_values(ascending=False).items():
        print(f"    {cat[:40]:40s} R$ {v:,.2f}")


def carregar_vendas_stone(caminho: str) -> None:
    """Relatório de vendas da conta Stone (Vendas > exportar). É a única
    fonte em que a venda de maquininha tem identidade: o cartão mascarado."""
    import financeiro
    bruto = pd.read_csv(caminho, sep=";", dtype=str, encoding="utf-8-sig").fillna("")
    bruto = bruto[bruto["DATA DA VENDA"].str.strip() != ""]

    def numero(s):
        return s.str.strip().str.replace(".", "", regex=False).str.replace(",", ".").replace("", "0").astype(float)

    df = pd.DataFrame({
        "stone_id": bruto["STONE ID"].str.strip(),
        "data": pd.to_datetime(bruto["DATA DA VENDA"].str.strip(), format="%d/%m/%Y %H:%M"),
        "bandeira": bruto["BANDEIRA"].str.strip(),
        "produto": bruto["PRODUTO"].str.strip(),
        "parcelas": bruto["N DE PARCELAS"].str.strip().replace("", "0").astype(int),
        "bruto": numero(bruto["VALOR BRUTO"]),
        "liquido": numero(bruto["VALOR LIQUIDO"]),
        "cartao": bruto["N DO CARTAO"].str.strip(),
        "captura": bruto["MEIO DE CAPTURA"].str.strip(),
        "status": bruto["ULTIMO STATUS"].str.strip(),
    })
    n = dados_fin.salvar_vendas_stone(df)
    print(f"vendas stone: {n} transações, de {df.data.min():%d/%m/%Y} a {df.data.max():%d/%m/%Y}")
    base = financeiro.base_fisica()
    print()
    for r in base["resumo"].itertuples(index=False):
        print(f"  {r.canal:22s} {r.identidades:>4} identidades  {r.voltaram:>3} voltaram  {r.taxa:>5.1f}%"
              f"   recompra R$ {r.receita_recompra:>9,.2f} = {r.fatia_recompra:>4.1f}% da receita")


def carregar_plano(caminho: str) -> None:
    """Plano de ação, uma lista só. Recarregar preserva o que já foi marcado.
    Item novo entra com id "AAAA-MM:x" e aparece no topo da aba."""
    import json
    import nuvem
    with open(caminho, encoding="utf-8") as f:
        plano = json.load(f)
    if "mes" in plano:
        sys.exit("arquivo no formato antigo, de um mês só: o plano agora é legado/plano.json, contínuo")
    n = nuvem.salvar_plano(plano["frentes"])
    feitos = sum(1 for i in nuvem.ler_plano() if i["feito_em"])
    print(f"plano: {n} itens, {feitos} feitos, {n - feitos} em aberto")


def carregar_rateio(caminho: str) -> None:
    """Um pagamento que é duas coisas. Sem isso o seguro incêndio dentro do
    boleto do aluguel de setembro fazia parecer que o aluguel tinha subido
    23%, que foi a conclusão errada de 30/09/2026."""
    df = pd.read_csv(caminho)
    print("rateios:", dados_fin.salvar_rateios(df))
    for r in df.itertuples(index=False):
        print(f"  {r.data} {r.contraparte}: de R$ {r.valor_total:,.2f} saem "
              f"R$ {r.valor_parte:,.2f} para {r.categoria_parte}")


def congelar_orcamento(mes: str, meta: str = None, roas: str = None) -> None:
    """Congela a projeção do mês. Com `meta`, congela uma decisão: o
    investimento escolhido e a receita que o ROAS assumido devolve.

    A projeção anda sozinha conforme os meses passam; sem congelar, o mês
    corrente não tem contra o que ser comparado.
    """
    import financeiro
    if meta:
        linhas = financeiro.orcamento_com_meta(mes, float(meta), float(roas or 0))
        print(f"decisão: Meta de R$ {float(meta):,.2f} com ROAS de {float(roas or 0):.2f}x\n")
    else:
        linhas = financeiro.orcamento_do_mes(mes)
    if not linhas:
        print(f"o plano não alcança {mes}")
        return
    # O caixa do mês congela junto com o PnL; sem isso ele seria recalculado a
    # cada leitura e mudaria quando entrasse extrato novo do próprio mês.
    caixa = financeiro.caixa_orcado(mes, congelado=False)
    linhas = {**linhas, **{financeiro.CAIXA_CONGELADO + k: v for k, v in caixa.items()}}
    n = dados_fin.salvar_orcamento(mes, linhas)
    print(f"orçamento de {mes} congelado, {n} linhas.\n")
    _mostrar_premissas()
    _conferir_orcamento(linhas)


def _mostrar_premissas() -> None:
    """Toda premissa medida, com a regra. Se alguma estiver com cara de
    velha, é aqui que aparece antes de virar orçamento congelado."""
    import financeiro
    print(f"  {'premissa':16s} {'regra':14s} {'medido':>10s}   o que é")
    for k, (v, regra, desc) in financeiro.premissas_medidas().items():
        valor = f"{v * 100:.1f}%" if v < 1 else f"{v:,.0f}"
        print(f"  {k:16s} {regra:14s} {valor:>10s}   {desc}")
    print()


# Toda linha projetada é comparada com o último mês fechado, e o que se
# afastar muito é apontado. Sem isto eu subi três projeções seguidas com
# defeito e foi o Pedro quem achou, de olho, uma por vez, em 30/09/2026.
_LIMITE = 25  # % de variação a partir do qual a linha precisa de explicação


def _conferir_orcamento(linhas: dict) -> None:
    import financeiro
    t = financeiro.pnl_competencia()["tabela"]
    ref = t[t.mes == financeiro.mes_fechado()]
    if ref.empty:
        return
    s = ref.iloc[0]
    campos = {
        "Pedidos no site": "pedidos", "Receita bruta": "receita_bruta",
        "Receita do site (Shopify)": "receita_site", "Maquininha (líquido de MDR)": "receita_maquininha",
        "Maquininha e link no cartão": "receita_maquininha",
        "Pix direto e link": "receita_pix_direto", "(-) Desconto Pix": "desconto_pix",
        "(-) Estornos": "estornos", "Receita líquida": "receita_liquida",
        "(-) Taxas Pagar.me": "taxas_pagarme", "(-) Taxas Stone": "taxas_stone", "(-) Imposto": "imposto", "(-) CMV do site": "cmv_site",
        "(-) CMV fora do site (estimado)": "cmv_fisico_estimado", "(-) CMV": "cmv",
        "Margem bruta": "margem_bruta", "(-) Meta e agência": "marketing",
        "(-) Demais despesas": "despesas_outras", "Resultado": "resultado",
    }
    print(f"  {'linha':34s} {financeiro.mes_fechado():>10s} {'projetado':>11s} {'variação':>9s}")
    suspeitas = []
    for rot, campo in campos.items():
        if rot not in linhas and rot.startswith("_"):
            continue
        real, proj = float(s[campo]), linhas.get(rot, 0.0)
        var = (proj / real - 1) * 100 if real else float("nan")
        marca = ""
        if real and abs(var) > _LIMITE:
            marca = "  <<< explique"
            suspeitas.append(rot)
        print(f"  {rot:34s} {real:>10,.0f} {proj:>11,.0f} {var:>8.0f}%{marca}")
    if suspeitas:
        print(f"\n  {len(suspeitas)} linha(s) acima de {_LIMITE}% de variação: " + ", ".join(suspeitas))
        print("  Cada uma precisa de um motivo, senão é defeito de estimador e não projeção.")


def pedido_link(caminho: str) -> None:
    """Pedido na Shopify para venda feita pelo link da Stone (ver
    shopify_client.criar_pedido_link). O arquivo fica em legado/, fora do Git,
    porque leva CPF e endereço da cliente."""
    import json
    import shopify_client
    with open(caminho, encoding="utf-8") as f:
        venda = json.load(f)
    pedido = shopify_client.criar_pedido_link(venda)
    print(f"pedido {pedido['name']} criado, {pedido['displayFinancialStatus']}")


def carregar_regras() -> None:
    import json
    with open(extrato.ARQUIVO_REGRAS, encoding="utf-8") as f:
        regras = json.load(f)
    print("regras:", dados_fin.salvar_regras(regras))


def main(argv):
    if not dados_fin.disponivel():
        print("sem conexão com o Supabase: confira SUPABASE_URL_BANCO no .env")
        return 1
    cmd = argv[1] if len(argv) > 1 else "resumo"
    if cmd == "extrato":
        carregar_extrato(argv[2])
    elif cmd == "contas":
        carregar_contas(argv[2])
    elif cmd == "meta":
        carregar_meta(argv[2], argv[3])
    elif cmd == "legado":
        carregar_legado()
    elif cmd == "regras":
        carregar_regras()
    elif cmd == "fatura":
        carregar_fatura(argv[2])
    elif cmd == "rateio":
        carregar_rateio(argv[2])
    elif cmd == "stone":
        carregar_vendas_stone(argv[2])
    elif cmd == "plano":
        carregar_plano(argv[2])
    elif cmd == "pedido-link":
        pedido_link(argv[2])
    elif cmd == "orcamento":
        congelar_orcamento(*argv[2:5])
    else:
        r = dados_fin.resumo()
        print(f"extrato: {r['extrato_linhas']} linhas, até {r['extrato_ate']}")
        print(f"contas a pagar: {r['contas_linhas']} linhas, carregadas em {r['contas_em']}")
        print(f"meta ads: até {r['meta_ate']}")
        return 0
    # O portal fica congelado até "Atualizar dados"; esta marca faz ele
    # recalcular sozinho no próximo clique, sem ninguém precisar apertar.
    import nuvem
    nuvem.marcar_dado_novo(f"carga {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
