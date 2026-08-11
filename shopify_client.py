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
      shippingAddress { phone }
      billingAddress { phone }
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
      customer { email displayName }
      shippingAddress { city province provinceCode }
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
