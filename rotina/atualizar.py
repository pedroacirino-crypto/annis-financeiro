"""Abre o painel num navegador de verdade, para o app atualizar os dados.

Requisição simples não serve: o Streamlit só executa o código quando um
navegador abre a sessão por websocket. Testado, o `curl` baixa o HTML e nada
roda. Por isso a rotina diária usa um Chromium sem tela.

Nenhum segredo é necessário aqui. O próprio app decide sincronizar quando o
último download passou de 12 horas, e isso acontece antes da tela de senha.
Esta rotina só provoca a abertura.
"""

import os
import sys

from playwright.sync_api import sync_playwright

URL = os.environ["APP_URL"].rstrip("/")
ESPERA_MS = 8 * 60 * 1000  # a carga varre todo o histórico de recebíveis


def main() -> int:
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()
        pagina.goto(URL, wait_until="domcontentloaded")

        # A tela de senha só aparece depois que a sincronização termina, então
        # esperar por ela é esperar o trabalho acabar.
        try:
            pagina.wait_for_selector("input[type=password]", timeout=ESPERA_MS)
            print("painel atualizado e no ar")
            ok = True
        except Exception:
            print("o painel não chegou na tela de senha a tempo", file=sys.stderr)
            ok = False

        navegador.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
