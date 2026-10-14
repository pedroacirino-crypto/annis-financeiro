"""Pedidos de exemplo para a prévia, os mesmos do blueprint (cliente fictícia)."""

_CDN = "https://cdn.shopify.com/s/files/1/0717/0005/4051/files/"
_TAM = "&width=300&height=450&crop=center"
BLAZER = _CDN + "ANNIS_BLAZER_DELAUNAY_BEGE_03_d058dc5e-3ac2-4901-bad1-e11d94b50e26.png?v=1757516138" + _TAM
CALCA = _CDN + "ANNIS_CALCA_JOAN_BEGE_01_84d0f0ef-65e2-42c5-ba1e-208cc9e0f89d.png?v=1751339827" + _TAM
BOLSA = _CDN + "628A1191-Edit.png?v=1779738240" + _TAM

# Campo Manutenção das descrições reais (o e-mail 8 lê de lá).
_LINHO = ["Lavar à mão com cuidado.", "Lavagem a seco profissional.", "Passar a 110º.", "Secar à sombra.", "Não alvejar.", "Não colocar na secadora."]
_BOLSA = ["Lavar à mão com cuidado.", "Lavagem a seco profissional.", "Secar à sombra.", "Não alvejar.", "Não colocar na secadora.",
          "Guardar a bolsa de forma estendida (não dobrar)."]

_ENDERECO = ["Maria Exemplo", "Rua das Flores, 120, ap. 32", "São Paulo, SP · 01310-100"]
_RASTREIO = {"codigo": "AE011801002BR", "link": "https://rastreamento.correios.com.br/app/index.php?objetos=AE011801002BR"}

ROUPA = {
    "nome": "Maria", "pedido": "#1160", "genero": "m", "peca": "blazer", "peca_nome": "Blazer de Linho Delaunay · Bege · PP",
    "material": "linho", "servico": "SEDEX",
    "itens": [
        {"img": BLAZER, "nome": "Blazer de Linho Delaunay", "variante": "Bege · PP · 1 un.", "preco": "R$ 528,00", "material": "linho", "cuidados": _LINHO},
        {"img": CALCA, "nome": "Calça de Linho Joan", "variante": "Bege · PP · 1 un.", "preco": "R$ 348,00", "material": "linho", "cuidados": _LINHO},
    ],
    "somas": [("Subtotal", "R$ 876,00"), ("Frete · SEDEX", "R$ 25,29"), ("Desconto Pix (5%)", "-R$ 43,80"), ("Total", "R$ 857,49")],
    "pagamento": "Pix", "endereco": _ENDERECO,
    "data_postagem": "qui, 15/10", "data_entrega": "até 16/10", "previsao_correios": "qui, 15/10",
    "rastreio": _RASTREIO, "nota": "000.248",
    "pesquisa_link": "https://annis.store/pages/sua-opiniao?p=EXEMPLO",
}

BOLSA_ENC = {
    "nome": "Maria", "pedido": "#1162", "genero": "f", "peca": "Relíquia", "peca_nome": "Bolsa de Jacquard Relíquia · Vinho",
    "dias_producao": 10, "servico": "SEDEX",
    "itens": [{"img": BOLSA, "nome": "Bolsa de Jacquard Relíquia", "variante": "Vinho · sob encomenda · 1 un.", "preco": "R$ 448,00", "material": "jacquard", "cuidados": _BOLSA}],
    "somas": [("Subtotal", "R$ 448,00"), ("Frete · SEDEX", "R$ 25,29"), ("Total", "R$ 473,29")],
    "pagamento": "Cartão, 3x sem juros", "endereco": _ENDERECO,
    "data_postagem": "qui, 29/10", "data_entrega": "até 03/11",
}


def _com(base: dict, **extra) -> dict:
    d = dict(base)
    d.update(extra)
    return d


def _somas(rotulo: str) -> list:
    return ROUPA["somas"][:-1] + [(rotulo, "R$ 857,49")]


SO_BLAZER = _com(ROUPA, itens=ROUPA["itens"][:1], somas=[("Subtotal", "R$ 528,00"), ("Frete · SEDEX", "R$ 25,29"), ("Total", "R$ 553,29")])
BOLSA_ENTREGUE = _com(BOLSA_ENC, roupa=False, rastreio=_RASTREIO, pesquisa_link="https://annis.store/pages/sua-opiniao?p=EXEMPLO")

# (id do e-mail, dados) na ordem da régua; o mesmo e-mail pode ter mais de uma
# prévia com sufixo depois de ":" (ex. "e8:uma-peca").
TODOS = [
    ("e0", ROUPA),
    ("e1", _com(ROUPA, hora_limite="14h32", pagamento="Pix, aguardando", somas=_somas("Total a pagar"),
                pix={"valor": "R$ 857,49", "codigo": "00020126580014br.gov.bcb.pix0136a1f4c2d8-0000-4000-8000-0000exemplo5204000053039865406857.495802BR5913ANNIS6009SAO PAULO62070503***6304ABCD",
                     "link_copiar": "https://annis.store/pages/pix?p=EXEMPLO"})),
    ("e2_pronta", _com(ROUPA, pagamento="Pix, aprovado em 13/10",
                       passos=[("feito", "Pago<br>13/10"), ("agora", "Separação<br>até 15/10"), ("", "Postado"), ("", "Entregue")])),
    ("e2_encomenda", _com(BOLSA_ENC, passos=[("feito", "Pago<br>13/10"), ("agora", "Produção<br>até 29/10"), ("", "Postado"), ("", "Entregue")])),
    ("e3", _com(BOLSA_ENC, passos=[("feito", "Pago<br>13/10"), ("agora", "Produção<br>até 29/10"), ("", "Postado"), ("", "Entregue")])),
    ("e5", BOLSA_ENC),
    ("e5_pronta", SO_BLAZER),
    ("e6", _com(ROUPA, somas=_somas("Total pago"), data_postado="qua, 14/10",
                passos=[("feito", "Pago<br>13/10"), ("feito", "Separação"), ("agora", "Postado<br>14/10"), ("", "Entregue")])),
    ("e7_atrasada", ROUPA),
    ("e8", ROUPA),
    ("e8:uma-peca", SO_BLAZER),
    ("e9", ROUPA),
    ("e8:bolsa", BOLSA_ENTREGUE),
    ("e9:bolsa", BOLSA_ENTREGUE),
]
