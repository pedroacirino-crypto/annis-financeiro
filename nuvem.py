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
        # CEP e coordenada vieram depois da tabela existir. O CEP é o que
        # permite pôr a venda no mapa; a coordenada é calculada uma vez e
        # guardada, para a tela nunca depender de serviço externo para abrir.
        for coluna, tipo in (("cep", "TEXT"), ("lat", "DOUBLE PRECISION"),
                             ("lon", "DOUBLE PRECISION")):
            con.execute(text(
                f"ALTER TABLE {TABELA} ADD COLUMN IF NOT EXISTS {coluna} {tipo}"
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
             total, situacao, origem, cep)
        VALUES
            (:numero, :criado_em, :email, :cliente, :cidade, :uf, :itens,
             :cupom, :total, :situacao, :origem, :cep)
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
            origem    = EXCLUDED.origem,
            cep       = COALESCE(NULLIF(EXCLUDED.cep, ''), {TABELA}.cep)
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
                "cep": "".join(c for c in str(p.get("cep") or "") if c.isdigit()),
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
                f" cupom, total, situacao, origem, cep, lat, lon FROM {TABELA}"
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
                "cidade": "", "uf": "", "cep": "", "pecas": [], "cupom": "", "total": 0,
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
            p["cep"] = p["cep"] or pega(linha, "Shipping Zip", "Billing Zip")
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
        p["criado_em"] = _para_utc(p["criado_em"])
        p["email"] = p["email"].lower()
        saida.append(p)
    return sorted(saida, key=lambda p: p["criado_em"])


def _para_utc(quando: str) -> str:
    """Converte a data da exportação para UTC.

    A exportação vem em horário local com o fuso junto, tipo
    `2026-07-18 19:36:23 -0300`, enquanto a Pagar.me e a própria API da Shopify
    guardam UTC. Sem converter, toda compra feita depois das 21h caía no dia
    anterior na hora de casar os dois lados, e a cliente ficava sem peça e sem
    cidade. Eram 9 dos 11 casos que sobraram na primeira importação.
    """
    from datetime import datetime, timezone
    texto = (quando or "").strip()
    if not texto:
        return ""
    for formato in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return (datetime.strptime(texto, formato)
                    .astimezone(timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
    # Sem fuso declarado não dá para converter; fica como veio.
    return texto[:19].replace(" ", "T")


def geocodificar_pendentes(limite: int = 200, avisar=None) -> dict:
    """Converte CEP em coordenada para os pedidos que ainda não têm.

    Roda uma vez por CEP e guarda o resultado, então a tela nunca depende de
    serviço externo para abrir. Só o CEP é enviado, sem nome nem e-mail.

    CEPs repetidos são resolvidos de uma vez: várias compras do mesmo endereço
    custam uma consulta só.
    """
    import requests
    from sqlalchemy import text

    garantir_tabelas()
    with _conectar().connect() as con:
        pendentes = con.execute(text(
            f"SELECT DISTINCT cep FROM {TABELA} "
            f"WHERE cep IS NOT NULL AND cep != '' AND lat IS NULL"
        )).scalars().all()

    pendentes = pendentes[:limite]
    achados, falhos = 0, 0
    for i, cep in enumerate(pendentes, 1):
        if avisar:
            avisar(f"Localizando CEP {i} de {len(pendentes)}…")
        lat = lon = None
        try:
            r = requests.get(
                f"https://brasilapi.com.br/api/cep/v2/{cep}", timeout=10)
            if r.ok:
                loc = (r.json().get("location") or {}).get("coordinates") or {}
                if loc.get("latitude"):
                    lat, lon = float(loc["latitude"]), float(loc["longitude"])
        except Exception:
            pass

        if lat is None:
            falhos += 1
            continue
        achados += 1
        with _conectar().begin() as con:
            con.execute(
                text(f"UPDATE {TABELA} SET lat=:lat, lon=:lon WHERE cep=:cep"),
                {"lat": lat, "lon": lon, "cep": cep},
            )
    return {"pendentes": len(pendentes), "achados": achados, "falhos": falhos}


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


TABELA_ESPERA = "lista_espera"


def garantir_espera() -> None:
    """Cria a tabela de lista de espera, se ainda não existir.

    Quem chega numa página esgotada some sem deixar rastro: não passa pelo
    checkout, então nem a aba Recuperar enxerga. Esta tabela é o registro
    dessa demanda, que é a metade invisível do funil.
    """
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_ESPERA} (
                id          BIGSERIAL PRIMARY KEY,
                criado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
                email       TEXT,
                telefone    TEXT,
                produto     TEXT NOT NULL,
                variante    TEXT,
                variant_id  TEXT,
                handle      TEXT,
                avisado_em  TIMESTAMPTZ,
                origem      TEXT NOT NULL DEFAULT 'site',
                CONSTRAINT contato_obrigatorio
                    CHECK (COALESCE(email,'') <> '' OR COALESCE(telefone,'') <> '')
            )
        """))
        con.execute(text(
            f"CREATE INDEX IF NOT EXISTS {TABELA_ESPERA}_variante "
            f"ON {TABELA_ESPERA} (produto, variante)"
        ))
        # Uma pessoa não precisa entrar duas vezes na fila da mesma peça.
        con.execute(text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {TABELA_ESPERA}_sem_repetir "
            f"ON {TABELA_ESPERA} (COALESCE(email,''), COALESCE(telefone,''),"
            f" produto, COALESCE(variante,'')) WHERE avisado_em IS NULL"
        ))


def salvar_espera(registro: dict) -> bool:
    """Grava um pedido de aviso. Devolve False se a pessoa já estava na fila."""
    from sqlalchemy import text
    garantir_espera()
    try:
        with _conectar().begin() as con:
            con.execute(text(f"""
                INSERT INTO {TABELA_ESPERA}
                    (email, telefone, produto, variante, variant_id, handle, origem)
                VALUES (:email, :telefone, :produto, :variante, :variant_id,
                        :handle, :origem)
            """), {
                "email": (registro.get("email") or "").strip().lower() or None,
                "telefone": (registro.get("telefone") or "").strip() or None,
                "produto": registro.get("produto", ""),
                "variante": registro.get("variante") or None,
                "variant_id": registro.get("variant_id") or None,
                "handle": registro.get("handle") or None,
                "origem": registro.get("origem", "site"),
            })
        return True
    except Exception:
        return False


def ler_espera(pendentes_apenas: bool = True) -> List[dict]:
    """Quem está esperando o quê. Lista vazia se o banco não estiver de pé."""
    if not configurado():
        return []
    from sqlalchemy import text
    try:
        garantir_espera()
        filtro = "WHERE avisado_em IS NULL" if pendentes_apenas else ""
        with _conectar().connect() as con:
            linhas = con.execute(text(
                f"SELECT id, criado_em, email, telefone, produto, variante,"
                f" variant_id, handle, avisado_em, origem"
                f" FROM {TABELA_ESPERA} {filtro} ORDER BY criado_em"
            )).mappings().all()
        return [dict(l) for l in linhas]
    except Exception:
        return []


def marcar_avisado(ids: List[int]) -> int:
    """Risca da fila quem já foi avisada."""
    if not ids:
        return 0
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(
            f"UPDATE {TABELA_ESPERA} SET avisado_em = now() WHERE id = ANY(:ids)"
        ), {"ids": list(ids)})
    return len(ids)


# ─── Fila de contatos: quem já foi chamada no WhatsApp ──────────────────────
#
# O painel não tem como saber se a mensagem foi enviada: o WhatsApp abre numa
# janela que ele não enxerga e não devolve nada. O que fica gravado aqui é a
# confirmação de quem está atendendo ("mandei para essa"), não uma detecção.
#
# Mora na nuvem porque o disco do Streamlit é apagado a cada reinício, e essa
# é a única informação da aba que não pode ser rebaixada da Shopify nem da
# Pagar.me: se sumir, a fila volta do zero no meio do trabalho.

TABELA_CONTATOS = "contatos_feitos"


def garantir_contatos() -> None:
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_CONTATOS} (
                id          TEXT PRIMARY KEY,
                cliente     TEXT,
                situacao    TEXT,
                valor       BIGINT,
                enviado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))


def marcar_contato(id_: str, cliente: str = "", situacao: str = "", valor: int = 0) -> bool:
    """Grava que a mensagem foi disparada. Idempotente: marcar de novo só
    atualiza a data, que é o comportamento útil se a pessoa for recontatada."""
    if not configurado() or not id_:
        return False
    from sqlalchemy import text
    try:
        garantir_contatos()
        with _conectar().begin() as con:
            con.execute(text(
                f"INSERT INTO {TABELA_CONTATOS} (id, cliente, situacao, valor)"
                f" VALUES (:id, :cliente, :situacao, :valor)"
                f" ON CONFLICT (id) DO UPDATE SET enviado_em = now(),"
                f" situacao = EXCLUDED.situacao"
            ), {"id": id_, "cliente": cliente, "situacao": situacao, "valor": int(valor or 0)})
        return True
    except Exception:
        return False


def desmarcar_contato(id_: str) -> bool:
    if not configurado() or not id_:
        return False
    from sqlalchemy import text
    try:
        garantir_contatos()
        with _conectar().begin() as con:
            con.execute(text(f"DELETE FROM {TABELA_CONTATOS} WHERE id = :id"), {"id": id_})
        return True
    except Exception:
        return False


def ler_contatos() -> dict:
    """{id do cartão: data do envio}. Vazio se o banco não estiver de pé, e
    aí a aba funciona como antes, sem esconder ninguém."""
    if not configurado():
        return {}
    from sqlalchemy import text
    try:
        garantir_contatos()
        with _conectar().connect() as con:
            linhas = con.execute(text(
                f"SELECT id, enviado_em FROM {TABELA_CONTATOS}"
            )).mappings().all()
        return {l["id"]: l["enviado_em"] for l in linhas}
    except Exception:
        return {}


# ─── Log de acesso ──────────────────────────────────────────────────────────
#
# A senha é uma só e o link é público, então o painel não sabe QUEM entrou,
# só de onde. O que ele consegue afirmar é "este aparelho nunca apareceu
# aqui", e é esse o sinal que vale alarme. O nome que aparece no log é
# etiqueta declarada por quem entra, não identificação.
#
# Mora na nuvem porque log que some no reinício não serve para investigar
# nada depois.

TABELA_ACESSOS = "acessos"
TABELA_APARELHOS = "aparelhos_conhecidos"


def garantir_acessos() -> None:
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_ACESSOS} (
                id          BIGSERIAL PRIMARY KEY,
                quando      TIMESTAMPTZ NOT NULL DEFAULT now(),
                aparelho    TEXT NOT NULL,
                quem        TEXT,
                ip          TEXT,
                navegador   TEXT,
                sistema     TEXT,
                via         TEXT,
                conhecido   BOOLEAN NOT NULL DEFAULT false
            )
        """))
        con.execute(text(
            f"CREATE INDEX IF NOT EXISTS {TABELA_ACESSOS}_quando"
            f" ON {TABELA_ACESSOS} (quando DESC)"
        ))
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_APARELHOS} (
                aparelho    TEXT PRIMARY KEY,
                apelido     TEXT,
                quem        TEXT,
                visto_em    TIMESTAMPTZ NOT NULL DEFAULT now(),
                suspeito    BOOLEAN NOT NULL DEFAULT false
            )
        """))


def registrar_acesso(aparelho: str, quem: str, ip: str, navegador: str,
                     sistema: str, via: str) -> bool:
    """Grava uma entrada e diz se o aparelho já era conhecido."""
    if not configurado():
        return True
    from sqlalchemy import text
    try:
        garantir_acessos()
        with _conectar().begin() as con:
            ja = con.execute(text(
                f"SELECT 1 FROM {TABELA_APARELHOS} WHERE aparelho = :a"
            ), {"a": aparelho}).first() is not None
            con.execute(text(
                f"INSERT INTO {TABELA_ACESSOS}"
                f" (aparelho, quem, ip, navegador, sistema, via, conhecido)"
                f" VALUES (:a, :q, :ip, :nav, :sis, :via, :con)"
            ), {"a": aparelho, "q": quem, "ip": ip, "nav": navegador,
                "sis": sistema, "via": via, "con": ja})
        return ja
    except Exception:
        # Falha de log não pode impedir alguém de usar o painel.
        return True


def conhecer_aparelho(aparelho: str, apelido: str = "", quem: str = "",
                      suspeito: bool = False) -> bool:
    if not configurado():
        return False
    from sqlalchemy import text
    try:
        garantir_acessos()
        with _conectar().begin() as con:
            con.execute(text(
                f"INSERT INTO {TABELA_APARELHOS} (aparelho, apelido, quem, suspeito)"
                f" VALUES (:a, :ap, :q, :s)"
                f" ON CONFLICT (aparelho) DO UPDATE SET apelido = EXCLUDED.apelido,"
                f" quem = EXCLUDED.quem, suspeito = EXCLUDED.suspeito, visto_em = now()"
            ), {"a": aparelho, "ap": apelido, "q": quem, "s": suspeito})
            con.execute(text(
                f"UPDATE {TABELA_ACESSOS} SET conhecido = true WHERE aparelho = :a"
            ), {"a": aparelho})
        return True
    except Exception:
        return False


def ler_acessos(limite: int = 300) -> List[dict]:
    if not configurado():
        return []
    from sqlalchemy import text
    try:
        garantir_acessos()
        with _conectar().connect() as con:
            linhas = con.execute(text(
                f"SELECT a.quando, a.aparelho, a.quem, a.ip, a.navegador, a.sistema,"
                f" a.via, a.conhecido, c.apelido, c.suspeito"
                f" FROM {TABELA_ACESSOS} a"
                f" LEFT JOIN {TABELA_APARELHOS} c ON c.aparelho = a.aparelho"
                f" ORDER BY a.quando DESC LIMIT :n"
            ), {"n": limite}).mappings().all()
        return [dict(l) for l in linhas]
    except Exception:
        return []


def ler_aparelhos() -> List[dict]:
    if not configurado():
        return []
    from sqlalchemy import text
    try:
        garantir_acessos()
        with _conectar().connect() as con:
            linhas = con.execute(text(
                f"SELECT aparelho, apelido, quem, visto_em, suspeito"
                f" FROM {TABELA_APARELHOS} ORDER BY visto_em DESC"
            )).mappings().all()
        return [dict(l) for l in linhas]
    except Exception:
        return []


# ─── Plano do mês: o que foi combinado fazer, e o que já foi feito ─────────
#
# O texto do plano mora na nuvem, não no código: o repositório é público e o
# plano tem meta, verba e número de cliente. Entra por `carga.py plano`. A
# marcação de feito fica na mesma linha, para o plano e o andamento nunca se
# separarem.

TABELA_PLANO = "plano_do_mes"


_plano_garantido = False


def garantir_plano() -> None:
    """Uma vez por processo: cada ida ao banco custa centenas de
    milissegundos da nuvem do Streamlit até São Paulo, e a aba do plano
    fazia seis delas a cada caixa marcada."""
    global _plano_garantido
    if _plano_garantido:
        return
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_PLANO} (
                id        TEXT PRIMARY KEY,
                mes       TEXT NOT NULL,
                ordem     INTEGER NOT NULL,
                frente    TEXT NOT NULL,
                alvo      TEXT,
                texto     TEXT NOT NULL,
                prazo     TEXT,
                feito_em  TIMESTAMPTZ
            )
        """))
    _plano_garantido = True


def salvar_plano(mes: str, frentes: list) -> int:
    """Grava o plano de um mês. Recarregar o mesmo mês atualiza texto e prazo
    e preserva o que já foi marcado como feito; item que saiu do arquivo sai
    da tabela."""
    from sqlalchemy import text
    garantir_plano()
    linhas, ordem = [], 0
    for f in frentes:
        for it in f["itens"]:
            ordem += 1
            linhas.append({"id": f"{mes}:{it['id']}", "mes": mes, "ordem": ordem,
                           "frente": f["frente"], "alvo": f.get("alvo", ""),
                           "texto": it["texto"], "prazo": it.get("prazo", ""),
                           "feito": bool(it.get("feito"))})
    with _conectar().begin() as con:
        con.execute(text(f"DELETE FROM {TABELA_PLANO} WHERE mes = :mes AND NOT (id = ANY(:ids))"),
                    {"mes": mes, "ids": [l["id"] for l in linhas]})
        for l in linhas:
            con.execute(text(
                f"INSERT INTO {TABELA_PLANO} (id, mes, ordem, frente, alvo, texto, prazo, feito_em)"
                f" VALUES (:id, :mes, :ordem, :frente, :alvo, :texto, :prazo,"
                f"         CASE WHEN :feito THEN now() ELSE NULL END)"
                f" ON CONFLICT (id) DO UPDATE SET ordem = EXCLUDED.ordem, frente = EXCLUDED.frente,"
                f" alvo = EXCLUDED.alvo, texto = EXCLUDED.texto, prazo = EXCLUDED.prazo,"
                f" feito_em = COALESCE({TABELA_PLANO}.feito_em, EXCLUDED.feito_em)"
            ), l)
    return len(linhas)


def ler_plano(mes: str) -> list:
    """Itens do plano do mês, na ordem. Vazio se o banco não estiver de pé."""
    if not configurado():
        return []
    from sqlalchemy import text
    try:
        garantir_plano()
        with _conectar().connect() as con:
            return [dict(l) for l in con.execute(text(
                f"SELECT id, frente, alvo, texto, prazo, feito_em FROM {TABELA_PLANO}"
                f" WHERE mes = :mes ORDER BY ordem"), {"mes": mes}).mappings().all()]
    except Exception:
        return []


def meses_com_plano() -> list:
    if not configurado():
        return []
    from sqlalchemy import text
    try:
        garantir_plano()
        with _conectar().connect() as con:
            return [l[0] for l in con.execute(text(
                f"SELECT DISTINCT mes FROM {TABELA_PLANO} ORDER BY mes DESC")).all()]
    except Exception:
        return []


def marcar_plano(id_: str, feito: bool) -> bool:
    if not configurado() or not id_:
        return False
    from sqlalchemy import text
    try:
        garantir_plano()
        with _conectar().begin() as con:
            con.execute(text(
                f"UPDATE {TABELA_PLANO} SET feito_em = CASE WHEN :feito THEN now() ELSE NULL END"
                f" WHERE id = :id"), {"id": id_, "feito": bool(feito)})
        return True
    except Exception:
        return False


# ─── Cupons pessoais de recuperação ─────────────────────────────────────────
#
# Um por carrinho. Fica aqui para a mensagem sair sempre com o mesmo código,
# para o cupom não ser criado duas vezes, e para no fim do mês dar para
# contar quantos nasceram e quantos foram usados.

TABELA_CUPONS = "cupons_recuperacao"


def garantir_cupons() -> None:
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_CUPONS} (
                carrinho    TEXT PRIMARY KEY,
                codigo      TEXT NOT NULL,
                cliente     TEXT,
                email       TEXT,
                shopify_id  TEXT,
                criado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
                expira_em   TIMESTAMPTZ NOT NULL
            )
        """))


def cupom_do_carrinho(carrinho: str):
    if not configurado() or not carrinho:
        return None
    from sqlalchemy import text
    try:
        garantir_cupons()
        with _conectar().connect() as con:
            l = con.execute(text(
                f"SELECT codigo, criado_em, expira_em FROM {TABELA_CUPONS} WHERE carrinho = :c"),
                {"c": carrinho}).mappings().first()
        return dict(l) if l else None
    except Exception:
        return None


def guardar_cupom(carrinho: str, codigo: str, cliente: str, email: str, shopify_id: str, expira_em) -> bool:
    if not configurado():
        return False
    from sqlalchemy import text
    try:
        garantir_cupons()
        with _conectar().begin() as con:
            con.execute(text(
                f"INSERT INTO {TABELA_CUPONS} (carrinho, codigo, cliente, email, shopify_id, expira_em)"
                f" VALUES (:c, :cod, :cli, :e, :s, :x) ON CONFLICT (carrinho) DO NOTHING"),
                {"c": carrinho, "cod": codigo, "cli": cliente, "e": email, "s": shopify_id, "x": expira_em})
        return True
    except Exception:
        return False


def codigos_de_cupom() -> set:
    if not configurado():
        return set()
    from sqlalchemy import text
    try:
        garantir_cupons()
        with _conectar().connect() as con:
            return {l[0] for l in con.execute(text(f"SELECT codigo FROM {TABELA_CUPONS}")).all()}
    except Exception:
        return set()


# ─── Cliques nos links de campanha ──────────────────────────────────────────
#
# A página da campanha (tema/catalogo-entretempos.liquid) grava aqui cada
# visita com o nome do link (?n=) e cada clique em comprar, direto do
# navegador da cliente com a chave pública do Supabase. O banco só aceita
# INSERT dessa chave, com campos curtos; ninguém lê nada pelo site. Quem lê é
# o portal, pela conexão direta. Pedido do Pedro em 09/10/2026, para saber
# quem abriu o link e não só quem usou o cupom.

TABELA_CLIQUES = "cliques_campanha"


def garantir_cliques() -> None:
    from sqlalchemy import text
    with _conectar().begin() as con:
        con.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_CLIQUES} (
                id         BIGSERIAL PRIMARY KEY,
                criado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
                campanha   TEXT NOT NULL,
                nome       TEXT,
                evento     TEXT NOT NULL,
                produto    TEXT,
                aparelho   TEXT
            )
        """))
        con.execute(text(f"ALTER TABLE {TABELA_CLIQUES} ENABLE ROW LEVEL SECURITY"))
        con.execute(text(f"""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = '{TABELA_CLIQUES}'
                             AND policyname = 'site_so_grava') THEN
                CREATE POLICY site_so_grava ON {TABELA_CLIQUES} FOR INSERT TO anon
                  WITH CHECK (campanha ~ '^[a-z0-9-]{{1,30}}$'
                              AND evento IN ('visita', 'comprar', 'ver_loja')
                              AND coalesce(length(nome), 0) <= 40
                              AND coalesce(length(produto), 0) <= 120
                              AND coalesce(length(aparelho), 0) <= 20);
              END IF;
            END $$
        """))
        con.execute(text(f"GRANT INSERT ON {TABELA_CLIQUES} TO anon"))
        con.execute(text(f"GRANT USAGE ON SEQUENCE {TABELA_CLIQUES}_id_seq TO anon"))
        con.execute(text(f"REVOKE SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON {TABELA_CLIQUES} FROM anon"))
        con.execute(text(f"REVOKE ALL ON {TABELA_CLIQUES} FROM authenticated"))


def ler_cliques(campanha: str) -> List[dict]:
    """Visitas e cliques da campanha, do mais recente ao mais antigo."""
    if not configurado():
        return []
    from sqlalchemy import text
    try:
        garantir_cliques()
        with _conectar().connect() as con:
            return [dict(l) for l in con.execute(text(
                f"SELECT criado_em, nome, evento, produto, aparelho FROM {TABELA_CLIQUES}"
                f" WHERE campanha = :c ORDER BY criado_em DESC"), {"c": campanha}).mappings().all()]
    except Exception:
        return []
