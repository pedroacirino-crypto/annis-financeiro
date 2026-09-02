import json, math, collections, unicodedata
import shopify_client as sc

def norm(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower().strip()
def brl(v): return f"R$ {v:,.2f}".replace(",","X").replace(".",",").replace("X",".")

# ---- 1. TOTAL DO MES, indexado ao orcamento -------------------------------
# Prior: agosto (19 pedidos) com peso de 1,5 mes. Dado novo: 3 pedidos em 2 dias.
A_PRIOR, B_PRIOR = 19*1.5, 1.5          # Gamma(a,b) sobre pedidos/mes
y_set, t_set = 3, 2/30
a_post, b_post = A_PRIOR + y_set, B_PRIOR + t_set
pedidos_mes = a_post / b_post
PECAS_POR_PEDIDO = 172/111
B0 = 2400.0                              # R$ 80/dia, orcamento atual

def total_pecas(orcamento_mes, elasticidade=1.0):
    """Peças esperadas no mês. Elasticidade 1 = eficiência constante."""
    return pedidos_mes * PECAS_POR_PEDIDO * (orcamento_mes/B0)**elasticidade

# ---- 2. REPARTICAO PELA GRADE ---------------------------------------------
vendas = {}
for k, q in json.load(open("/tmp/vendas_grade.json")).items():
    n, t, m = k.split("||"); vendas[(norm(n), t.upper(), m)] = q

q = """query($cursor: String) { productVariants(first: 100, after: $cursor) {
  pageInfo { hasNextPage endCursor }
  nodes { id title price inventoryQuantity product { title status } } } }"""
grade=[]; cur=None
while True:
    d=sc._graphql(q,{"cursor":cur}); b=d["productVariants"]
    grade += [v for v in b["nodes"] if (v.get("product") or {}).get("status")=="ACTIVE"]
    if not b["pageInfo"]["hasNextPage"]: break
    cur=b["pageInfo"]["endCursor"]

MESES=["2026-06","2026-07","2026-08"]
y={}
for v in grade:
    ch=(norm(v["product"]["title"]), (v["title"] or "U").upper())
    y[v["id"]]=sum(vendas.get((ch[0],ch[1],m),0) for m in MESES)

taxa_media = sum(y.values())/(3*len(grade))
KAPPA=2.0
alfa_pri = taxa_media*KAPPA
# posterior Gamma por variante: forma = y+alfa, taxa = 3+KAPPA (em meses)
post = {vid: (y[vid]+alfa_pri, 3+KAPPA) for vid in y}
peso = {vid: f/r for vid,(f,r) in post.items()}
soma_peso = sum(peso.values())

# ---- 3. INTERVALO POR PECA: preditiva binomial negativa -------------------
def nb_pmf(k, r, p):
    return math.exp(math.lgamma(k+r)-math.lgamma(r)-math.lgamma(k+1)
                    + r*math.log(p) + k*math.log1p(-p))

def faixa(forma, taxa, escala, cred=0.80):
    """Intervalo de credibilidade da quantidade no mês.

    Gamma(forma, taxa) sobre a taxa mensal, escalada pelo orçamento, gera uma
    binomial negativa para a contagem do mês. `escala` > 1 aumenta a
    expectativa sem fingir que a incerteza some junto.
    """
    r = forma
    p = taxa/(taxa + escala)          # prob. de "sucesso" da NB
    baixo=(1-cred)/2; alto=1-baixo
    acum=0.0; lo=hi=0
    for k in range(0, 60):
        acum += nb_pmf(k, r, p)
        if acum < baixo: lo = k+1
        if acum >= alto: hi = k; break
    return lo, max(hi, lo)

def p_vender(forma, taxa, escala):
    p = taxa/(taxa+escala)
    return 1 - nb_pmf(0, forma, p)

def cenario(orcamento_mes, elasticidade=1.0, mostrar=22):
    alvo = total_pecas(orcamento_mes, elasticidade)
    escala_global = alvo/soma_peso
    print(f"\n{'='*74}\nORÇAMENTO R$ {orcamento_mes:,.0f}/mês  "
          f"(R$ {orcamento_mes/30:,.0f}/dia)   elasticidade {elasticidade}")
    linhas=[]
    for v in grade:
        f_, r_ = post[v["id"]]
        esc = escala_global * (1/r_)          # escala em "meses equivalentes"
        media = f_/r_*escala_global
        lo,hi = faixa(f_, r_/escala_global, 1.0)
        linhas.append({"nome":f"{v['product']['title']} [{v['title']}]",
                       "media":media,"lo":lo,"hi":hi,
                       "p":p_vender(f_, r_/escala_global, 1.0),
                       "estoque":v.get("inventoryQuantity") or 0,
                       "preco":float(v["price"]), "y":y[v["id"]]})
    linhas.sort(key=lambda d:-d["media"])
    print(f"total esperado: {sum(d['media'] for d in linhas):.1f} peças  |  "
          f"receita {brl(sum(d['media']*d['preco'] for d in linhas))}\n")
    print(f"{'peça':46}{'jun-ago':>8}{'média':>7}{'faixa 80%':>12}{'P':>6}{'estoq':>7}")
    for d in linhas[:mostrar]:
        faixa_txt = f"{d['lo']}–{d['hi']}"
        print(f"{d['nome'][:46]:46}{d['y']:>8}{d['media']:>7.2f}"
              f"{faixa_txt:>12}{d['p']*100:>5.0f}%{d['estoque']:>7}")
    return linhas
