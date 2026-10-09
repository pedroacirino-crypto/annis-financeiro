"""Etiqueta dos Correios automática, 09/10/2026.

Depois que a nota do pedido é autorizada (avisos/danfe.py), este módulo:
  1. cria a pré-postagem na API dos Correios, no contrato da Annis, com o
     serviço que a cliente escolheu no checkout (PAC ou SEDEX), o endereço do
     pedido e o número e a chave da nota;
  2. lança o código de rastreio no pedido da Shopify, que manda o e-mail de
     envio para a cliente;
  3. gera a etiqueta em PDF e manda no grupo do Telegram, para imprimir.

Nada de entrega passa pela Olist: dela só vem a nota, que já é lida para a
DANFE. A pré-postagem não cobra nada; o frete só é faturado quando o pacote é
postado, e a pré-postagem expira sozinha se não for (14 dias, padrão).

Rodar duas vezes não duplica: o pedido com etiqueta fica anotado como
"etq:<número do pedido>" em avisos.enviados, com o código do objeto.

    python avisos/etiqueta.py 1146             cria a etiqueta do pedido #1146
    python avisos/etiqueta.py 1146 --simular   só mostra o que seria enviado

Variáveis: CORREIOS_USUARIO (CNPJ, só números), CORREIOS_CODIGO_ACESSO (o
código de 40 caracteres do portal CWS, não a senha do Meu Correios),
CORREIOS_CARTAO (cartão de postagem 0079350682), CORREIOS_AMBIENTE (hom ou
prod; o código da Annis só vale em prod, o ambiente de teste pede outro
login), CORREIOS_SERVICO_PAC e CORREIOS_SERVICO_SEDEX (03298 e 03220,
conferidos no contrato em 09/10/2026), OLIST_TOKEN, SUPABASE_URL_BANCO, TELEGRAM_*.
"""

import base64
import datetime as dt
import os
import re
import sys
import tempfile
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shopify_client  # noqa: E402  (carrega o .env)

CONTRATO = "9912705946"

# Remetente igual ao das etiquetas que a loja já gera no Meu Correios.
REMETENTE = {
    "nome": "ANNIS",
    "cpfCnpj": "58859303000144",
    "email": "contato@annis.store",
    "endereco": {
        "cep": "86050450", "logradouro": "Rua João Wyclif", "numero": "111",
        "complemento": "sala 1005", "bairro": "Gleba Fazenda Palhano",
        "cidade": "Londrina", "uf": "PR",
    },
}

# Embalagem e peso: a caixa é a que a loja usa no Meu Correios. Em todas as
# 21 vendas postadas pelo contrato de 28/09 a 05/10/2026 foi caixa de
# 10 x 34 x 45 cm, com 1 ou 3 peças (lido da API em 09/10); envelope só nas
# devoluções. O peso informado lá era "1", que a API guarda em gramas; o
# Pedro confirmou que é 1 kg (09/10). A agência pesa de novo na postagem.
EMBALAGEM = {
    "altura_cm": 10,
    "largura_cm": 34,
    "comprimento_cm": 45,
    "peso_caixa_g": 1000,
    "peso_por_peca_g": 0,
}
EMBALAGEM_TESTE = EMBALAGEM

URLS = {"hom": "https://apihom.correios.com.br", "prod": "https://api.correios.com.br"}


def configurado() -> bool:
    """Credenciais presentes e, em produção, embalagem com medidas reais. Sem
    isso a rotina da nota nem tenta: senão cada nota viraria aviso de erro."""
    if not all(os.environ.get(k) for k in ("CORREIOS_USUARIO", "CORREIOS_CODIGO_ACESSO", "CORREIOS_CARTAO")):
        return False
    # Interruptor: a variável CORREIOS_AMBIENTE do GitHub fica "desligado" até o
    # Pedro dar o ok, para não sair etiqueta em dobro com a feita à mão.
    if _ambiente() not in URLS:
        return False
    return _ambiente() != "prod" or all(v is not None for v in EMBALAGEM.values())


def _ambiente() -> str:
    return os.environ.get("CORREIOS_AMBIENTE", "hom").strip().lower()


def _embalagem() -> dict:
    if all(v is not None for v in EMBALAGEM.values()):
        return EMBALAGEM
    if _ambiente() == "prod":
        raise RuntimeError("Embalagem sem medidas e peso: preencher EMBALAGEM em avisos/etiqueta.py")
    return EMBALAGEM_TESTE


# ── Correios ────────────────────────────────────────────────────────────────

_token = {"valor": None, "ate": 0.0}


def _token_correios() -> str:
    if _token["valor"] and time.time() < _token["ate"]:
        return _token["valor"]
    r = requests.post(
        URLS[_ambiente()] + "/token/v1/autentica/cartaopostagem",
        auth=(os.environ["CORREIOS_USUARIO"], os.environ["CORREIOS_CODIGO_ACESSO"]),
        json={"numero": os.environ["CORREIOS_CARTAO"], "contrato": CONTRATO}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Correios token: HTTP {r.status_code} {r.text[:300]}")
    _token["valor"] = r.json()["token"]
    _token["ate"] = time.time() + 50 * 60  # o token vale um dia; renova antes por garantia
    return _token["valor"]


def _correios(metodo: str, caminho: str, **kw) -> dict:
    r = requests.request(metodo, URLS[_ambiente()] + "/prepostagem" + caminho, timeout=60,
                         headers={"Authorization": "Bearer " + _token_correios()}, **kw)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Correios {caminho}: HTTP {r.status_code} {r.text[:400]}")
    return r.json() if r.content else {}


def _so_digitos(t) -> str:
    return re.sub(r"\D", "", t or "")


def _corta(t, n) -> str:
    return (t or "").strip()[:n]


def separar_endereco(a: dict) -> dict:
    """A Shopify guarda "Rua, número" no address1 e "complemento, bairro" no
    address2 (é assim que o checkout da Annis monta). Os Correios querem cada
    parte num campo."""
    a1 = (a.get("address1") or "").strip()
    logradouro, _, numero = a1.rpartition(",")
    if not logradouro:
        logradouro, numero = a1, "S/N"
    a2 = (a.get("address2") or "").strip()
    complemento, _, bairro = a2.rpartition(",")
    if not bairro:
        complemento, bairro = "", a2
    return {
        "cep": _so_digitos(a.get("zip")),
        "logradouro": _corta(logradouro, 50),
        "numero": _corta(numero, 10) or "S/N",
        "complemento": _corta(complemento, 30),
        "bairro": _corta(bairro, 30) or "Centro",
        "cidade": _corta(a.get("city"), 30),
        "uf": (a.get("provinceCode") or "").strip().upper()[:2],
    }


def _celular(fone) -> dict:
    d = _so_digitos(fone)
    if d.startswith("55") and len(d) > 11:
        d = d[2:]
    if len(d) == 11:
        return {"dddCelular": d[:2], "celular": d[2:]}
    if len(d) == 10:
        return {"dddTelefone": d[:2], "telefone": d[2:]}
    return {}


def _servico(titulo: str) -> str:
    t = (titulo or "").upper()
    if "SEDEX" in t:
        return os.environ.get("CORREIOS_SERVICO_SEDEX", "03220")
    if "PAC" in t:
        return os.environ.get("CORREIOS_SERVICO_PAC", "03298")
    raise RuntimeError(f"frete '{titulo}' não é PAC nem SEDEX")


def montar_prepostagem(pedido: dict, nota: dict) -> dict:
    a = pedido["shippingAddress"]
    itens = pedido["lineItems"]["nodes"]
    pecas = sum(int(i["quantity"]) for i in itens)
    e = _embalagem()
    nome = _corta(a.get("name") or pedido.get("customer", {}).get("displayName"), 50)
    return {
        "remetente": REMETENTE,
        "destinatario": {
            "nome": nome,
            "email": _corta(pedido.get("email"), 255),
            **_celular(a.get("phone") or pedido.get("phone")),
            "endereco": separar_endereco(a),
        },
        "codigoServico": _servico(pedido["shippingLines"]["nodes"][0]["title"]),
        "numeroNotaFiscal": str(int(nota["numero"])),
        "chaveNFe": _so_digitos(nota.get("chave_acesso")),
        "itensDeclaracaoConteudo": [
            {"conteudo": _corta(i["title"], 60), "quantidade": str(i["quantity"]),
             "valor": f"{float(i['originalUnitPriceSet']['shopMoney']['amount']):.2f}"}
            for i in itens
        ],
        "pesoInformado": str(e["peso_caixa_g"] + e["peso_por_peca_g"] * pecas),
        "codigoFormatoObjetoInformado": "2",
        "alturaInformada": str(e["altura_cm"]),
        "larguraInformada": str(e["largura_cm"]),
        "comprimentoInformado": str(e["comprimento_cm"]),
        "cienteObjetoNaoProibido": "1",
        "modalidadePagamento": "2",  # à faturar, como no contrato
        "emiteDCe": "N",
        "observacao": f"Pedido {pedido['name']}",
        "pedidoExternoOrigem": pedido["name"].lstrip("#"),
    }


def _esperar_prepostado(id_prepostagem: str) -> None:
    """A pré-postagem nasce "Pendente" (7) e vira "Pré-postado" (2) em alguns
    segundos; rótulo pedido antes disso nunca sai (PPN-288, testado em 09/10)."""
    for _ in range(24):
        itens = _correios("GET", "/v2/prepostagens", params={"id": id_prepostagem}).get("itens") or [{}]
        if itens[0].get("statusAtual") == 2:
            return
        time.sleep(5)
    raise RuntimeError(f"Correios: pré-postagem {id_prepostagem} não saiu de Pendente em 2 minutos")


def etiqueta_pdf(id_prepostagem: str, destino: str) -> None:
    """Pede o rótulo (assíncrono) e espera ficar pronto, até 2 minutos."""
    _esperar_prepostado(id_prepostagem)
    recibo = _correios("POST", "/v1/prepostagens/rotulo/assincrono/pdf", json={
        "idsPrePostagem": [id_prepostagem], "numeroCartaoPostagem": os.environ["CORREIOS_CARTAO"],
        "tipoRotulo": "P", "formatoRotulo": "ET", "imprimeRemetente": "S", "layoutImpressao": "PADRAO",
    })["idRecibo"]
    for _ in range(24):
        time.sleep(5)
        # Enquanto não fica pronto, a resposta vem só com "mensagem".
        r = _correios("GET", f"/v1/prepostagens/rotulo/download/assincrono/{recibo}")
        if r.get("dados"):
            with open(destino, "wb") as f:
                f.write(base64.b64decode(r["dados"]))
            return
    raise RuntimeError(f"Correios: rótulo do recibo {recibo} não ficou pronto em 2 minutos")


# ── Shopify ─────────────────────────────────────────────────────────────────

_PEDIDO = """query($q: String!) { orders(first: 1, query: $q) { nodes {
  id name email phone displayFulfillmentStatus
  customer { displayName }
  shippingAddress { name address1 address2 city provinceCode zip phone }
  shippingLines(first: 1) { nodes { title } }
  lineItems(first: 50) { nodes { title quantity originalUnitPriceSet { shopMoney { amount } } } }
} } }"""

# Ler e criar envio exige a permissão de pedidos de envio no app da Shopify
# (read/write_merchant_managed_fulfillment_orders), separada do resto.
_A_ENVIAR = """query($id: ID!) { order(id: $id) { fulfillmentOrders(first: 10) { nodes { id status } } } }"""

_ENVIO = """mutation($f: FulfillmentInput!) { fulfillmentCreate(fulfillment: $f) {
  fulfillment { id status } userErrors { field message } } }"""


def pedido_shopify(numero: str) -> dict:
    nos = shopify_client._graphql(_PEDIDO, {"q": f"name:#{numero}"})["orders"]["nodes"]
    if not nos:
        raise RuntimeError(f"pedido #{numero} não encontrado na Shopify")
    return nos[0]


def rastreio_na_shopify(pedido: dict, codigo: str) -> None:
    ordens = shopify_client._graphql(_A_ENVIAR, {"id": pedido["id"]})["order"]["fulfillmentOrders"]["nodes"]
    abertos = [f["id"] for f in ordens if f["status"] in ("OPEN", "IN_PROGRESS")]
    if not abertos:
        return  # já foi marcado como enviado à mão
    r = shopify_client._graphql(_ENVIO, {"f": {
        "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": i} for i in abertos],
        "notifyCustomer": True,
        "trackingInfo": {"company": "Correios", "number": codigo,
                         "url": f"https://rastreamento.correios.com.br/app/index.php?objeto={codigo}"},
    }})["fulfillmentCreate"]
    if r["userErrors"]:
        raise RuntimeError(f"Shopify: {r['userErrors']}")


# ── Estado e fluxo ──────────────────────────────────────────────────────────

def _ja_tem(numero: str):
    import nuvem
    from sqlalchemy import text
    with nuvem._conectar().connect() as con:
        row = con.execute(text("select tipo from avisos.enviados where id = :id"),
                          {"id": f"etq:{numero}"}).fetchone()
    return row[0] if row else None


def _anotar(numero: str, codigo: str) -> None:
    import nuvem
    from sqlalchemy import text
    with nuvem._conectar().begin() as con:
        con.execute(text("insert into avisos.enviados (id, tipo) values (:id, :tipo) on conflict do nothing"),
                    {"id": f"etq:{numero}", "tipo": f"etiqueta {codigo}"})


def nota_do_pedido(numero: str) -> dict:
    """Nota autorizada do pedido, com a chave (a pesquisa da Olist não traz)."""
    from avisos import danfe
    for n in danfe.notas_recentes(dias=7):
        if str(n.get("numero_ecommerce") or "") == numero and int(n["situacao"]) in danfe.AUTORIZADAS:
            completa = danfe.olist("nota.fiscal.obter", id=n["id"])["nota_fiscal"]
            n["chave_acesso"] = completa.get("chave_acesso")
            return n
    raise RuntimeError(f"pedido #{numero} sem nota autorizada na Olist")


def processar(numero: str, nota: dict = None, simular: bool = False) -> str:
    """Faz a etiqueta de um pedido. Devolve o código do objeto."""
    numero = str(numero).lstrip("#")
    if not simular:
        feito = _ja_tem(numero)
        if feito:
            return feito.split()[-1]
    pedido = pedido_shopify(numero)
    nota = nota or nota_do_pedido(numero)
    if not nota.get("chave_acesso"):
        from avisos import danfe
        nota["chave_acesso"] = danfe.olist("nota.fiscal.obter", id=nota["id"])["nota_fiscal"].get("chave_acesso")
    corpo = montar_prepostagem(pedido, nota)
    if simular:
        import json
        print(json.dumps(corpo, ensure_ascii=False, indent=2))
        return ""

    pre = _correios("POST", "/v1/prepostagens", json=corpo)
    codigo = pre["codigoObjeto"]
    _anotar(numero, codigo)  # antes do resto: rótulo e Shopify podem repetir, a pré-postagem não

    from avisos import danfe
    servico = pedido["shippingLines"]["nodes"][0]["title"]
    nome = (pedido["shippingAddress"].get("name") or "").strip().title()
    with tempfile.TemporaryDirectory() as pasta:
        arquivo = os.path.join(pasta, f"Etiqueta {pedido['name']} {codigo}.pdf")
        etiqueta_pdf(pre["id"], arquivo)
        danfe.telegram(arquivo, os.path.basename(arquivo),
                       f"Etiqueta {servico} · pedido {pedido['name']}\n{nome} · {codigo}")
    try:
        rastreio_na_shopify(pedido, codigo)
    except Exception as erro:
        # A etiqueta já foi para o grupo; falta só o rastreio no pedido (por
        # exemplo, sem a permissão de envios no app da Shopify).
        danfe.mensagem(f"Etiqueta do pedido {pedido['name']} saiu ({codigo}), mas o rastreio não entrou "
                       f"na Shopify: {str(erro)[:200]}\nLançar o rastreio à mão no pedido.")
    return codigo


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("uso: python avisos/etiqueta.py <número do pedido> [--simular]")
    print(processar(args[0], simular="--simular" in sys.argv))
