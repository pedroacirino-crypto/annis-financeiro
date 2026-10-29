"""Preço da sale do Novembro Annis por variante, 29/10/2026.

Regras fechadas com o Pedro (resumo no PDF "Novembro Annis 2026" em Code/ANNIS):

  - Fora da sale: coleção nova (cadastrada a partir de 14/09/2026), linhas best
    seller inteiras (Giverny, Relíquia, Monet, Relicário, Bijou) e Loulou, que
    é sob encomenda. Peça sem estoque também fica fora.
  - Desconto = 15% + 25% x sinal, arredondado a 5%, com o sinal pesando
    50% o giro da peça (meses de estoque no ritmo atual), 30% o giro do
    tamanho (quanto o tamanho vende sobre quanto tem, na loja toda) e 20% a
    grade furada (parte da grade com estoque). Vai de 15% a 40%.
  - Teto pela margem: depois de custo da ficha, taxas (7%) e imposto (2,64%),
    sobra pelo menos 10% do preço de venda, sem contar frete.
  - O desconto é sobre o preço "de" (compareAtPrice) e nunca deixa a peça
    mais cara do que está.

    python campanha/modelo_sale.py            mostra a tabela
    python campanha/modelo_sale.py --salvar   congela em campanha.sale_precos

Roda no Mac: o custo vem das fichas (financeiro.custo_por_ficha) e não fica
gravado em lugar nenhum do repositório, que é público.
"""

import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

import financeiro as fin  # noqa: E402
import nuvem  # noqa: E402
import shopify_client  # noqa: E402

TAXAS = 0.07 + 0.0264
PISO = 0.10
DESCONTO_MIN, DESCONTO_MAX = 0.15, 0.40
PESOS = (0.5, 0.3, 0.2)
COLECAO_NOVA = "2026-09-14"
LINHAS_BEST = r"Giverny|Rel[ií]quia|Monet|Relic[aá]rio|Bijou"
GIRO_BEST = 0.9  # peças por mês; a partir daí é best seller mesmo fora das linhas
TAMANHOS = ["PP", "P", "M", "G", "GG"]

_PRODUTOS = """query($c: String) { products(first: 50, after: $c, query: "status:active") {
  pageInfo { hasNextPage endCursor }
  nodes { id title createdAt
    variants(first: 30) { nodes { id title price compareAtPrice inventoryQuantity } } } } }"""


def _variantes() -> pd.DataFrame:
    linhas, cursor = [], None
    while True:
        d = shopify_client._graphql(_PRODUTOS, {"c": cursor})["products"]
        for p in d["nodes"]:
            for v in p["variants"]["nodes"]:
                preco = float(v["price"])
                riscado = float(v["compareAtPrice"]) if v["compareAtPrice"] else None
                linhas.append({
                    "produto_id": p["id"], "produto": p["title"], "criado": p["createdAt"][:10],
                    "variante_id": v["id"], "tamanho": v["title"], "preco": preco, "riscado": riscado,
                    "original": riscado if riscado and riscado > preco else preco,
                    "estoque": max(0, v["inventoryQuantity"] or 0),
                })
        if not d["pageInfo"]["hasNextPage"]:
            return pd.DataFrame(linhas)
        cursor = d["pageInfo"]["endCursor"]


def _vendidas() -> Counter:
    """Peças vendidas no site por (produto, tamanho), no histórico guardado.
    Pedido antigo traz o tamanho no nome ("Monet Noite - P")."""
    vend = Counter()
    for p in nuvem.ler_pedidos():
        if str(p.get("situacao") or "").lower() not in ("paid", "partially_refunded", ""):
            continue
        for parte in re.split(r", (?=\d+x )", p.get("itens") or ""):
            m = re.match(r"(\d+)x (.+?)(?: \(([^)]*)\))?$", parte.strip())
            if not m:
                continue
            nome, tam = m.group(2).strip(), (m.group(3) or "").strip()
            if not tam or tam == "Default Title":
                mm = re.match(r"(.+) - (PP|P|M|G|GG|U)$", nome)
                nome, tam = (mm.group(1), mm.group(2)) if mm else (nome, "U")
            vend[(nome, tam)] += int(m.group(1))
    return vend


def _corte(x: float) -> float:
    return max(0.0, min(1.0, x))


def tabela() -> pd.DataFrame:
    V = _variantes()
    vend = _vendidas()
    V["vendidas"] = [vend.get((a, "U" if b == "Default Title" else b), 0) for a, b in zip(V.produto, V.tamanho)]
    hoje = pd.Timestamp(datetime.now(timezone.utc).date())
    V["meses"] = ((hoje - pd.to_datetime(V.criado)).dt.days / 30.4).clip(lower=1)
    P = V.groupby("produto").agg(criado=("criado", "first"), estoque=("estoque", "sum"),
                                 vendidas=("vendidas", "sum"), meses=("meses", "first"),
                                 tamanhos=("tamanho", "count"),
                                 com_estoque=("estoque", lambda x: int((x > 0).sum())))
    P["giro"] = P.vendidas / P.meses

    R = V[V.tamanho.isin(TAMANHOS)]
    vs, es = R.groupby("tamanho").vendidas.sum(), R.groupby("tamanho").estoque.sum()
    indice = (vs / vs.sum()) / (es / es.sum())

    custos = fin.custo_por_ficha()
    linhas = []
    for nome, p in P.iterrows():
        if (p.estoque <= 0 or "Loulou" in nome or p.criado >= COLECAO_NOVA
                or re.search(LINHAS_BEST, nome) or p.giro >= GIRO_BEST):
            continue
        ficha = fin.ficha_da_peca(nome)
        custo = custos.get(ficha) if ficha else None
        if custo is None:
            raise SystemExit(f"{nome} sem custo na ficha: ligar em financeiro.MAPA_FICHAS antes de congelar")
        cobertura = p.estoque / p.giro if p.giro > 0 else 99
        s_peca = _corte((math.log(max(cobertura, 0.1)) - math.log(3)) / (math.log(36) - math.log(3)))
        s_grade = _corte((0.8 - p.com_estoque / p.tamanhos) / 0.6)
        for _, v in V[(V.produto == nome) & (V.estoque > 0)].iterrows():
            s_tam = _corte((1 - indice.get(v.tamanho, 1.0)) / 0.6) if v.tamanho in indice.index else 0.0
            sinal = PESOS[0] * s_peca + PESOS[1] * s_tam + PESOS[2] * s_grade
            desconto = round((DESCONTO_MIN + (DESCONTO_MAX - DESCONTO_MIN) * sinal) * 20) / 20
            teto = math.floor(max(0.0, 1 - custo / (v.original * (1 - TAXAS - PISO))) * 20) / 20
            desconto = min(desconto, teto)
            preco_sale = float(round(v.original * (1 - desconto)))
            if preco_sale >= v.preco:  # nunca mais caro que hoje: fica como está
                continue
            linhas.append({
                "variante_id": v.variante_id, "produto_id": v.produto_id, "produto": nome,
                "tamanho": v.tamanho, "preco_antes": v.preco, "riscado_antes": v.riscado,
                "preco_sale": preco_sale, "riscado_sale": v.original, "desconto": desconto,
                "estoque": int(v.estoque),
            })
    return pd.DataFrame(linhas)


def salvar(T: pd.DataFrame) -> None:
    from sqlalchemy import text
    cols = ["variante_id", "produto_id", "produto", "tamanho", "preco_antes", "riscado_antes",
            "preco_sale", "riscado_sale", "desconto"]
    with nuvem._conectar().begin() as con:
        con.execute(text("delete from campanha.sale_precos"))
        for r in T[cols].to_dict("records"):
            r["riscado_antes"] = None if pd.isna(r["riscado_antes"]) else r["riscado_antes"]
            con.execute(text(
                "insert into campanha.sale_precos (variante_id, produto_id, produto, tamanho, preco_antes,"
                " riscado_antes, preco_sale, riscado_sale, desconto) values (:variante_id, :produto_id,"
                " :produto, :tamanho, :preco_antes, :riscado_antes, :preco_sale, :riscado_sale, :desconto)"), r)


if __name__ == "__main__":
    T = tabela()
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    print(T[["produto", "tamanho", "estoque", "preco_antes", "riscado_sale", "preco_sale", "desconto"]].to_string(index=False))
    print(f"\n{len(T)} variantes, {int(T.estoque.sum())} peças, maior desconto {T.desconto.max():.0%}")
    if "--salvar" in sys.argv:
        salvar(T)
        print("congelado em campanha.sale_precos")
