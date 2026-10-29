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

TAG = "sale-novembro"
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


def aplicar(simular):
    print("preços da sale:")
    _atualizar_precos(_precos(), "preco_sale", "riscado_sale", simular)
    print("cupons:")
    cupons_fora_da_sale(simular)
    print("vitrine:")
    vitrine_ligar(simular)


def reverter(simular):
    print("preços de antes:")
    _atualizar_precos(_precos(), "preco_antes", "riscado_antes", simular)
    print("cupons:")
    cupons_voltar(simular)
    print("vitrine:")
    vitrine_desligar(simular)


def status():
    linhas = _precos()
    ids = [r["variante_id"] for r in linhas]
    d = _g('query($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id price compareAtPrice}}}', {"ids": ids})
    atual = {n["id"]: n for n in d["nodes"] if n}
    na_sale = sum(1 for r in linhas if float(atual[r["variante_id"]]["price"]) == float(r["preco_sale"]))
    print(f"{na_sale} de {len(linhas)} variantes com o preço da sale")
    print("coleções:", {t: bool(_colecao(t)) for t in (COLECAO_SALE, COLECAO_FORA)})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    simular = "--simular" in sys.argv
    {"preparar": preparar, "aplicar": aplicar, "reverter": reverter}.get(cmd, lambda s: status())(simular)
