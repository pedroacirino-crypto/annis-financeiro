"""Carrega no Supabase o que o Pedro manda na conversa.

    python carga.py extrato  caminho/Extrato.csv       exportação da conta Stone (csv ou xlsx)
    python carga.py contas   caminho/contas.xlsx       planilha de contas a pagar (uma aba por mês)
    python carga.py meta     2026-09 2500              investimento em Meta de um mês
    python carga.py legado                             fichas e lançamentos pré-conta, da planilha de 2025
    python carga.py regras                             regras de classificação com nome de pessoa (legado/regras.json)
    python carga.py fatura   legado/faturas_cartao.csv linhas das faturas dos cartões das sócias
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
    for aba in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=aba)
        df.columns = [str(c).strip().upper() for c in df.columns]
        if "VALOR" not in df or "DATA" not in df:
            continue
        df = df.dropna(subset=["VALOR", "DATA"])
        partes.append(pd.DataFrame({
            "data": pd.to_datetime(df["DATA"]),
            "descricao": df["DESCRIÇÃO"].astype(str).str.strip(),
            "valor": (df["VALOR"].astype(str).str.replace("R$", "", regex=False).str.replace(".", "", regex=False)
                      .str.replace(",", ".").astype(float)),
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
    else:
        r = dados_fin.resumo()
        print(f"extrato: {r['extrato_linhas']} linhas, até {r['extrato_ate']}")
        print(f"contas a pagar: {r['contas_linhas']} linhas, carregadas em {r['contas_em']}")
        print(f"meta ads: até {r['meta_ate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
