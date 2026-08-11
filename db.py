"""
Banco de dados local SQLite para persistir operações de saldo e recebíveis.
Usa upsert por id para evitar duplicação em sincronizações repetidas.
"""

import sqlite3
import os
from datetime import date, timedelta
from typing import List


def _next_day(day_str: str) -> str:
    """Retorna o dia seguinte (YYYY-MM-DD) para limite exclusivo de intervalo."""
    return (date.fromisoformat(day_str[:10]) + timedelta(days=1)).isoformat()

DB_PATH = os.path.join(os.path.dirname(__file__), "dados.db")


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    """Cria as tabelas se não existirem."""
    con = _conn()
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS balance_operations (
            id TEXT PRIMARY KEY,
            status TEXT,
            type TEXT,
            amount INTEGER,
            fee INTEGER,
            created_at TEXT,
            recipient_id TEXT,
            movement_object_id TEXT,
            movement_object_type TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS payables (
            id TEXT PRIMARY KEY,
            status TEXT,
            type TEXT,
            amount INTEGER,
            fee INTEGER,
            created_at TEXT,
            payment_date TEXT,
            recipient_id TEXT,
            payment_method TEXT,
            charge_id TEXT,
            order_id TEXT,
            installment INTEGER,
            anticipation_fee INTEGER DEFAULT 0,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS charges (
            id TEXT PRIMARY KEY,
            code TEXT,
            status TEXT,
            payment_method TEXT,
            amount INTEGER,
            paid_amount INTEGER,
            installments INTEGER,
            customer_name TEXT,
            created_at TEXT,
            paid_at TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS abandoned_checkouts (
            id TEXT PRIMARY KEY,
            nome TEXT,
            criado_em TEXT,
            url_recuperacao TEXT,
            valor INTEGER,
            cliente TEXT,
            email TEXT,
            telefone TEXT,
            pedidos_anteriores INTEGER,
            itens TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS shopify_orders (
            id TEXT PRIMARY KEY,
            numero TEXT,
            criado_em TEXT,
            email TEXT,
            cliente TEXT,
            cidade TEXT,
            uf TEXT,
            itens TEXT,
            cupom TEXT,
            total INTEGER,
            situacao TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TEXT DEFAULT (datetime('now')),
            table_name TEXT,
            records_upserted INTEGER
        );
    """)

    # Migração para bancos criados antes da coluna anticipation_fee existir.
    cols = {r[1] for r in cur.execute("PRAGMA table_info(payables)")}
    if "anticipation_fee" not in cols:
        cur.execute("ALTER TABLE payables ADD COLUMN anticipation_fee INTEGER DEFAULT 0")
    cur.execute("""
        UPDATE payables
           SET anticipation_fee = COALESCE(json_extract(raw_json, '$.anticipation_fee'), 0)
         WHERE COALESCE(anticipation_fee, 0) = 0
    """)

    # `liquidation_arrangement_id` é o que liga uma liquidação de antecipação
    # ao recebível que ela pagou, sem isso o extrato só consegue dizer
    # "liquidação de recebíveis", sem apontar a venda.
    if "arranjo" not in cols:
        cur.execute("ALTER TABLE payables ADD COLUMN arranjo TEXT")
    cur.execute("""
        UPDATE payables
           SET arranjo = json_extract(raw_json, '$.liquidation_arrangement_id')
         WHERE arranjo IS NULL
    """)
    # E-mail do cliente: chave de cruzamento com o checkout abandonado muito
    # mais confiável que o nome, que varia com acento e grafia.
    if "customer_email" not in {r[1] for r in cur.execute("PRAGMA table_info(charges)")}:
        cur.execute("ALTER TABLE charges ADD COLUMN customer_email TEXT")
    cur.execute("""
        UPDATE charges
           SET customer_email = lower(json_extract(raw_json, '$.customer.email'))
         WHERE customer_email IS NULL
    """)

    # Cupom que a própria cliente digitou no checkout. Sem guardar isso, a
    # mensagem de recuperação oferecia VOLTE5 para quem já tinha FRETEGRATIS,
    # que costuma valer mais e não acumula, ou seja, oferecia uma piora.
    cols_ab = {r[1] for r in cur.execute("PRAGMA table_info(abandoned_checkouts)")}
    if "cupom" not in cols_ab:
        cur.execute("ALTER TABLE abandoned_checkouts ADD COLUMN cupom TEXT")
    if "desconto" not in cols_ab:
        cur.execute("ALTER TABLE abandoned_checkouts ADD COLUMN desconto INTEGER DEFAULT 0")

    cols_op = {r[1] for r in cur.execute("PRAGMA table_info(balance_operations)")}
    if "arranjo" not in cols_op:
        cur.execute("ALTER TABLE balance_operations ADD COLUMN arranjo TEXT")
    cur.execute("""
        UPDATE balance_operations
           SET arranjo = json_extract(raw_json, '$.movement_object.liquidation_arrangement_id')
         WHERE arranjo IS NULL
    """)

    con.commit()
    con.close()


def upsert_balance_operations(items: List[dict]) -> int:
    """Insere ou atualiza operações de saldo. Retorna quantidade upserted."""
    import json
    con = _conn()
    cur = con.cursor()
    count = 0
    for item in items:
        mov = item.get("movement_object", {}) or {}
        cur.execute("""
            INSERT INTO balance_operations
                (id, status, type, amount, fee, created_at, recipient_id,
                 movement_object_id, movement_object_type, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                type=excluded.type,
                amount=excluded.amount,
                fee=excluded.fee,
                created_at=excluded.created_at,
                recipient_id=excluded.recipient_id,
                movement_object_id=excluded.movement_object_id,
                movement_object_type=excluded.movement_object_type,
                raw_json=excluded.raw_json
        """, (
            item.get("id", ""),
            item.get("status", ""),
            item.get("type", ""),
            item.get("amount", 0),
            item.get("fee", 0),
            item.get("created_at", ""),
            item.get("recipient_id", ""),
            mov.get("id", ""),
            mov.get("object", ""),
            json.dumps(item, ensure_ascii=False),
        ))
        count += 1
    con.commit()
    con.close()
    _log_sync("balance_operations", count)
    return count


def upsert_payables(items: List[dict]) -> int:
    """Insere ou atualiza recebíveis. Retorna quantidade upserted."""
    import json
    con = _conn()
    cur = con.cursor()
    count = 0
    for item in items:
        cur.execute("""
            INSERT INTO payables
                (id, status, type, amount, fee, created_at, payment_date,
                 recipient_id, payment_method, charge_id, order_id, installment,
                 anticipation_fee, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                type=excluded.type,
                amount=excluded.amount,
                fee=excluded.fee,
                created_at=excluded.created_at,
                payment_date=excluded.payment_date,
                recipient_id=excluded.recipient_id,
                payment_method=excluded.payment_method,
                charge_id=excluded.charge_id,
                order_id=excluded.order_id,
                installment=excluded.installment,
                anticipation_fee=excluded.anticipation_fee,
                raw_json=excluded.raw_json
        """, (
            item.get("id", ""),
            item.get("status", ""),
            item.get("type", ""),
            item.get("amount", 0),
            item.get("fee", 0),
            item.get("created_at", ""),
            item.get("payment_date", ""),
            item.get("recipient_id", ""),
            item.get("payment_method", ""),
            item.get("charge_id", ""),
            item.get("order_id", ""),
            item.get("installment", 1),
            item.get("anticipation_fee", 0) or 0,
            json.dumps(item, ensure_ascii=False),
        ))
        count += 1
    con.commit()
    con.close()
    _log_sync("payables", count)
    return count


def upsert_charges(items: List[dict]) -> int:
    """Insere ou atualiza cobranças (vendas). Retorna quantidade upserted."""
    import json
    con = _conn()
    cur = con.cursor()
    count = 0
    for item in items:
        lt = item.get("last_transaction") or {}
        cust = item.get("customer") or {}
        cur.execute("""
            INSERT INTO charges
                (id, code, status, payment_method, amount, paid_amount,
                 installments, customer_name, created_at, paid_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                code=excluded.code,
                status=excluded.status,
                payment_method=excluded.payment_method,
                amount=excluded.amount,
                paid_amount=excluded.paid_amount,
                installments=excluded.installments,
                customer_name=excluded.customer_name,
                created_at=excluded.created_at,
                paid_at=excluded.paid_at,
                raw_json=excluded.raw_json
        """, (
            item.get("id", ""),
            item.get("code", ""),
            item.get("status", ""),
            item.get("payment_method", ""),
            item.get("amount", 0),
            item.get("paid_amount", 0) or 0,
            lt.get("installments") or 1,
            cust.get("name", ""),
            item.get("created_at", ""),
            item.get("paid_at", "") or "",
            json.dumps(item, ensure_ascii=False),
        ))
        count += 1
    con.commit()
    con.close()
    _log_sync("charges", count)
    return count


def upsert_pedidos(itens: List[dict]) -> int:
    """Grava pedidos da Shopify: é de onde saem peça, tamanho e cidade.

    A Pagar.me não tem nada disso. Ela guarda o dinheiro, não o que foi
    vendido nem para onde foi: das 78 cobranças pagas, zero têm endereço.
    """
    import json
    con = _conn()
    cur = con.cursor()
    n = 0
    for it in itens:
        c = it.get("customer") or {}
        end = it.get("shippingAddress") or {}
        partes = []
        for li in ((it.get("lineItems") or {}).get("nodes") or []):
            opcoes = ((li.get("variant") or {}).get("selectedOptions")) or []
            detalhe = "/".join(o.get("value", "") for o in opcoes if o.get("value"))
            partes.append(
                f"{li.get('quantity')}x {li.get('title')}"
                + (f" ({detalhe})" if detalhe else "")
            )
        total = (it.get("totalPriceSet") or {}).get("shopMoney", {}).get("amount") or "0"
        cur.execute("""
            INSERT INTO shopify_orders
                (id, numero, criado_em, email, cliente, cidade, uf, itens,
                 cupom, total, situacao, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                numero=excluded.numero, criado_em=excluded.criado_em,
                email=excluded.email, cliente=excluded.cliente,
                cidade=excluded.cidade, uf=excluded.uf, itens=excluded.itens,
                cupom=excluded.cupom, total=excluded.total,
                situacao=excluded.situacao, raw_json=excluded.raw_json
        """, (
            it.get("id", ""),
            it.get("name", ""),
            it.get("createdAt", ""),
            (c.get("email") or "").strip().lower(),
            (c.get("displayName") or "").strip(),
            (end.get("city") or "").strip(),
            (end.get("provinceCode") or end.get("province") or "").strip(),
            ", ".join(partes),
            ", ".join(it.get("discountCodes") or []),
            int(round(float(total) * 100)),
            it.get("displayFinancialStatus", ""),
            json.dumps(it, ensure_ascii=False),
        ))
        n += 1
    con.commit()
    con.close()
    _log_sync("shopify_orders", n)
    return n


def alcance_pedidos() -> str:
    """Data do pedido mais antigo que a Shopify deixa ver. '' se não houver."""
    con = _conn()
    r = con.execute("SELECT MIN(substr(criado_em,1,10)) FROM shopify_orders").fetchone()
    con.close()
    return (r[0] or "") if r else ""


def upsert_abandonados(itens: List[dict]) -> int:
    """Grava checkouts abandonados vindos da Shopify."""
    import json
    con = _conn()
    cur = con.cursor()
    n = 0
    for it in itens:
        c = it.get("customer") or {}
        linhas = (it.get("lineItems") or {}).get("nodes") or []
        itens_txt = ", ".join(
            f"{li.get('quantity')}x {li.get('title')}" for li in linhas
        )
        valor = it.get("totalPriceSet", {}).get("shopMoney", {}).get("amount") or "0"
        desconto = (it.get("totalDiscountSet") or {}).get("shopMoney", {}).get("amount") or "0"
        cur.execute("""
            INSERT INTO abandoned_checkouts
                (id, nome, criado_em, url_recuperacao, valor, cliente, email,
                 telefone, pedidos_anteriores, itens, cupom, desconto, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nome=excluded.nome, criado_em=excluded.criado_em,
                url_recuperacao=excluded.url_recuperacao, valor=excluded.valor,
                cliente=excluded.cliente, email=excluded.email,
                telefone=excluded.telefone,
                pedidos_anteriores=excluded.pedidos_anteriores,
                itens=excluded.itens, cupom=excluded.cupom,
                desconto=excluded.desconto, raw_json=excluded.raw_json
        """, (
            it.get("id", ""),
            it.get("name", ""),
            it.get("createdAt", ""),
            it.get("abandonedCheckoutUrl", ""),
            int(round(float(valor) * 100)),
            (c.get("displayName") or "").strip(),
            (c.get("email") or "").strip().lower(),
            # O telefone quase nunca está no cadastro do cliente; vem do
            # endereço, que o checkout exige. Consultar só customer.phone dá a
            # impressão errada de que a loja não coleta telefone.
            ((c.get("phone") or "")
             or ((it.get("shippingAddress") or {}).get("phone") or "")
             or ((it.get("billingAddress") or {}).get("phone") or "")).strip(),
            c.get("numberOfOrders") or 0,
            itens_txt,
            # A Shopify devolve lista; na prática vem no máximo um cupom.
            ", ".join(it.get("discountCodes") or []),
            int(round(float(desconto) * 100)),
            json.dumps(it, ensure_ascii=False),
        ))
        n += 1
    con.commit()
    con.close()
    _log_sync("abandoned_checkouts", n)
    return n


def _normalizar(texto: str) -> str:
    """Nome comparável: sem acento, sem caixa, sem espaço duplicado.

    'Andréa Fontoura' e 'Andrea Fontoura' são a mesma pessoa para a Pagar.me e
    para a Shopify, mas não para uma comparação literal.
    """
    import unicodedata
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


def clientes() -> "list[dict]":
    """Uma linha por pessoa que já comprou, com quanto vale e há quanto sumiu.

    Só entra cobrança paga: quem tentou e não passou não virou cliente, e
    contá-la aqui inflaria a base com gente que a loja nunca atendeu.

    A identidade é o e-mail, que está em todas as cobranças desta base. Nome
    normalizado fica de reserva para cobrança sem e-mail. A ressalva honesta é
    que a mesma pessoa com dois e-mails conta como duas, não há como saber.

    O telefone sai do `raw_json`, onde a Pagar.me guarda o celular do
    comprador. Não tem coluna própria porque é o único lugar que precisa dele.
    """
    import json

    con = _conn()
    con.row_factory = sqlite3.Row
    linhas = con.execute(
        "SELECT customer_name, customer_email, amount, paid_amount, created_at, "
        "code, payment_method, installments, raw_json "
        "FROM charges WHERE status = 'paid' ORDER BY created_at"
    ).fetchall()
    con.close()

    pessoas = {}
    for r in linhas:
        email = (r["customer_email"] or "").strip().lower()
        chave = email or "nome:" + _normalizar(r["customer_name"])
        if chave == "nome:":
            continue  # cobrança sem e-mail e sem nome: não dá para atribuir

        try:
            bruto = json.loads(r["raw_json"] or "{}")
        except Exception:
            bruto = {}
        fones = ((bruto.get("customer") or {}).get("phones") or {})
        cel = fones.get("mobile_phone") or fones.get("home_phone") or {}
        fone = f"{cel.get('country_code','')}{cel.get('area_code','')}{cel.get('number','')}"

        p = pessoas.setdefault(chave, {
            "nome": "", "email": email, "telefone": "",
            "compras": 0, "total": 0, "primeira": "", "ultima": "", "pedidos": [],
        })
        # As cobranças vêm em ordem crescente, então o último nome e telefone
        # que passam por aqui são os mais recentes que a cliente cadastrou.
        p["nome"] = (r["customer_name"] or "").strip() or p["nome"]
        p["telefone"] = fone or p["telefone"]
        valor = r["paid_amount"] or r["amount"] or 0
        dia = (r["created_at"] or "")[:10]
        # Uma cobrança paga é um pedido: nesta base as 78 cobranças pagas
        # correspondem a 78 pedidos distintos, então contar cobrança é contar
        # vez que a cliente comprou, não peça nem parcela.
        p["compras"] += 1
        p["total"] += valor
        p["primeira"] = p["primeira"] or dia
        p["ultima"] = dia
        p["pedidos"].append({
            "dia": dia, "valor": valor, "codigo": r["code"] or "",
            "metodo": r["payment_method"] or "", "parcelas": r["installments"] or 1,
        })

    # Peça, tamanho e cidade vêm da Shopify e só existem dentro da janela que
    # ela devolve. O casamento é por dia e e-mail, porque a cobrança da
    # Pagar.me e o pedido da Shopify não compartilham identificador.
    con = _conn()
    con.row_factory = sqlite3.Row
    pedidos_loja = con.execute(
        "SELECT email, cidade, uf, itens, numero, criado_em FROM shopify_orders"
    ).fetchall()
    limite_loja = (con.execute(
        "SELECT MIN(substr(criado_em,1,10)) FROM shopify_orders"
    ).fetchone() or [""])[0] or ""
    con.close()

    por_dia = {}
    for o in pedidos_loja:
        por_dia[((o["email"] or "").lower(), (o["criado_em"] or "")[:10])] = o

    for chave, p in pessoas.items():
        p["cidade"] = ""
        p["uf"] = ""
        p["limite_loja"] = limite_loja
        for ped in p["pedidos"]:
            o = por_dia.get((p["email"], ped["dia"]))
            ped["itens"] = o["itens"] if o else ""
            ped["numero"] = o["numero"] if o else ""
            # Distingue "a loja não devolve esse período" de "pedido sem itens".
            ped["fora_do_alcance"] = bool(limite_loja) and ped["dia"] < limite_loja
            if o and o["cidade"] and not p["cidade"]:
                p["cidade"], p["uf"] = o["cidade"], o["uf"]

    hoje = date.today()
    for p in pessoas.values():
        try:
            p["dias_sem_comprar"] = (hoje - date.fromisoformat(p["ultima"])).days
        except Exception:
            p["dias_sem_comprar"] = None

    return sorted(pessoas.values(), key=lambda p: -p["total"])


def abandonados_classificados(dias: int = 90) -> "list[dict]":
    """Checkouts abandonados cruzados com as cobranças da Pagar.me.

    O ponto da tela: a lista crua da Shopify inclui gente que abandonou e
    comprou depois. Mandar mensagem para quem já pagou constrange a cliente e
    gasta o tempo de quem atende. Aqui cada abandono é classificado:

      Já comprou           , existe cobrança paga da mesma pessoa em janela próxima
      Tentou e não passou  , tentou pagar e não passou; precisa de ajuda, não de convencimento
      Não tentou pagar     , não há cobrança nenhuma; é oportunidade de verdade

    O cruzamento é por e-mail quando existe, com nome normalizado de reserva.
    Na dúvida a linha continua aparecendo, marcada, esconder lead real custa
    mais caro que mostrar um a mais.
    """
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT customer_name, customer_email, amount, status, created_at FROM charges")
    cobrancas = [dict(r) for r in cur.fetchall()]
    por_email, por_nome = {}, {}
    for c in cobrancas:
        if c["customer_email"]:
            por_email.setdefault(c["customer_email"], []).append(c)
        n = _normalizar(c["customer_name"])
        if n:
            por_nome.setdefault(n, []).append(c)

    corte = (date.today() - timedelta(days=dias)).isoformat()
    cur.execute(
        "SELECT * FROM abandoned_checkouts WHERE substr(criado_em,1,10) >= ? "
        "ORDER BY criado_em DESC", (corte,)
    )
    linhas = []
    for r in cur.fetchall():
        a = dict(r)
        candidatas = por_email.get(a["email"]) or por_nome.get(_normalizar(a["cliente"])) or []
        # Só cobranças a partir do dia do abandono: compra anterior é outra venda.
        dia = (a["criado_em"] or "")[:10]
        proximas = [c for c in candidatas if (c["created_at"] or "")[:10] >= dia]

        if any(c["status"] == "paid" for c in proximas):
            a["situacao"] = "Já comprou"
        elif any(c["status"] in ("failed", "canceled") for c in proximas):
            a["situacao"] = "Tentou e não passou"
        else:
            a["situacao"] = "Não tentou pagar"
        a["por_email"] = bool(por_email.get(a["email"]))
        a["tentativas"] = len(proximas)
        linhas.append(a)

    con.close()
    return linhas


def query_charges(
    date_from: str = None,
    date_to: str = None,
    status: str = None,
    payment_method: str = None,
) -> "list[dict]":
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    sql = "SELECT * FROM charges WHERE 1=1"
    params = []
    if date_from:
        sql += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND created_at < ?"
        params.append(_next_day(date_to))
    if status:
        sql += " AND status = ?"
        params.append(status)
    if payment_method:
        sql += " AND payment_method = ?"
        params.append(payment_method)
    sql += " ORDER BY created_at DESC"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def custo_das_vendas(date_from: str = None, date_to: str = None) -> dict:
    """A ponte entre 'quanto vendeu' e 'quanto recebe'.

    Parte dos recebíveis (uma venda parcelada vira N recebíveis) e abre
    bruto → MDR → antecipação → líquido. Filtra pela data da VENDA, não pela
    data de liquidação.

    O corte é feito pelas COBRANÇAS do período, não pela data dos recebíveis:
    amarrando os dois lados pelo charge_id, o bruto da ponte é exatamente o
    total vendido mostrado acima dela. Filtrar cada lado pela sua própria data
    faz os números divergirem (uma venda criada num dia e capturada no
    seguinte cai em janelas diferentes).
    """
    con = _conn()
    cur = con.cursor()
    sql = """
        SELECT COALESCE(SUM(p.amount),0), COALESCE(SUM(p.fee),0),
               COALESCE(SUM(p.anticipation_fee),0), COUNT(*)
          FROM payables p
          JOIN charges c ON c.id = p.charge_id
         WHERE c.status = 'paid'
    """
    params = []
    if date_from:
        sql += " AND c.created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND c.created_at < ?"
        params.append(_next_day(date_to))
    cur.execute(sql, params)
    bruto, mdr, antec, n = cur.fetchone()
    con.close()
    return {
        "bruto": bruto,
        "mdr": mdr,
        "antecipacao": antec,
        "liquido": bruto - mdr - antec,
        "parcelas": n,
        "custo_pct": ((mdr + antec) / bruto * 100) if bruto else 0.0,
    }


def extrato_bancario(recipient_id: str = None) -> "list[dict]":
    """Lançamentos que de fato mexeram no saldo, do mais antigo ao mais novo.

    Só status 'available': os outros são contrapartida contábil (recebível
    futuro, contrapartida de transferência) e não movimentam a conta.

    Traz junto o recebível e a venda de origem quando existem, para o extrato
    poder dizer "liquidação da venda da Fulana" em vez de um id solto. O LEFT
    JOIN falha de propósito em transferências e tarifas, que não têm venda.
    """
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    # Dois caminhos até a venda, porque o lançamento aponta para coisas
    # diferentes conforme o tipo: recebível liquidado normalmente traz o id do
    # próprio recebível; liquidação de antecipação traz um arranjo de
    # liquidação, que é a chave que o recebível também carrega.
    sql = """
        SELECT o.id, o.created_at, o.type, o.amount, o.fee,
               o.amount - o.fee AS valor,
               o.movement_object_id, o.movement_object_type,
               COALESCE(p.type, pa.type)                     AS tipo_recebivel,
               COALESCE(p.payment_method, pa.payment_method)  AS payment_method,
               COALESCE(p.installment, pa.installment)        AS installment,
               COALESCE(c.customer_name, ca.customer_name)    AS customer_name,
               COALESCE(c.amount, ca.amount)                  AS venda_total,
               COALESCE(c.installments, ca.installments)      AS venda_parcelas,
               json_extract(o.raw_json, '$.movement_object.brand')       AS bandeira,
               json_extract(o.raw_json, '$.movement_object.description') AS descricao_tarifa
          FROM balance_operations o
          LEFT JOIN payables p  ON p.id = o.movement_object_id
          LEFT JOIN charges  c  ON c.id = p.charge_id
          -- Um arranjo pode casar com mais de um recebível (o original e sua
          -- reversão). Sem reduzir a um só, o LEFT JOIN duplica o lançamento e
          -- o saldo corrido passa a somar o mesmo valor duas vezes.
          LEFT JOIN (
                SELECT arranjo, MIN(id) AS pid
                  FROM payables
                 WHERE arranjo IS NOT NULL AND amount > 0
                 GROUP BY arranjo
          ) ar ON ar.arranjo = o.arranjo
          LEFT JOIN payables pa ON pa.id = ar.pid
          LEFT JOIN charges  ca ON ca.id = pa.charge_id
         WHERE o.status = 'available'
    """
    params = []
    if recipient_id:
        sql += " AND o.recipient_id = ?"
        params.append(recipient_id)
    sql += " ORDER BY o.created_at, o.id"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def resumo_mensal() -> "list[dict]":
    """Série mensal de todo o histórico, sem recorte de período.

    Duas consultas em vez de um JOIN só: juntar payables a charges multiplica
    as linhas (uma venda parcelada vira N recebíveis) e inflaria a contagem
    de vendas. Cada lado é agregado no seu grão e depois casado por mês.

    O mês sai em horário de Brasília: sem o ajuste, venda feita depois das 21h
    do último dia do mês cairia no mês seguinte.
    """
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        SELECT strftime('%Y-%m', datetime(created_at, '-3 hours')) AS mes,
               SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) AS faturamento,
               SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS vendas,
               COUNT(*) AS tentativas
          FROM charges
         GROUP BY mes
    """)
    meses = {r["mes"]: dict(r) for r in cur.fetchall()}

    cur.execute("""
        SELECT strftime('%Y-%m', datetime(c.created_at, '-3 hours')) AS mes,
               SUM(p.fee) AS mdr,
               SUM(COALESCE(p.anticipation_fee, 0)) AS antecipacao
          FROM payables p
          JOIN charges c ON c.id = p.charge_id
         WHERE c.status = 'paid'
         GROUP BY mes
    """)
    custos = {r["mes"]: dict(r) for r in cur.fetchall()}
    con.close()

    linhas = []
    for mes in sorted(meses):
        m = meses[mes]
        c = custos.get(mes, {})
        fat = m["faturamento"] or 0
        mdr = c.get("mdr") or 0
        antec = c.get("antecipacao") or 0
        linhas.append({
            "mes": mes,
            "faturamento": fat,
            "vendas": m["vendas"] or 0,
            "tentativas": m["tentativas"] or 0,
            "ticket": int(fat / m["vendas"]) if m["vendas"] else 0,
            "aprovacao": (m["vendas"] / m["tentativas"] * 100) if m["tentativas"] else 0,
            "custo": mdr + antec,
            "custo_pct": ((mdr + antec) / fat * 100) if fat else 0,
            "liquido": fat - mdr - antec,
        })
    return linhas


def a_receber_detalhado(recipient_id: str = None) -> "list[dict]":
    """Parcelas a receber com a venda que as originou.

    O recebível sozinho não diz de quem é, o nome do cliente e o valor da
    compra vivem em `charges`, alcançados pelo charge_id. LEFT JOIN porque
    a venda pode ser anterior à janela sincronizada.
    """
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    sql = """
        SELECT p.payment_date, p.created_at, p.payment_method,
               p.amount, p.fee, COALESCE(p.anticipation_fee, 0) AS anticipation_fee,
               p.amount - p.fee - COALESCE(p.anticipation_fee, 0) AS liquido,
               p.installment,
               c.customer_name, c.amount AS venda_total, c.installments AS venda_parcelas
          FROM payables p
          LEFT JOIN charges c ON c.id = p.charge_id
         WHERE p.status = 'waiting_funds'
    """
    params = []
    if recipient_id:
        sql += " AND p.recipient_id = ?"
        params.append(recipient_id)
    sql += " ORDER BY p.payment_date, c.customer_name, p.installment"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def custo_por_meio(date_from: str = None, date_to: str = None) -> "list[dict]":
    """Taxa efetivamente paga, por meio de pagamento, no período.

    Mesmo corte por cobrança de custo_das_vendas, para os números baterem.
    """
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    sql = """
        SELECT p.payment_method AS meio,
               SUM(p.amount) AS bruto,
               SUM(p.fee) AS mdr,
               SUM(COALESCE(p.anticipation_fee, 0)) AS antec,
               COUNT(*) AS parcelas
          FROM payables p
          JOIN charges c ON c.id = p.charge_id
         WHERE c.status = 'paid'
    """
    params = []
    if date_from:
        sql += " AND c.created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND c.created_at < ?"
        params.append(_next_day(date_to))
    sql += " GROUP BY p.payment_method HAVING bruto > 0 ORDER BY bruto DESC"
    cur.execute(sql, params)
    linhas = []
    for r in cur.fetchall():
        d = dict(r)
        d["mdr_pct"] = d["mdr"] / d["bruto"] * 100
        d["antec_pct"] = d["antec"] / d["bruto"] * 100
        d["total_pct"] = (d["mdr"] + d["antec"]) / d["bruto"] * 100
        linhas.append(d)
    con.close()
    return linhas


def agenda_recebimentos(recipient_id: str = None) -> "list[dict]":
    """Quando cada valor cai na conta, agrupado por data de liquidação.

    Inclui 'prepaid' porque os estornos de antecipação dão baixa nos
    recebíveis já antecipados que ainda constam como pendentes.
    """
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    sql = """
        SELECT substr(payment_date, 1, 10) AS dia,
               SUM(amount - fee - COALESCE(anticipation_fee, 0)) AS liquido,
               COUNT(*) AS parcelas
          FROM payables
         WHERE status IN ('waiting_funds', 'prepaid')
    """
    params = []
    if recipient_id:
        sql += " AND recipient_id = ?"
        params.append(recipient_id)
    sql += " GROUP BY dia HAVING liquido != 0 ORDER BY dia"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def _log_sync(table_name: str, count: int):
    con = _conn()
    con.execute(
        "INSERT INTO sync_log (table_name, records_upserted) VALUES (?, ?)",
        (table_name, count),
    )
    con.commit()
    con.close()


def query_balance_operations(
    date_from: str = None,
    date_to: str = None,
    status: str = None,
    type_filter: str = None,
    recipient_id: str = None,
) -> "list[dict]":
    import json
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    sql = "SELECT * FROM balance_operations WHERE 1=1"
    params = []
    if date_from:
        sql += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND created_at < ?"
        params.append(_next_day(date_to))
    if status:
        sql += " AND status = ?"
        params.append(status)
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if recipient_id:
        sql += " AND recipient_id = ?"
        params.append(recipient_id)
    sql += " ORDER BY created_at DESC"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def query_payables(
    date_from: str = None,
    date_to: str = None,
    payment_date_from: str = None,
    payment_date_to: str = None,
    status: str = None,
    type_filter: str = None,
    recipient_id: str = None,
) -> "list[dict]":
    import json
    con = _conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    sql = "SELECT * FROM payables WHERE 1=1"
    params = []
    if date_from:
        sql += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND created_at < ?"
        params.append(_next_day(date_to))
    if payment_date_from:
        sql += " AND payment_date >= ?"
        params.append(payment_date_from)
    if payment_date_to:
        sql += " AND payment_date < ?"
        params.append(_next_day(payment_date_to))
    if status:
        sql += " AND status = ?"
        params.append(status)
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if recipient_id:
        sql += " AND recipient_id = ?"
        params.append(recipient_id)
    sql += " ORDER BY payment_date DESC, created_at DESC"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def compute_a_receber(recipient_id: str = None) -> dict:
    """Reproduz o card 'A receber' do Dash a partir dos dados locais.

    Três componentes, todos fora de qualquer filtro de período (o saldo futuro
    do portal é total, não recortado por data):
      1. recebíveis pendentes, líquidos de taxa E de taxa de antecipação;
      2. estornos de antecipação (status 'prepaid'), que dão baixa nos
         recebíveis já antecipados mas ainda marcados como pendentes;
      3. tarifas lançadas e ainda não cobradas (fee_collection pendente).
    """
    con = _conn()
    cur = con.cursor()

    where_recip = " AND recipient_id = ?" if recipient_id else ""
    args = [recipient_id] if recipient_id else []

    cur.execute(
        "SELECT COALESCE(SUM(amount - fee - COALESCE(anticipation_fee, 0)), 0) "
        "FROM payables WHERE status = 'waiting_funds'" + where_recip, args)
    pendentes = cur.fetchone()[0]

    cur.execute(
        "SELECT COALESCE(SUM(amount - fee - COALESCE(anticipation_fee, 0)), 0) "
        "FROM payables WHERE status = 'prepaid'" + where_recip, args)
    antecipados = cur.fetchone()[0]

    cur.execute(
        "SELECT COALESCE(SUM(amount - fee), 0) FROM balance_operations "
        "WHERE status = 'waiting_funds' AND type = 'fee_collection'" + where_recip, args)
    tarifas = cur.fetchone()[0]

    con.close()
    return {
        "pendentes": pendentes,
        "antecipados": antecipados,
        "tarifas": tarifas,
        "total": pendentes + antecipados + tarifas,
    }


def get_oldest_waiting_payable_date() -> "str | None":
    """Data de criação do recebível pendente mais antigo (para refresh de status)."""
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT MIN(created_at) FROM payables WHERE status = 'waiting_funds'")
    row = cur.fetchone()
    con.close()
    return row[0] if row and row[0] else None


def get_db_counts() -> dict:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM balance_operations")
    ops = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM payables")
    pay = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM charges")
    chg = cur.fetchone()[0]
    cur.execute(
        "SELECT synced_at, table_name, records_upserted FROM sync_log ORDER BY synced_at DESC LIMIT 5"
    )
    logs = cur.fetchall()
    con.close()
    return {"balance_operations": ops, "payables": pay, "charges": chg, "recent_sync": logs}
