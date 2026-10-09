"""Salva a nota e a etiqueta de cada pedido na pasta de envios do Drive.

A pasta é de outra conta Google, então quem grava é um App da Web do Apps
Script nessa conta (avisos/drive.gs). Aqui só se manda o arquivo com o token.

Organização pedida pelo Pedro em 09/10/2026, como repositório por cliente:
"Nome da cliente - CPF (só números) / #pedido · data", com a NF e a etiqueta
dentro.

Variáveis: DRIVE_WEBAPP_URL (endereço do App da Web) e DRIVE_TOKEN.
"""

import base64
import os

import requests


def configurado() -> bool:
    return bool(os.environ.get("DRIVE_WEBAPP_URL") and os.environ.get("DRIVE_TOKEN"))


def pasta_do_pedido(nome_cliente: str, cpf: str, pedido: str, data_br: str) -> list:
    """["Maria Silva - 12345678900", "#1146 · 09-10-2026"]; data_br em dd/mm/aaaa.

    Sem pedido da loja (venda do ateliê, por exemplo), a segunda pasta vira
    "NF 123 · data" com o número passado em pedido como "NF 123"."""
    import re
    nome = re.sub(r"[\d.\-/]+", " ", nome_cliente or "")
    nome = " ".join(nome.split()).title()[:80] or "Sem Nome"
    doc = re.sub(r"\D", "", cpf or "")
    topo = f"{nome} - {doc}" if doc else nome
    rotulo = str(pedido or "").strip()
    rotulo = rotulo if rotulo.upper().startswith("NF") else f"#{rotulo.lstrip('#')}"
    return [topo, f"{rotulo} · {(data_br or '').replace('/', '-')}"]


def salvar(caminho: list, nome: str, arquivo: str) -> str:
    with open(arquivo, "rb") as f:
        dados = base64.b64encode(f.read()).decode()
    r = requests.post(os.environ["DRIVE_WEBAPP_URL"], timeout=120, json={
        "token": os.environ["DRIVE_TOKEN"], "caminho": caminho, "nome": nome,
        "base64": dados, "tipo": "application/pdf"})
    if r.status_code != 200:
        raise RuntimeError(f"Drive: HTTP {r.status_code} {r.text[:200]}")
    ret = r.json()
    if not ret.get("ok"):
        raise RuntimeError(f"Drive: {ret.get('erro')}")
    return ret.get("url", "")
