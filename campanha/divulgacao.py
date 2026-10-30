"""E-mails de divulgação do Novembro Annis, 30/10/2026.

Régua (Pedro, 30/10), para quem já comprou e para quem tem cadastro sem compra:

  d1   dom 01/11 10h   todo mundo
  d2   qua 04/11 19h   quem recebeu o d1, não clicou e não comprou:
                       d2a para quem não abriu (reenvio com outro assunto),
                       d2b para quem abriu e não clicou (os tamanhos que restam)
  d3   sex 13/11 10h   quem não comprou, junto do lançamento (data a confirmar)
  d4   qui 26/11 19h   quem não comprou, semana da Black Friday

"Comprou" é qualquer pedido no site a partir de 01/11 com o mesmo e-mail; quem
compra sai da fila. Quem clicou em "Não quero mais receber" (ou cancelou o
marketing na Shopify) sai de tudo. Abertura vem do pixel (campanha/divulgacao.sql);
o Mail do iPhone abre tudo sozinho, então "abriu" de iPhone não quer dizer que leu.

Textos em avisos/regua/textos.yaml (seção divulgacao). Cada e-mail só sai com
"aprovado: true" e dentro da sua janela de datas; nunca duas vezes para a mesma
pessoa (avisos.enviados, "div:<qual>:<email>").

    python campanha/divulgacao.py previa          manda todos para contato@annis.store
    python campanha/divulgacao.py enviar d1       manda (o pg_cron chama pelo divulgacao.yml)
    python campanha/divulgacao.py enviar d1 --simular
    python campanha/divulgacao.py status          enviados, aberturas, cliques e compras
"""

import os
import re
import secrets
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402
from avisos import gmail  # noqa: E402
from avisos.regua import blocos as B  # noqa: E402
from avisos.regua import modelos, motor  # noqa: E402
from sqlalchemy import text  # noqa: E402

CONTATO = "contato@annis.store"
BRT = timezone(timedelta(hours=-3))
INICIO = datetime(2026, 11, 1, 0, 0, tzinfo=BRT)
PIXEL = ("https://mkgrmljousyvggpxmofj.supabase.co/rest/v1/rpc/abriu"
         "?apikey=sb_publishable_KMXewGoRe3rCgXEhknGzdQ_pP09F1YW&p=")
SAIR = "https://annis.store/pages/sair?p="
PAUSA = 2  # segundos entre um e-mail e outro
NAO_MANDAR = {"contato@annis.store", "pedidos@annis.store"}

# Janela em que cada e-mail pode sair (Brasília); fora dela o script recusa.
JANELA = {
    "d1": (datetime(2026, 11, 1, 0, 0, tzinfo=BRT), datetime(2026, 11, 3, 0, 0, tzinfo=BRT)),
    "d2": (datetime(2026, 11, 3, 0, 0, tzinfo=BRT), datetime(2026, 11, 7, 0, 0, tzinfo=BRT)),
    "d3": (datetime(2026, 11, 9, 0, 0, tzinfo=BRT), datetime(2026, 11, 21, 0, 0, tzinfo=BRT)),
    "d4": (datetime(2026, 11, 24, 0, 0, tzinfo=BRT), datetime(2026, 12, 1, 0, 0, tzinfo=BRT)),
}
# Os que mostram os tamanhos que restam de cada peça.
COM_TAMANHOS = ("d2a", "d2b", "d4")


def _g(q, v=None):
    return shopify_client._graphql(q, v or {})


# Quem recebe -------------------------------------------------------------------

_CLIENTES = """query($c:String){customers(first:250, after:$c){pageInfo{hasNextPage endCursor}
  nodes{firstName numberOfOrders defaultEmailAddress{emailAddress marketingState}}}}"""


def lista() -> list:
    """Todo cadastro com e-mail, menos quem saiu. grupo: cliente (já comprou) ou cadastro."""
    saida, c = {}, None
    while True:
        d = _g(_CLIENTES, {"c": c})["customers"]
        for x in d["nodes"]:
            e = x["defaultEmailAddress"]
            if not e or not e["emailAddress"]:
                continue
            email = e["emailAddress"].strip().lower()
            if email in NAO_MANDAR or e["marketingState"] in ("UNSUBSCRIBED", "REDACTED", "INVALID"):
                continue
            grupo = "cliente" if int(x["numberOfOrders"] or 0) > 0 else "cadastro"
            if email not in saida or grupo == "cliente":
                saida[email] = {"email": email, "nome": (x["firstName"] or "").strip().split(" ")[0].title(), "grupo": grupo}
        if not d["pageInfo"]["hasNextPage"]:
            break
        c = d["pageInfo"]["endCursor"]
    with nuvem._conectar().connect() as con:
        fora = set(con.execute(text("select email from campanha.descadastro")).scalars())
    return [p for e, p in saida.items() if e not in fora]


def compraram() -> set:
    """E-mails com pedido no site desde 01/11 (cancelado não conta)."""
    q = """query($c:String,$q:String!){orders(first:100, after:$c, query:$q){pageInfo{hasNextPage endCursor}
      nodes{email cancelledAt test customer{defaultEmailAddress{emailAddress}}}}}"""
    filtro = f"created_at:>='{INICIO.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}'"
    saida, c = set(), None
    while True:
        d = _g(q, {"c": c, "q": filtro})["orders"]
        for o in d["nodes"]:
            if o["cancelledAt"] or o["test"]:
                continue
            for e in (o["email"], ((o["customer"] or {}).get("defaultEmailAddress") or {}).get("emailAddress")):
                if e:
                    saida.add(e.strip().lower())
        if not d["pageInfo"]["hasNextPage"]:
            return saida
        c = d["pageInfo"]["endCursor"]


def _sinais(qual: str) -> tuple:
    """Quem recebeu `qual`, quem abriu e quem clicou (por e-mail)."""
    with nuvem._conectar().connect() as con:
        receberam = set(con.execute(text("select email from campanha.divulgacao_envios where qual = :q"), {"q": qual}).scalars())
        abriram = set(con.execute(text(
            "select distinct e.email from campanha.divulgacao_aberturas a join campanha.divulgacao_envios e using (token)"
            " where e.qual = :q"), {"q": qual}).scalars())
        clicaram = set(con.execute(text(
            "select distinct e.email from avisos.regua_cliques c join campanha.divulgacao_envios e"
            " on c.pedido = 'div:' || e.token where e.qual = :q"), {"q": qual}).scalars())
    return receberam, abriram, clicaram


def fila(qual: str) -> list:
    """[(pessoa, versão)] para o e-mail `qual` (d2 se divide em d2a e d2b)."""
    pessoas = lista()
    comprou = compraram()
    with nuvem._conectar().connect() as con:
        ja = {r[0] for r in con.execute(text("select id from avisos.enviados where id like 'div:%'"))}
    saida = []
    if qual == "d2":
        receberam, abriram, clicaram = _sinais("d1")
    for p in pessoas:
        e = p["email"]
        if e in comprou:
            continue
        if qual == "d2":
            if e not in receberam or e in clicaram:
                continue
            versao = "d2b" if e in abriram else "d2a"
        else:
            versao = qual
        if f"div:{qual}:{e}" in ja:
            continue
        saida.append((p, versao))
    return saida


# Peças --------------------------------------------------------------------------

_PRODUTOS = """query($ids:[ID!]!){nodes(ids:$ids){... on Product{id title handle status
  featuredMedia{preview{image{url}}}
  variants(first:20){nodes{title inventoryQuantity}}}}}"""
ORDEM_TAMANHOS = ["PP", "P", "M", "G", "GG", "U"]


def pecas(n: int = 6) -> list:
    """As peças com maior desconto, uma de cada tipo (corset, calça, top...),
    com os tamanhos que ainda têm estoque agora."""
    with nuvem._conectar().connect() as con:
        sale = con.execute(text(
            "select produto_id, max(desconto) as desconto, min(preco_sale) as por, max(riscado_sale) as de"
            " from campanha.sale_precos group by produto_id")).mappings().all()
    info = {p["id"]: p for p in _g(_PRODUTOS, {"ids": [s["produto_id"] for s in sale]})["nodes"] if p}
    linhas = []
    for s in sale:
        p = info.get(s["produto_id"])
        if not p or p["status"] != "ACTIVE" or not p["featuredMedia"]:
            continue
        tams = [v["title"] for v in p["variants"]["nodes"] if (v["inventoryQuantity"] or 0) > 0]
        if not tams:
            continue
        tams.sort(key=lambda t: ORDEM_TAMANHOS.index(t) if t in ORDEM_TAMANHOS else 99)
        estoque = sum(max(0, v["inventoryQuantity"] or 0) for v in p["variants"]["nodes"])
        linhas.append({"nome": p["title"], "tipo": p["title"].split(" ")[0], "url": f"https://annis.store/products/{p['handle']}",
                       "img": p["featuredMedia"]["preview"]["image"]["url"].split("?")[0] + "?width=560",
                       "desconto": float(s["desconto"]), "de": float(s["de"]), "por": float(s["por"]),
                       "tamanhos": ", ".join("Único" if t in ("U", "Default Title") else t for t in tams), "estoque": estoque})
    linhas.sort(key=lambda x: (-x["desconto"], -x["estoque"]))
    escolhidas, tipos = [], set()
    for x in linhas:
        if x["tipo"] not in tipos:
            escolhidas.append(x)
            tipos.add(x["tipo"])
    return escolhidas[:n]


# Montagem -----------------------------------------------------------------------

def _reais(v: float) -> str:
    return f"R$ {v:,.0f}".replace(",", ".")


def _banner(img: str, link: str) -> str:
    return (f'<tr><td align="center" style="padding:0;"><a href="{link}"><img src="{img}" width="600" alt="Novembro Annis" '
            f'style="width:100%;max-width:600px;height:auto;display:block;"></a></td></tr>')


def _cartao(x: dict, restam: str) -> str:
    tam = (f'<p style="margin:4px 0 0 0;font-size:11.5px;color:{B.APOIO};">{B.esc(restam.format(tamanhos=x["tamanhos"]))}</p>'
           if restam else "")
    return f"""<td width="50%" valign="top" style="width:50%;padding:0 6px 22px 6px;">
<a href="{x['url']}" style="text-decoration:none;color:{B.TINTA};"><img src="{x['img']}" width="248" alt="{B.esc(x['nome'])}" style="width:100%;height:auto;display:block;background:#efe3d6;">
<p style="margin:10px 0 0 0;font-family:{B.SERIF};font-size:17px;line-height:1.25;color:{B.TINTA};">{B.esc(x['nome'])}</p>
<p style="margin:4px 0 0 0;font-size:13px;color:{B.TINTA};"><span style="text-decoration:line-through;color:{B.APOIO};">{_reais(x['de'])}</span>&nbsp; {_reais(x['por'])}&nbsp; <span style="color:{B.MARROM};font-weight:500;">{round(x['desconto'] * 100)}% off</span></p></a>
{tam}</td>"""


def _grade(rotulo: str, itens: list, restam: str) -> str:
    linhas = "".join("<tr>" + "".join(_cartao(x, restam) for x in itens[i:i + 2]) + "</tr>" for i in range(0, len(itens), 2))
    return (f'<tr><td class="px" style="padding:10px 38px 6px 38px;">{B._eyebrow(rotulo)}'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{linhas}</table></td></tr>')


def _como(rotulo: str, linhas: list, link_regulamento: str) -> str:
    itens = "".join(f'<tr><td style="padding:8px 0;border-top:1px solid {B.LINHA};font-size:13px;">{B.esc(l)}</td></tr>' for l in linhas)
    regulamento = (f'<p style="margin:10px 0 0 0;font-size:12px;"><a href="{link_regulamento}" style="color:{B.MARROM};">'
                   f"Ver o regulamento</a></p>")
    return B._sec(f'{B._eyebrow(rotulo)}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
                  f"{itens}</table>{regulamento}", topo=4, base=14)


def montar(versao: str, nome: str, itens: list) -> dict:
    T = modelos.textos()
    g, d = T["geral"], T["divulgacao"]
    t = modelos._preencher(d[versao], {"virgula_nome": f", {nome}" if nome else ""})
    linhas = [B.faixa(t["faixa"]), B.cabecalho()]
    if d.get("banner") and versao != "d3":
        linhas.append(_banner(d["banner"], d["link_colecao"]))
    else:
        linhas.append(B.abertura(modelos.ABERTURA, t["hero_titulo"], t["hero_sub"]))
    linhas.append(B.texto(t["texto"], t["eyebrow"], t["titulo"]))
    if versao == "d3":
        linhas.append(B.botao(t["botao_lancamento"], t["link_lancamento"]))
    linhas.append(_grade(d["rotulo_pecas"], itens, d["restam"] if versao in COM_TAMANHOS else ""))
    linhas.append(B.botao(t["botao"], d["link_colecao"]))
    linhas.append(_como(d["rotulo_como"], d["como"], d["link_regulamento"]))
    links = [(a, b.replace("{whatsapp}", B.link_whatsapp(g["whatsapp"], "Oi, vi o Novembro Annis"))) for a, b in g["rodape_links"]]
    rodape = B.rodape(g["rodape"], links, g["empresa"])
    # O link de sair entra depois de com_cliques (não é clique de interesse).
    rodape = rodape.replace("</td></tr>", f'<p style="margin:14px 0 0 0;"><a href="{{SAIR}}" style="color:{B.APOIO};font-size:11px;">'
                                          f'{B.esc(d["sair"])}</a></p>{{PIXEL}}</td></tr>', 1)
    linhas.append(rodape)
    html = B.documento(t["assunto"], t["previa"], linhas)
    return {"assunto": t["assunto"], "html": html, "texto": modelos._texto_plano(html, t["previa"])}


def _finalizar(html: str, texto: str, token: str) -> tuple:
    pixel = f'<img src="{PIXEL}{token}" width="1" height="1" alt="" style="display:block;width:1px;height:1px;border:0;">'
    html = html.replace("{SAIR}", SAIR + token).replace("{PIXEL}", pixel)
    texto = texto.replace("({SAIR})", f"({SAIR}{token})")
    return html, texto


# Envio --------------------------------------------------------------------------

def _telegram(texto: str) -> None:
    from campanha.cashback import _telegram as t
    try:
        t(texto)
    except Exception as e:  # aviso não derruba o envio
        print(f"Telegram: {e}")


def enviar(qual: str, simular: bool = False) -> None:
    agora = datetime.now(BRT)
    ini, fim = JANELA[qual]
    if not simular and not (ini <= agora < fim):
        raise SystemExit(f"{qual} fora da janela ({ini:%d/%m %Hh} a {fim:%d/%m %Hh}); nada enviado")
    d = modelos.textos()["divulgacao"]
    versoes = ("d2a", "d2b") if qual == "d2" else (qual,)
    falta = [v for v in versoes if not d[v].get("aprovado")]
    if falta and not simular:
        _telegram(f"E-mail {qual} do Novembro Annis não saiu: falta aprovar o texto ({', '.join(falta)}).")
        raise SystemExit(f"{qual}: falta aprovar {falta}")
    itens = pecas()
    fila_ = fila(qual)
    print(f"{qual}: {len(fila_)} na fila, {len(itens)} peças")
    contagem, erros, copia_feita = Counter(), [], set()
    for p, versao in fila_:
        m = montar(versao, p["nome"], itens)
        if simular:
            contagem[(versao, p["grupo"])] += 1
            continue
        token = secrets.token_urlsafe(9)
        try:
            with nuvem._conectar().begin() as con:
                con.execute(text("insert into campanha.divulgacao_envios (token, email, nome, grupo, qual)"
                                 " values (:t, :e, :n, :g, :q)"), {"t": token, "e": p["email"], "n": p["nome"], "g": p["grupo"], "q": versao})
                html, texto = motor.com_cliques(con, m["html"], m["texto"], f"div:{token}", versao, "no_ar", False)
                html, texto = _finalizar(html, texto, token)
                cab = {"List-Unsubscribe": f"<{SAIR}{token}>, <mailto:{CONTATO}?subject=sair>"}
                # Uma cópia de cada versão na contato@, para conferência.
                cco = [] if versao in copia_feita else [CONTATO]
                mid = gmail.enviar(p["email"], m["assunto"], html, texto, cco=cco, cabecalhos=cab)
                copia_feita.add(versao)
                con.execute(text("update campanha.divulgacao_envios set message_id = :m where token = :t"), {"m": mid, "t": token})
                con.execute(text("insert into avisos.enviados (id, tipo) values (:i, :t) on conflict do nothing"),
                            {"i": f"div:{qual}:{p['email']}", "t": f"divulgação {versao}"})
            contagem[(versao, p["grupo"])] += 1
        except Exception as e:
            erros.append(f"{type(e).__name__}: {e}"[:160])
            if len(erros) >= 5:
                break
        time.sleep(PAUSA)
    resumo = ", ".join(f"{v} {g}: {n}" for (v, g), n in sorted(contagem.items())) or "ninguém"
    print(("(simulado) " if simular else "") + resumo)
    if not simular:
        msg = f"E-mail {qual} do Novembro Annis saiu para {sum(contagem.values())} pessoas ({resumo})."
        if erros:
            msg += f" {len(erros)} erro(s), o primeiro: {erros[0]}"
        _telegram(msg)
        if erros:
            raise SystemExit(1)


def previa() -> None:
    itens = pecas()
    for versao in ("d1", "d2a", "d2b", "d3", "d4"):
        m = montar(versao, "Michelle", itens)
        html, texto = _finalizar(m["html"], m["texto"], "previa")
        gmail.enviar(CONTATO, f"[prévia divulgação {versao}] {m['assunto']}", html, texto)
        print(f"{versao}: {m['assunto']}")


def status() -> None:
    with nuvem._conectar().connect() as con:
        envios = con.execute(text(
            "select e.qual, e.grupo, count(*) as enviados,"
            " count(distinct a.token) as abriram,"
            " count(distinct c.pedido) as clicaram"
            " from campanha.divulgacao_envios e"
            " left join campanha.divulgacao_aberturas a on a.token = e.token"
            " left join avisos.regua_cliques c on c.pedido = 'div:' || e.token"
            " group by 1, 2 order by 1, 2")).mappings().all()
        por_email = {}
        for r in con.execute(text("select email, qual from campanha.divulgacao_envios")):
            por_email.setdefault(r[0], set()).add(r[1])
        sairam = con.execute(text("select count(*) from campanha.descadastro")).scalar()
    comprou = compraram() & set(por_email)
    print("e-mail  grupo     enviados  abriram  clicaram")
    for r in envios:
        print(f"{r['qual']:7} {r['grupo']:9} {r['enviados']:8} {r['abriram']:8} {r['clicaram']:9}")
    print(f"\ncompraram depois de receber: {len(comprou)} · saíram da lista: {sairam}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["previa"]:
        previa()
    elif args[:1] == ["enviar"] and len(args) > 1 and args[1] in JANELA:
        enviar(args[1], "--simular" in args)
    elif args[:1] == ["status"]:
        status()
    else:
        print(__doc__)
