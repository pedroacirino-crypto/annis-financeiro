"""Vendas da Stone pela API de Conciliação, no lugar do relatório CSV.

A Annis tem três StoneCodes: a maquininha (157096605) e dois de link de
pagamento (407033891 e 136373022). Cada um tem a sua chave, criada pelo
titular no Portal Stone (Perfil > Chaves de Autenticação > API de Conciliação
Stone), e o .env guarda as três em STONE_CHAVES no formato
"stonecode:chave,stonecode:chave,...".

O arquivo de um dia só fica pronto na manhã seguinte e traz as capturas
(vendas) e os cancelamentos daquele dia. As vendas vão para a mesma tabela do
relatório CSV (fin_vendas_stone, chave Stone ID), no mesmo formato, então o
resto do painel não muda. Pix QR Code não vem neste arquivo (a Stone entrega
Pix por outro fluxo); continua chegando pelo extrato.

Limite da Stone: 7 pedidos por hora para cada par StoneCode e data.

    python stone_api.py                 últimos 10 dias
    python stone_api.py 2026-09-01      desde a data
"""

import base64
import datetime as dt
import os
import sys
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

import dados_fin
import shopify_client  # noqa: F401  (carrega o .env)

URL = "https://conciliation.stone.com.br/v2/merchant/{sc}/conciliation-file/{dia}"

BANDEIRAS = {"1": "Visa", "2": "MasterCard", "3": "Amex", "4": "Cabal",
             "5": "UnionPay", "9": "Hipercard", "171": "Elo"}
PRODUTOS = {"1": "Debito", "2": "Credito", "3": "Debito", "4": "Credito", "5": "Voucher"}
# O relatório CSV chama de POS tudo que é presencial e de E-commerce o link.
CAPTURAS = {"4": "E-commerce"}


def _chaves() -> dict:
    valor = os.environ.get("STONE_CHAVES", "")
    return dict(par.split(":", 1) for par in valor.split(",") if ":" in par)


def baixar(stonecode: str, dia: dt.date) -> ET.Element:
    chave = _chaves()[stonecode]
    auth = base64.b64encode(f"{chave}:".encode()).decode()
    for tentativa in range(3):
        r = requests.get(
            URL.format(sc=stonecode, dia=dia.strftime("%Y%m%d")),
            params={"layout": "XML2_2"},
            headers={"Authorization": f"Basic {auth}", "x-user-type": "client",
                     "Accept-Encoding": "gzip"},
            timeout=60,
        )
        if r.status_code == 429:
            raise RuntimeError(f"Stone: limite de pedidos para {stonecode} em {dia}")
        if r.status_code >= 500 and tentativa < 2:
            time.sleep(5)
            continue
        r.raise_for_status()
        return ET.fromstring(r.content)
    raise RuntimeError("Stone: sem resposta")


def _texto(no, caminho, padrao=""):
    achado = no.find(caminho)
    return achado.text.strip() if achado is not None and achado.text else padrao


def ler_arquivo(raiz: ET.Element) -> tuple:
    """Vendas capturadas no dia e Stone IDs cancelados no dia."""
    vendas, cancelados = [], []
    for t in raiz.iter("Transaction"):
        stone_id = _texto(t, "AcquirerTransactionKey")
        if not stone_id:
            continue
        if int(_texto(t, "Events/Cancellations", "0") or 0) > 0:
            cancelados.append(stone_id)
        if int(_texto(t, "Events/Captures", "0") or 0) == 0:
            continue
        liquido = sum(float(_texto(i, "NetAmount", "0")) for i in t.iter("Installment"))
        vendas.append({
            "stone_id": stone_id,
            # Minuto, como no relatório CSV, para a mesma venda não mudar de hora.
            "data": dt.datetime.strptime(_texto(t, "CaptureLocalDateTime")[:12], "%Y%m%d%H%M"),
            "bandeira": BANDEIRAS.get(_texto(t, "BrandId"), _texto(t, "BrandId")),
            "produto": PRODUTOS.get(_texto(t, "AccountType"), _texto(t, "AccountType")),
            "parcelas": int(_texto(t, "NumberOfInstallments", "1") or 1),
            "bruto": float(_texto(t, "CapturedAmount", "0")),
            "liquido": round(liquido, 6),
            "cartao": _texto(t, "CardNumber"),
            "captura": CAPTURAS.get(_texto(t, "Poi/PoiType"), "POS"),
            "status": "Aprovada",
        })
    return vendas, cancelados


def sincronizar(desde: dt.date = None, ate: dt.date = None) -> dict:
    """Busca os arquivos de cada StoneCode e grava as vendas no banco."""
    ate = ate or dt.date.today() - dt.timedelta(days=1)
    desde = desde or ate - dt.timedelta(days=9)
    vendas, cancelados, falhas = [], [], []
    for sc in _chaves():
        dia = desde
        while dia <= ate:
            try:
                v, c = ler_arquivo(baixar(sc, dia))
                vendas += v
                cancelados += c
            except Exception as e:  # um dia ruim não derruba os outros
                falhas.append(f"{sc} {dia:%d/%m}: {str(e)[:80]}")
            dia += dt.timedelta(days=1)
    gravadas = dados_fin.salvar_vendas_stone(pd.DataFrame(vendas)) if vendas else 0
    if cancelados:
        dados_fin.marcar_vendas_stone_canceladas(cancelados)
    return {"vendas": gravadas, "cancelamentos": len(cancelados), "falhas": falhas,
            "de": desde, "ate": ate}


if __name__ == "__main__":
    inicio = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    r = sincronizar(inicio)
    print(f"Stone {r['de']:%d/%m} a {r['ate']:%d/%m}: {r['vendas']} vendas, "
          f"{r['cancelamentos']} cancelamentos")
    for f in r["falhas"]:
        print("  falhou", f)
