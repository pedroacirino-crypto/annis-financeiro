"""Monta cada e-mail da régua com os textos de textos.yaml e os dados do pedido.

    montar("e2_pronta", dados) -> {"assunto", "previa", "html", "texto", "imagens"}

"dados" é um dicionário com o pedido já mastigado (nomes, datas no calendário,
valores formatados); ver exemplos.py. "imagens" são as que vão embutidas no
e-mail (o QR do Pix), como [(cid, bytes_png)].

O e-mail 4 é o alerta interno do Telegram e não passa por aqui. O 0 é a
confirmação da Shopify: este modelo serve de referência para o HTML que vai
em Configurações > Notificações > Confirmação do pedido.
"""

from __future__ import annotations

import html as _html
import io
import os
import re

import yaml

from . import blocos as B

_PASTA = os.path.dirname(os.path.abspath(__file__))

GENERO = {
    "m": {"Seu": "Seu", "seu": "seu", "o_seu": "o seu", "do_seu": "do seu", "ele": "ele", "feito": "feito", "postado": "postado"},
    "f": {"Seu": "Sua", "seu": "sua", "o_seu": "a sua", "do_seu": "da sua", "ele": "ela", "feito": "feita", "postado": "postada"},
}


def cuidados_da_descricao(html: str) -> list[str]:
    """Instruções do campo "Manutenção" da descrição do produto (o texto da
    etiqueta), uma por linha. Lista vazia se o produto não tiver o campo."""
    t = re.sub(r"<(br|/p|/div|/li|/h[1-6])[^>]*>", " ", html or "", flags=re.I)
    t = re.sub(r"<[^>]+>", "", t).replace("&nbsp;", " ").replace("\xa0", " ")
    t = re.sub(r"\s+", " ", _html.unescape(t))
    m = re.search(r"Manuten[çc][ãa]o\s*:?\s*(.+)", t, re.I)
    if not m:
        return []
    texto = re.split(r"\s(?:Obs\s*:|A modelo|Para maiores|Para mais|Composi[çc][ãa]o|Medidas|Modelagem|Tabela)\b", m.group(1))[0]
    linhas = [l.strip(" .") for l in re.split(r"(?<=\.)\s+", texto)]
    # Para antes da linha da modelo ("Maria tem 1,65 m...") ou do "Para dúvidas...".
    fim = next((k for k, l in enumerate(linhas) if re.search(r"\btem \d|veste tamanho|^Para (d|m)|^Obs", l)), len(linhas))
    return [l[0].upper() + l[1:] + "." for l in linhas[:fim] if len(l) > 3]

ABERTURA = "https://annis.store/cdn/shop/files/628A2821.png?width=1400&format=pjpg"


def textos() -> dict:
    with open(os.path.join(_PASTA, "textos.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


class _Vars(dict):
    def __missing__(self, chave):
        raise KeyError(f"variável {{{chave}}} sem valor no pedido")


def _preencher(valor, v: dict):
    if isinstance(valor, str):
        return valor.format_map(_Vars(v))
    if isinstance(valor, list):
        return [_preencher(x, v) for x in valor]
    if isinstance(valor, dict):
        return {k: _preencher(x, v) for k, x in valor.items()}
    return valor


def _qr_png(codigo: str) -> bytes:
    import qrcode
    img = qrcode.make(codigo, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _texto_plano(html: str, previa: str) -> str:
    """Versão em texto do e-mail. Começa pela prévia: com imagem embutida (Pix),
    o Gmail tira dela o texto que aparece ao lado do assunto."""
    corpo = html.split("<body", 1)[-1].split(">", 1)[-1]
    corpo = re.sub(r"<!--.*?-->", "", corpo, flags=re.S)
    corpo = re.sub(r'<div style="display:none.*?</div>', "", corpo, count=1, flags=re.S)
    corpo = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                   lambda m: f"{m.group(2)} ({m.group(1)})" if re.sub(r"<[^>]+>", "", m.group(2)).strip() else "", corpo, flags=re.S)
    corpo = re.sub(r"<br\s*/?>|</p>|</tr>|</h1>", "\n", corpo)
    corpo = re.sub(r"</td>", " ", corpo)
    corpo = re.sub(r"<[^>]+>", "", corpo)
    corpo = _html.unescape(corpo).replace("͏", "").replace("‌", "").replace("\xa0", " ")
    linhas = [re.sub(r"[ \t]+", " ", l).strip() for l in corpo.splitlines()]
    saida, vazio = [], False
    for l in linhas:
        if l:
            saida.append(l)
            vazio = False
        elif not vazio and saida:
            saida.append("")
            vazio = True
    return previa + "\n\n" + "\n".join(saida).strip() + "\n"


def _valor(preco: str) -> float:
    return float(preco.replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)


def montar(qual: str, dados: dict) -> dict:
    T = textos()
    g = T["geral"]
    v = {"material": "", **GENERO[dados.get("genero", "m")], **dados}
    bruto = dict(T[qual])
    plural = len(dados.get("itens", [])) > 1
    if plural:  # pedido com mais de uma peça: "suas peças"
        for k in [k for k in bruto if k.endswith("_plural")]:
            bruto[k[:-len("_plural")]] = bruto[k]
    if not dados.get("roupa", True):  # só bolsa ou acessório: não se "veste"
        for k in [k for k in bruto if k.endswith("_acessorio")]:
            bruto[k[:-len("_acessorio")]] = bruto[k]
        if plural:
            v.update(do_seu="das suas", peca="peças")
    if dados.get("pago"):  # o 0 de quem já pagou (cartão): versão "pagamento aprovado"
        for k in [k for k in bruto if k.endswith("_pago")]:
            bruto[k[:-len("_pago")]] = bruto[k]
    if dados.get("misto"):  # peça pronta + sob encomenda no mesmo pedido
        for k in [k for k in bruto if k.endswith("_misto")]:
            bruto[k[:-len("_misto")]] = bruto[k]
    t = _preencher({k: x for k, x in bruto.items() if not k.endswith(("_plural", "_misto", "_acessorio", "_pago"))}, v)
    imagens = []
    # Uma peça: o nome dela; várias: todas, em fonte menor (blocos.curto).
    nomes = [i["nome"] for i in dados.get("itens", [])] if plural else dados.get("peca_nome", "")

    whats = lambda frase=None, principal=False: B.whatsapp(  # noqa: E731
        g["whatsapp"], _preencher(g["whatsapp_mensagem"], v), frase if frase is not None else g["frase_whats"],
        principal)
    resumo = lambda: B.resumo(t["resumo"], dados["itens"], dados["somas"], dados["pagamento"], dados["endereco"])  # noqa: E731
    linha = lambda: B.linha_do_tempo(dados["passos"])  # noqa: E731
    link_wa = B.link_whatsapp(g["whatsapp"], _preencher(g["whatsapp_mensagem"], v))
    fim = B.rodape(g["rodape"], [(a, b.replace("{whatsapp}", link_wa)) for a, b in g["rodape_links"]], g["empresa"])

    L = [B.faixa(t["faixa"]), B.cabecalho()]
    if qual == "e0":
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]), resumo(), whats(), fim]
    elif qual == "pagamento_shopify":
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]), whats(), fim]
    elif qual == "e1":
        p = dados["pix"]
        imagens.append(("qrpix", _qr_png(p["codigo"])))
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]),
              B.pix("cid:qrpix", p["valor"], t["pix_titulo"], t["pix_instrucao"], p["codigo"], t["pix_botao"], p["link_copiar"]),
              resumo(), whats(t["frase_whats"]), fim]
    elif qual in ("e2_pronta", "e2_encomenda"):
        L += [B.abertura(ABERTURA, t["hero_titulo"], t["hero_sub"]), B.texto(t["texto"]),
              B.datas(dados["data_postagem"], dados["data_entrega"], t.get("etiqueta", "")),
              linha(), resumo(), whats(), fim]
    elif qual == "e3":
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]),
              B.curto([i["img"] for i in dados["itens"]], nomes, dados["pedido"]),
              linha(), B.datas(dados["data_postagem"], dados["data_entrega"]), whats(), fim]
    elif qual in ("e5", "e5_pronta"):
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]),
              B.curto([i["img"] for i in dados["itens"]], nomes, dados["pedido"]),
              whats(t["frase_whats"], principal=True), fim]
    elif qual == "e6":
        r = dados["rastreio"]
        # Previsão em destaque, com a data que os Correios dão para o pacote.
        prev = [B.datas(dados["data_postado"], dados["previsao_correios"], rotulos=("Postado em", "Entrega prevista"))] \
            if dados.get("previsao_correios") else []
        L += [B.abertura(ABERTURA, t["hero_titulo"], t["hero_sub"]),
              B.rastreio(dados["servico"], r["codigo"], "", r["link"]), *prev,
              B.botao(t["botao"], r["link"]), linha(), resumo(),
              *([B.nota(t["nota"])] if dados.get("nota") else []), whats(), fim]
    elif qual == "e7_retirada":
        r = dados["rastreio"]
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]),
              B.texto(dados["agencia"], t["agencia_rotulo"]),
              B.datas(None, dados["prazo_retirada"], rotulos=("", "Retire até")),
              B.rastreio(dados["servico"], r["codigo"], "", r["link"]),
              B.botao(t["botao"], r["link"]), whats(t["frase_whats"]), fim]
    elif qual in ("e7_atrasada", "e7_tentativa"):
        r = dados["rastreio"]
        # Tentativa sem sucesso: a previsão atual dos Correios, se ainda não passou.
        prev = [B.datas(None, dados["previsao_correios"])] \
            if qual == "e7_tentativa" and dados.get("previsao_correios") else []
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]),
              B.rastreio(dados["servico"], r["codigo"], t["rastreio_previsao"], r["link"]), *prev,
              B.botao(t["botao"], r["link"]), whats(t["frase_whats"]), fim]
    elif qual == "e8":
        # Cuidados vêm da descrição de cada produto (campo Manutenção). Peças com a
        # mesma instrução dividem um bloco; a foto é da mais cara do grupo. Peça
        # sem o campo (ex.: cinto) não ganha bloco.
        grupos = {}
        for i in sorted(dados["itens"], key=lambda i: -_valor(i["preco"])):
            if i.get("cuidados"):
                grupos.setdefault(tuple(i["cuidados"]), []).append(i)
        blocos_cuidados = [B.cuidados(g[0]["img"], " e ".join(x["nome"] for x in g), t["cuidados_titulo"], list(linhas))
                           for linhas, g in list(grupos.items())[:3]]
        abertura_txt = t["texto"] if blocos_cuidados else t["texto_sem_cuidados"]
        if not blocos_cuidados:
            t["previa"] = t["previa_sem_cuidados"]
        L += [B.abertura(ABERTURA, t["hero_titulo"], t["hero_sub"]), B.texto(abertura_txt), *blocos_cuidados,
              B.texto(t["depois"]), whats(" "), fim]
    elif qual == "e9":
        if not dados.get("nome"):
            # O assunto começa pelo nome (Pedro, 14/10); sem nome, não pode começar por vírgula.
            t["assunto"] = t["assunto"].lstrip(", ")
            t["assunto"] = t["assunto"][:1].upper() + t["assunto"][1:]
        pergunta_tamanho = dados.get("roupa", True) and not plural
        L += [B.texto(t["texto"], t["eyebrow"], t["titulo"]),
              B.curto([i["img"] for i in dados["itens"]], nomes, dados["pedido"]),
              # Tamanho só para uma peça de roupa: bolsa, acessório ou pedido com
              # mais de uma peça recebem só a nota (Pedro, 13/10).
              B.pesquisa(t["pergunta_tamanho"] if pergunta_tamanho else "",
                         t["opcoes_tamanho"] if pergunta_tamanho else [], t["pergunta_nota"], t["nota_pontas"],
                         t["comentario"], dados["pesquisa_link"]), fim]
    else:
        raise ValueError(f"e-mail desconhecido: {qual}")

    html = B.documento(t["assunto"], t["previa"], L)
    return {"assunto": t["assunto"], "previa": t["previa"], "html": html,
            "texto": _texto_plano(html, t["previa"]), "imagens": imagens}
