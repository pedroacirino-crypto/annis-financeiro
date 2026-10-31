"""Novembro Annis 2026 na Shopify: liga e desliga a sale, 29/10/2026.

    python campanha/novembro.py preparar [--simular]   tags, coleções e desconto simbólico agendado
    python campanha/novembro.py aplicar  [--simular]   preços da sale e cupons só fora da sale
    python campanha/novembro.py reverter [--simular]   preços e cupons como estavam
    python campanha/novembro.py status

O preço de cada variante vem de campanha.sale_precos, congelado por
campanha/modelo_sale.py no Mac. Quem roda "aplicar" à meia-noite de 01/11 e
"reverter" às 23h59 de 30/11 é o workflow campanha.yml, disparado pelo pg_cron
do Supabase (ver campanha/campanha.sql).

Frete grátis (ideia do Pedro, 29/10): as peças da sale ganham um desconto
automático de R$ 0,01, "Novembro Annis". O FRETEGRATIS não soma com desconto
de produto, e a Shopify dá prioridade ao desconto de produto: carrinho com peça
da sale fica sem frete grátis, inclusive o misto.

Cupons de influenciadora e pessoais valiam em todo o site e não somam com
desconto de produto. Pela regra de melhor desconto da Shopify eles ganhariam do
R$ 0,01, entrariam nas peças da sale e devolveriam o frete grátis. No período
eles passam a valer só na coleção Fora da sale e a somar com desconto de
produto; o estado anterior fica em campanha.cupons_antes para o reverter.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402
from sqlalchemy import text  # noqa: E402

TAG = "Novembro Annis"           # era "sale-novembro"; o filtro Coleção do tema mostra a etiqueta para a cliente (30/10)
COLECAO_SALE = "Novembro Annis"   # renomeada em 29/10 (era "Sale Novembro Annis"); /collections/novembro-annis
LOJA_ONLINE = "Online Store"
MENU = "main-menu"
ITEM_MENU_ANTIGO = "Sale"         # item do menu que vira "Novembro Annis" no período
COLECAO_FORA = "Fora da sale"
DESCONTO_SIMBOLICO = "Novembro Annis"
INICIO = "2026-11-01T03:00:00Z"   # 00h de 01/11 em Brasília
FIM = "2026-12-01T02:59:59Z"      # 23h59min59s de 30/11 em Brasília
NAO_MEXER = {"FRETEGRATIS"}       # cupom de frete; os de cashback têm título "Cashback #pedido"


def _g(q, v=None):
    return shopify_client._graphql(q, v or {})


def _erros(r, chave):
    if r[chave].get("userErrors"):
        raise RuntimeError(f"Shopify {chave}: {r[chave]['userErrors']}")
    return r[chave]


def _precos():
    with nuvem._conectar().connect() as con:
        return [dict(r) for r in con.execute(text("select * from campanha.sale_precos order by produto, tamanho")).mappings()]


def _colecao(titulo):
    d = _g('query($q:String!){collections(first:5, query:$q){nodes{id title}}}', {"q": f'title:"{titulo}"'})
    return next((c["id"] for c in d["collections"]["nodes"] if c["title"] == titulo), None)


# ── preparar ─────────────────────────────────────────────────────────────────

def preparar(simular):
    linhas = _precos()
    produtos = sorted({r["produto_id"] for r in linhas})
    print(f"{len(produtos)} produtos recebem a tag {TAG}")
    if not simular:
        for pid in produtos:
            _erros(_g('mutation($id:ID!,$t:[String!]!){tagsAdd(id:$id, tags:$t){userErrors{message}}}',
                      {"id": pid, "t": [TAG]}), "tagsAdd")

    regras = {COLECAO_SALE: "EQUALS", COLECAO_FORA: "NOT_EQUALS"}
    ids = {}
    for titulo, relacao in regras.items():
        ids[titulo] = _colecao(titulo)
        if ids[titulo]:
            print(f"coleção {titulo} já existe")
            continue
        print(f"cria coleção {titulo} (tag {relacao} {TAG})")
        if not simular:
            r = _erros(_g('mutation($c:CollectionInput!){collectionCreate(input:$c){collection{id} userErrors{message}}}',
                          {"c": {"title": titulo, "ruleSet": {"appliedDisjunctively": False, "rules": [
                              {"column": "TAG", "relation": relacao, "condition": TAG}]}}}), "collectionCreate")
            ids[titulo] = r["collection"]["id"]

    existe = _g('query($q:String!){automaticDiscountNodes(first:5, query:$q){nodes{id automaticDiscount{... on DiscountAutomaticBasic{title}}}}}',
                {"q": f'title:"{DESCONTO_SIMBOLICO}"'})["automaticDiscountNodes"]["nodes"]
    if any((n["automaticDiscount"] or {}).get("title") == DESCONTO_SIMBOLICO for n in existe):
        print(f"desconto {DESCONTO_SIMBOLICO} já existe")
    else:
        print(f"cria desconto automático {DESCONTO_SIMBOLICO}: R$ 0,01 por peça da sale, de {INICIO} a {FIM}")
        if not simular:
            _erros(_g('''mutation($d:DiscountAutomaticBasicInput!){discountAutomaticBasicCreate(automaticBasicDiscount:$d){
                automaticDiscountNode{id} userErrors{field message}}}''', {"d": {
                "title": DESCONTO_SIMBOLICO, "startsAt": INICIO, "endsAt": FIM,
                "combinesWith": {"productDiscounts": True, "orderDiscounts": True, "shippingDiscounts": False},
                "customerGets": {"value": {"discountAmount": {"amount": "0.01", "appliesOnEachItem": True}},
                                 "items": {"collections": {"add": [ids[COLECAO_SALE]]}}}}}), "discountAutomaticBasicCreate")


# ── preços ───────────────────────────────────────────────────────────────────

def _atualizar_precos(linhas, chave_preco, chave_riscado, simular):
    por_produto = {}
    for r in linhas:
        por_produto.setdefault(r["produto_id"], []).append(r)
    for pid, rs in por_produto.items():
        variantes = [{"id": r["variante_id"], "price": f"{float(r[chave_preco]):.2f}",
                      "compareAtPrice": f"{float(r[chave_riscado]):.2f}" if r[chave_riscado] is not None else None}
                     for r in rs]
        print(f"  {rs[0]['produto']}: " + ", ".join(f"{r['tamanho']} {float(r[chave_preco]):.0f}" for r in rs))
        if not simular:
            _erros(_g('''mutation($p:ID!,$v:[ProductVariantsBulkInput!]!){productVariantsBulkUpdate(productId:$p, variants:$v){
                userErrors{field message}}}''', {"p": pid, "v": variantes}), "productVariantsBulkUpdate")


# ── cupons ───────────────────────────────────────────────────────────────────

_CUPONS = '''query($c:String){codeDiscountNodes(first:100, after:$c, query:"status:active"){pageInfo{hasNextPage endCursor}
  nodes{id codeDiscount{__typename ... on DiscountCodeBasic{title codes(first:1){nodes{code}}
    combinesWith{productDiscounts orderDiscounts shippingDiscounts}
    customerGets{items{__typename ... on DiscountCollections{collections(first:10){nodes{id}}}}}}}}}}'''


def _cupons():
    saida, c = [], None
    while True:
        d = _g(_CUPONS, {"c": c})["codeDiscountNodes"]
        saida += [n for n in d["nodes"] if n["codeDiscount"]["__typename"] == "DiscountCodeBasic"]
        if not d["pageInfo"]["hasNextPage"]:
            return saida
        c = d["pageInfo"]["endCursor"]


_ATUALIZA_CUPOM = '''mutation($id:ID!,$d:DiscountCodeBasicInput!){discountCodeBasicUpdate(id:$id, basicCodeDiscount:$d){
  codeDiscountNode{id} userErrors{field message}}}'''


def cupons_fora_da_sale(simular):
    fora = _colecao(COLECAO_FORA)
    if not fora:
        raise SystemExit("coleção Fora da sale não existe: rodar preparar")
    for n in _cupons():
        cd = n["codeDiscount"]
        codigo = cd["codes"]["nodes"][0]["code"] if cd["codes"]["nodes"] else cd["title"]
        if codigo in NAO_MEXER or cd["title"].startswith("Cashback"):
            continue
        print(f"  {codigo}: passa a valer só em {COLECAO_FORA} e a somar com desconto de produto")
        if simular:
            continue
        with nuvem._conectar().begin() as con:
            con.execute(text("insert into campanha.cupons_antes (cupom_id, codigo, estado) values (:i, :c, :e)"
                             " on conflict (cupom_id) do nothing"),
                        {"i": n["id"], "c": codigo, "e": json.dumps(cd)})
        _erros(_g(_ATUALIZA_CUPOM, {"id": n["id"], "d": {
            "customerGets": {"items": {"collections": {"add": [fora]}}},
            "combinesWith": {**cd["combinesWith"], "productDiscounts": True}}}), "discountCodeBasicUpdate")


def cupons_voltar(simular):
    with nuvem._conectar().connect() as con:
        antes = [dict(r) for r in con.execute(text("select * from campanha.cupons_antes")).mappings()]
    for a in antes:
        estado = a["estado"] if isinstance(a["estado"], dict) else json.loads(a["estado"])
        print(f"  {a['codigo']}: volta a valer em todo o site")
        if simular:
            continue
        itens = estado["customerGets"]["items"]
        alvo = {"all": True} if itens["__typename"] == "AllDiscountItems" else \
            {"collections": {"add": [c["id"] for c in itens["collections"]["nodes"]]}}
        _erros(_g(_ATUALIZA_CUPOM, {"id": a["cupom_id"], "d": {
            "customerGets": {"items": alvo}, "combinesWith": estado["combinesWith"]}}), "discountCodeBasicUpdate")
        with nuvem._conectar().begin() as con:
            con.execute(text("delete from campanha.cupons_antes where cupom_id = :i"), {"i": a["cupom_id"]})


# ── comandos ─────────────────────────────────────────────────────────────────

def _estado(chave, valor=None):
    """Estado guardado para reverter (campanha.estado). Sem valor, lê."""
    with nuvem._conectar().begin() as con:
        if valor is None:
            r = con.execute(text("select valor from campanha.estado where chave = :c"), {"c": chave}).scalar()
            return r if r is None or isinstance(r, (dict, list)) else json.loads(r)
        con.execute(text("insert into campanha.estado (chave, valor) values (:c, cast(:v as jsonb))"
                         " on conflict (chave) do update set valor = excluded.valor"), {"c": chave, "v": json.dumps(valor)})


_MENUS = "{menus(first:5){nodes{id handle title items{id title type url resourceId tags items{id title type url resourceId tags}}}}}"
_GRAVA_MENU = ("mutation($id:ID!,$t:String!,$h:String!,$i:[MenuItemUpdateInput!]!){"
               "menuUpdate(id:$id,title:$t,handle:$h,items:$i){userErrors{field message}}}")
_PUBLICA = "mutation($id:ID!,$p:[PublicationInput!]!){publishablePublish(id:$id, input:$p){userErrors{message}}}"
_DESPUBLICA = "mutation($id:ID!,$p:[PublicationInput!]!){publishableUnpublish(id:$id, input:$p){userErrors{message}}}"


def _menu():
    return next(m for m in _g(_MENUS)["menus"]["nodes"] if m["handle"] == MENU)


def _item_entrada(i):
    d = {"id": i["id"], "title": i["title"], "type": i["type"], "tags": i.get("tags") or []}
    if i.get("resourceId"):
        d["resourceId"] = i["resourceId"]
    else:
        d["url"] = i["url"]
    d["items"] = [_item_entrada(x) for x in i.get("items") or []]
    return d


def _gravar_menu(menu, itens):
    _erros(_g(_GRAVA_MENU, {"id": menu["id"], "t": menu["title"], "h": menu["handle"], "i": itens}), "menuUpdate")


def _publicacao():
    return next(p["id"] for p in _g('{publications(first:10){nodes{id name}}}')["publications"]["nodes"]
                if p["name"] == LOJA_ONLINE)


def vitrine_ligar(simular):
    """Publica a coleção da campanha e troca o item "Sale" do menu por ela."""
    colecao = _colecao(COLECAO_SALE)
    menu = _menu()
    itens = [_item_entrada(i) for i in menu["items"]]
    alvo = next((i for i in itens if i["title"] in (ITEM_MENU_ANTIGO, COLECAO_SALE)), None)
    print(f"  publica {COLECAO_SALE} na loja; menu: {alvo['title'] if alvo else 'sem item'} -> {COLECAO_SALE};"
          " publica o regulamento e põe o link no rodapé")
    if simular:
        return
    _erros(_g(_PUBLICA, {"id": colecao, "p": [{"publicationId": _publicacao()}]}), "publishablePublish")
    if alvo and alvo["title"] == ITEM_MENU_ANTIGO:
        if _estado("menu_antes") is None:
            _estado("menu_antes", menu["items"])
        alvo.update({"title": COLECAO_SALE, "type": "COLLECTION", "resourceId": colecao})
        alvo.pop("url", None)
        _gravar_menu(menu, itens)
    regulamento_ligar()


REGULAMENTO = "regulamento-novembro-annis"
RODAPE = "footer"


def regulamento_ligar():
    """Publica a página de regras e põe o link no rodapé. Fica no ar até o fim
    do cashback (31/01/2027); o reverter da sale não tira (tirar à mão em fevereiro)."""
    pag = _g('query($q:String!){pages(first:1, query:$q){nodes{id isPublished}}}', {"q": f"handle:{REGULAMENTO}"})["pages"]["nodes"]
    if not pag:
        print("  página de regulamento não existe")
        return
    if not pag[0]["isPublished"]:
        _erros(_g('mutation($id:ID!,$p:PageUpdateInput!){pageUpdate(id:$id, page:$p){userErrors{message}}}',
                  {"id": pag[0]["id"], "p": {"isPublished": True}}), "pageUpdate")
    rod = next(m for m in _g(_MENUS)["menus"]["nodes"] if m["handle"] == RODAPE)
    itens = [_item_entrada(i) for i in rod["items"]]
    if not any(i.get("resourceId") == pag[0]["id"] for i in itens):
        itens.append({"title": "Regulamento Novembro Annis", "type": "PAGE", "resourceId": pag[0]["id"], "tags": [], "items": []})
        _gravar_menu(rod, itens)


def vitrine_desligar(simular):
    antes = _estado("menu_antes")
    print(f"  menu volta como era ({'guardado' if antes else 'nada guardado'}); {COLECAO_SALE} sai da loja")
    if simular:
        return
    if antes:
        _gravar_menu(_menu(), [_item_entrada(i) for i in antes])
        with nuvem._conectar().begin() as con:
            con.execute(text("delete from campanha.estado where chave = 'menu_antes'"))
    _erros(_g(_DESPUBLICA, {"id": _colecao(COLECAO_SALE), "p": [{"publicationId": _publicacao()}]}), "publishableUnpublish")


TEMA = "gid://shopify/OnlineStoreTheme/161637400611"  # Atelier publicado
_LER_ARQUIVO = ('query($t:ID!,$f:[String!]!){theme(id:$t){files(filenames:$f, first:2){nodes{'
                'body{... on OnlineStoreThemeFileBodyText{content}}}}}}')
_GRAVA_ARQUIVO = ('mutation($t:ID!,$f:[OnlineStoreThemeFilesUpsertFileInput!]!){themeFilesUpsert(themeId:$t, files:$f){'
                  'userErrors{filename message}}}')


def _arquivo_tema(nome):
    return _g(_LER_ARQUIVO, {"t": TEMA, "f": [nome]})["theme"]["files"]["nodes"][0]["body"]["content"]


def _gravar_arquivo_tema(nome, conteudo):
    _erros(_g(_GRAVA_ARQUIVO, {"t": TEMA, "f": [{"filename": nome, "body": {"type": "TEXT", "value": conteudo}}]}),
           "themeFilesUpsert")


def faixa_ligar(simular):
    """Ressalva pequena no FRETEGRATIS e mensagem do cashback (campanha/faixa.py)."""
    import faixa
    print("  faixa: ressalva do frete em letra pequena e mensagem do cashback")
    if simular:
        return
    atual = _arquivo_tema(faixa.ARQUIVO)
    if _estado("faixa_antes") is None:
        _estado("faixa_antes", {"conteudo": atual})
    _gravar_arquivo_tema(faixa.ARQUIVO, faixa.com_campanha(atual))


def faixa_desligar(simular):
    import faixa
    antes = _estado("faixa_antes")
    print(f"  faixa volta como era ({'guardada' if antes else 'nada guardado'})")
    if simular or not antes:
        return
    _gravar_arquivo_tema(faixa.ARQUIVO, antes["conteudo"])
    with nuvem._conectar().begin() as con:
        con.execute(text("delete from campanha.estado where chave = 'faixa_antes'"))


# ── home ─────────────────────────────────────────────────────────────────────
# Na virada o primeiro bloco da Home (o hero em tela cheia, hoje da Monet) troca
# para a arte do Novembro Annis e o clique leva para a coleção (Pedro, 29/10).
# Sem HOME_ARTE a Home fica como está e o checklist acusa. Título e texto por
# cima: None tira o bloco (a arte já traz o texto); senão, o que estiver aqui.

HOME = "templates/index.json"
HOME_HERO = "hero_iyt6zg"
HOME_LINK = "/collections/novembro-annis"
# Artes do Pedro (31/10): foto de fundo e o título "Novembro Annis" em branco.
HOME_ARTE = "shopify://shop_images/YLWT5445.png"
HOME_TITULO = "shopify://shop_images/NOVEMBRO_ANNIS_TITLE_01.png"
HOME_TEXTO = (f'<h3><a href="{HOME_LINK}" title="{HOME_LINK}"><em>Até 40% off + 10% de volta</em></a></h3>')
# Prévia (31/10): a mesma home numa página publicada e escondida (fora do menu e
# da busca do Google), em annis.store/pages/home-novembro. Sai do tema depois.
HOME_PREVIA = "templates/page.home-novembro.json"


def home_com_campanha(conteudo):
    import re
    import faixa
    d = json.loads(faixa._sem_comentario(conteudo))
    sec = d["sections"][HOME_HERO]
    sec["settings"].update({"link": HOME_LINK, "image_1": HOME_ARTE})
    for chave, b in list(sec.get("blocks", {}).items()):
        novo = HOME_TITULO if b["type"] == "image" else HOME_TEXTO if b["type"] == "text" else ""
        if novo is None:
            del sec["blocks"][chave]
            sec["block_order"].remove(chave)
            continue
        if b["type"] == "image":
            b["settings"].update({"image": novo, "link": HOME_LINK})
        elif b["type"] == "text":
            b["settings"]["text"] = re.sub(r'(href|title)="[^"]*"', lambda m: f'{m.group(1)}="{HOME_LINK}"', novo)
    # No celular a home da Monet põe o título no pé da foto, que fica abaixo da
    # primeira tela: aqui o título vai para o meio (Pedro, 31/10).
    sec["custom_css"] = [HOME_CSS_CELULAR]
    return json.dumps(d, ensure_ascii=False, indent=2)


HOME_CSS_CELULAR = ("@media screen and (max-width: 749px) {.hero__image {object-position: 70% 50% !important; } "
                    ".hero__content-wrapper {justify-content: center !important; padding-bottom: 0 !important; }}")


def home_ligar(simular):
    if not HOME_ARTE:
        print("  home: arte ainda não chegou, fica como está")
        return
    print(f"  home: arte do Novembro Annis, clique para {HOME_LINK}")
    if simular:
        return
    atual = _arquivo_tema(HOME)
    if _estado("home_antes") is None:
        _estado("home_antes", {"conteudo": atual})
    _gravar_arquivo_tema(HOME, home_com_campanha(atual))


def home_desligar(simular):
    antes = _estado("home_antes")
    print(f"  home volta como era ({'guardada' if antes else 'nada guardado'})")
    if simular or not antes:
        return
    _gravar_arquivo_tema(HOME, antes["conteudo"])
    with nuvem._conectar().begin() as con:
        con.execute(text("delete from campanha.estado where chave = 'home_antes'"))


def aplicar(simular):
    print("preços da sale:")
    _atualizar_precos(_precos(), "preco_sale", "riscado_sale", simular)
    print("cupons:")
    cupons_fora_da_sale(simular)
    print("vitrine:")
    vitrine_ligar(simular)
    faixa_ligar(simular)
    home_ligar(simular)


def reverter(simular):
    print("preços de antes:")
    _atualizar_precos(_precos(), "preco_antes", "riscado_antes", simular)
    print("cupons:")
    cupons_voltar(simular)
    print("vitrine:")
    vitrine_desligar(simular)
    faixa_desligar(simular)
    home_desligar(simular)


def status():
    linhas = _precos()
    ids = [r["variante_id"] for r in linhas]
    d = _g('query($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id price compareAtPrice}}}', {"ids": ids})
    atual = {n["id"]: n for n in d["nodes"] if n}
    na_sale = sum(1 for r in linhas if float(atual[r["variante_id"]]["price"]) == float(r["preco_sale"]))
    print(f"{na_sale} de {len(linhas)} variantes com o preço da sale")
    print("coleções:", {t: bool(_colecao(t)) for t in (COLECAO_SALE, COLECAO_FORA)})


# ── conferência (mensagem do Telegram) ───────────────────────────────────────
# Depois do aplicar e do reverter, o workflow roda "conferir" e manda no grupo a
# lista do que deveria ter ido, com check no que a loja mostra que foi (Pedro,
# 29/10). Cada item lê o estado de verdade, não o que o script acha que fez.

LOJA_URL = "https://annis.store"


def _pagina(caminho):
    import random
    import requests
    return requests.get(f"{LOJA_URL}{caminho}", params={"conferir": random.randint(1, 10**9)}, timeout=30)


def _confere_precos(fase):
    linhas = _precos()
    d = _g('query($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id price compareAtPrice}}}',
           {"ids": [r["variante_id"] for r in linhas]})
    atual = {n["id"]: n for n in d["nodes"] if n}
    preco, riscado = ("preco_sale", "riscado_sale") if fase == "aplicar" else ("preco_antes", "riscado_antes")

    def certo(r):
        v = atual.get(r["variante_id"])
        if not v or float(v["price"]) != float(r[preco]):
            return False
        esperado = float(r[riscado]) if r[riscado] is not None else None
        return (float(v["compareAtPrice"]) if v["compareAtPrice"] else None) == esperado

    ok = sum(1 for r in linhas if certo(r))
    pecas = len({r["produto_id"] for r in linhas})
    nome = "Preços da sale" if fase == "aplicar" else "Preços de antes"
    return ok == len(linhas), f"{nome}: {ok} de {len(linhas)} tamanhos ({pecas} peças)"


def _desconto_simbolico():
    d = _g('query($q:String!){automaticDiscountNodes(first:5, query:$q){nodes{automaticDiscount{'
           '... on DiscountAutomaticBasic{title status endsAt}}}}}', {"q": f'title:"{DESCONTO_SIMBOLICO}"'})
    return next((n["automaticDiscount"] for n in d["automaticDiscountNodes"]["nodes"]
                 if (n["automaticDiscount"] or {}).get("title") == DESCONTO_SIMBOLICO), None)


def _confere_frete_bloqueado(fase):
    d = _desconto_simbolico()
    if fase == "aplicar":
        return bool(d and d["status"] == "ACTIVE"), "Frete grátis bloqueado com peça da sale (desconto de R$ 0,01 ativo)"
    return bool(d and d["endsAt"] == FIM), "Desconto de R$ 0,01 encerra 23h59 de hoje e o frete grátis volta para tudo"


def _confere_fretegratis(fase):
    d = _g('{codeDiscountNodes(first:1, query:"code:FRETEGRATIS"){nodes{codeDiscount{'
           '... on DiscountCodeFreeShipping{status combinesWith{productDiscounts}}}}}}')["codeDiscountNodes"]["nodes"]
    c = d[0]["codeDiscount"] if d else {}
    return (c.get("status") == "ACTIVE" and c["combinesWith"]["productDiscounts"] is False,
            "FRETEGRATIS ativo, sem somar com a sale")


def _confere_cupons(fase):
    fora = _colecao(COLECAO_FORA)
    alvo = [n["codeDiscount"] for n in _cupons()
            if n["codeDiscount"]["title"] not in NAO_MEXER and not n["codeDiscount"]["title"].startswith("Cashback")]

    def so_fora(cd):
        itens = cd["customerGets"]["items"]
        return (itens["__typename"] == "DiscountCollections"
                and fora in [c["id"] for c in itens["collections"]["nodes"]] and cd["combinesWith"]["productDiscounts"])

    if fase == "aplicar":
        ok = sum(1 for cd in alvo if so_fora(cd))
        return ok == len(alvo), f"Cupons de influenciadora só fora da sale: {ok} de {len(alvo)}"
    with nuvem._conectar().connect() as con:
        pendentes = con.execute(text("select count(*) from campanha.cupons_antes")).scalar()
    ok = sum(1 for cd in alvo if not so_fora(cd))
    return ok == len(alvo) and not pendentes, f"Cupons de influenciadora de volta para o site todo: {ok} de {len(alvo)}"


def _confere_colecao(fase):
    d = _g('query($id:ID!){collection(id:$id){productsCount{count} resourcePublicationsV2(first:10){nodes{isPublished publication{name}}}}}',
           {"id": _colecao(COLECAO_SALE)})["collection"]
    no_ar = any(p["isPublished"] and p["publication"]["name"] == LOJA_ONLINE for p in d["resourcePublicationsV2"]["nodes"])
    if fase == "aplicar":
        return no_ar, f"Coleção {COLECAO_SALE} na loja, {d['productsCount']['count']} peças"
    return not no_ar, f"Coleção {COLECAO_SALE} fora da loja"


def _confere_menu(fase):
    titulos = [i["title"] for i in _menu()["items"]]
    if fase == "aplicar":
        return COLECAO_SALE in titulos and ITEM_MENU_ANTIGO not in titulos, f"Menu: {ITEM_MENU_ANTIGO} virou {COLECAO_SALE}"
    return ITEM_MENU_ANTIGO in titulos and COLECAO_SALE not in titulos, f"Menu: {COLECAO_SALE} voltou a ser {ITEM_MENU_ANTIGO}"


def _confere_regulamento(fase):
    pag = _g('query($q:String!){pages(first:1, query:$q){nodes{id isPublished}}}', {"q": f"handle:{REGULAMENTO}"})["pages"]["nodes"]
    rod = next(m for m in _g(_MENUS)["menus"]["nodes"] if m["handle"] == RODAPE)
    ok = bool(pag) and pag[0]["isPublished"] and any(i.get("resourceId") == pag[0]["id"] for i in rod["items"])
    return ok, "Regulamento no ar e no rodapé" + ("" if fase == "aplicar" else " (fica até o fim do cashback, 31/01)")


def _confere_faixa(fase):
    import faixa
    atual = json.dumps(json.loads(faixa._sem_comentario(_arquivo_tema(faixa.ARQUIVO))), ensure_ascii=False)
    tem = faixa.RESSALVA in atual and faixa.CASHBACK in atual
    if fase == "aplicar":
        return tem, "Faixa do topo: ressalva do frete e 10% de cashback"
    return faixa.RESSALVA not in atual and faixa.CASHBACK not in atual, "Faixa do topo como era antes"


def _confere_home(fase):
    import faixa
    hero = json.loads(faixa._sem_comentario(_arquivo_tema(HOME)))["sections"][HOME_HERO]["settings"]
    if fase == "aplicar":
        if not HOME_ARTE:
            return False, "Home nova: a arte ainda não chegou, ficou a Home de antes"
        return hero.get("image_1") == HOME_ARTE and hero.get("link") == HOME_LINK, "Home nova, com o clique para a coleção"
    return hero.get("link") != HOME_LINK, "Home como era antes"


def _confere_cron(nome):
    with nuvem._conectar().connect() as con:
        ativo = con.execute(text("select active from cron.job where jobname = :n"), {"n": nome}).scalar()
        ultima = con.execute(text("select d.status from cron.job_run_details d join cron.job j using (jobid)"
                                  " where j.jobname = :n order by d.start_time desc limit 1"), {"n": nome}).scalar()
    return bool(ativo), ultima


def _confere_cashback(fase):
    ativo, ultima = _confere_cron("annis-cashback")
    return ativo and ultima == "succeeded", "Cashback ligado: confere os pedidos a cada 10 min, última rodada ok"


def _confere_desligamento(fase):
    ativo, _ = _confere_cron("annis-campanha-reverter")
    return ativo, "Desligamento agendado para 30/11 às 23h59"


def _respondeu(r):
    return "" if r.status_code == 200 else f" (a loja respondeu {r.status_code})"


def _confere_filtro(fase):
    r = _pagina("/collections/all")
    aparece = 'value="Novembro Annis"' in r.text
    if fase == "aplicar":
        return r.status_code == 200 and aparece, f"Filtro Coleção do site mostra {COLECAO_SALE}" + _respondeu(r)
    return r.status_code == 200 and not aparece, f"Filtro Coleção do site sem {COLECAO_SALE}" + _respondeu(r)


def _confere_vitrine(fase):
    import re
    r = _pagina("/collections/novembro-annis")
    if fase == "aplicar":
        # algumas peças já tinham preço riscado antes da campanha e a chamada do topo diz "até 40% off":
        # procura o maior desconto da sale no selo de um card
        maior = max(round((1 - float(x["preco_sale"]) / float(x["riscado_sale"])) * 100) for x in _precos())
        selo = rf'product-badges__badge[^>]*>\s*(?:at\S+ )?{maior}% off'
        return (r.status_code == 200 and bool(re.search(selo, r.text)),
                f"Página da coleção com o % off nas peças (até {maior}%)" + _respondeu(r))
    return r.status_code == 404, "Página da coleção fora do ar"


_ITENS = {
    "aplicar": [_confere_precos, _confere_frete_bloqueado, _confere_fretegratis, _confere_cupons, _confere_colecao,
                _confere_menu, _confere_home, _confere_regulamento, _confere_faixa, _confere_vitrine, _confere_filtro,
                _confere_cashback, _confere_desligamento],
    "reverter": [_confere_precos, _confere_frete_bloqueado, _confere_cupons, _confere_colecao, _confere_menu,
                 _confere_home, _confere_faixa, _confere_vitrine, _confere_filtro, _confere_regulamento, _confere_cashback],
}
_DA_VITRINE = (_confere_vitrine, _confere_filtro)  # página pública: o cache da loja segura uns minutos


def conferir(fase, tentativas=6, espera=30):
    """Texto do Telegram com o checklist de `fase` (aplicar ou reverter)."""
    import time
    resultado = {}
    for vez in range(tentativas):
        for f in _ITENS[fase]:
            if resultado.get(f, (False,))[0] or (vez and f not in _DA_VITRINE):
                continue
            try:
                resultado[f] = f(fase)
            except Exception as e:  # um item que não deu para ler não derruba a lista
                resultado[f] = (False, f"{f.__name__.replace('_confere_', '').title()}: não consegui conferir ({type(e).__name__})")
        if all(resultado[f][0] for f in _DA_VITRINE) or vez == tentativas - 1:
            break
        time.sleep(espera)

    linhas = [resultado[f] for f in _ITENS[fase]]
    ok = sum(1 for certo, _ in linhas if certo)
    if fase == "aplicar":
        topo = "Novembro Annis no ar." if ok == len(linhas) else "Novembro Annis: virada com pendência, olhar os itens sem check."
    else:
        topo = "Novembro Annis encerrado." if ok == len(linhas) else "Novembro Annis: encerramento com pendência, olhar os itens sem check."
    corpo = [f"{'✓' if certo else '✗'} {texto}" for certo, texto in linhas]
    return "\n".join([topo, f"{ok} de {len(linhas)} conferidos", ""] + corpo)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    simular = "--simular" in sys.argv
    if cmd == "conferir":
        print(conferir(sys.argv[2] if len(sys.argv) > 2 else "aplicar"))
    else:
        {"preparar": preparar, "aplicar": aplicar, "reverter": reverter}.get(cmd, lambda s: status())(simular)
