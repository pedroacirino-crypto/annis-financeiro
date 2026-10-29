"""Faixa de anúncios do tema no Novembro Annis, 29/10/2026.

Durante a campanha: o aviso do FRETEGRATIS ganha a ressalva em letra pequena
("*Não vale para pedidos com peças da sale.", Pedro 29/10) e entra uma
segunda mensagem, do cashback. A ressalva vai em <em> (o campo do tema é
inline_richtext, sem tamanho de letra) e o CSS da seção a deixa pequena e
numa linha própria. O arquivo de antes fica em campanha.estado para voltar.
"""

import copy
import json
import re

ARQUIVO = "sections/header-group.json"
RESSALVA = "*Não vale para pedidos com peças da sale."
CASHBACK = "Novembro Annis: 10% de cashback em todo o site"
CSS = ("em { display: block; font-size: 0.62em; font-style: normal; letter-spacing: 0.03em; "
       "opacity: 0.85; margin-top: 2px; }")


def _sem_comentario(texto):
    return re.sub(r"(?s)^\s*/\*.*?\*/", "", texto, count=1)


def com_campanha(conteudo: str) -> str:
    d = json.loads(_sem_comentario(conteudo))
    for sec in d["sections"].values():
        if sec["type"] != "header-announcements":
            continue
        frete = next((b for b in sec["blocks"].values() if "FRETEGRATIS" in b["settings"].get("text", "")), None)
        if frete is None:
            continue
        texto = re.sub(r"\*?\s*<em>.*?</em>", "", frete["settings"]["text"]).rstrip("* ")
        frete["settings"]["text"] = f"{texto}* <em>{RESSALVA}</em>"
        if not any(CASHBACK in b["settings"].get("text", "") for b in sec["blocks"].values()):
            novo = copy.deepcopy(frete)
            novo["settings"]["text"] = CASHBACK
            novo["settings"]["link"] = "shopify://collections/novembro-annis"
            sec["blocks"]["announcement_cashback"] = novo
            sec["block_order"].append("announcement_cashback")
        css = [c for c in sec.get("custom_css", []) if "em {" not in c]
        sec["custom_css"] = css + [CSS]
    return json.dumps(d, ensure_ascii=False, indent=2)
