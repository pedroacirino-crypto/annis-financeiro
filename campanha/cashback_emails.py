"""E-mails do cashback do Novembro Annis, no visual da régua, 29/10/2026.

Textos em avisos/regua/textos.yaml (seção cashback). Quando sai cada um
(Pedro, 29/10):

  cb1  crédito chegou: logo depois do e-mail de confirmação do pedido
  cb2  30 dias depois, se ainda tem saldo
  cb3  15/12, Natal, se ainda tem saldo
  cb4  17/01, 15 dias antes de vencer
  cb5  29/01, 3 dias antes de vencer
  cb6  crédito cancelado por devolução (só para quem recebeu o cb1)

Lembrete (cb2 a cb5) só das 9h às 20h e no máximo um por semana por e-mail.
Enviado fica em avisos.enviados como "cb:<qual>:<número do pedido>".

    python campanha/cashback_emails.py previa   manda os seis para contato@annis.store
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from avisos import gmail  # noqa: E402
from avisos.regua import blocos as B  # noqa: E402
from avisos.regua import modelos  # noqa: E402

CONTATO = "contato@annis.store"
LOJA = "https://annis.store/collections/all"
BRT = timezone(timedelta(hours=-3))
NATAL = datetime(2026, 12, 15, 9, 0, tzinfo=BRT)
LEMBRETES = ("cb2", "cb3", "cb4", "cb5")


def _codigo(rotulo: str, codigo: str) -> str:
    return B._sec(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border:1px solid {B.MARROM};background:#ffffff;"><tr><td align="center" style="padding:20px 12px;text-align:center;">'
        f'<p style="margin:0;font-size:10.5px;letter-spacing:0.16em;text-transform:uppercase;color:{B.MARROM};font-weight:500;">{B.esc(rotulo)}</p><p style="margin:6px 0 0 0;font-family:{B.SERIF};font-size:32px;letter-spacing:0.14em;'
        f'color:{B.MARROM};">{B.esc(codigo)}</p></td></tr></table>', topo=6, base=18, alinhar="center")


def _regras(rotulo: str, linhas: list) -> str:
    itens = "".join(f'<tr><td style="padding:8px 0;border-top:1px solid {B.LINHA};font-size:13px;">{B.esc(l)}</td></tr>'
                    for l in linhas)
    return B._sec(f'{B._eyebrow(rotulo)}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                  f'border="0">{itens}</table>', topo=4, base=14)


def montar(qual: str, v: dict) -> dict:
    """v: nome, valor, codigo, validade, minimo, pedido."""
    T = modelos.textos()
    g, c = T["geral"], T["cashback"]
    t = modelos._preencher(c[qual], v)
    link_wa = B.link_whatsapp(g["whatsapp"], f"Oi, é sobre o meu cashback {v['codigo']}")
    linhas = [B.faixa(t["faixa"]), B.cabecalho(), B.texto(t["texto"], t["eyebrow"], t["titulo"])]
    if qual != "cb6":
        linhas.append(_codigo(c["rotulo_codigo"], v["codigo"]))
        linhas.append(_regras(c["rotulo_regras"], t["regras"]))
        linhas.append(B.botao(t["botao"], LOJA))
    linhas.append(B.rodape(g["rodape"], [(a, b.replace("{whatsapp}", link_wa)) for a, b in g["rodape_links"]], g["empresa"]))
    html = B.documento(t["assunto"], t["previa"], linhas)
    return {"assunto": t["assunto"], "html": html, "texto": modelos._texto_plano(html, t["previa"])}


def vencido_o_prazo(qual: str, c: dict, agora: datetime) -> bool:
    """O e-mail `qual` já deveria ter saído para o crédito `c`?"""
    if qual == "cb2":
        return agora >= c["emitido_em"] + timedelta(days=30)
    if qual == "cb3":
        return agora >= NATAL and c["emitido_em"] < NATAL - timedelta(days=3)
    if qual == "cb4":
        return agora >= c["vence_em"] - timedelta(days=15)
    if qual == "cb5":
        return agora >= c["vence_em"] - timedelta(days=3)
    return False


def dados(c: dict) -> dict:
    from campanha.cashback import minimo_de
    nome = (c.get("cliente") or "").strip().split(" ")[0].title() or "tudo bem"
    return {"nome": nome, "valor": f"{float(c['valor']):.0f}", "codigo": c["codigo"],
            "validade": c["vence_em"].astimezone(BRT).strftime("%d/%m"), "minimo": minimo_de(float(c["valor"])),
            "pedido": c["pedido"]}


def previa():
    exemplo = {"nome": "Michelle", "valor": "73", "codigo": "CB8YKSRS", "validade": "31/01", "minimo": 244, "pedido": "#1159"}
    for qual in ("cb1", "cb2", "cb3", "cb4", "cb5", "cb6"):
        m = montar(qual, exemplo)
        gmail.enviar(CONTATO, f"[prévia cashback {qual}] {m['assunto']}", m["html"], m["texto"])
        print(f"{qual}: {m['assunto']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "previa":
        previa()
