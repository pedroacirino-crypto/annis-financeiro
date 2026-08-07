"""
Cliente para a API da Pagar.me v5.
Autenticação via HTTP Basic Auth (Secret Key como usuário, senha vazia).
Todas as funções paginam automaticamente até esgotar os resultados.
"""

import os
import time
import requests
from typing import Optional

BASE_URL = "https://api.pagar.me/core/v5"


def _load_dotenv():
    """Carrega variáveis de um arquivo .env ao lado deste módulo (se existir)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _load_streamlit_secrets():
    """Lê a chave do cofre do Streamlit quando rodando hospedado.

    Local a chave vem do .env; na Streamlit Cloud vem de st.secrets, que não
    existe como arquivo. Falha em silêncio de propósito: fora do Streamlit
    (scripts, testes) o import nem sempre está disponível.
    """
    try:
        import streamlit as st
        chave = st.secrets.get("PAGARME_SECRET_KEY")
        if chave:
            os.environ.setdefault("PAGARME_SECRET_KEY", chave)
    except Exception:
        pass


_load_dotenv()
_load_streamlit_secrets()


def _get_auth():
    key = os.environ.get("PAGARME_SECRET_KEY", "")
    return (key, "")


def _get(endpoint: str, params: dict = None, max_retries: int = 3) -> dict:
    """Realiza uma requisição GET com retry automático para 429."""
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, auth=_get_auth(), params=params or {}, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
    # Todas as tentativas esgotadas em 429 — falhar explicitamente para não
    # gravar sincronização parcial como se fosse completa.
    raise RuntimeError(
        f"Limite de requisições da Pagar.me excedido (429) após {max_retries} tentativas em {endpoint}."
    )


def get_default_recipient_id() -> Optional[str]:
    """Descobre o recipient_id padrão da conta (primeiro recebedor)."""
    data = _get("/recipients", {"size": 1, "page": 1})
    items = data.get("data", [])
    if items:
        return items[0].get("id")
    return None


def get_balance(recipient_id: Optional[str] = None) -> dict:
    """Retorna saldo do recebedor (disponível, a receber, transferido).

    Na API v5 o saldo é sempre por recebedor. Se nenhum recipient_id for
    informado, descobre automaticamente o recebedor padrão da conta.
    """
    if not recipient_id:
        recipient_id = get_default_recipient_id()
    if not recipient_id:
        raise ValueError("Nenhum recebedor encontrado na conta.")
    return _get(f"/recipients/{recipient_id}/balance")


def get_balance_operations(
    created_since: Optional[str] = None,
    created_until: Optional[str] = None,
    status: Optional[str] = None,
    recipient_id: Optional[str] = None,
) -> list:
    """
    Busca operações de saldo (extrato) com paginação automática.
    created_since / created_until: strings ISO 8601 (ex: '2024-01-01T00:00:00Z')
    status: waiting_funds | available | transferred

    IMPORTANTE: a API usa status='available' como padrão quando o parâmetro
    é omitido. Por isso, quando status=None, buscamos os 3 status e
    deduplicamos por id — senão o extrato ficaria incompleto.
    """
    if status is None:
        seen = {}
        for st in ("available", "waiting_funds", "transferred"):
            for op in get_balance_operations(created_since, created_until, st, recipient_id):
                seen[op.get("id")] = op
        return list(seen.values())

    all_items = []
    page = 1
    while True:
        params = {"size": 1000, "page": page}
        if created_since:
            params["created_since"] = created_since
        if created_until:
            params["created_until"] = created_until
        if status:
            params["status"] = status
        if recipient_id:
            params["recipient_id"] = recipient_id

        data = _get("/balance/operations", params)
        items = data.get("data", [])
        all_items.extend(items)

        paging = data.get("paging", {})
        if not items or not paging.get("next"):
            break
        page += 1

    return all_items


def get_payables(
    created_since: Optional[str] = None,
    created_until: Optional[str] = None,
    payment_date_since: Optional[str] = None,
    payment_date_until: Optional[str] = None,
    status: Optional[str] = None,
    type_filter: Optional[str] = None,
    recipient_id: Optional[str] = None,
) -> list:
    """
    Busca recebíveis com paginação por cursor.
    status: paid | waiting_funds
    type_filter: chargeback | refund | chargeback_refund | credit
    """
    all_items = []
    cursor = None

    while True:
        params = {"size": 1000}
        if created_since:
            params["created_since"] = created_since
        if created_until:
            params["created_until"] = created_until
        if payment_date_since:
            params["payment_date_since"] = payment_date_since
        if payment_date_until:
            params["payment_date_until"] = payment_date_until
        if status:
            params["status"] = status
        if type_filter:
            params["type"] = type_filter
        if recipient_id:
            params["recipient_id"] = recipient_id
        if cursor:
            params["forward_cursor"] = cursor

        data = _get("/payables", params)
        items = data.get("data", [])
        all_items.extend(items)

        paging = data.get("paging", {})
        cursor = paging.get("forward_cursor")
        if not items or not cursor:
            break

    return all_items


def get_charges(
    created_since: Optional[str] = None,
    created_until: Optional[str] = None,
    status: Optional[str] = None,
) -> list:
    """Busca cobranças (vendas) com paginação automática.

    É a fonte de 'quanto vendeu': o extrato mostra dinheiro que entrou na
    conta, enquanto a cobrança é a venda em si — uma venda parcelada entra
    ao longo de meses, mas é uma venda só, na data em que foi feita.
    status: paid | pending | failed | canceled
    """
    all_items = []
    page = 1
    while True:
        params = {"size": 100, "page": page}
        if created_since:
            params["created_since"] = created_since
        if created_until:
            params["created_until"] = created_until
        if status:
            params["status"] = status

        data = _get("/charges", params)
        items = data.get("data", [])
        all_items.extend(items)

        if not items or not data.get("paging", {}).get("next"):
            break
        page += 1

    return all_items


def get_recipients() -> list:
    """Lista os recebedores disponíveis na conta."""
    all_items = []
    page = 1
    while True:
        data = _get("/recipients", {"size": 100, "page": page})
        items = data.get("data", [])
        all_items.extend(items)
        paging = data.get("paging", {})
        if not items or not paging.get("next"):
            break
        page += 1
    return all_items
