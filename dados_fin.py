"""Dados financeiros que ninguém baixa por API: extrato, fichas, provisões.

Moram no Supabase, no mesmo banco dos pedidos, porque o Streamlit apaga o
disco a cada reinício e o repositório é público (extrato tem fornecedor e
valor). O fluxo combinado com o Pedro em 20/09/2026: ele manda o arquivo ou
o número na conversa, eu carrego daqui com `carga.py`, o app em produção lê.

Tabelas, todas com prefixo `fin_`:

  fin_extrato            movimentações da conta Stone, cruas. A classificação
                         fica no código (extrato.py), para uma regra nova
                         valer para trás também.
  fin_fichas             custo por peça, da planilha de precificação
  fin_lancamentos_legado planilha de 2025: só o que foi pago antes da conta
  fin_meta_ads           investimento em Meta por mês, dos relatórios da agência
  fin_contas_a_pagar     provisões: contas contratadas e ainda não pagas
  fin_regras             regras de classificação do extrato com nome de
                         pessoa física, que não podem ir para o Git público

Sem conexão configurada, tudo devolve vazio e os módulos caem nos arquivos
locais em `legado/`, que é como se trabalha nesta máquina.
"""

import hashlib
from typing import List

import pandas as pd

import nuvem


def disponivel() -> bool:
    return nuvem.configurado()


def garantir() -> None:
    from sqlalchemy import text
    with nuvem._conectar().begin() as con:
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS fin_extrato (
                id            TEXT PRIMARY KEY,
                data          TIMESTAMP NOT NULL,
                sentido       TEXT NOT NULL,
                tipo          TEXT,
                valor         DOUBLE PRECISION NOT NULL,
                contraparte   TEXT,
                documento     TEXT,
                conta_origem  TEXT,
                saldo_depois  DOUBLE PRECISION,
                carregado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""))
        con.execute(text("CREATE INDEX IF NOT EXISTS fin_extrato_data ON fin_extrato (data)"))
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS fin_fichas (
                peca        TEXT PRIMARY KEY,
                custo       DOUBLE PRECISION NOT NULL,
                preco       DOUBLE PRECISION,
                margem_pct  DOUBLE PRECISION,
                incompleta  BOOLEAN NOT NULL DEFAULT false
            )"""))
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS fin_lancamentos_legado (
                id          TEXT PRIMARY KEY,
                data        DATE NOT NULL,
                descricao   TEXT,
                setor       TEXT,
                atividade   TEXT,
                tipo        TEXT,
                categoria   TEXT,
                terceiro    TEXT,
                parcelas    INTEGER NOT NULL DEFAULT 1,
                valor       DOUBLE PRECISION NOT NULL
            )"""))
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS fin_meta_ads (
                mes         TEXT PRIMARY KEY,
                valor       DOUBLE PRECISION NOT NULL,
                fonte       TEXT,
                atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""))
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS fin_regras (
                id          TEXT PRIMARY KEY,
                ordem       INTEGER NOT NULL DEFAULT 0,
                sentido     TEXT NOT NULL,
                padrao      TEXT NOT NULL,
                natureza    TEXT NOT NULL,
                categoria   TEXT,
                confianca   TEXT NOT NULL DEFAULT 'ok',
                excecao     TEXT
            )"""))
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS fin_contas_a_pagar (
                id          TEXT PRIMARY KEY,
                data        DATE NOT NULL,
                descricao   TEXT NOT NULL,
                valor       DOUBLE PRECISION NOT NULL,
                situacao    TEXT,
                observacao  TEXT,
                carregado_em TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""))


def _id(*partes) -> str:
    return hashlib.sha1("|".join(str(p) for p in partes).encode()).hexdigest()[:20]


def _ler(sql: str) -> pd.DataFrame:
    if not disponivel():
        return pd.DataFrame()
    try:
        garantir()
        with nuvem._conectar().connect() as con:
            return pd.read_sql_query(sql, con)
    except Exception:
        # Banco fora do ar não derruba o painel; os módulos caem no local.
        return pd.DataFrame()


def _upsert(tabela: str, linhas: List[dict], chave: str) -> int:
    if not linhas:
        return 0
    from sqlalchemy import text
    garantir()
    colunas = list(linhas[0].keys())
    atualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c in colunas if c != chave)
    sql = text(
        f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join(':' + c for c in colunas)}) "
        f"ON CONFLICT ({chave}) DO UPDATE SET {atualiza}"
    )
    with nuvem._conectar().begin() as con:
        for i in range(0, len(linhas), 200):
            con.execute(sql, linhas[i:i + 200])
    return len(linhas)


# ─── Extrato ────────────────────────────────────────────────────────────────

def salvar_extrato(df: pd.DataFrame) -> int:
    """`df` no formato cru de extrato.ler_xlsx. A chave é o conteúdo da
    linha, então subir o mesmo arquivo duas vezes não duplica nada."""
    linhas = []
    for r in df.itertuples(index=False):
        linhas.append({
            "id": _id(r.data, r.sentido, r.tipo, round(r.valor, 2), r.contraparte, r.saldo_depois),
            "data": r.data.to_pydatetime(), "sentido": r.sentido, "tipo": r.tipo,
            "valor": float(r.valor), "contraparte": r.contraparte, "documento": r.documento,
            "conta_origem": r.conta_origem, "saldo_depois": None if pd.isna(r.saldo_depois) else float(r.saldo_depois),
        })
    return _upsert("fin_extrato", linhas, "id")


def ler_extrato() -> pd.DataFrame:
    df = _ler("SELECT data, sentido, tipo, valor, contraparte, documento, conta_origem, saldo_depois FROM fin_extrato ORDER BY data")
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


# ─── Fichas e legado ────────────────────────────────────────────────────────

def salvar_fichas(df: pd.DataFrame) -> int:
    linhas = [{"peca": r.peca, "custo": float(r.custo), "preco": None if pd.isna(r.preco) else float(r.preco),
               "margem_pct": None if pd.isna(r.margem_pct) else float(r.margem_pct), "incompleta": bool(r.incompleta)}
              for r in df.itertuples(index=False)]
    return _upsert("fin_fichas", linhas, "peca")


def ler_fichas() -> pd.DataFrame:
    return _ler("SELECT peca, custo, preco, margem_pct, incompleta FROM fin_fichas ORDER BY peca")


def salvar_lancamentos_legado(df: pd.DataFrame) -> int:
    linhas = [{"id": _id(r.data, r.descricao, r.categoria, round(r.valor, 2)), "data": r.data.date(),
               "descricao": r.descricao, "setor": r.setor, "atividade": r.atividade, "tipo": r.tipo,
               "categoria": r.categoria, "terceiro": r.terceiro, "parcelas": int(r.parcelas), "valor": float(r.valor)}
              for r in df.itertuples(index=False)]
    return _upsert("fin_lancamentos_legado", linhas, "id")


def ler_lancamentos_legado() -> pd.DataFrame:
    df = _ler("SELECT data, descricao, setor, atividade, tipo, categoria, terceiro, parcelas, valor FROM fin_lancamentos_legado ORDER BY data")
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


# ─── Provisões ──────────────────────────────────────────────────────────────

def salvar_meta_ads(por_mes: dict, fonte: str = "relatório da agência") -> int:
    return _upsert("fin_meta_ads", [{"mes": m, "valor": float(v), "fonte": fonte} for m, v in por_mes.items()], "mes")


def ler_meta_ads() -> dict:
    df = _ler("SELECT mes, valor FROM fin_meta_ads ORDER BY mes")
    return dict(zip(df.mes, df.valor)) if not df.empty else {}


def salvar_contas_a_pagar(df: pd.DataFrame, substituir: bool = True) -> int:
    """A planilha de contas é a foto inteira das provisões: o que saiu dela
    (foi pago ou cancelado) sai daqui também, por isso o padrão é substituir."""
    from sqlalchemy import text
    garantir()
    if substituir:
        with nuvem._conectar().begin() as con:
            con.execute(text("DELETE FROM fin_contas_a_pagar"))
    linhas = [{"id": _id(r.data, r.descricao, round(r.valor, 2)), "data": r.data.date(), "descricao": r.descricao,
               "valor": float(r.valor), "situacao": r.situacao, "observacao": r.observacao}
              for r in df.itertuples(index=False)]
    return _upsert("fin_contas_a_pagar", linhas, "id")


def ler_contas_a_pagar() -> pd.DataFrame:
    df = _ler("SELECT data, descricao, valor, situacao, observacao, carregado_em FROM fin_contas_a_pagar ORDER BY data")
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


# ─── Regras ─────────────────────────────────────────────────────────────────

def salvar_regras(regras: List[dict]) -> int:
    linhas = [{"id": _id(r["sentido"], r["padrao"], r.get("excecao") or ""), "ordem": int(r.get("ordem", 0)), "sentido": r["sentido"],
               "padrao": r["padrao"], "natureza": r["natureza"], "categoria": r.get("categoria", ""),
               "confianca": r.get("confianca", "ok"), "excecao": r.get("excecao")} for r in regras]
    return _upsert("fin_regras", linhas, "id")


def ler_regras() -> List[dict]:
    df = _ler("SELECT ordem, sentido, padrao, natureza, categoria, confianca, excecao FROM fin_regras ORDER BY ordem")
    return df.to_dict("records") if not df.empty else []


def resumo() -> dict:
    """Quantas linhas e até quando, para a tela dizer o quão fresco está."""
    ext = _ler("SELECT COUNT(*) AS n, MAX(data) AS ate FROM fin_extrato")
    cap = _ler("SELECT COUNT(*) AS n, MAX(carregado_em) AS em FROM fin_contas_a_pagar")
    meta = _ler("SELECT COUNT(*) AS n, MAX(mes) AS ate FROM fin_meta_ads")
    return {
        "extrato_linhas": int(ext.n.iloc[0]) if not ext.empty else 0,
        "extrato_ate": ext.ate.iloc[0] if not ext.empty else None,
        "contas_linhas": int(cap.n.iloc[0]) if not cap.empty else 0,
        "contas_em": cap.em.iloc[0] if not cap.empty else None,
        "meta_ate": meta.ate.iloc[0] if not meta.empty else None,
    }
