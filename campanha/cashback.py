"""Cashback do Novembro Annis 2026, 29/10/2026.

Regras (Pedro, 29/10):
  - Todo pedido pago no site de 01/11 a 30/11 gera crédito de 10% do valor
    pago nas peças, sem frete e depois dos descontos. Pedido pago no Pix leva
    o desconto de 5% do app de pagamento, que a Shopify não enxerga.
  - Venda por link (etiqueta link-stone) não gera: a regra é só site.
  - O crédito vira um código pessoal na Shopify: valor fixo, uso único, só
    na coleção Fora da sale, compra mínima de crédito / 30% (o crédito cobre
    até 30% da compra), vale até 31/01/2027 (data fixa, Pedro 29/10). Soma com desconto de produto
    para conviver com o R$ 0,01 da sale no carrinho misto.
  - Devolução: reembolso total cancela o código; parcial recalcula o crédito
    na proporção do que ficou. Se o crédito já foi usado (Pedro, 29/10), o
    usado sai do reembolso do pedido de origem: em devolução total, tudo o
    que foi usado; na parcial, só o que passar do crédito ajustado. O código
    de saldo, se houver, cai primeiro. O aviso vai para o grupo no Telegram
    quando o crédito é usado e de novo quando o pedido de origem é devolvido.
  - Saldo (Pedro, 29/10): se a compra usar menos do que o valor do código, um
    código novo nasce com o que sobrou, mesma validade e regras, até zerar.
    Fica como pedido "#1160-2", "#1160-3"... e a cliente recebe o e-mail cb7.

    python campanha/cashback.py            gera, confere devolução, uso e vencimento
    python campanha/cashback.py --simular  só mostra

Roda a cada 10 minutos (workflow cashback.yml, disparado pelo Supabase).
"""

import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402
from sqlalchemy import text  # noqa: E402

INICIO = "2026-11-01T03:00:00Z"
FIM = "2026-12-01T02:59:59Z"
TAXA = 0.10
COBERTURA = 0.30
VENCE = datetime(2027, 2, 1, 2, 59, 59, tzinfo=timezone.utc)  # 31/01/2027 23h59min59s em Brasília
DESCONTO_PIX = 0.05
COLECAO_FORA = "Fora da sale"

_PEDIDOS = """query($c:String, $q:String!){orders(first:50, after:$c, query:$q){pageInfo{hasNextPage endCursor}
  nodes{id name createdAt test cancelledAt displayFinancialStatus tags paymentGatewayNames email
    customer{displayName email}
    subtotalPriceSet{shopMoney{amount}} currentSubtotalPriceSet{shopMoney{amount}}
    discountCodes}}}"""


def _g(q, v=None):
    return shopify_client._graphql(q, v or {})


def _pedidos(consulta):
    saida, c = [], None
    while True:
        d = _g(_PEDIDOS, {"c": c, "q": consulta})["orders"]
        saida += d["nodes"]
        if not d["pageInfo"]["hasNextPage"]:
            return saida
        c = d["pageInfo"]["endCursor"]


def _base(subtotal, gateways) -> float:
    fator = 1 - DESCONTO_PIX if any("PIX" in g.upper() for g in gateways or []) else 1.0
    return round(float(subtotal) * fator, 2)


def credito_de(base: float) -> int:
    return int(math.floor(base * TAXA + 0.5))


def minimo_de(valor: float) -> int:
    return int(math.ceil(valor / COBERTURA))


def _colecao_fora():
    d = _g('query($q:String!){collections(first:3, query:$q){nodes{id title}}}', {"q": f'title:"{COLECAO_FORA}"'})
    cid = next((c["id"] for c in d["collections"]["nodes"] if c["title"] == COLECAO_FORA), None)
    if not cid:
        raise RuntimeError("coleção Fora da sale não existe (campanha/novembro.py preparar)")
    return cid


def _primeiro_nome(cliente) -> str:
    import unicodedata
    nome = (cliente or "").strip().split(" ")[0]
    nome = unicodedata.normalize("NFD", nome)
    nome = "".join(ch for ch in nome if unicodedata.category(ch) != "Mn" and ch.isalpha())
    return (nome.upper() or "ANNIS")[:12]


def _codigo(cliente, valor, n) -> str:
    """Contador + nome da cliente + valor, ex. 007MICHELLE73 (Pedro, 29/10)."""
    return f"{n:03d}{_primeiro_nome(cliente)}{int(valor)}"


def _contador() -> int:
    with nuvem._conectar().connect() as con:
        return con.execute(text("select count(*) from campanha.cashback")).scalar() + 1


def _criar_codigo(pedido, valor, colecao, vence, cliente=""):
    n = _contador()
    for _ in range(5):  # o contador garante código único; se a Shopify recusar, tenta o próximo
        codigo = _codigo(cliente, valor, n)
        try:
            return codigo, _criar_na_shopify(pedido, codigo, valor, colecao, vence)
        except RuntimeError as e:
            if not any(k in str(e).lower() for k in ("unique", "already", "taken", "único")):
                raise
            n += 1
    raise RuntimeError(f"não consegui um código livre para {pedido}")


def _criar_na_shopify(pedido, codigo, valor, colecao, vence):
    d = _g('''mutation($d:DiscountCodeBasicInput!){discountCodeBasicCreate(basicCodeDiscount:$d){
        codeDiscountNode{id} userErrors{field message}}}''', {"d": {
        "title": f"Cashback {pedido}", "code": codigo,
        "startsAt": datetime.now(timezone.utc).isoformat(), "endsAt": vence.isoformat(),
        "customerSelection": {"all": True}, "usageLimit": 1, "appliesOncePerCustomer": True,
        "customerGets": {"value": {"discountAmount": {"amount": f"{valor:.2f}", "appliesOnEachItem": False}},
                         "items": {"collections": {"add": [colecao]}}},
        "minimumRequirement": {"subtotal": {"greaterThanOrEqualToSubtotal": f"{minimo_de(valor):.2f}"}},
        "combinesWith": {"productDiscounts": True, "orderDiscounts": False, "shippingDiscounts": True}}})
    r = d["discountCodeBasicCreate"]
    if r["userErrors"]:
        raise RuntimeError(f"Shopify: {r['userErrors']}")
    return r["codeDiscountNode"]["id"]


def _ja_tem():
    with nuvem._conectar().connect() as con:
        return {r["pedido"]: dict(r) for r in con.execute(text("select * from campanha.cashback")).mappings()}


def gerar(simular):
    ja = _ja_tem()
    agora = datetime.now(timezone.utc)
    pedidos = _pedidos(f"created_at:>={INICIO} created_at:<={FIM} financial_status:paid")
    colecao = None
    for o in pedidos:
        if o["name"] in ja or o["test"] or o["cancelledAt"] or "link-stone" in (o["tags"] or []):
            continue
        email = (o.get("email") or (o.get("customer") or {}).get("email") or "").strip().lower()
        if not email:
            print(f"{o['name']}: sem e-mail, sem cashback")
            continue
        base = _base(o["subtotalPriceSet"]["shopMoney"]["amount"], o["paymentGatewayNames"])
        valor = credito_de(base)
        if valor < 1:
            continue
        vence = VENCE
        print(f"{o['name']}: base R$ {base:.2f}, crédito R$ {valor}, compra mínima R$ {minimo_de(valor)}")
        if simular:
            continue
        colecao = colecao or _colecao_fora()
        codigo, desc_id = _criar_codigo(o["name"], valor, colecao, vence, (o.get("customer") or {}).get("displayName"))
        with nuvem._conectar().begin() as con:
            con.execute(text(
                "insert into campanha.cashback (pedido, pedido_id, cliente, email, base, valor, codigo, desconto_id, vence_em)"
                " values (:p, :pid, :cl, :e, :b, :v, :c, :d, :vence) on conflict (pedido) do nothing"),
                {"p": o["name"], "pid": o["id"], "cl": (o.get("customer") or {}).get("displayName"), "e": email,
                 "b": base, "v": valor, "c": codigo, "d": desc_id, "vence": vence})


def _atualizar(pedido, **campos):
    sets = ", ".join(f"{k} = :{k}" for k in campos)
    with nuvem._conectar().begin() as con:
        con.execute(text(f"update campanha.cashback set {sets}, atualizado_em = now() where pedido = :pedido"),
                    {**campos, "pedido": pedido})


def _telegram(texto):
    import requests
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(f"(sem Telegram aqui) {texto}")
        return
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  data={"chat_id": chat, "text": texto}, timeout=20).raise_for_status()


def _reais(v) -> str:
    return f"R$ {float(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def acerto(devido: float, usado: float, ativo: float) -> tuple:
    """Depois de devolução no pedido de origem: (quanto fica no código ativo,
    quanto do que já foi usado sai do reembolso)."""
    resta = max(0, int(math.floor(devido - usado + 1e-9)))
    return min(resta, int(ativo)), round(max(0.0, usado - devido), 2)


def devolucoes(simular):
    """Reembolso ou cancelamento do pedido de origem: o crédito cai para o que
    ficou devido; do que já foi usado, o excesso sai do reembolso (aviso no grupo).
    Olha a cadeia toda do pedido: o código original e os de saldo (#1160-2...)."""
    cadeias = {}
    for c in _ja_tem().values():
        cadeias.setdefault(c["pedido_id"], []).append(c)
    abertas = {pid: cs for pid, cs in cadeias.items() if any(c["situacao"] in ("ativo", "usado") for c in cs)}
    if not abertas:
        return
    d = _g('query($ids:[ID!]!){nodes(ids:$ids){... on Order{id name cancelledAt displayFinancialStatus paymentGatewayNames currentSubtotalPriceSet{shopMoney{amount}}}}}',
           {"ids": list(abertas)})
    por_id = {n["id"]: n for n in d["nodes"] if n}
    for pid, cs in abertas.items():
        o = por_id.get(pid)
        if not o:
            continue
        status = o["displayFinancialStatus"]
        if o["cancelledAt"] or status in ("REFUNDED", "VOIDED"):
            devido = 0
        elif status == "PARTIALLY_REFUNDED":
            devido = credito_de(_base(o["currentSubtotalPriceSet"]["shopMoney"]["amount"], o["paymentGatewayNames"]))
        else:
            continue
        raiz = next((c for c in cs if "-" not in c["pedido"]), cs[0])
        devido = min(devido, credito_de(float(raiz["base"])))
        usados = [c for c in cs if c["situacao"] == "usado"]
        usado = sum(float(c["valor_usado"] or 0) for c in usados)
        ativo = next((c for c in cs if c["situacao"] == "ativo"), None)
        fica, descontar = acerto(devido, usado, float(ativo["valor"]) if ativo else 0)

        if ativo and fica < float(ativo["valor"]):
            print(f"{raiz['pedido']}: devolução, {ativo['codigo']} de R$ {float(ativo['valor']):.0f} para R$ {fica}")
            if not simular:
                if fica < 1:
                    _g('mutation($id:ID!){discountCodeDeactivate(id:$id){userErrors{message}}}', {"id": ativo["desconto_id"]})
                    _atualizar(ativo["pedido"], situacao="cancelado", valor=0)
                else:
                    r = _g('''mutation($id:ID!,$d:DiscountCodeBasicInput!){discountCodeBasicUpdate(id:$id, basicCodeDiscount:$d){
                        userErrors{message}}}''', {"id": ativo["desconto_id"], "d": {
                        "customerGets": {"value": {"discountAmount": {"amount": f"{fica:.2f}", "appliesOnEachItem": False}}},
                        "minimumRequirement": {"subtotal": {"greaterThanOrEqualToSubtotal": f"{minimo_de(fica):.2f}"}}}})
                    if r["discountCodeBasicUpdate"]["userErrors"]:
                        raise RuntimeError(f"Shopify: {r['discountCodeBasicUpdate']['userErrors']}")
                    _atualizar(ativo["pedido"], valor=fica)

        if descontar > float(raiz["descontar"] or 0) + 0.004:
            onde = ", ".join(f"{c['pedido_uso']} ({_reais(c['valor_usado'] or 0)})" for c in usados)
            msg = (f"Cashback: o pedido {raiz['pedido']} ({raiz['cliente'] or raiz['email']}) teve devolução, mas "
                   f"{_reais(usado)} do cashback dele já foram usados em {onde}. "
                   + (f"Ficou devido {_reais(devido)} de cashback. " if devido else "")
                   + f"Pela regra, o reembolso de {raiz['pedido']} desconta {_reais(descontar)}. "
                   "Conferir se o estorno saiu com esse desconto.")
            print(msg)
            if not simular:
                _telegram(msg)
                _atualizar(raiz["pedido"], descontar=descontar)


def _descontado(pedido_id, codigo) -> float:
    """Quanto o código descontou de fato no pedido (soma das alocações nas linhas)."""
    d = _g("""query($id:ID!){order(id:$id){lineItems(first:50){nodes{discountAllocations{
        allocatedAmountSet{shopMoney{amount}} discountApplication{... on DiscountCodeApplication{code}}}}}}}""",
           {"id": pedido_id})
    return round(sum(float(a["allocatedAmountSet"]["shopMoney"]["amount"])
                     for li in d["order"]["lineItems"]["nodes"] for a in li["discountAllocations"]
                     if (a["discountApplication"] or {}).get("code", "").upper() == codigo), 2)


def _reemitir(c, sobra):
    """Código novo com o saldo que sobrou, mesma validade."""
    raiz = c["pedido"].split("-")[0]
    n = 2 + sum(1 for p in _ja_tem() if p.startswith(raiz + "-"))
    codigo, desc_id = _criar_codigo(f"{raiz}-{n}", sobra, _colecao_fora(), c["vence_em"], c["cliente"])
    with nuvem._conectar().begin() as con:
        con.execute(text(
            "insert into campanha.cashback (pedido, pedido_id, cliente, email, base, valor, codigo, desconto_id, vence_em)"
            " values (:p, :pid, :cl, :e, :b, :v, :c, :d, :vence)"),
            {"p": f"{raiz}-{n}", "pid": c["pedido_id"], "cl": c["cliente"], "e": c["email"], "b": c["base"],
             "v": sobra, "c": codigo, "d": desc_id, "vence": c["vence_em"]})
    return f"{raiz}-{n}", codigo


def usos(simular):
    """Código que aparece num pedido pago vira "usado", ligado ao pedido; se
    sobrou saldo, nasce um código novo com o restante."""
    ativos = {c["codigo"]: c for c in _ja_tem().values() if c["situacao"] == "ativo"}
    if not ativos:
        return
    # Todos os pedidos desde o início e filtro aqui: a busca da Shopify por
    # discount_code não aceita curinga. São poucas dezenas por mês.
    for o in _pedidos(f"created_at:>={INICIO}"):
        for cod in o["discountCodes"] or []:
            c = ativos.get(cod.upper())
            if not c or o["test"] or o["cancelledAt"]:
                continue
            usado = _descontado(o["id"], c["codigo"])
            sobra = int(math.floor(float(c["valor"]) - usado + 1e-9))
            print(f"{c['codigo']} ({c['pedido']}) usado em {o['name']}: R$ {usado:.2f}" + (f", sobra R$ {sobra}" if sobra >= 1 else ""))
            if simular:
                continue
            _atualizar(c["pedido"], situacao="usado", usado_em=o["createdAt"], pedido_uso=o["name"], valor_usado=usado)
            raiz = c["pedido"].split("-")[0]
            saldo = ""
            if sobra >= 1:
                novo, codigo = _reemitir(c, sobra)
                print(f"  saldo de R$ {sobra} em {codigo} ({novo})")
                saldo = f" O saldo de R$ {sobra} virou o código {codigo}."
            _telegram(f"Cashback: {_reais(usado)} do cashback do pedido {raiz} ({c['cliente'] or c['email']}) "
                      f"foram usados no {o['name']}.{saldo} Se o {raiz} for devolvido, o valor usado sai do reembolso "
                      "dele (em devolução parcial, só o que passar do cashback que ficar devido).")


def vencimentos(simular):
    agora = datetime.now(timezone.utc)
    for c in _ja_tem().values():
        if c["situacao"] == "ativo" and c["vence_em"] < agora:
            print(f"{c['codigo']} ({c['pedido']}) venceu")
            if not simular:
                _atualizar(c["pedido"], situacao="vencido")


def _enviados():
    with nuvem._conectar().connect() as con:
        return {r[0]: r[1] for r in con.execute(text(
            "select id, criado from avisos.enviados where id like 'cb:%' or id like 'regua:e2%'"))}


def emails(simular):
    """Manda o que venceu (regras em campanha/cashback_emails.py)."""
    from avisos import gmail
    from avisos.regua import motor
    from campanha import cashback_emails as E
    modo = os.environ.get("REGUA_MODO") or "no_ar"
    cco = [x.strip() for x in (os.environ.get("REGUA_CCO") or E.CONTATO).split(",") if x.strip()]
    agora = datetime.now(timezone.utc)
    hora_brt = agora.astimezone(E.BRT).hour
    ja = _enviados()
    ultimo_lembrete = {}
    creditos = sorted(_ja_tem().values(), key=lambda c: c["emitido_em"])
    for c in creditos:
        for q in E.LEMBRETES:
            quando = ja.get(f"cb:{q}:{c['pedido'].lstrip('#')}")
            if quando:
                ultimo_lembrete[c["email"]] = max(ultimo_lembrete.get(c["email"], quando), quando)
    for c in creditos:
        num = c["pedido"].lstrip("#")
        fila = []
        reemitido = "-" in num
        if reemitido and f"cb:cb7:{num}" not in ja and c["situacao"] == "ativo":
            fila.append("cb7")  # saldo que sobrou de um uso parcial
        elif not reemitido and f"cb:cb1:{num}" not in ja and c["situacao"] in ("ativo", "usado"):
            # Logo depois do e-mail de confirmação (régua e2); sem ele, 30 min após emitir.
            if any(k.startswith("regua:e2") and k.endswith(f":{num}") for k in ja) or agora - c["emitido_em"] > timedelta(minutes=30):
                fila.append("cb1")
        if c["situacao"] == "cancelado" and (f"cb:cb1:{num}" in ja or f"cb:cb7:{num}" in ja) and f"cb:cb6:{num}" not in ja:
            fila.append("cb6")
        if c["situacao"] == "ativo" and (f"cb:cb1:{num}" in ja or f"cb:cb7:{num}" in ja) and 9 <= hora_brt < 20:
            ultimo = ultimo_lembrete.get(c["email"])
            if not ultimo or agora - ultimo > timedelta(days=6):
                devidos = [q for q in E.LEMBRETES if f"cb:{q}:{num}" not in ja and E.vencido_o_prazo(q, c, agora)]
                if devidos:
                    fila.append(devidos[-1])  # o mais recente vencido; os anteriores ficam para trás
                    for q in devidos[:-1]:
                        ja[f"cb:{q}:{num}"] = agora  # atropelado: não manda mais
                        if not simular:
                            with nuvem._conectar().begin() as con:
                                con.execute(text("insert into avisos.enviados (id, tipo) values (:i, 'cashback pulado') on conflict do nothing"),
                                            {"i": f"cb:{q}:{num}"})
        for qual in fila:
            m = E.montar(qual, E.dados(c))
            para, assunto, copia = c["email"], m["assunto"], cco
            if modo == "sombra":
                para, assunto, copia = E.CONTATO, f"[sombra {c['pedido']}] {assunto}", []
            print(f"{c['pedido']} {qual} -> {'(simulado) ' if simular else ''}{'contato@' if para == E.CONTATO else 'cliente'}: {assunto}")
            if simular:
                continue
            with nuvem._conectar().begin() as con:
                html, texto = motor.com_cliques(con, m["html"], m["texto"], c["pedido"], qual, modo, False)
                mid = gmail.enviar(para, assunto, html, texto, cco=copia)
                con.execute(text("insert into avisos.enviados (id, tipo) values (:i, :t) on conflict do nothing"),
                            {"i": f"cb:{qual}:{num}", "t": f"cashback {mid}"})
            ja[f"cb:{qual}:{num}"] = agora
            if qual in E.LEMBRETES:
                ultimo_lembrete[c["email"]] = agora


def main(simular=False):
    gerar(simular)
    devolucoes(simular)
    usos(simular)
    vencimentos(simular)
    emails(simular)


if __name__ == "__main__":
    main("--simular" in sys.argv)
