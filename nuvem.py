"""
Armazenamento que sobrevive ao reinício, num Postgres do Supabase.

Por que existe: o disco do Streamlit é apagado a cada reinício, e o app volta
com o banco local vazio. Tudo que ele consegue rebaixar de uma API volta
sozinho; o que não consegue, some. Dois casos hoje:

  1. Pedidos anteriores a 60 dias, que a Shopify se recusa a devolver sem o
     escopo `read_all_orders`. Sem eles, peça, tamanho e cidade faltam para a
     maior parte da base.
  2. Qualquer anotação da própria loja, como "já mandei mensagem para essa
     pessoa", que é a base do CRM.

O `dados.db` local continua sendo o cache rápido, rebaixado das APIs. Aqui fica
só o que nenhuma API devolve.

Falha em silêncio quando não está configurado: sem a linha de conexão o app
inteiro continua funcionando como antes, só sem o histórico antigo.
"""

import os
from typing import List, Optional

TABELA = "pedidos_historicos"

_motor = None


def _carregar_env():
    """Carrega o .env ao lado deste módulo, se existir.

    Precisa carregar aqui também, e não só via `app.py`, para que scripts
    soltos e testes enxerguem a conexão sem subir o Streamlit inteiro.
    """
    caminho = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(caminho):
        return
    with open(caminho) as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, _, v = linha.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_carregar_env()


def _cfg(nome: str) -> Optional[str]:
    """Lê config do ambiente, do .env local ou do cofre do Streamlit."""
    v = os.environ.get(nome)
    if v:
        return v
    try:
        import streamlit as st
        return st.secrets.get(nome)
    except Exception:
        return None


def _url() -> Optional[str]:
    """Linha de conexão do Supabase, no formato URI do Postgres."""
    u = _cfg("SUPABASE_URL_BANCO") or _cfg("DATABASE_URL")
    if not u:
        return None
    # A Supabase entrega `postgresql://`; o SQLAlchemy 2 pede o driver
    # explícito quando há mais de um instalado.
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql+psycopg2://", 1)
    elif u.startswith("postgresql://"):
        u = u.replace("postgresql://", "postgresql+psycopg2://", 1)
    return u


def configurado() -> bool:
    return bool(_url())


def _conectar():
    """Motor de conexão com pool pequeno, reaproveitado entre execuções.

    O Streamlit reexecuta o script inteiro a cada clique. Criar conexão nova
    toda vez estouraria o limite de conexões do plano gratuito.
    """
    global _motor
    if _motor is not None:
        return _motor
    from sqlalchemy import create_engine
    _motor = create_engine(
        _url(), pool_size=1, max_overflow=1, pool_pre_ping=True,
        pool_recycle=300, connect_args={"connect_timeout": 10},
    )
    return _motor


def garantir_tabelas() -> None:
    """Cria a tabela de histórico se ainda não existir."""
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA} (
                numero      TEXT PRIMARY KEY,
                criado_em   TEXT NOT NULL,
                email       TEXT,
                cliente     TEXT,
                cidade      TEXT,
                uf          TEXT,
                itens       TEXT,
                cupom       TEXT,
                total       BIGINT,
                situacao    TEXT,
                origem      TEXT
            )
        """))
        con.execute(text(
            f"CREATE INDEX IF NOT EXISTS {TABELA}_email ON {TABELA} (email)"
        ))


def salvar_pedidos(pedidos: List[dict]) -> int:
    """Grava pedidos, atualizando os que já existirem.

    A chave é o número do pedido da Shopify, que não muda e não repete. Assim
    reimportar o mesmo arquivo não duplica nada.
    """
    if not pedidos:
        return 0
    from sqlalchemy import text
    garantir_tabelas()
    sql = text(f"""
        INSERT INTO {TABELA}
            (numero, criado_em, email, cliente, cidade, uf, itens, cupom,
             total, situacao, origem)
        VALUES
            (:numero, :criado_em, :email, :cliente, :cidade, :uf, :itens,
             :cupom, :total, :situacao, :origem)
        ON CONFLICT (numero) DO UPDATE SET
            criado_em = EXCLUDED.criado_em,
            email     = EXCLUDED.email,
            cliente   = EXCLUDED.cliente,
            cidade    = EXCLUDED.cidade,
            uf        = EXCLUDED.uf,
            itens     = EXCLUDED.itens,
            cupom     = EXCLUDED.cupom,
            total     = EXCLUDED.total,
            situacao  = EXCLUDED.situacao,
            origem    = EXCLUDED.origem
    """)
    with _conectar().begin() as con:
        for p in pedidos:
            con.execute(sql, {
                "numero": p.get("numero", ""),
                "criado_em": p.get("criado_em", ""),
                "email": (p.get("email") or "").strip().lower(),
                "cliente": p.get("cliente", ""),
                "cidade": p.get("cidade", ""),
                "uf": p.get("uf", ""),
                "itens": p.get("itens", ""),
                "cupom": p.get("cupom", ""),
                "total": int(p.get("total") or 0),
                "situacao": p.get("situacao", ""),
                "origem": p.get("origem", "shopify"),
            })
    return len(pedidos)


def ler_pedidos() -> List[dict]:
    """Todos os pedidos guardados. Lista vazia se não estiver configurado."""
    if not configurado():
        return []
    from sqlalchemy import text
    try:
        garantir_tabelas()
        with _conectar().connect() as con:
            linhas = con.execute(text(
                f"SELECT numero, criado_em, email, cliente, cidade, uf, itens,"
                f" cupom, total, situacao, origem FROM {TABELA}"
            )).mappings().all()
        return [dict(l) for l in linhas]
    except Exception:
        # Banco fora do ar não pode derrubar o painel: sem histórico antigo o
        # app ainda responde as perguntas do dia a dia.
        return []


def ler_csv_shopify(caminho_ou_arquivo) -> List[dict]:
    """Lê a exportação de pedidos da Shopify e devolve pedidos agrupados.

    O arquivo tem **uma linha por peça**, não por pedido: um pedido com três
    peças ocupa três linhas, e só a primeira traz e-mail, endereço e total. Por
    isso o agrupamento é por número do pedido, guardando o primeiro valor não
    vazio de cada campo e juntando as peças.

    Os nomes de coluna variam entre versões da exportação, então cada campo é
    procurado por uma lista de apelidos em vez de um nome fixo.
    """
    import csv
    import io

    if hasattr(caminho_ou_arquivo, "read"):
        dados = caminho_ou_arquivo.read()
        if isinstance(dados, bytes):
            dados = dados.decode("utf-8-sig")
        fonte = io.StringIO(dados)
    else:
        fonte = open(caminho_ou_arquivo, encoding="utf-8-sig")

    def pega(linha, *apelidos):
        for a in apelidos:
            v = (linha.get(a) or "").strip()
            if v:
                return v
        return ""

    pedidos = {}
    with fonte:
        for linha in csv.DictReader(fonte):
            numero = pega(linha, "Name", "Order Name", "Nome")
            if not numero:
                continue
            p = pedidos.setdefault(numero, {
                "numero": numero, "criado_em": "", "email": "", "cliente": "",
                "cidade": "", "uf": "", "pecas": [], "cupom": "", "total": 0,
                "situacao": "", "origem": "csv",
            })
            p["criado_em"] = p["criado_em"] or pega(linha, "Created at", "Processed At")
            p["email"] = p["email"] or pega(linha, "Email", "Customer Email")
            p["cliente"] = p["cliente"] or pega(
                linha, "Billing Name", "Shipping Name", "Customer Name")
            p["cidade"] = p["cidade"] or pega(linha, "Shipping City", "Billing City")
            p["uf"] = p["uf"] or pega(
                linha, "Shipping Province", "Billing Province",
                "Shipping Province Name", "Billing Province Name")
            p["cupom"] = p["cupom"] or pega(linha, "Discount Code")
            p["situacao"] = p["situacao"] or pega(linha, "Financial Status")

            bruto = pega(linha, "Total", "Total Price")
            if bruto and not p["total"]:
                try:
                    p["total"] = int(round(float(bruto.replace(",", ".")) * 100))
                except ValueError:
                    pass

            nome_peca = pega(linha, "Lineitem name", "Lineitem Name")
            if nome_peca:
                variante = pega(linha, "Lineitem variant title", "Lineitem sku")
                qtd = pega(linha, "Lineitem quantity", "Lineitem Quantity") or "1"
                # A exportação às vezes já traz o tamanho colado no nome, como
                # "Saia - Giverny - PP". Só acrescenta a variante quando ela
                # ainda não estiver ali, para não repetir.
                if variante and variante.lower() not in nome_peca.lower():
                    nome_peca = f"{nome_peca} ({variante})"
                p["pecas"].append(f"{qtd}x {nome_peca}")

    saida = []
    for p in pedidos.values():
        p["itens"] = ", ".join(p.pop("pecas"))
        p["criado_em"] = (p["criado_em"] or "")[:19].replace(" ", "T")
        p["email"] = p["email"].lower()
        saida.append(p)
    return sorted(saida, key=lambda p: p["criado_em"])


def resumo() -> dict:
    """Quantos pedidos e desde quando, para mostrar na tela."""
    if not configurado():
        return {"conectado": False, "pedidos": 0, "desde": ""}
    from sqlalchemy import text
    try:
        garantir_tabelas()
        with _conectar().connect() as con:
            r = con.execute(text(
                f"SELECT COUNT(*), MIN(substr(criado_em,1,10)) FROM {TABELA}"
            )).first()
        return {"conectado": True, "pedidos": r[0] or 0, "desde": r[1] or ""}
    except Exception as e:
        return {"conectado": False, "pedidos": 0, "desde": "", "erro": str(e)[:200]}
