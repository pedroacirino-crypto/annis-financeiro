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
    # ao recebível que ela pagou — sem isso o extrato só consegue dizer
    # "liquidação de recebíveis", sem apontar a venda.
    if "arranjo" not in cols:
        cur.execute("ALTER TABLE payables ADD COLUMN arranjo TEXT")
    cur.execute("""
        UPDATE payables
           SET arranjo = json_extract(raw_json, '$.liquidation_arrangement_id')
         WHERE arranjo IS NULL
    """)
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

    O recebível sozinho não diz de quem é — o nome do cliente e o valor da
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
    """Quando cada valor cai na conta — agrupado por data de liquidação.

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
