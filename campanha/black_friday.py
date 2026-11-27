"""Black Friday 2026 na Shopify: 10% com preço riscado, 26/11/2026.

    python campanha/black_friday.py aplicar  [--simular]   preço -10%, preço de hoje vira o riscado
    python campanha/black_friday.py reverter [--simular]   preços e riscados como estavam
    python campanha/black_friday.py status
    python campanha/black_friday.py conferir aplicar|reverter

Decisões do Pedro em 26/11/2026: de 26/11 até 29/11 às 23h59, 10% em todo o
site menos acessórios e peças da sale (coleção Fora da sale menos Acessórios);
cashback mantido; FRETEGRATIS pausado. Primeiro foi um desconto automático no
carrinho, que eu escolhi sem perguntar; ele preferiu o preço riscado, que
aparece na vitrine, na página da peça e no catálogo dos anúncios.

O estado de antes de cada variante fica em campanha.bf_precos, gravado no
primeiro "aplicar" e nunca sobrescrito. Quem roda o "reverter" às 23h59 de
29/11 é o workflow black-friday.yml, disparado pelo pg_cron do Supabase.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402
from sqlalchemy import text  # noqa: E402

DESCONTO = 0.10
COLECAO_FORA = "fora-da-sale"
COLECAO_ACESSORIOS = "acessorios"
DESCONTO_CARRINHO = "Black Friday 10%"   # a primeira versão, no carrinho; sai no aplicar

_SQL = """
create table if not exists campanha.bf_precos (
    variante_id   text primary key,
    produto_id    text not null,
    produto       text not null,
    tamanho       text not null,
    preco_antes   numeric not null,
    riscado_antes numeric,
    preco_bf      numeric not null,
    riscado_bf    numeric not null,
    gravado_em    timestamptz default now()
)"""


def _g(q, v=None):
    return shopify_client._graphql(q, v or {})


def _erros(r, chave):
    if r[chave].get("userErrors"):
        raise RuntimeError(f"Shopify {chave}: {r[chave]['userErrors']}")
    return r[chave]


def _produtos(handle):
    d = _g('''query($h:String!){collectionByHandle(handle:$h){products(first:250){nodes{id title
        variants(first:50){nodes{id title price compareAtPrice}}}}}}''', {"h": handle})
    return d["collectionByHandle"]["products"]["nodes"]


def _linhas():
    with nuvem._conectar().connect() as con:
        con.execute(text(_SQL))
        con.commit()
        return [dict(r) for r in con.execute(text("select * from campanha.bf_precos order by produto, tamanho")).mappings()]


def _gravar_antes(simular):
    """Fotografa as variantes uma vez só. Um segundo "aplicar" reusa a foto,
    senão gravaria o preço já com desconto como "antes"."""
    linhas = _linhas()
    if linhas:
        print(f"{len(linhas)} variantes já gravadas em campanha.bf_precos")
        return linhas
    acessorios = {p["id"] for p in _produtos(COLECAO_ACESSORIOS)}
    novas = []
    for p in _produtos(COLECAO_FORA):
        if p["id"] in acessorios:
            continue
        for v in p["variants"]["nodes"]:
            antes = float(v["price"])
            novas.append({"variante_id": v["id"], "produto_id": p["id"], "produto": p["title"], "tamanho": v["title"],
                          "preco_antes": antes,
                          "riscado_antes": float(v["compareAtPrice"]) if v["compareAtPrice"] else None,
                          "preco_bf": round(antes * (1 - DESCONTO), 2), "riscado_bf": antes})
    print(f"{len({r['produto_id'] for r in novas})} peças, {len(novas)} variantes")
    if not simular:
        with nuvem._conectar().connect() as con:
            for r in novas:
                con.execute(text("""insert into campanha.bf_precos (variante_id, produto_id, produto, tamanho,
                    preco_antes, riscado_antes, preco_bf, riscado_bf) values (:variante_id, :produto_id, :produto,
                    :tamanho, :preco_antes, :riscado_antes, :preco_bf, :riscado_bf)"""), r)
            con.commit()
    return novas


def _atualizar(linhas, preco, riscado, simular):
    por_produto = {}
    for r in linhas:
        por_produto.setdefault(r["produto_id"], []).append(r)
    for pid, rs in por_produto.items():
        variantes = [{"id": r["variante_id"], "price": f"{float(r[preco]):.2f}",
                      "compareAtPrice": f"{float(r[riscado]):.2f}" if r[riscado] is not None else None} for r in rs]
        print(f"  {rs[0]['produto']}: " + ", ".join(f"{r['tamanho']} {float(r[preco]):.2f}" for r in rs))
        if not simular:
            _erros(_g('''mutation($p:ID!,$v:[ProductVariantsBulkInput!]!){productVariantsBulkUpdate(productId:$p, variants:$v){
                userErrors{field message}}}''', {"p": pid, "v": variantes}), "productVariantsBulkUpdate")


def _tirar_desconto_carrinho(simular):
    d = _g('query($q:String!){automaticDiscountNodes(first:5, query:$q){nodes{id automaticDiscount{... on DiscountAutomaticBasic{title}}}}}',
           {"q": f'title:"{DESCONTO_CARRINHO}"'})["automaticDiscountNodes"]["nodes"]
    for n in d:
        if (n["automaticDiscount"] or {}).get("title") == DESCONTO_CARRINHO:
            print(f"apaga o desconto automático {DESCONTO_CARRINHO} (senão a peça levaria 10% duas vezes)")
            if not simular:
                _erros(_g('mutation($id:ID!){discountAutomaticDelete(id:$id){userErrors{message}}}', {"id": n["id"]}),
                       "discountAutomaticDelete")


def aplicar(simular):
    linhas = _gravar_antes(simular)
    _tirar_desconto_carrinho(simular)
    print("preços da Black Friday:")
    _atualizar(linhas, "preco_bf", "riscado_bf", simular)


def reverter(simular):
    print("preços de antes:")
    _atualizar(_linhas(), "preco_antes", "riscado_antes", simular)


def _atual(linhas):
    d = _g('query($ids:[ID!]!){nodes(ids:$ids){... on ProductVariant{id price compareAtPrice}}}',
           {"ids": [r["variante_id"] for r in linhas]})
    return {n["id"]: n for n in d["nodes"] if n}


def conferir(fase):
    linhas = _linhas()
    atual = _atual(linhas)
    preco, riscado = ("preco_bf", "riscado_bf") if fase == "aplicar" else ("preco_antes", "riscado_antes")

    def certo(r):
        v = atual.get(r["variante_id"])
        if not v or float(v["price"]) != float(r[preco]):
            return False
        esperado = float(r[riscado]) if r[riscado] is not None else None
        return (float(v["compareAtPrice"]) if v["compareAtPrice"] else None) == esperado

    ok = sum(1 for r in linhas if certo(r))
    nome = "Preços da Black Friday" if fase == "aplicar" else "Preços de antes da Black Friday"
    marca = "✅" if ok == len(linhas) else "❌"
    print(f"{marca} {nome}: {ok} de {len(linhas)} tamanhos ({len({r['produto_id'] for r in linhas})} peças)")
    return ok == len(linhas)


def status():
    linhas = _linhas()
    atual = _atual(linhas)
    na_bf = sum(1 for r in linhas if r["variante_id"] in atual and float(atual[r["variante_id"]]["price"]) == float(r["preco_bf"]))
    print(f"{na_bf} de {len(linhas)} variantes com o preço da Black Friday")


if __name__ == "__main__":
    args = sys.argv[1:]
    simular = "--simular" in args
    acao = args[0] if args else "status"
    if acao == "aplicar":
        aplicar(simular)
    elif acao == "reverter":
        reverter(simular)
    elif acao == "conferir":
        sys.exit(0 if conferir(args[1] if len(args) > 1 else "aplicar") else 1)
    else:
        status()
