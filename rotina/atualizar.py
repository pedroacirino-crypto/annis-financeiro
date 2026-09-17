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
ESPERA_MS = 5 * 60 * 1000


def contar_o_que_esta_na_tela(pagina) -> None:
    """Sem isto, a falha diz apenas 'não chegou', que não ajuda ninguém."""
    try:
        print(f"  url final : {pagina.url}", file=sys.stderr)
        print(f"  título    : {pagina.title()}", file=sys.stderr)
        texto = (pagina.inner_text("body") or "").strip().replace("\n", " | ")
        print(f"  tela      : {texto[:400]}", file=sys.stderr)
        botoes = pagina.locator("button").all_inner_texts()
        print(f"  botões    : {botoes[:8]}", file=sys.stderr)
    except Exception as e:
        print(f"  não consegui ler a tela: {e}", file=sys.stderr)


def main() -> int:
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()
        pagina.goto(URL, wait_until="domcontentloaded")

        # A Streamlit Cloud hiberna o app e mostra uma tela pedindo para
        # acordar. Sem clicar aqui, a rotina espera para sempre por uma tela
        # de senha que não vem.
        for rotulo in ("Yes, get this app back up!", "app back up",
                       "Acordar", "Wake"):
            try:
                botao = pagina.get_by_text(rotulo, exact=False).first
                if botao.is_visible(timeout=3000):
                    print(f"app estava hibernando, cliquei em: {rotulo}")
                    botao.click()
                    break
            except Exception:
                continue

        # A tela de senha só aparece depois que a sincronização termina, então
        # esperar por ela é esperar o trabalho acabar.
        try:
            pagina.wait_for_selector("input[type=password]", timeout=ESPERA_MS)
            print("painel atualizado e no ar")
            ok = True
        except Exception:
            print("o painel não chegou na tela de senha a tempo", file=sys.stderr)
            contar_o_que_esta_na_tela(pagina)
            ok = False

        navegador.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
