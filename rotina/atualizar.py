"""Abre o painel num navegador de verdade, para o app atualizar os dados.

Requisição simples não serve: o Streamlit só executa o código quando um
navegador abre a sessão por websocket. Testado, o `curl` baixa o HTML e nada
roda. Por isso a rotina diária usa um Chromium sem tela.

Nenhum segredo é necessário aqui. O próprio app decide sincronizar quando o
último download passou de 12 horas, e isso acontece antes da tela de senha.
Esta rotina só provoca a abertura.

Dois estados, dois caminhos:

  - App acordado: `/~/+/` entra direto nele e a tela de senha aparece quando
    a sincronização termina. O endereço normal devolve o console da Streamlit
    Cloud, que embute o app num iframe, e o seletor de senha não o enxerga.
  - App hibernado: `/~/+/` responde 400 com a página vazia, porque não há
    container rodando. Foi isso que quebrou a rotina de 19/09. Quem acorda o
    app é o botão "Yes, get this app back up!" do console, então a rotina
    passa pelo console primeiro, aperta o botão se ele estiver lá e espera o
    container subir antes de ir para `/~/+/`.
"""

import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

RAIZ = os.environ["APP_URL"].rstrip("/") + "/"
APP = RAIZ + "~/+/"
ESPERA_SENHA_MS = 5 * 60 * 1000
ESPERA_ACORDAR_S = 8 * 60
BOTAO_ACORDAR = re.compile(r"get this app back up|wake", re.I)


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


def app_responde(pagina) -> bool:
    """O `_stcore/health` só responde `ok` com o container de pé."""
    try:
        r = pagina.request.get(APP + "_stcore/health", timeout=15000)
        return r.status == 200 and r.text().strip() == "ok"
    except Exception:
        return False


def acordar_se_preciso(pagina) -> None:
    if app_responde(pagina):
        print("app já estava acordado")
        return

    print("app hibernado, indo pelo console para acordar")
    pagina.goto(RAIZ, wait_until="domcontentloaded")
    botao = pagina.get_by_role("button", name=BOTAO_ACORDAR)
    try:
        botao.first.wait_for(timeout=60000)
        botao.first.click()
        print("botão de acordar apertado")
    except Exception:
        # Sem botão pode ser que outro visitante já tenha acordado o app e o
        # container esteja subindo. Só espera, então.
        print("não achei o botão de acordar, esperando o container mesmo assim")
        contar_o_que_esta_na_tela(pagina)

    inicio = time.time()
    while time.time() - inicio < ESPERA_ACORDAR_S:
        if app_responde(pagina):
            print(f"container de pé depois de {int(time.time() - inicio)}s")
            return
        time.sleep(10)
    raise RuntimeError("o container não subiu a tempo")


def main() -> int:
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()

        # Sem isto o diagnóstico vira adivinhação: título certo com corpo
        # vazio pode ser websocket barrado, script quebrado ou bloqueio de
        # cookie, e cada um pede um conserto diferente.
        erros = []
        pagina.on("console", lambda m: erros.append(f"console {m.type}: {m.text[:160]}")
                  if m.type in ("error", "warning") else None)
        pagina.on("requestfailed", lambda r: erros.append(
            f"falhou {r.failure}: {r.url[:110]}"))
        pagina.on("websocket", lambda ws: erros.append(f"websocket aberto: {ws.url[:110]}"))
        pagina.on("response", lambda r: erros.append(f"HTTP {r.status}: {r.url[:130]}")
                  if r.status >= 400 else None)

        ok = False
        try:
            acordar_se_preciso(pagina)
            pagina.goto(APP, wait_until="domcontentloaded")

            # A tela de senha só aparece depois que a sincronização termina,
            # então esperar por ela é esperar o trabalho acabar.
            pagina.wait_for_selector("input[type=password]", timeout=ESPERA_SENHA_MS)
            print("painel atualizado e no ar")
            ok = True
        except Exception as e:
            print(f"o painel não chegou na tela de senha a tempo: {e}", file=sys.stderr)
            contar_o_que_esta_na_tela(pagina)
            for err in erros[:25]:
                print(f"  {err}", file=sys.stderr)

        navegador.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
