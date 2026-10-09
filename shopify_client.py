"""
Cliente para a Admin API da Shopify (GraphQL).

Apps criados no Dev Dashboard não têm token permanente: o `shpat_` só existia
nos apps antigos feitos pelo admin da loja, que a Shopify descontinuou. Aqui o
fluxo é client credentials, troca-se client id + client secret por um token
de 24h, renovado automaticamente.

Somente leitura: nenhuma mutation é usada.
"""

import os
import time
import requests
from typing import Optional

VERSAO_API = "2026-07"

_token_cache = {"valor": None, "expira_em": 0.0}


def _carregar_env():
    """Carrega o .env ao lado deste módulo, se existir."""
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


def configurado() -> bool:
    return all(_cfg(n) for n in ("SHOPIFY_LOJA", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"))


def _token() -> str:
    """Token de acesso, com cache até 60s antes de expirar."""
    agora = time.time()
    if _token_cache["valor"] and agora < _token_cache["expira_em"]:
        return _token_cache["valor"]

    loja = _cfg("SHOPIFY_LOJA")
    r = requests.post(
        f"https://{loja}.myshopify.com/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": _cfg("SHOPIFY_CLIENT_ID"),
            "client_secret": _cfg("SHOPIFY_CLIENT_SECRET"),
        },
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    _token_cache["valor"] = d["access_token"]
    _token_cache["expira_em"] = agora + int(d.get("expires_in", 86399)) - 60
    return _token_cache["valor"]


def _graphql(consulta: str, variaveis: dict = None) -> dict:
    loja = _cfg("SHOPIFY_LOJA")
    r = requests.post(
        f"https://{loja}.myshopify.com/admin/api/{VERSAO_API}/graphql.json",
        headers={"X-Shopify-Access-Token": _token(), "Content-Type": "application/json"},
        json={"query": consulta, "variables": variaveis or {}},
        timeout=60,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(f"Shopify: {d['errors']}")
    return d["data"]


_CONSULTA_ABANDONADOS = """
query($cursor: String) {
  abandonedCheckouts(first: 50, sortKey: CREATED_AT, reverse: true, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      abandonedCheckoutUrl
      discountCodes
      totalPriceSet { shopMoney { amount } }
      totalDiscountSet { shopMoney { amount } }
      customer { displayName email phone numberOfOrders }
      shippingAddress { phone city province provinceCode }
      billingAddress { phone city province provinceCode }
      lineItems(first: 20) { nodes { title quantity } }
    }
  }
}
"""


_CONSULTA_PEDIDOS = """
query($cursor: String) {
  orders(first: 50, sortKey: CREATED_AT, reverse: true, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      displayFinancialStatus
      totalPriceSet { shopMoney { amount } }
      discountCodes
      customer { email displayName phone }
      shippingAddress { name city province provinceCode zip phone }
      billingAddress { name phone }
      lineItems(first: 30) {
        nodes { title quantity variant { selectedOptions { name value } } }
      }
    }
  }
}
"""


def listar_pedidos(limite: int = 500) -> list:
    """Pedidos, do mais recente para o mais antigo.

    Atenção ao teto: sem o escopo `read_all_orders`, a Shopify só devolve os
    últimos 60 dias, hoje 25 pedidos, enquanto a numeração da loja já passou
    de #1090. Tudo que for mais antigo que isso é invisível para o app, e a
    tela precisa dizer isso em vez de fingir que a cliente não comprou nada.
    """
    itens, cursor = [], None
    while len(itens) < limite:
        d = _graphql(_CONSULTA_PEDIDOS, {"cursor": cursor})
        bloco = d["orders"]
        itens.extend(bloco["nodes"])
        if not bloco["pageInfo"]["hasNextPage"]:
            break
        cursor = bloco["pageInfo"]["endCursor"]
    return itens[:limite]


def listar_abandonados(limite: int = 500) -> list:
    """Checkouts abandonados, do mais recente para o mais antigo.

    Diferente de pedidos, esta consulta não sofre o corte de 60 dias, devolve
    todo o histórico disponível na loja.
    """
    itens, cursor = [], None
    while len(itens) < limite:
        d = _graphql(_CONSULTA_ABANDONADOS, {"cursor": cursor})
        bloco = d["abandonedCheckouts"]
        itens.extend(bloco["nodes"])
        if not bloco["pageInfo"]["hasNextPage"]:
            break
        cursor = bloco["pageInfo"]["endCursor"]
    return itens[:limite]


# ─── Cupom pessoal de recuperação ───────────────────────────────────────────
#
# Um código de desconto na Shopify tem validade única, igual para todo mundo.
# "48 horas a partir do envio" só existe se cada pessoa tiver o seu, criado
# na hora. Escolha do Pedro em 04/10/2026: nome da pessoa mais 10, 10%,
# 48 horas, uso único.

_MUT_CUPOM = """
mutation($d: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $d) {
    codeDiscountNode { id }
    userErrors { field message code }
  }
}
"""

_Q_CLIENTE = """
query($q: String!) { customers(first: 1, query: $q) { nodes { id email } } }
"""

_Q_CODIGO = """
query($c: String!) { codeDiscountNodeByCode(code: $c) { id } }
"""


def codigo_existe(codigo: str) -> bool:
    d = _graphql(_Q_CODIGO, {"c": codigo})
    return bool(d.get("codeDiscountNodeByCode"))


def _id_cliente(email: str):
    if not email:
        return None
    d = _graphql(_Q_CLIENTE, {"q": f"email:{email}"})
    nos = d.get("customers", {}).get("nodes") or []
    return nos[0]["id"] if nos and (nos[0].get("email") or "").lower() == email.lower() else None


def criar_cupom_pessoal(codigo: str, titulo: str, email: str = "", percentual: float = 0.10,
                        horas: int = 48) -> dict:
    """Cria o cupom e devolve {id, codigo, comeca, expira}.

    Restrito à cliente quando ela existe como cliente na loja; quem comprou
    como visitante fica com uso único e uma vez por cliente, que é o mais
    perto que dá. Não acumula com outro desconto, então a cliente escolhe
    entre este e o frete grátis de primeira compra.
    """
    from datetime import datetime, timedelta, timezone
    comeca = datetime.now(timezone.utc)
    # Vence no fim do dia, horário de Brasília, não no meio da tarde: um
    # cupom criado dia 4 às 14h vale até dia 6 às 23:59. Pedido do Pedro em
    # 04/10/2026, para a mensagem dizer só a data.
    brasilia = timezone(timedelta(hours=-3))
    ultimo_dia = (comeca + timedelta(hours=horas)).astimezone(brasilia)
    expira = ultimo_dia.replace(hour=23, minute=59, second=59, microsecond=0).astimezone(timezone.utc)
    cliente = _id_cliente(email)
    selecao = {"customers": {"add": [cliente]}} if cliente else {"all": True}
    entrada = {
        "title": titulo, "code": codigo,
        "startsAt": comeca.isoformat(), "endsAt": expira.isoformat(),
        "usageLimit": 1, "appliesOncePerCustomer": True,
        "customerSelection": selecao,
        "customerGets": {"value": {"percentage": percentual}, "items": {"all": True}},
        "combinesWith": {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False},
    }
    d = _graphql(_MUT_CUPOM, {"d": entrada})
    r = d["discountCodeBasicCreate"]
    if r.get("userErrors"):
        raise RuntimeError(f"Shopify: {r['userErrors']}")
    return {"id": r["codeDiscountNode"]["id"], "codigo": codigo,
            "comeca": comeca, "expira": expira, "restrito": bool(cliente)}
