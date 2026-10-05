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
  fin_fatura_cartao      linhas das faturas dos cartões das sócias: o extrato
                         só mostra o reembolso, a fatura mostra no que foi
  fin_rateios            pagamento que é duas coisas: o boleto do aluguel de
                         setembro tinha o seguro incêndio dentro
  fin_orcamento          projeção congelada de um mês, linha a linha do PnL,
                         para o mês corrente ter contra o que ser comparado

Sem conexão configurada, tudo devolve vazio e os módulos caem nos arquivos
locais em `legado/`, que é como se trabalha nesta máquina.
"""

import hashlib
from typing import List

import pandas as pd

import nuvem
from memo import memo

_tabelas_garantidas = False


def disponivel() -> bool:
    return nuvem.configurado()


def garantir() -> None:
    """Cria as tabelas se faltarem. Uma vez por processo: são seis comandos
    de DDL e antes rodavam antes de cada leitura."""
    global _tabelas_garantidas
    if _tabelas_garantidas:
        return
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
        con.execute(text(_SQL_FATURA))
        con.execute(text(_SQL_RATEIO))
        con.execute(text(_SQL_ORCAMENTO))
        con.execute(text(_SQL_VENDAS_STONE))
    _tabelas_garantidas = True


# Relatório de vendas da conta Stone: a venda física só tem identidade aqui.
# O extrato mostra a maquininha como um repasse líquido por dia, sem dizer
# de quem veio; o relatório traz o cartão mascarado de cada venda, e é por
# ele que se mede quem voltou a comprar no presencial.
_SQL_VENDAS_STONE = """
    CREATE TABLE IF NOT EXISTS fin_vendas_stone (
        stone_id     TEXT PRIMARY KEY,
        data         TIMESTAMP NOT NULL,
        bandeira     TEXT,
        produto      TEXT,
        parcelas     INTEGER,
        bruto        DOUBLE PRECISION NOT NULL,
        liquido      DOUBLE PRECISION NOT NULL,
        cartao       TEXT,
        captura      TEXT,
        status       TEXT,
        carregado_em TIMESTAMPTZ NOT NULL DEFAULT now()
    )"""


_SQL_FATURA = """
    CREATE TABLE IF NOT EXISTS fin_fatura_cartao (
        id             TEXT PRIMARY KEY,
        fatura         TEXT NOT NULL,
        titular        TEXT NOT NULL,
        data           DATE NOT NULL,
        cartao         TEXT NOT NULL,
        estabelecimento TEXT NOT NULL,
        valor          DOUBLE PRECISION NOT NULL,
        parcela        TEXT,
        natureza       TEXT NOT NULL,
        categoria      TEXT,
        carregado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
    )"""


_SQL_RATEIO = """
    CREATE TABLE IF NOT EXISTS fin_rateios (
        id              TEXT PRIMARY KEY,
        data            DATE NOT NULL,
        contraparte     TEXT NOT NULL,
        valor_total     DOUBLE PRECISION NOT NULL,
        valor_parte     DOUBLE PRECISION NOT NULL,
        categoria_parte TEXT NOT NULL,
        natureza_parte  TEXT NOT NULL,
        observacao      TEXT
    )"""


_SQL_ORCAMENTO = """
    CREATE TABLE IF NOT EXISTS fin_orcamento (
        mes          TEXT NOT NULL,
        linha        TEXT NOT NULL,
        valor        DOUBLE PRECISION NOT NULL,
        congelado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (mes, linha)
    )"""


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
    import memo as _m
    _m.limpar_tudo()
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


@memo()
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


@memo()
def ler_fichas() -> pd.DataFrame:
    return _ler("SELECT peca, custo, preco, margem_pct, incompleta FROM fin_fichas ORDER BY peca")


def salvar_lancamentos_legado(df: pd.DataFrame) -> int:
    linhas = [{"id": _id(r.data, r.descricao, r.categoria, round(r.valor, 2)), "data": r.data.date(),
               "descricao": r.descricao, "setor": r.setor, "atividade": r.atividade, "tipo": r.tipo,
               "categoria": r.categoria, "terceiro": r.terceiro, "parcelas": int(r.parcelas), "valor": float(r.valor)}
              for r in df.itertuples(index=False)]
    return _upsert("fin_lancamentos_legado", linhas, "id")


@memo()
def ler_lancamentos_legado() -> pd.DataFrame:
    df = _ler("SELECT data, descricao, setor, atividade, tipo, categoria, terceiro, parcelas, valor FROM fin_lancamentos_legado ORDER BY data")
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


# ─── Provisões ──────────────────────────────────────────────────────────────

def salvar_meta_ads(por_mes: dict, fonte: str = "relatório da agência") -> int:
    return _upsert("fin_meta_ads", [{"mes": m, "valor": float(v), "fonte": fonte} for m, v in por_mes.items()], "mes")


@memo()
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


@memo()
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


@memo()
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


# ─── Faturas de cartão ──────────────────────────────────────────────────────

def salvar_fatura_cartao(df: pd.DataFrame, substituir_faturas: bool = True) -> int:
    """Linhas de fatura. A chave é o conteúdo, então subir a mesma fatura
    duas vezes não duplica. Por padrão limpa antes as faturas presentes no
    arquivo, para correção de transcrição não deixar linha órfã."""
    from sqlalchemy import text
    garantir()
    if substituir_faturas and not df.empty:
        with nuvem._conectar().begin() as con:
            for fat in sorted(df.fatura.unique()):
                con.execute(text("DELETE FROM fin_fatura_cartao WHERE fatura = :f"), {"f": fat})
    linhas = [{"id": _id(r.fatura, r.data, r.cartao, r.estabelecimento, round(r.valor, 2), r.parcela),
               "fatura": r.fatura, "titular": r.titular, "data": pd.Timestamp(r.data).date(), "cartao": str(r.cartao),
               "estabelecimento": r.estabelecimento, "valor": float(r.valor),
               "parcela": None if pd.isna(r.parcela) else str(r.parcela),
               "natureza": r.natureza, "categoria": r.categoria}
              for r in df.itertuples(index=False)]
    return _upsert("fin_fatura_cartao", linhas, "id")


def salvar_vendas_stone(df: pd.DataFrame) -> int:
    """A chave é o Stone ID, único por transação: subir o mesmo relatório
    duas vezes, ou um relatório mais longo por cima, não duplica."""
    linhas = [{"stone_id": str(r.stone_id), "data": pd.Timestamp(r.data).to_pydatetime(),
               "bandeira": r.bandeira or None, "produto": r.produto or None,
               "parcelas": int(r.parcelas) if r.parcelas else None,
               "bruto": float(r.bruto), "liquido": float(r.liquido),
               "cartao": r.cartao or None, "captura": r.captura or None, "status": r.status or None}
              for r in df.itertuples(index=False)]
    return _upsert("fin_vendas_stone", linhas, "stone_id")


def marcar_vendas_stone_canceladas(stone_ids: List[str]) -> int:
    """Cancelamento chega da API num dia posterior ao da venda."""
    if not stone_ids:
        return 0
    from sqlalchemy import text
    garantir()
    with nuvem._conectar().begin() as con:
        n = con.execute(text("UPDATE fin_vendas_stone SET status = 'Cancelada' WHERE stone_id = ANY(:ids)"),
                        {"ids": list(stone_ids)}).rowcount
    import memo as _m
    _m.limpar_tudo()
    return n


@memo()
def ler_vendas_stone() -> pd.DataFrame:
    df = _ler("SELECT stone_id, data, bandeira, produto, parcelas, bruto, liquido, cartao, captura, status "
              "FROM fin_vendas_stone ORDER BY data")
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


@memo()
def ler_fatura_cartao() -> pd.DataFrame:
    df = _ler("SELECT fatura, titular, data, cartao, estabelecimento, valor, parcela, natureza, categoria "
              "FROM fin_fatura_cartao ORDER BY fatura, data")
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


# ─── Rateios ────────────────────────────────────────────────────────────────

def salvar_rateios(df: pd.DataFrame) -> int:
    df = df.copy()
    for c in ("valor_total", "valor_parte"):
        df[c] = df[c].astype(float)
    linhas = [{"id": _id(r.data, r.contraparte, round(float(r.valor_total), 2), round(float(r.valor_parte), 2)),
               "data": pd.Timestamp(r.data).date(), "contraparte": r.contraparte,
               "valor_total": float(r.valor_total), "valor_parte": float(r.valor_parte),
               "categoria_parte": r.categoria_parte, "natureza_parte": r.natureza_parte,
               "observacao": "" if pd.isna(r.observacao) else str(r.observacao)}
              for r in df.itertuples(index=False)]
    return _upsert("fin_rateios", linhas, "id")


@memo()
def ler_rateios() -> pd.DataFrame:
    df = _ler("SELECT data, contraparte, valor_total, valor_parte, categoria_parte, "
              "natureza_parte, observacao FROM fin_rateios ORDER BY data")
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


# ─── Orçamento ──────────────────────────────────────────────────────────────

def salvar_orcamento(mes: str, linhas: dict) -> int:
    """Congela a projeção de um mês. Substitui a anterior daquele mês: se o
    Pedro mexer nas premissas e congelar de novo, vale a última."""
    from sqlalchemy import text
    garantir()
    dados = [{"mes": mes, "linha": k, "valor": float(v)} for k, v in linhas.items()]
    with nuvem._conectar().begin() as con:
        con.execute(text("DELETE FROM fin_orcamento WHERE mes = :m"), {"m": mes})
        if dados:
            con.execute(text("INSERT INTO fin_orcamento (mes, linha, valor) "
                             "VALUES (:mes, :linha, :valor)"), dados)
    import memo as _m
    _m.limpar_tudo()
    return len(dados)


@memo()
def ler_orcamento() -> pd.DataFrame:
    df = _ler("SELECT mes, linha, valor, congelado_em FROM fin_orcamento ORDER BY mes, linha")
    return df
