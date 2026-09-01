"""Abre o painel num navegador de verdade e espera a sincronização terminar.

Requisição simples não serve: o Streamlit só executa o código quando um
navegador abre a sessão por websocket. Testado, o `curl` baixa o HTML e o app
não roda nada. Por isso a rotina diária usa um Chromium sem tela.

O token vai na URL. O app reconhece, sincroniza e encerra antes da senha, então
as chaves da Pagar.me e da Shopify continuam só no cofre do Streamlit.
"""

import os
import sys

from playwright.sync_api import sync_playwright

URL = os.environ["APP_URL"].rstrip("/")
TOKEN = os.environ["TOKEN_ROTINA"]
ESPERA_MS = 8 * 60 * 1000  # a primeira carga do dia varre todo o histórico


def main() -> int:
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()
        pagina.goto(f"{URL}/?rotina={TOKEN}", wait_until="domcontentloaded")
        try:
            marca = pagina.wait_for_selector(
                "text=/^(ok|falhou) ·/", timeout=ESPERA_MS)
            recado = marca.inner_text().strip()
        except Exception:
            print("não veio resposta da rotina dentro do tempo", file=sys.stderr)
            navegador.close()
            return 1
        navegador.close()

    print(recado)
    return 0 if recado.startswith("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
