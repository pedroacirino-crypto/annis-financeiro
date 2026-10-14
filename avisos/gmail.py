"""Envio dos e-mails da régua pós-compra pelo Gmail da loja, 13/10/2026.

Sai como "Annis <pedidos@annis.store>", que é alias da conta contato@annis.store
(Gmail > Configurações > Contas > Enviar e-mail como). O login é o da contato@
com uma senha de app (Conta do Google > Senhas de app > regua); a resposta da
cliente cai na mesma caixa. O Gmail guarda cada envio em Enviados.

Variável: GMAIL_SENHA_APP (os 16 caracteres, com ou sem espaços).

    python avisos/gmail.py teste    manda um e-mail de teste para contato@annis.store
"""

from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import make_msgid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shopify_client  # noqa: E402,F401  (carrega o .env)

CONTA = "contato@annis.store"
REMETENTE = "Annis <pedidos@annis.store>"


def enviar(para: str, assunto: str, html: str, texto: str, anexos: list | None = None,
           fio: str | None = None, imagens: list | None = None, cco: list | None = None) -> str:
    """Manda um e-mail e devolve o Message-ID.

    anexos: lista de (nome, bytes, tipo/subtipo). fio: Message-ID de um e-mail
    anterior do mesmo pedido, para o Gmail da cliente juntar tudo numa conversa.
    imagens: [(cid, bytes_png)] embutidas no HTML como src="cid:<cid>" (QR do Pix).
    cco: cópia oculta (não aparece no e-mail; vai só no envelope).
    """
    msg = EmailMessage()
    msg["From"] = REMETENTE
    msg["To"] = para
    msg["Subject"] = assunto
    msg["Message-ID"] = make_msgid(domain="annis.store")
    if fio:
        msg["In-Reply-To"] = fio
        msg["References"] = fio
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    parte_html = msg.get_payload()[1]
    for cid, dados in imagens or []:
        parte_html.add_related(dados, maintype="image", subtype="png", cid=f"<{cid}>")
    for nome, dados, tipo in anexos or []:
        principal, sub = tipo.split("/")
        msg.add_attachment(dados, maintype=principal, subtype=sub, filename=nome)
    senha = os.environ["GMAIL_SENHA_APP"].replace(" ", "")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(CONTA, senha)
        smtp.send_message(msg, to_addrs=[para] + list(cco or []))
    return msg["Message-ID"]


if __name__ == "__main__" and sys.argv[1:] == ["teste"]:
    mid = enviar(CONTA, "Teste da régua: envio pelo pedidos@",
                 "<p>Oi. Este é um teste do envio da régua pós-compra, saindo de pedidos@annis.store.</p><p>Annis</p>",
                 "Oi. Este é um teste do envio da régua pós-compra, saindo de pedidos@annis.store.\n\nAnnis")
    print("enviado", mid)
