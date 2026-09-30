"""Extrato da conta Stone da empresa, classificado linha a linha.

É a única fonte que enxerga o mundo físico: venda na maquininha, Pix direto
de cliente, e todo custo pago de mar/2025 até hoje. A planilha legada para
em jul/2025 e a Shopify só vê o site.

Como o extrato se encaixa nas outras fontes:

  - "Transferência entre contas Stone" vinda da própria CIRINO E PAGNAN é a
    liquidação da Pagar.me. Essa receita já entra pela Shopify; aqui ela só
    conta para o caixa, nunca para a receita do PnL, senão dobra.
  - "Stone Principal" (conta 30772-8, sempre de madrugada) é a liquidação da
    maquininha Stone: venda física no cartão, já líquida de MDR.
  - Pix recebido de pessoa que não é sócia é venda física paga direto.
  - Pix de sócia e de família é aporte. Pix da empresa para uma sócia nunca
    foi retirada: é reembolso de despesa paga no cartão pessoal (anúncios,
    aluguel, outras). Confirmado pelo Pedro em 20/09/2026. Essas regras têm
    nome de pessoa e por isso vivem fora do código (fin_regras).

A classificação é por regra sobre o nome da contraparte. O que não casa com
nenhuma regra fica como "a classificar", visível, em vez de sumir numa
categoria genérica.
"""

import os
import re

import pandas as pd

from memo import memo

ARQUIVO = os.path.join(os.path.dirname(__file__), "legado", "extrato.xlsx")
INICIO_DA_CONTA = pd.Timestamp("2025-03-25")

# (padrão no nome da contraparte, natureza, categoria, confiança)
# Naturezas: estoque, despesa, capex, imposto, aporte, venda_fisica,
# liquidacao_online, devolucao, interna, a_classificar.
#
# Aqui só ficam fornecedores pessoa jurídica. Regras com nome de pessoa
# física (costureira, modelo, fotógrafo, sócias) moram na tabela fin_regras
# do Supabase, com cópia em legado/regras.json para o trabalho local, porque
# este repositório é público. Elas são aplicadas antes destas.
REGRAS_DEBITO = [
    # Produção: facção, tecido, aviamento, embalagem, frete de insumo
    (r"VIOLET COD", "estoque", "Facção", "ok"),
    # Nika e Aliança são securitizadoras: compram o boleto do fornecedor de
    # tecido (Eco Simple) e recebem no lugar dele.
    (r"CONCEPT TXTL|EXCIM|PROMEX|MIKKONOS|M ALMEIDA COMERCIO DE TECIDOS|CENTRAL MALHAS|APM MODA|NIKA SECURITIZADORA|ALIANCA FINAN|ECO SIMPLE", "estoque", "Tecido", "ok"),
    (r"PAULISTANA ZIPER|PARANA AVIAMENTOS|ARMARINHOS|CARDOSO DISTRIBUIDORA|DUMA AVIAMENTOS|ALTERO|MIXMETAIS|GEMMA BIJOUX|BONOR|GM2 IMPORTACAO|XIAZHI|PALACIO DAS ESPUMAS|SHPP BRASIL", "estoque", "Aviamento", "ok"),
    (r"GRAFICA PORTO BELO|AJ FERREIRA|A R FERREIRA|FM IMPRESSOS|PRINTI", "estoque", "Embalagem", "ok"),
    (r"TRANS APUCARANA|BRASPRESS", "estoque", "Frete de insumo", "ok"),
    # Marketing
    (r"PROADZ|V60 ANUNCIOS|FACEBK", "despesa", "Ads e agência", "ok"),  # mesma agência, nome e CNPJ novos desde 09/2026
    (r"PORTOSEG", "despesa", "Fatura do cartão de anúncios", "ok"),  # a Meta em si entra pelo relatório da agência
    (r"GASP ESTUDIO|YELLOW ESTUDIO|SARDI E VERCEZI|FOTO CELULA|CALDI GOMES|S FERREIRA DA SILVA PUBLICIDADES", "despesa", "Foto, vídeo e conteúdo", "ok"),
    # Eventos
    (r"THAIS ALVES GASTRONOMIA|BUFFET|VICA'S|HIGIENOPOLIS|DO MEU JEITO|CRISTALLO|FLORICULTURA|PROTEA|MANDARINA|RUBINI", "despesa", "Eventos", "ok"),
    # Estrutura
    (r"MODELLO EXPOSITORES|EXCLUSIVE EXPOSITORES|GOUVEIA E NOBRE TINTAS|BENVENHO", "capex", "Estrutura da loja", "ok"),
    # Logística de venda
    (r"CORREIOS|UBER", "despesa", "Frete e entrega", "ok"),
    # Fixas e sistemas
    (r"KOYASHIKI", "despesa", "Contador", "ok"),
    (r"OLIST TINY", "despesa", "Sistemas", "ok"),
    (r"CONDOMINIO|PJBANK", "despesa", "Aluguel e condomínio", "ok"),
    (r"ZOOP|SAFE2PAY|DLOCAL|PIX MARKETPLACE|PAGAR ME", "despesa", "Taxas e apps", "a confirmar"),
    (r"KALUNGA|LJS ESTACIONAMENTOS|PAPELARIA|PAPEL DE PAPEL", "despesa", "Miudezas", "ok"),
    (r"TABELIONATO|CARTORIO", "despesa", "Cartório e protesto", "ok"),
    # Imposto
    (r"MINISTERIO DA FAZENDA|DAS - SIMPLES", "imposto", "Simples Nacional", "ok"),
    (r"^CIRINO E PAGNAN", "interna", "Transferência interna", "ok"),
]

REGRAS_CREDITO = [
    (r"PROADZ|BONOR|MIKKONOS|PAULISTANA|SARDI E VERCEZI|SHPP BRASIL", "devolucao", "Devolução de fornecedor", "ok"),
]

ARQUIVO_REGRAS = os.path.join(os.path.dirname(__file__), "legado", "regras.json")


@memo()
def regras_externas() -> list:
    """Regras com nome de pessoa: Supabase primeiro, JSON local depois.
    Cada uma: {sentido, padrao, natureza, categoria, confianca, excecao?}.
    `excecao` = "AAAA-MM-DD|valor": a regra só vale para essa movimentação
    (é como a compra de R$ 180 do Pedro deixa de ser aporte)."""
    import dados_fin
    regras = dados_fin.ler_regras() if dados_fin.disponivel() else []
    if not regras and os.path.exists(ARQUIVO_REGRAS):
        import json
        with open(ARQUIVO_REGRAS, encoding="utf-8") as f:
            regras = json.load(f)
    return sorted(regras, key=lambda r: r.get("ordem", 0))


_cache_regras = None


def _regras(sentido: str, data=None, valor=None) -> list:
    externas = []
    for r in (_cache_regras if _cache_regras is not None else regras_externas()):
        if r["sentido"] != sentido:
            continue
        exc = r.get("excecao")
        if exc:
            d, v = exc.split("|")
            if data is None or data.strftime("%Y-%m-%d") != d or round(float(valor), 2) != float(v):
                continue
            externas.insert(0, (r["padrao"], r["natureza"], r["categoria"], r["confianca"]))
        else:
            externas.append((r["padrao"], r["natureza"], r["categoria"], r["confianca"]))
    return externas + (REGRAS_DEBITO if sentido == "saida" else REGRAS_CREDITO)


def _classificar_debito(nome: str, tipo: str, valor: float):
    # "Transação" sem contraparte é o aluguel mensal da maquininha. Com
    # contraparte é boleto pago, e aí quem manda é o nome: no csv de
    # setembro/26 vieram Violet, Excim, Mikkonos e o contador por esse tipo,
    # e a regra antiga jogava todos em "Maquininha".
    if tipo == "Transação" and (not nome or nome.lower() in ("desconhecido", "nan")):
        return ("despesa", "Maquininha", "ok")
    for padrao, nat, cat, conf in _regras("saida"):
        if re.search(padrao, nome, flags=re.I):
            return (nat, cat, conf)
    return ("a_classificar", "", "a classificar")


def _classificar_credito(nome: str, tipo: str, valor: float, conta_origem: str, data: pd.Timestamp):
    if tipo == "Transferência entre contas Stone":
        if re.search(r"STONE PRINCIPAL", nome, flags=re.I):
            return ("venda_fisica", "Maquininha (líquido)", "ok")
        return ("liquidacao_online", "Pagar.me", "ok")
    if tipo == "Recebível de Cartão":
        return ("venda_fisica", "Maquininha (líquido)", "ok")
    if tipo.startswith("Devolução"):
        return ("devolucao", "Devolução no cartão", "ok")
    for padrao, nat, cat, conf in _regras("entrada", data, valor):
        if re.search(padrao, nome, flags=re.I):
            return (nat, cat, conf)
    if valor < 0.05:
        return ("interna", "Teste de conta", "ok")
    return ("venda_fisica", "Pix direto" if tipo == "Pix" else "Link de pagamento", "ok")


def _limpar_valor(serie: pd.Series) -> pd.Series:
    return (serie.astype(str).str.replace("R$", "", regex=False).str.replace(".", "", regex=False)
            .str.replace(",", ".").str.strip().replace({"": None, "-": None, "Grátis": None}).astype(float))


def ler_arquivo(caminho_ou_arquivo) -> pd.DataFrame:
    """Exportação da Stone, crua e limpa: uma linha por movimentação, sem
    classificar. É este formato que vai para o Supabase.

    A Stone exporta em xlsx e em csv, com as mesmas colunas. O csv traz uma
    coluna `Horário` a mais e o valor entre aspas com separador brasileiro,
    que `_limpar_valor` já resolve."""
    nome = getattr(caminho_ou_arquivo, "name", str(caminho_ou_arquivo)).lower()
    df = (pd.read_csv(caminho_ou_arquivo, dtype=str) if nome.endswith(".csv")
          else pd.read_excel(caminho_ou_arquivo))
    credito = df["Movimentação"] == "Crédito"
    return pd.DataFrame({
        "data": pd.to_datetime(df["Data"], format="%d/%m/%Y %H:%M"),
        "sentido": credito.map({True: "entrada", False: "saida"}),
        "tipo": df["Tipo"].astype(str),
        # A tarifa é cobrada dentro da própria movimentação (o saldo depois
        # já vem líquido dela), então o valor que entra é o valor menos a
        # tarifa. São poucas linhas, todas de link de pagamento em 2025,
        # mas sem isto o caixa calculado não fecha com o saldo da conta.
        "valor": _limpar_valor(df["Valor"]) - _limpar_valor(df["Tarifa"]).fillna(0.0),
        "contraparte": df["Origem"].where(credito, df["Destino"]).fillna("").astype(str).str.strip(),
        "documento": df["Origem Documento"].where(credito, df["Destino Documento"]).fillna("").astype(str),
        "conta_origem": df["Origem Conta"].fillna("").astype(str),
        "saldo_depois": _limpar_valor(df["Saldo depois"]) if "Saldo depois" in df else None,
    })


def classificar(cru: pd.DataFrame) -> pd.DataFrame:
    """Aplica as regras. Separado da leitura para uma regra nova valer para
    tudo que já está guardado."""
    df = cru.copy()
    global _cache_regras
    _cache_regras = regras_externas()
    nat, cat, conf = [], [], []
    for r in df.itertuples():
        if r.sentido == "entrada":
            n, c, f = _classificar_credito(r.contraparte, str(r.tipo), r.valor, str(r.conta_origem), r.data)
        else:
            n, c, f = _classificar_debito(r.contraparte, str(r.tipo), r.valor)
        nat.append(n); cat.append(c); conf.append(f)
    df["natureza"], df["categoria"], df["confianca"] = nat, cat, conf
    df["mes"] = df.data.dt.strftime("%Y-%m")
    df["valor_abs"] = df.valor.abs()
    return df[["data", "mes", "sentido", "tipo", "valor", "valor_abs", "contraparte",
               "natureza", "categoria", "confianca"]].sort_values("data").reset_index(drop=True)


def disponivel() -> bool:
    """Tem extrato em algum lugar: no Supabase ou no arquivo local."""
    import dados_fin
    return (dados_fin.disponivel() and not dados_fin.ler_extrato().empty) or os.path.exists(ARQUIVO)


@memo()
def carregar() -> pd.DataFrame:
    """Extrato classificado. Supabase primeiro; sem ele, o xlsx em legado/."""
    import dados_fin
    cru = dados_fin.ler_extrato() if dados_fin.disponivel() else pd.DataFrame()
    if cru.empty and os.path.exists(ARQUIVO):
        cru = ler_arquivo(ARQUIVO)
    if cru.empty:
        return pd.DataFrame(columns=["data", "mes", "sentido", "tipo", "valor", "valor_abs", "contraparte",
                                     "natureza", "categoria", "confianca"])
    return classificar(cru)


def resumo_por_natureza() -> pd.DataFrame:
    df = carregar()
    return df.groupby(["sentido", "natureza"])["valor"].agg(["count", "sum"]).round(2)


def a_classificar() -> pd.DataFrame:
    df = carregar()
    return df[df.confianca != "ok"].groupby(["sentido", "natureza", "categoria", "contraparte"])["valor"].agg(["count", "sum"]).round(2).sort_values("sum")
