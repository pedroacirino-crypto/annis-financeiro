"""Gera o e-mail 0 no formato da Shopify (13/10/2026).

    python -m avisos.regua.shopify_e0

Sai avisos/regua/shopify-confirmacao-pedido.liquid, para colar em
Configurações > Notificações > Confirmação do pedido (corpo do e-mail, HTML).
Assunto lá: Recebemos seu pedido {{ order_name }}

E avisos/regua/shopify-pagamento-realizado.liquid, para "Pagamento pendente
realizado". Assunto lá: Pagamento do pedido {{ order_name }} confirmado

É o mesmo e-mail 0 da régua (modelos.py, textos.yaml), com as variáveis da
Shopify no lugar dos dados do pedido. Leva as duas versões: pedido que já
nasce pago (cartão e Pix pago no checkout) e Pix pendente.
"""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avisos.regua import modelos  # noqa: E402

ITEM = {
    "img": "{{ line.image | image_url: width: 300, height: 450, crop: 'center' }}",
    "nome": "{{ line.product.title | default: line.title }}",
    "variante": "{% if line.variant_title != blank %}{{ line.variant_title }} · {% endif %}{{ line.quantity }} un.",
    "preco": "{{ line.final_line_price | money }}",
}
PAGAMENTO = ("{% assign gw = transactions.first.gateway_display_name | default: gateway | downcase %}"
             "{% if gw contains 'pix' %}Pix{% else %}Cartão de crédito{% endif %}")
DESCONTO = "{{ total_discounts | money }}"


def _versao(pago: bool) -> str:
    dados = {
        "nome": "{{ customer.first_name | default: billing_address.first_name }}",
        "pedido": "{{ order_name }}", "genero": "m", "peca": "", "pago": pago,
        "itens": [dict(ITEM)],
        "somas": [("Subtotal", "{{ subtotal_price | money }}"),
                  ("Frete · {{ shipping_method.title }}", "{{ shipping_price | money }}"),
                  ("Desconto", "-" + DESCONTO), ("Total", "{{ total_price | money }}")],
        "pagamento": PAGAMENTO,
        "endereco": ["{{ shipping_address.name }}",
                     "{{ shipping_address.address1 }}{% if shipping_address.address2 != blank %}, {{ shipping_address.address2 }}{% endif %}",
                     "{{ shipping_address.city }}, {{ shipping_address.province_code }} · {{ shipping_address.zip }}"],
    }
    html = modelos.montar("e0", dados)["html"]
    # Uma linha de peça vira o laço das peças do pedido.
    i = html.index('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 18px 0;">')
    j = html.index("</table>", i) + len("</table>")
    html = html[:i] + "{% for line in line_items %}" + html[i:j] + "{% endfor %}" + html[j:]
    # A linha de desconto só aparece quando há desconto.
    k = html.index("-" + DESCONTO)
    a, b = html.rindex("<tr>", 0, k), html.index("</tr>", k) + len("</tr>")
    html = html[:a] + "{% if total_discounts > 0 %}" + html[a:b] + "{% endif %}" + html[b:]
    # Links do WhatsApp com o número do pedido.
    html = html.replace(quote("{{ order_name }}"), "{{ order_name | url_encode }}")
    return html


def gerar_pagamento() -> str:
    """Modelo curto para "Pagamento pendente realizado"."""
    html = modelos.montar("pagamento_shopify", {"nome": "{{ customer.first_name | default: billing_address.first_name }}",
                                                "pedido": "{{ order_name }}"})["html"]
    html = html.replace(quote("{{ order_name }}"), "{{ order_name | url_encode }}")
    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shopify-pagamento-realizado.liquid")
    with open(destino, "w", encoding="utf-8") as f:
        f.write("{% comment %}Régua pós-compra. Gerado por avisos/regua/shopify_e0.py; não editar aqui.{% endcomment %}\n" + html)
    return destino


def gerar() -> str:
    pago, pendente = _versao(True), _versao(False)
    corpo = ("{% comment %}Régua pós-compra, e-mail 0. Gerado por avisos/regua/shopify_e0.py; não editar aqui.{% endcomment %}\n"
             "{% if financial_status == 'paid' or financial_status == 'authorized' %}\n" + pago +
             "\n{% else %}\n" + pendente + "\n{% endif %}\n")
    assert "{{" not in re.sub(r"\{\{[^}]*\}\}", "", corpo), "chave de Liquid quebrada"
    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shopify-confirmacao-pedido.liquid")
    with open(destino, "w", encoding="utf-8") as f:
        f.write(corpo)
    return destino


if __name__ == "__main__":
    print(gerar())
    print(gerar_pagamento())
