"""Grava as combinações em cada produto da Shopify, 18/10/2026.

O campo é um metafield do produto, `custom.combina_com` (lista de produtos),
fixado na página do produto no admin: dá para conferir e mudar à mão por lá,
sem passar por aqui. O bloco "Combina com" da página de produto e a página de
Combinações leem dele (tema/combinacoes/*.liquid).

Precisa do escopo write_products no app do portal (versão combinacoes-produtos).

    python tema/combinacoes/montar.py             refaz combinacoes.json a partir da lista
    python tema/combinacoes/gravar.py             grava na loja
    python tema/combinacoes/gravar.py --simular   só mostra o que gravaria
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import shopify_client  # noqa: E402

NAMESPACE, CHAVE = "custom", "combina_com"

_DEFINICAO = """mutation($d: MetafieldDefinitionInput!) {
  metafieldDefinitionCreate(definition: $d) {
    createdDefinition { id } userErrors { field message code } } }"""

_GRAVAR = """mutation($m: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $m) { metafields { id } userErrors { field message } } }"""


def garantir_definicao() -> None:
    r = shopify_client._graphql(_DEFINICAO, {"d": {
        "name": "Combina com", "namespace": NAMESPACE, "key": CHAVE,
        "description": "Peças que combinam com esta, na ordem em que aparecem no site "
                       "(bloco Combina com e página de Combinações).",
        "type": "list.product_reference", "ownerType": "PRODUCT", "pin": True,
        "access": {"storefront": "PUBLIC_READ"},
    }})["metafieldDefinitionCreate"]
    erros = [e for e in r["userErrors"] if e.get("code") != "TAKEN"]
    if erros:
        raise RuntimeError(f"Shopify: {erros}")


def produtos() -> dict:
    d = shopify_client._graphql('{products(first: 250) { nodes { id title } } }')
    return {p["title"]: p["id"] for p in d["products"]["nodes"]}


def main(simular: bool = False) -> None:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "combinacoes.json"), encoding="utf-8") as f:
        combina = json.load(f)
    ids = produtos()
    faltam = sorted({t for k, v in combina.items() for t in [k, *v] if t not in ids})
    if faltam:
        raise RuntimeError(f"sem produto na loja: {faltam}")
    entradas = [{"ownerId": ids[peca], "namespace": NAMESPACE, "key": CHAVE, "type": "list.product_reference",
                 "value": json.dumps([ids[t] for t in lista])} for peca, lista in combina.items()]
    if simular:
        for peca, lista in combina.items():
            print(f"{peca}: {len(lista)} -> {', '.join(lista)}")
        return
    garantir_definicao()
    for i in range(0, len(entradas), 25):       # metafieldsSet aceita até 25 por vez
        r = shopify_client._graphql(_GRAVAR, {"m": entradas[i:i + 25]})["metafieldsSet"]
        if r["userErrors"]:
            raise RuntimeError(f"Shopify: {r['userErrors']}")
    print(f"{len(entradas)} produtos com combinações gravadas")


if __name__ == "__main__":
    main(simular="--simular" in sys.argv)
