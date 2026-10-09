"""Salva a nota e a etiqueta de cada pedido na pasta de envios do Drive.

A pasta é de outra conta Google, então quem grava é um App da Web do Apps
Script nessa conta (avisos/drive.gs). Aqui só se manda o arquivo com o token.

Organização, combinada com o Pedro em 09/10/2026: "Nome da cliente / #pedido ·
data", com a NF e a etiqueta dentro. Sem CPF no nome da pasta: ele já está na
nota, e nome de pasta fica à vista de quem acessa o Drive.

Variáveis: DRIVE_WEBAPP_URL (endereço do App da Web) e DRIVE_TOKEN.
"""

import base64
import os

import requests


def configurado() -> bool:
    return bool(os.environ.get("DRIVE_WEBAPP_URL") and os.environ.get("DRIVE_TOKEN"))


def pasta_do_pedido(nome_cliente: str, pedido: str, data_br: str) -> list:
    """["Maria Silva", "#1146 · 09-10-2026"]; data_br em dd/mm/aaaa."""
    nome = " ".join((nome_cliente or "Sem nome").split()).title()[:80]
    return [nome, f"#{str(pedido).lstrip('#')} · {(data_br or '').replace('/', '-')}"]


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
