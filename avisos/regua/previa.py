"""Prévia dos e-mails da régua com os pedidos de exemplo.

    python -m avisos.regua.previa                 gera os HTML numa pasta e abre o índice
    python -m avisos.regua.previa --enviar        manda os 10 de teste para contato@annis.store
    python -m avisos.regua.previa --enviar e6     manda só um

A pasta fica fora do repositório (Code/ANNIS/regua-previa), porque é gerada.
Ajuste de texto é em textos.yaml; de layout, em blocos.py.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avisos import gmail  # noqa: E402
from avisos.regua import exemplos, modelos  # noqa: E402

PASTA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "regua-previa")


def gerar() -> str:
    os.makedirs(PASTA, exist_ok=True)
    itens = []
    for arquivo, dados in exemplos.TODOS:
        qual = arquivo.split(":")[0]
        arquivo = arquivo.replace(":", "_")
        m = modelos.montar(qual, dados)
        html = m["html"]
        for cid, png in m["imagens"]:  # no navegador não existe cid:, vira imagem embutida
            html = html.replace(f"cid:{cid}", "data:image/png;base64," + base64.b64encode(png).decode())
        with open(os.path.join(PASTA, f"{arquivo}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        with open(os.path.join(PASTA, f"{arquivo}.txt"), "w", encoding="utf-8") as f:
            f.write(m["texto"])
        itens.append((arquivo, m["assunto"]))
    cartoes = "".join(
        f'<section><h2>{q} · {a}</h2><div class="par"><iframe src="{q}.html" width="640"></iframe>'
        f'<iframe src="{q}.html" width="375"></iframe></div></section>' for q, a in itens)
    indice = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Prévia da régua</title>
<style>:root{{color-scheme:light}} body{{background:#fff;font-family:Arial,sans-serif;margin:24px}}
h2{{font-size:15px;color:#68380a;margin:32px 0 10px}} .par{{display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap}}
iframe{{border:1px solid #e6d5c4;height:1400px;background:#fff}}</style></head>
<body><h1 style="font-size:20px">Prévia da régua pós-compra · computador e celular</h1>{cartoes}</body></html>"""
    caminho = os.path.join(PASTA, "index.html")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(indice)
    return caminho


def enviar(so: str | None = None, para: str = "contato@annis.store") -> None:
    for arquivo, dados in exemplos.TODOS:
        if so and arquivo != so:
            continue
        m = modelos.montar(arquivo.split(":")[0], dados)
        mid = gmail.enviar(para, "[teste] " + m["assunto"], m["html"], m["texto"], imagens=m["imagens"])
        print(arquivo, "enviado", mid)


if __name__ == "__main__":
    if "--enviar" in sys.argv:
        resto = [a for a in sys.argv[1:] if a != "--enviar"]
        enviar(resto[0] if resto else None)
    else:
        c = gerar()
        print(c)
        if "--sem-abrir" not in sys.argv:
            subprocess.run(["open", c])
