"""DANFE em PDF de cada pedido, 07/10/2026; desde 09/10 vai para a pasta do
pedido no Drive (avisos/drive.py) em vez do grupo do Telegram.

A Olist gera e autoriza a nota do pedido da Shopify sozinha, mas o "link da
nota" que a API devolve é uma página HTML, não um arquivo. O Telegram só anexa
arquivo, então este script abre a página num navegador, imprime em PDF e manda
no grupo com a legenda da nota.

Quem chama é o workflow danfe.yml, disparado pelo Supabase (avisos.notas) assim
que aparece nota autorizada de um pedido que está aguardando nota. O aviso de
pedido novo (avisos.checar) anota "aguarda_nf:<número do pedido>" em
avisos.enviados; aqui, depois de mandar o PDF, a nota vira "nf:<id na Olist>"
e a marca do pedido sai. Rodar duas vezes não repete nada, e nota sem pedido
da Shopify (venda do ateliê, por exemplo) nunca vai para o grupo.

    python avisos/danfe.py           envia as notas autorizadas pendentes
    python avisos/danfe.py 12345     envia só a nota de id 12345 (teste)

Variáveis: OLIST_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SUPABASE_URL_BANCO.
"""

import datetime as dt
import os
import sys
import tempfile

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402,F401  (carrega o .env)
from sqlalchemy import text  # noqa: E402

from avisos import drive, etiqueta  # noqa: E402

API = "https://api.tiny.com.br/api2/{}.php"
AUTORIZADAS = {6, 7}  # Autorizada, Emitida DANFE (tabela de situações da API 2.0)


def olist(metodo: str, **params) -> dict:
    r = requests.post(API.format(metodo), timeout=30,
                      data={"token": os.environ["OLIST_TOKEN"], "formato": "json", **params})
    r.raise_for_status()
    ret = r.json()["retorno"]
    if ret.get("status") != "OK":
        raise RuntimeError(f"Olist {metodo}: {ret.get('erros')}")
    return ret


def notas_recentes(dias: int = 3) -> list:
    desde = (dt.date.today() - dt.timedelta(days=dias)).strftime("%d/%m/%Y")
    notas, pagina = [], 1
    while True:
        ret = olist("notas.fiscais.pesquisa", dataInicial=desde, tipoNota="S", pagina=pagina)
        notas += [n["nota_fiscal"] for n in ret.get("notas_fiscais", [])]
        if pagina >= int(ret.get("numero_paginas") or 1):
            return notas
        pagina += 1


def pedidos_aguardando() -> set:
    with nuvem._conectar().connect() as con:
        rows = con.execute(text("select substr(id, 12) from avisos.enviados "
                                "where id like 'aguarda_nf:%'")).fetchall()
    return {r[0] for r in rows}


def anotar(n: dict) -> None:
    with nuvem._conectar().begin() as con:
        con.execute(text("insert into avisos.enviados (id, tipo) values (:id, 'nota fiscal') "
                         "on conflict do nothing"), {"id": f"nf:{n['id']}"})
        con.execute(text("delete from avisos.enviados where id = :id"),
                    {"id": f"aguarda_nf:{n.get('numero_ecommerce')}"})


def legenda(n: dict) -> str:
    valor = f"{float(n['valor']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    pedido = f" · pedido #{n['numero_ecommerce']}" if n.get("numero_ecommerce") else ""
    nome = (n.get("nome") or "").strip().title()
    return f"NF {n['numero']} autorizada{pedido}\n{nome} · R$ {valor}"


def pdf_da_danfe(navegador, link: str, destino: str) -> None:
    pagina = navegador.new_page()
    try:
        pagina.goto(link, wait_until="networkidle", timeout=60000)
        pagina.pdf(path=destino, format="A4", print_background=True,
                   margin={"top": "8mm", "bottom": "8mm", "left": "8mm", "right": "8mm"})
    finally:
        pagina.close()


def telegram(arquivo: str, nome: str, texto: str) -> None:
    with open(arquivo, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendDocument",
            data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": texto},
            files={"document": (nome, f, "application/pdf")}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram: HTTP {r.status_code} {r.text[:200]}")


def mensagem(texto: str) -> None:
    requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                  data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": texto}, timeout=30)


def main(so_id: str = None) -> None:
    notas = [n for n in notas_recentes() if int(n["situacao"]) in AUTORIZADAS]
    if so_id:
        notas = [n for n in notas if str(n["id"]) == so_id]
    else:
        aguardando = pedidos_aguardando()
        notas = [n for n in notas if str(n.get("numero_ecommerce") or "") in aguardando]
    if not notas:
        print("nenhuma nota nova")
        return

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p, tempfile.TemporaryDirectory() as pasta:
        navegador = p.chromium.launch()
        for n in notas:
            link = olist("nota.fiscal.obter.link", id=n["id"])["link_nfe"]
            # Padrão do Pedro para salvar: "NF ANNIS_145", número sem os zeros à esquerda.
            nome = f"NF ANNIS_{int(n['numero'])}.pdf"
            arquivo = os.path.join(pasta, nome)
            pdf_da_danfe(navegador, link, arquivo)
            # Desde 09/10/2026 a nota vai para a pasta do pedido no Drive e entra
            # no PDF do dia (avisos/envios.py); no grupo, só se o Drive não
            # estiver configurado, como era antes.
            if drive.configurado() and n.get("numero_ecommerce"):
                cliente = olist("nota.fiscal.obter", id=n["id"])["nota_fiscal"]
                n["cpf"], n["chave_acesso"] = cliente["cliente"].get("cpf_cnpj"), cliente.get("chave_acesso")
                drive.salvar(drive.pasta_do_pedido(n.get("nome"), n["cpf"], n["numero_ecommerce"],
                                                   n.get("data_emissao")), nome, arquivo)
            else:
                telegram(arquivo, nome, legenda(n))
            if not so_id:
                anotar(n)
            print(f"NF {n['numero']} salva")
            # Com a nota autorizada, a etiqueta dos Correios sai em seguida
            # (avisos/etiqueta.py). Só roda com as credenciais dos Correios
            # configuradas; erro na etiqueta não segura a nota.
            if not so_id and etiqueta.configurado() and n.get("numero_ecommerce"):
                try:
                    print("etiqueta", etiqueta.processar(n["numero_ecommerce"], nota=n))
                except Exception as erro:
                    mensagem(f"Etiqueta do pedido #{n['numero_ecommerce']} não saiu: {str(erro)[:300]}\n"
                             "Gerar à mão no Meu Correios.")
        navegador.close()

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
