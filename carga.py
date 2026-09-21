"""Carrega no Supabase o que o Pedro manda na conversa.

    python carga.py extrato  caminho/Extrato.xlsx      exportação da conta Stone
    python carga.py contas   caminho/contas.xlsx       planilha de contas a pagar (uma aba por mês)
    python carga.py meta     2026-09 2500              investimento em Meta de um mês
    python carga.py legado                             fichas e lançamentos pré-conta, da planilha de 2025
    python carga.py regras                             regras de classificação com nome de pessoa (legado/regras.json)
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
    cru = extrato.ler_xlsx(caminho)
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
    todas = pd.concat(partes, ignore_index=True)
    n = dados_fin.salvar_contas_a_pagar(todas, substituir=True)
    pend = todas[todas.situacao != "PAGO"]
    print(f"contas a pagar: {n} linhas gravadas (substituindo as anteriores). Pendentes: {len(pend)}, R$ {pend.valor.sum():,.2f}")
    for m, v in pend.groupby(pend.data.dt.strftime("%Y-%m")).valor.sum().items():
        print(f"  {m}: R$ {v:,.2f}")


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
    else:
        r = dados_fin.resumo()
        print(f"extrato: {r['extrato_linhas']} linhas, até {r['extrato_ate']}")
        print(f"contas a pagar: {r['contas_linhas']} linhas, carregadas em {r['contas_em']}")
        print(f"meta ads: até {r['meta_ate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
