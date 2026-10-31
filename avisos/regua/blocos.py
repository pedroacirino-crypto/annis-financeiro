"""Blocos de e-mail da régua pós-compra, no visual do site (13/10/2026).

Cada função devolve uma linha <tr> da coluna de 600 px. O e-mail é montado
em tabela, com o estilo dentro de cada elemento, porque Gmail e Outlook
ignoram grid, flex e boa parte do CSS de página. O <style> do cabeçalho só
cuida do celular (abaixo de 480 px), que Gmail, Apple Mail e iOS respeitam;
onde não pegar, a versão de computador encolhe sem quebrar.

As fontes do site (Newsreader e Poppins) carregam no Apple Mail e no iPhone.
O Gmail não carrega fonte de fora: lá os títulos saem em Georgia e o texto em
Arial, que é o que o design aprovou para essas caixas.

Texto sobre foto (abertura) usa imagem de fundo e, para o Outlook do
computador, o mesmo desenho em VML.
"""

from __future__ import annotations

import html as _html
from urllib.parse import quote

MARROM = "#68380a"
CREME = "#fff6f0"
FUNDO = "#f3e9df"
TINTA = "#1c1c1c"
APOIO = "#6e6258"
LINHA = "#e6d5c4"
SERIF = "Newsreader, Georgia, 'Times New Roman', serif"
SANS = "Poppins, Arial, Helvetica, sans-serif"

LOGO = "https://annis.store/cdn/shop/files/Artboard_1_copy_6.png?width=600"

CSS = """
:root { color-scheme:light only; supported-color-schemes:light only; }
body { margin:0; padding:0; background:FUNDO; }
table { border-collapse:collapse; }
img { border:0; outline:none; text-decoration:none; -ms-interpolation-mode:bicubic; }
a { color:MARROM; }
@media (max-width:480px) {
  .px { padding-left:20px !important; padding-right:20px !important; }
  .logo { width:110px !important; }
  .hero { height:380px !important; }
  .hero-in { padding:50px 20px 20px !important; }
  .hero-t { font-size:28px !important; }
  .h1 { font-size:28px !important; }
  .pilha { display:block !important; width:100% !important; box-sizing:border-box !important; }
  .sem-dir { padding-right:0 !important; }
  .pilha-pad { padding:12px 0 0 0 !important; }
  .item-img { width:76px !important; height:114px !important; }
  .item-preco { display:block !important; padding:6px 0 0 0 !important; }
  .so-pc { display:none !important; }
  .data-d { font-size:26px !important; }
  .data-dir { padding-left:14px !important; }
  .rastreio { font-size:27px !important; }
  .passo { font-size:11px !important; }
  .btn-bloco { display:block !important; padding-left:12px !important; padding-right:12px !important; }
  .pix-qr { padding:0 0 16px 0 !important; text-align:center !important; }
  .cuidados-img { width:100% !important; height:auto !important; }
  .oferta-g { font-size:26px !important; }
  .escala td { padding:9px 0 !important; font-size:12px !important; }
}
""".replace("FUNDO", FUNDO).replace("MARROM", MARROM)


def esc(t: str) -> str:
    return _html.escape(t, quote=False)


def documento(assunto: str, previa: str, linhas: list[str]) -> str:
    """Envolve os blocos na página do e-mail, com a prévia escondida."""
    corpo = "\n".join(linhas)
    # Espaços invisíveis depois da prévia: sem eles, o Gmail completa a prévia
    # com o começo do texto do e-mail (a faixa de status).
    enchimento = "&#847;&zwnj;&nbsp;" * 60
    return f"""<!doctype html>
<html lang="pt-BR" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>{esc(assunto)}</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400&family=Poppins:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body style="margin:0;padding:0;background:{FUNDO};">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{FUNDO};">{esc(previa)}{enchimento}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{FUNDO};">
<tr><td align="center" style="padding:24px 0;">
<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:{CREME};font-family:{SANS};color:{TINTA};font-size:14px;line-height:1.6;">
{corpo}
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>"""


def _sec(conteudo: str, topo: int = 28, base: int = 28, alinhar: str = "left") -> str:
    return (f'<tr><td class="px" align="{alinhar}" style="padding:{topo}px 44px {base}px 44px;text-align:{alinhar};">'
            f"{conteudo}</td></tr>")


def _p(texto: str, estilo: str = "") -> str:
    return f'<p style="margin:0 0 14px 0;{estilo}">{texto}</p>'


def _eyebrow(t: str) -> str:
    return (f'<p style="margin:0 0 10px 0;font-size:11px;letter-spacing:0.18em;text-transform:uppercase;'
            f'color:{MARROM};font-weight:500;">{esc(t)}</p>')


def _rotulo(t: str) -> str:
    return (f'<span style="display:block;font-size:10.5px;letter-spacing:0.16em;text-transform:uppercase;'
            f'color:{MARROM};font-weight:500;margin-bottom:4px;">{esc(t)}</span>')


def _botao(texto: str, href: str, vazado: bool = False) -> str:
    fundo, cor, borda = ("transparent", MARROM, MARROM) if vazado else (MARROM, CREME, MARROM)
    # VML para o Outlook do computador desenhar o botão com fundo.
    vml = "" if vazado else (
        f'<!--[if mso]><v:rect xmlns:v="urn:schemas-microsoft-com:vml" href="{href}" style="height:48px;v-text-anchor:middle;width:280px;" '
        f'stroke="f" fillcolor="{MARROM}"><center style="color:{CREME};font-family:Arial,sans-serif;font-size:12px;letter-spacing:2px;">'
        f"{esc(texto.upper())}</center></v:rect><![endif]--><!--[if !mso]><!-->")
    fim = "" if vazado else "<!--<![endif]-->"
    return (f'{vml}<a class="btn-bloco" href="{href}" style="display:inline-block;background:{fundo};color:{cor};'
            f"border:1px solid {borda};text-decoration:none;text-transform:uppercase;letter-spacing:0.12em;"
            f'font-size:12px;font-weight:500;padding:16px 32px;text-align:center;font-family:{SANS};">{esc(texto)}</a>{fim}')


# Blocos -----------------------------------------------------------------------

def faixa(t: str) -> str:
    return (f'<tr><td align="center" style="background:{MARROM};color:{CREME};font-size:12px;letter-spacing:0.08em;'
            f'padding:10px 16px;text-align:center;">{esc(t)}</td></tr>')


def cabecalho() -> str:
    return (f'<tr><td align="center" style="padding:22px 0 18px 0;">'
            f'<a href="https://annis.store"><img class="logo" src="{LOGO}" width="140" alt="ANNIS" '
            f'style="width:140px;height:auto;display:inline-block;"></a></td></tr>')


def abertura(img: str, titulo: str, sub: str) -> str:
    """Foto da campanha de ponta a ponta, com o título em branco por cima."""
    return f"""<tr><td class="hero" background="{img}" bgcolor="#3a2a1c" valign="bottom" height="330" style="height:330px;background-color:#3a2a1c;background-image:url('{img}');background-size:cover;background-position:center;">
<!--[if gte mso 9]><v:rect xmlns:v="urn:schemas-microsoft-com:vml" fill="true" stroke="false" style="width:600px;height:330px;"><v:fill type="frame" src="{img}" color="#3a2a1c" /><v:textbox inset="0,0,0,0"><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td class="hero-in" valign="bottom" style="padding:70px 44px 28px 44px;background-image:linear-gradient(to top, rgba(28,18,8,0.66), rgba(28,18,8,0));">
<p class="hero-t" style="margin:0;font-family:{SERIF};font-weight:300;font-size:40px;line-height:1.05;color:#ffffff;text-shadow:0 1px 12px rgba(0,0,0,0.45);">{esc(titulo)}</p>
<p style="margin:6px 0 0 0;font-size:13px;letter-spacing:0.04em;color:#ffffff;text-shadow:0 1px 8px rgba(0,0,0,0.5);">{esc(sub)}</p>
</td></tr></table>
<!--[if gte mso 9]></v:textbox></v:rect><![endif]-->
</td></tr>"""


def texto(paragrafos: list[str], eyebrow: str = "", titulo: str = "") -> str:
    c = _eyebrow(eyebrow) if eyebrow else ""
    if titulo:
        c += (f'<h1 class="h1" style="margin:0 0 14px 0;font-family:{SERIF};font-weight:300;font-size:34px;'
              f'line-height:1.1;color:{TINTA};">{esc(titulo).replace(chr(10), "<br>")}</h1>')
    c += "".join(_p(esc(p)) for p in paragrafos)
    return _sec(c, base=14)


def _item(i: dict) -> str:
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 18px 0;"><tr>
<td width="96" valign="middle" style="width:96px;"><img class="item-img" src="{i['img']}" width="96" height="144" alt="" style="width:96px;height:144px;object-fit:cover;display:block;background:#efe3d6;"></td>
<td valign="middle" style="padding:0 0 0 18px;"><p style="margin:0;font-family:{SERIF};font-size:19px;line-height:1.2;">{esc(i['nome'])}</p>
<p style="margin:4px 0 0 0;color:{APOIO};font-size:12px;">{esc(i['variante'])}</p>
<p class="item-preco" style="display:none;margin:0;font-size:14px;">{esc(i['preco'])}</p></td>
<td class="so-pc" valign="middle" align="right" style="font-size:14px;white-space:nowrap;padding-left:12px;">{esc(i['preco'])}</td>
</tr></table>"""


def resumo(titulo: str, itens: list[dict], somas: list[tuple[str, str]], pagamento: str,
           endereco: list[str]) -> str:
    """O pedido inteiro, como no e-mail da Shopify: peças, valores, pagamento e entrega."""
    linhas_somas = ""
    for k, (rot, val) in enumerate(somas):
        ultimo = k == len(somas) - 1
        estilo = (f"font-family:{SERIF};font-size:22px;padding-top:10px;border-top:1px solid {LINHA};"
                  if ultimo else "font-size:13px;padding:2px 0;")
        linhas_somas += f'<tr><td style="{estilo}">{esc(rot)}</td><td align="right" style="{estilo}text-align:right;">{esc(val)}</td></tr>'
    c = _eyebrow(titulo) + "".join(_item(i) for i in itens)
    c += f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid {LINHA};margin-top:4px;"><tr><td style="height:12px;"></td><td></td></tr>{linhas_somas}</table>'
    c += f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:18px;font-size:13px;"><tr>
<td class="pilha" width="50%" valign="top" style="padding-right:8px;">{_rotulo('Pagamento')}{esc(pagamento)}</td>
<td class="pilha pilha-pad" width="50%" valign="top" style="padding-left:8px;">{_rotulo('Entrega')}{'<br>'.join(esc(l) for l in endereco)}</td>
</tr></table>"""
    return _sec(c, topo=14)


def curto(imgs: list[str], nome: str | list[str], pedido: str) -> str:
    """Fotos pequenas e o nome da peça; com várias peças, um nome por linha, em fonte menor."""
    nomes = "<br>".join(esc(n) for n in nome) if isinstance(nome, list) else esc(nome)
    fotos = "".join(f'<td style="padding-right:6px;"><img src="{i}" width="56" height="84" alt="" style="width:56px;height:84px;object-fit:cover;display:block;background:#efe3d6;"></td>' for i in imgs)
    return _sec(f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>{fotos}
<td valign="middle" style="padding-left:8px;"><p style="margin:0;font-family:{SERIF};font-size:{14 if len(imgs) > 1 else 18}px;line-height:{1.4 if len(imgs) > 1 else 1.25};">{nomes}</p>
<p style="margin:2px 0 0 0;color:{APOIO};font-size:12px;">Pedido {esc(pedido)}</p></td></tr></table>""", topo=8, base=14)


def pix(qr_img: str, valor: str, titulo: str, instrucao: str, codigo: str, botao: str, link_copiar: str) -> str:
    return _sec(f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;table-layout:fixed;"><tr>
<td class="pilha pix-qr" width="168" valign="middle" style="padding:22px 0 22px 22px;"><img src="{qr_img}" width="168" height="168" alt="QR Code do Pix" style="width:168px;height:168px;display:block;margin:0 auto;"></td>
<td class="pilha" valign="middle" style="padding:22px;">
{_eyebrow('Pix · ' + valor)}
<p style="margin:0 0 8px 0;font-family:{SERIF};font-size:22px;line-height:1.2;">{esc(titulo)}</p>
<p style="margin:0 0 10px 0;color:{APOIO};font-size:12px;">{esc(instrucao)}</p>
<p style="margin:0 0 12px 0;font-family:Menlo,Consolas,monospace;font-size:11.5px;background:{CREME};border:1px solid {LINHA};padding:9px 10px;color:#3d3a36;word-break:break-all;overflow-wrap:anywhere;line-height:1.4;">{esc(codigo)}</p>
{_botao(botao, link_copiar)}
</td></tr></table>""", topo=8, base=14)


def datas(postagem: str | None, entrega: str, etiqueta: str = "",
          rotulos: tuple[str, str] = ("Postagem prevista", "Entrega prevista")) -> str:
    """Datas grandes, no calendário. Sem postagem, só a entrega, em largura cheia."""
    tag = (f'<p style="margin:0 0 12px 0;"><span style="display:inline-block;background:{MARROM};color:{CREME};font-size:10.5px;'
           f'letter-spacing:0.16em;text-transform:uppercase;padding:6px 12px;font-weight:500;">{esc(etiqueta)}</span></p>') if etiqueta else ""
    d = f"font-family:{SERIF};font-weight:300;font-size:32px;line-height:1.1;color:{MARROM};"
    return _sec(f"""{tag}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid {MARROM};border-bottom:1px solid {MARROM};"><tr>
{f'<td width="50%" valign="top" style="padding:16px 0;">{_rotulo(rotulos[0])}<span class="data-d" style="{d}">{esc(postagem)}</span></td>' if postagem else ""}
<td class="data-dir" {'width="50%" ' if postagem else ""}valign="top" style="padding:16px 0 16px {22 if postagem else 0}px;{f"border-left:1px solid {LINHA};" if postagem else ""}">{_rotulo(rotulos[1])}<span class="data-d" style="{d}">{esc(entrega)}</span></td>
</tr></table>""", topo=8, base=14)


def linha_do_tempo(passos: list[tuple[str, str]]) -> str:
    """passos: (estado, rótulo); estado é feito, agora ou vazio. Rótulo aceita <br>."""
    celulas = ""
    for estado, rotulo in passos:
        if estado == "feito":
            ponto = f"background:{MARROM};border:1px solid {MARROM};width:15px;height:15px;"
            cor = TINTA
        elif estado == "agora":
            ponto = f"background:{CREME};border:5px solid {MARROM};width:7px;height:7px;"
            cor = MARROM
        else:
            ponto = f"background:{CREME};border:1px solid #c9ad93;width:15px;height:15px;"
            cor = "#8a7a6c"
        peso = "600" if estado == "agora" else "400"
        partes = [esc(x) for x in rotulo.split("<br>")]
        celulas += (f'<td width="25%" align="center" valign="top" style="text-align:center;">'
                    f'<div style="{ponto}border-radius:50%;margin:0 auto 8px auto;font-size:0;line-height:0;">&nbsp;</div>'
                    f'<span class="passo" style="font-size:12px;line-height:1.3;color:{cor};font-weight:{peso};">{"<br>".join(partes)}</span></td>')
    return _sec(f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-image:linear-gradient(#c9ad93,#c9ad93);background-size:75% 1px;background-repeat:no-repeat;background-position:center 8px;"><tr>{celulas}</tr></table>""", topo=8, base=14)


def rastreio(servico: str, codigo: str, previsao: str, link: str = "") -> str:
    return _sec(f"""{_rotulo('Código de rastreio · ' + servico)}
<p class="rastreio" style="margin:0 0 6px 0;font-family:{SERIF};font-size:34px;font-weight:300;letter-spacing:0.06em;color:{MARROM};line-height:1.1;">{esc(codigo)}</p>
{f'<p style="margin:0;color:{APOIO};font-size:12px;">{esc(previsao)}</p>' if previsao else ""}""", topo=8, base=14)


def botao(texto_: str, href: str) -> str:
    return _sec(_botao(texto_, href), topo=8, base=14, alinhar="center")


def link_whatsapp(numero: str, mensagem: str) -> str:
    return f"https://wa.me/{numero}?text={quote(mensagem)}"


def whatsapp(numero: str, mensagem: str, frase: str, principal: bool = False) -> str:
    href = link_whatsapp(numero, mensagem)
    frase_html = _p(esc(frase), f"color:{APOIO};font-size:12px;") if frase.strip() else ""
    return _sec(f"{frase_html}{_botao('Falar no WhatsApp', href, vazado=not principal)}", topo=8, base=28, alinhar="center")


def nota(texto_: str) -> str:
    return _sec(f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-top:1px solid {LINHA};"><tr>
<td width="26" style="padding:14px 12px 0 0;"><div style="width:22px;height:28px;border:1.5px solid {MARROM};background:#ffffff;font-size:0;">&nbsp;</div></td>
<td style="padding:14px 0 0 0;font-size:13px;">{esc(texto_)}</td></tr></table>""", topo=4, base=14)


def cuidados(img: str, nomes: str, titulo: str, linhas: list[str]) -> str:
    """Cuidados de uma peça (ou de peças com a mesma etiqueta), como estão no
    campo Manutenção da descrição do produto: uma linha por instrução."""
    itens = "".join(f'<tr><td valign="top" style="padding:8px 0;border-top:1px solid {LINHA};font-size:13px;">{esc(l)}</td></tr>'
                    for l in linhas)
    return _sec(f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td class="pilha sem-dir" width="150" valign="top" style="padding-right:22px;"><img class="cuidados-img" src="{img}" width="150" height="225" alt="" style="width:150px;height:225px;object-fit:cover;display:block;"></td>
<td class="pilha pilha-pad" valign="top">{_eyebrow(nomes)}<p style="margin:0 0 12px 0;font-family:{SERIF};font-size:22px;line-height:1.2;">{esc(titulo)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{itens}</table></td>
</tr></table>""", topo=8, base=14)


def pesquisa(pergunta_tamanho: str, opcoes: list[str], pergunta_nota: str, pontas: list[str],
             comentario: str, link_base: str) -> str:
    """Um toque registra a resposta: cada opção é um link para a página da pesquisa."""
    sep = "&amp;" if "?" in link_base else "?"  # &amp;: sem isso, "&nota" vira "¬a"
    ops = "".join(f'<td width="33%" style="padding:0 4px;"><a href="{link_base}{sep}tamanho={quote(o.lower())}" style="display:block;border:1px solid {MARROM};color:{MARROM};text-align:center;padding:12px 4px;font-size:13px;background:#ffffff;text-decoration:none;">{esc(o)}</a></td>' for o in opcoes)
    notas = "".join(f'<td style="padding:0 2px;"><a href="{link_base}{sep}nota={n}" style="display:block;background:{MARROM};color:{CREME};text-align:center;padding:10px 0;font-size:13px;text-decoration:none;">{n}</a></td>' for n in range(11))
    sub = f"margin:0 0 12px 0;font-family:{SERIF};font-size:22px;line-height:1.2;"
    tamanho = (f'<p style="{sub}">{esc(pergunta_tamanho)}</p><table role="presentation" width="100%" cellpadding="0" '
               f'cellspacing="0" border="0" style="margin:0 0 22px 0;"><tr>{ops}</tr></table>') if opcoes else ""
    return _sec(f"""{tamanho}
<p style="{sub}">{esc(pergunta_nota)}</p>
<table class="escala" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{notas}</tr></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px;font-size:11px;color:{APOIO};"><tr><td>{esc(pontas[0])}</td><td align="right" style="text-align:right;">{esc(pontas[1])}</td></tr></table>
<p style="margin:18px 0 0 0;text-align:center;"><a href="{link_base}{sep}comentario=1" style="color:{MARROM};font-size:12px;">{esc(comentario)}</a></p>""", topo=8, base=28)


def rodape(frase: str, links: list, empresa: str) -> str:
    """links: [(texto, url)], separados por ponto no meio."""
    lista = " · ".join(f'<a href="{u}" style="color:{MARROM};text-decoration:none;">{esc(t)}</a>' for t, u in links)
    return f"""<tr><td class="px" align="center" style="padding:30px 30px 26px 30px;text-align:center;font-size:11.5px;color:{MARROM};line-height:1.6;border-top:1px solid {LINHA};">
<p style="margin:0 0 10px 0;">{esc(frase)}</p>
<p style="margin:0 0 14px 0;">{lista}</p>
<img src="{LOGO}" width="420" alt="ANNIS" style="width:100%;max-width:420px;height:auto;display:block;margin:0 auto 14px auto;">
<p style="margin:0;">{esc(empresa)}</p>
</td></tr>"""
