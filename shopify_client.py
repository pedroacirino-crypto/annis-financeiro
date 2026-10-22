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
      tags
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


_CONSULTA_RASTREIOS = """
query($cursor: String) {
  orders(first: 50, sortKey: CREATED_AT, reverse: true, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      name
      createdAt
      cancelledAt
      test
      customer { displayName }
      shippingAddress { name city provinceCode }
      shippingLines(first: 1) { nodes { title } }
      lineItems(first: 10) { nodes { title quantity } }
      fulfillments { createdAt trackingInfo { number } }
    }
  }
}
"""


def pedidos_com_rastreio() -> list:
    """Uma linha por código de rastreio, do pedido mais recente para o mais
    antigo. Mesmo teto de 60 dias de `listar_pedidos`."""
    linhas, cursor = [], None
    while True:
        bloco = _graphql(_CONSULTA_RASTREIOS, {"cursor": cursor})["orders"]
        for o in bloco["nodes"]:
            if o["test"]:
                continue
            end = o.get("shippingAddress") or {}
            frete = (o.get("shippingLines") or {}).get("nodes") or [{}]
            pecas = [f"{i['quantity']}x {i['title']}" if i["quantity"] > 1 else i["title"]
                     for i in o["lineItems"]["nodes"]]
            for f in o.get("fulfillments") or []:
                for t in f.get("trackingInfo") or []:
                    if not t.get("number"):
                        continue
                    linhas.append({
                        "pedido": o["name"], "criado": o["createdAt"], "postado": f["createdAt"],
                        "cliente": end.get("name") or (o.get("customer") or {}).get("displayName") or "",
                        "cidade": " / ".join(x for x in (end.get("city"), end.get("provinceCode")) if x),
                        "frete": frete[0].get("title") or "", "pecas": pecas,
                        "codigo": t["number"].strip().upper(), "cancelado": bool(o.get("cancelledAt")),
                    })
        if not bloco["pageInfo"]["hasNextPage"]:
            break
        cursor = bloco["pageInfo"]["endCursor"]
    return linhas


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

    Uso único e uma vez por cliente, aberto a qualquer comprador. Até
    19/10/2026 ele era restrito à cliente quando ela tinha cadastro na loja,
    mas a Shopify recusava o código no checkout sem login ("não está
    disponível para você neste momento", Daniela Monaco). Não acumula com
    outro desconto, então a cliente escolhe entre este e o frete grátis de
    primeira compra.
    """
    from datetime import datetime, timedelta, timezone
    comeca = datetime.now(timezone.utc)
    # Vence no fim do dia, horário de Brasília, não no meio da tarde: um
    # cupom criado dia 4 às 14h vale até dia 6 às 23:59. Pedido do Pedro em
    # 04/10/2026, para a mensagem dizer só a data.
    brasilia = timezone(timedelta(hours=-3))
    ultimo_dia = (comeca + timedelta(hours=horas)).astimezone(brasilia)
    expira = ultimo_dia.replace(hour=23, minute=59, second=59, microsecond=0).astimezone(timezone.utc)
    selecao = {"all": True}
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
            "comeca": comeca, "expira": expira, "restrito": False}


# Venda fora do checkout (link de pagamento da Stone) vira pedido na Shopify
# para seguir o mesmo caminho das outras: nota pela Olist, etiqueta, rastreio
# e e-mails da régua. A etiqueta "link-stone" é o que impede o portal e o
# fechamento das 23h de contar a venda duas vezes: o dinheiro dela já entra
# pela conciliação da Stone, como venda física. Caso que abriu isto: Michelle
# Gundi, 21/10/2026, que não conseguiu pagar no site e pagou pelo link.
TAG_LINK = "link-stone"

_MUT_RASCUNHO = """
mutation($d: DraftOrderInput!) {
  draftOrderCreate(input: $d) { draftOrder { id } userErrors { field message } }
}
"""

_MUT_FECHAR_RASCUNHO = """
mutation($id: ID!) {
  draftOrderComplete(id: $id) {
    draftOrder { order { id name displayFinancialStatus } }
    userErrors { field message }
  }
}
"""


def cpf_valido(cpf: str) -> bool:
    d = [int(c) for c in str(cpf) if c.isdigit()]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        soma = sum(d[i] * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != d[n]:
            return False
    return True


def criar_pedido_link(v: dict) -> dict:
    """Cria o pedido já pago de uma venda feita pelo link da Stone.

    `v` tem: nome, sobrenome, email, telefone, cpf, endereco (rua, numero,
    complemento, bairro, cidade, uf, cep), itens [{variant_id, quantidade,
    preco}], frete ("Sedex" ou "PAC"), valor_frete e quando (data da venda).
    O CPF vai no campo nativo TAX_CREDENTIAL_BR, que é o que a Olist lê para
    a nota. O frete precisa dizer Sedex ou PAC: é dele que a etiqueta tira o
    serviço (avisos/etiqueta.py).
    """
    if not cpf_valido(v["cpf"]):
        raise ValueError(f"CPF {v['cpf']} não confere")
    if v.get("frete", "Sedex").upper() not in ("SEDEX", "PAC"):
        raise ValueError("frete precisa ser Sedex ou PAC")
    e = v["endereco"]
    endereco = {
        "firstName": v["nome"], "lastName": v["sobrenome"],
        # Mesmo formato que a Olist espera dos pedidos do site:
        # "Rua, Número" e "Complemento, Bairro".
        "address1": f"{e['rua']}, {e['numero']}",
        "address2": ", ".join(x for x in (e.get("complemento"), e.get("bairro")) if x),
        "city": e["cidade"], "provinceCode": e["uf"], "countryCode": "BR",
        "zip": e["cep"], "phone": v.get("telefone", ""),
    }
    entrada = {
        "email": v["email"], "phone": v.get("telefone") or None,
        "shippingAddress": endereco, "billingAddress": endereco,
        "localizedFields": [{"key": "TAX_CREDENTIAL_BR",
                             "value": "".join(c for c in v["cpf"] if c.isdigit())}],
        "lineItems": [
            {"variantId": i["variant_id"], "quantity": int(i.get("quantidade", 1)),
             **({"priceOverride": {"amount": str(i["preco"]), "currencyCode": "BRL"}}
                if i.get("preco") is not None else {})}
            for i in v["itens"]
        ],
        "shippingLine": {"title": v.get("frete", "Sedex"),
                         "priceWithCurrency": {"amount": str(v.get("valor_frete", 0)),
                                               "currencyCode": "BRL"}},
        "tags": [TAG_LINK],
        "note": f"Venda pelo link de pagamento da Stone em {v.get('quando', '')}".strip(),
    }
    r = _graphql(_MUT_RASCUNHO, {"d": entrada})["draftOrderCreate"]
    if r["userErrors"]:
        raise RuntimeError(f"Shopify: {r['userErrors']}")
    f = _graphql(_MUT_FECHAR_RASCUNHO, {"id": r["draftOrder"]["id"]})["draftOrderComplete"]
    if f["userErrors"]:
        raise RuntimeError(f"Shopify: {f['userErrors']}")
    return f["draftOrder"]["order"]
