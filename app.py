"""
Dashboard de Conciliação Financeira sobre Pagar.me
"""

import hmac
import os

import streamlit as st
import pandas as pd
import altair as alt
import json
from datetime import date, timedelta, datetime

import db
import pagarme_client
import shopify_client

# Precisa ser o primeiro comando Streamlit do arquivo.
st.set_page_config(
    page_title="Conciliação Pagar.me",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)


JANELA_SYNC = 90  # dias de extrato e recebíveis baixados a cada sincronização


def sincronizar(ate: date, avisar=None):
    """Baixa vendas, extrato e recebíveis para o banco local.

    Vendas vêm por inteiro (a aba Histórico precisa de todos os meses e o
    volume é baixo); extrato e recebíveis vêm da janela recente. O corte de
    dia é em horário de Brasília, igual ao Dash oficial, em UTC as vendas da
    noite cairiam no dia seguinte.
    """
    tz_ini = (ate - timedelta(days=JANELA_SYNC)).isoformat() + "T00:00:00-03:00"
    tz_fim = ate.isoformat() + "T23:59:59-03:00"
    conta = {}

    if avisar:
        avisar("Buscando vendas…")
    conta["vendas"] = db.upsert_charges(
        pagarme_client.get_charges(
            created_since="2020-01-01T00:00:00-03:00", created_until=tz_fim
        )
    )

    if avisar:
        avisar("Buscando extrato…")
    conta["operacoes"] = db.upsert_balance_operations(
        pagarme_client.get_balance_operations(created_since=tz_ini, created_until=tz_fim)
    )

    # Recebíveis também vêm por inteiro: a aba Histórico calcula o custo de
    # cada mês cruzando recebível com venda, e com janela curta os meses
    # antigos apareceriam com custo zero, errado, não vazio.
    if avisar:
        avisar("Buscando recebíveis…")
    conta["recebiveis"] = db.upsert_payables(
        pagarme_client.get_payables(
            created_since="2020-01-01T00:00:00-03:00", created_until=tz_fim
        )
    )
    # Checkouts abandonados: só se a Shopify estiver configurada. Diferente
    # de pedidos, esta consulta não sofre o corte de 60 dias.
    if shopify_client.configurado():
        if avisar:
            avisar("Buscando checkouts abandonados…")
        try:
            conta["abandonados"] = db.upsert_abandonados(
                shopify_client.listar_abandonados(limite=500)
            )
            if avisar:
                avisar("Buscando pedidos da loja…")
            conta["pedidos_loja"] = db.upsert_pedidos(
                shopify_client.listar_pedidos(limite=500)
            )
        except Exception as e:
            conta["abandonados"] = 0
            conta["erro_shopify"] = str(e)
    return conta


# ── Identidade visual ────────────────────────────────────────────────────────
# Padrão da annis.store: Newsreader serif light nos títulos, Poppins na
# interface, marrom #68380A sobre creme #FFF6F0. As cores base ficam em
# .streamlit/config.toml; aqui vão tipografia e ajustes de componente.
MARROM = "#68380A"
# #9A7B5A, mais próximo do site, reprova em contraste sobre o creme (3,68:1).
# Este tom dá 5,4:1 e mantém a temperatura da paleta.
MARROM_CLARO = "#7F6040"
LINHA = "#E8DACB"
CREME = "#FFF6F0"
LOGO_URL = "https://annis.store/cdn/shop/files/Artboard_1_copy_6.png"

# Cupom de recuperação, lido de Descontos no admin da loja. Só é aplicado a
# quem abandonou sem tentar pagar, ver _card_recuperar. Se o código mudar ou
# expirar, atualize aqui; não há endpoint que descubra sozinho qual usar.
CUPOM_RECUPERACAO = "VOLTE5"
DESCONTO_RECUPERACAO = "5% OFF"

# O bloco abaixo não pode conter linhas em branco: no markdown do Streamlit
# uma linha vazia encerra o bloco HTML e o resto do CSS vaza como texto na
# tela. As fontes vêm por @import porque tags <link> são removidas.
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,200..500&family=Poppins:wght@300;400;500&display=swap');
/* Não usar seletores amplos (button, [class*=st-]): eles atingem os spans de
   ícone do Streamlit e quebram as ligaduras do Material Symbols. */
html, body, .stApp {{ font-family: 'Poppins', sans-serif; }}
/* Tarja da marca ocupando a largura toda: o próprio cabeçalho do Streamlit,
   que já é full-bleed e passa por cima da barra lateral. O logo entra por
   ::before com filtro que o torna branco, evitando depender de existir um
   arquivo invertido no CDN da loja. */
/* `position: fixed` com left:0 e 100vw: por padrão o cabeçalho é absolute
   dentro da área de conteúdo, então em tela larga ele começa depois da barra
   lateral (left: 256px) e a tarja fica pela metade. Em tela estreita o
   Streamlit sobrepõe a lateral e o problema não aparece, daí passar
   despercebido. O z-index sobe acima do 999991 da lateral. */
[data-testid="stHeader"] {{ background: {MARROM}; height: 3.4rem; border-bottom: 1px solid rgba(0,0,0,0.15); position: fixed; top: 0; left: 0; width: 100vw; z-index: 999992; }}
section[data-testid="stSidebar"] > div {{ padding-top: 3.4rem; }}
[data-testid="stHeader"]::before {{ content: ""; position: absolute; left: 1.5rem; top: 50%; transform: translateY(-50%); width: 104px; height: 23px; background: url("{LOGO_URL}") no-repeat left center / contain; filter: brightness(0) invert(1); opacity: 0.95; }}
[data-testid="stHeader"] button, [data-testid="stHeader"] span, [data-testid="stHeader"] svg {{ color: #FFFFFF !important; fill: #FFFFFF !important; }}
[data-testid="stSidebarCollapsedControl"] button svg, [data-testid="stSidebarCollapseButton"] svg {{ color: #FFFFFF !important; }}
/* No celular a barra lateral nasce recolhida e o Streamlit põe o botão de
   abrir no canto esquerdo do cabeçalho, em cima do logo. Empurra o logo
   para depois do botão. */
@media (max-width: 768px) {{
  [data-testid="stHeader"]::before {{ left: 3.6rem; width: 88px; }}
}}
h1, h2, h3, [data-testid="stMetricValue"] {{ font-family: 'Newsreader', serif !important; font-weight: 200 !important; color: {MARROM} !important; letter-spacing: 0.01em; }}
h1 {{ font-size: 2.4rem !important; line-height: 1.15; }}
h2 {{ font-size: 1.8rem !important; }}
h3 {{ font-size: 1.35rem !important; }}
[data-testid="stMetricLabel"] p {{ font-family: 'Poppins', sans-serif !important; font-size: 0.66rem !important; font-weight: 400 !important; text-transform: uppercase; letter-spacing: 0.11em; color: {MARROM_CLARO} !important; }}
[data-testid="stMetricValue"] {{ font-size: 1.4rem !important; }}
[data-testid="stMetric"] {{ background: #FFFFFF; border: 1px solid rgba(104,56,10,0.12); border-radius: 2px; padding: 0.85rem 0.9rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 1.8rem; border-bottom: 1px solid rgba(104,56,10,0.15); }}
.stTabs [data-baseweb="tab"] {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.14em; color: {MARROM_CLARO}; padding: 0 0 0.6rem 0; }}
.stTabs [aria-selected="true"] {{ color: {MARROM} !important; }}
section[data-testid="stSidebar"] {{ background: #FFFFFF; border-right: 1px solid rgba(104,56,10,0.12); }}
.stButton button {{ border-radius: 2px; text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.72rem; }}
[data-testid="stCaptionContainer"] p {{ color: {MARROM_CLARO}; font-size: 0.78rem; }}
hr {{ border-color: rgba(104,56,10,0.15); }}
[data-testid="stDataFrame"] {{ border: 1px solid rgba(104,56,10,0.12); border-radius: 2px; }}
.tbl-wrap {{ background:#FFFFFF; border:1px solid rgba(104,56,10,0.12); border-radius:2px; overflow:auto; }}
.tbl {{ width:100%; border-collapse:collapse; font-family:'Poppins',sans-serif; font-size:0.82rem; }}
.tbl thead th {{ position:sticky; top:0; background:#FFFFFF; text-align:left; font-weight:400; font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em; color:{MARROM_CLARO}; padding:0.85rem 0.9rem 0.5rem; border-bottom:1px solid {LINHA}; white-space:nowrap; }}
.tbl tbody td {{ padding:0.6rem 0.9rem; border-bottom:1px solid rgba(232,218,203,0.55); color:#4A2C0F; white-space:nowrap; }}
.tbl tbody tr:last-child td {{ border-bottom:none; }}
.tbl tbody tr:hover td {{ background:#FFFBF7; }}
.tbl th.num, .tbl td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.tbl td.neg {{ color:#8C2F0D; }}
.btn-acao {{ display:block; text-align:center; padding:0.5rem 0.6rem; border:1px solid rgba(104,56,10,0.35); border-radius:2px; color:{MARROM}; text-decoration:none; font-family:'Poppins',sans-serif; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em; background:#FFFFFF; }}
.btn-acao:hover {{ background:#FFF3EA; text-decoration:none; }}
.btn-acao.off {{ opacity:0.4; pointer-events:none; border-style:dashed; }}
/* Cartão em volta de cada gráfico: lado a lado e sem moldura, dois gráficos
   viram uma faixa só de barras e o olho não sabe onde um termina.
   O `>` é essencial: sem ele o seletor casa também os blocos externos que
   contêm o gráfico, e o painel inteiro fica branco. Só o container criado por
   st.container(border=True) tem o gráfico como filho direto. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] [data-testid="stFullScreenFrame"]) {{ background:#FFFFFF; border:1px solid rgba(104,56,10,0.12); border-radius:2px; padding:0.6rem 1.1rem 1rem; }}
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] [data-testid="stFullScreenFrame"]) h3 {{ font-size:1.15rem !important; }}
</style>
""", unsafe_allow_html=True)

# ── Acesso ───────────────────────────────────────────────────────────────────

def _senha_configurada():
    """Senha vinda do cofre do host (hospedado) ou do .env (local)."""
    try:
        s = st.secrets.get("APP_PASSWORD")
        if s:
            return s
    except Exception:
        pass
    return os.environ.get("APP_PASSWORD")


def exigir_senha():
    """Porta de entrada do painel.

    Falha fechada de propósito: sem senha configurada o app não abre. A
    Streamlit Community Cloud só publica apps públicos, qualquer um com o
    link entraria, e aqui aparecem nome de cliente, faturamento e agenda de
    recebimentos. Deixar passar quando a senha falta seria transformar um
    esquecimento de configuração em vazamento.
    """
    esperada = _senha_configurada()
    if not esperada:
        st.error(
            "**APP_PASSWORD não configurada.** Defina a senha no cofre de "
            "secrets do host (ou no `.env`, se estiver rodando local) antes "
            "de usar o painel."
        )
        st.stop()

    if st.session_state.get("_autenticado"):
        return

    st.markdown(
        f"<div style='text-align:center;padding:3rem 0 1rem'>"
        f"<img src='{LOGO_URL}' alt='ANNIS' style='width:150px'>"
        f"<div style='font-family:Poppins;font-size:0.66rem;letter-spacing:0.22em;"
        f"text-transform:uppercase;color:{MARROM_CLARO};padding-top:0.5rem'>"
        f"Financeiro</div></div>",
        unsafe_allow_html=True,
    )
    _, meio, _ = st.columns([1, 1.4, 1])
    with meio:
        with st.form("entrar"):
            senha = st.text_input("Senha", type="password", label_visibility="collapsed",
                                  placeholder="Senha de acesso")
            if st.form_submit_button("Entrar", use_container_width=True, type="primary"):
                # compare_digest evita vazar o tamanho da senha pelo tempo de resposta
                if hmac.compare_digest(senha, esperada):
                    st.session_state["_autenticado"] = True
                    st.rerun()
                else:
                    st.error("Senha incorreta.")
    st.stop()


exigir_senha()

# Daqui para baixo só roda autenticado: a carga inicial dispara uma varredura
# na API, e não faz sentido um visitante sem senha provocar isso.
db.init_db()

# Hospedado o disco é efêmero, o banco some a cada reinício e a tela abriria
# vazia. Sem isto, alguém teria que clicar em "Atualizar dados" toda vez.
if db.get_db_counts()["charges"] == 0:
    try:
        with st.spinner("Primeira carga dos dados…"):
            sincronizar(date.today())
    except Exception as _e:
        st.warning(f"Não foi possível carregar os dados automaticamente: {_e}")

# ── Helpers ──────────────────────────────────────────────────────────────────

def fmt_brl(centavos: int) -> str:
    return f"R$ {centavos / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def md(texto: str) -> str:
    """Escapa o cifrão, em markdown o Streamlit trata `$...$` como LaTeX."""
    return texto.replace("$", r"\$")


def tabela(df, num=(), altura_max=None):
    """Tabela em HTML no padrão da marca.

    O st.dataframe desenha num canvas com grade em volta de cada célula, não
    dá para estilizar por CSS e destoa do resto. Aqui sai HTML de verdade:
    sem linhas verticais, cabeçalho em caixa alta discreta, régua fina entre
    as linhas. `num` são as colunas alinhadas à direita (valores).
    """
    import html as _html

    cab = "".join(
        f'<th class="num">{_html.escape(str(c))}</th>' if c in num
        else f"<th>{_html.escape(str(c))}</th>"
        for c in df.columns
    )
    corpo = []
    for _, linha in df.iterrows():
        celulas = []
        for c in df.columns:
            v = "" if pd.isna(linha[c]) else str(linha[c])
            classe = "num" if c in num else ""
            if classe and v.strip().startswith("-"):
                classe += " neg"
            celulas.append(
                f'<td class="{classe}">{_html.escape(v)}</td>' if classe
                else f"<td>{_html.escape(v)}</td>"
            )
        corpo.append("<tr>" + "".join(celulas) + "</tr>")

    estilo = f' style="max-height:{altura_max}px"' if altura_max else ""
    # Sem quebras de linha: linha em branco encerraria o bloco HTML no markdown.
    st.markdown(
        f'<div class="tbl-wrap"{estilo}><table class="tbl">'
        f"<thead><tr>{cab}</tr></thead><tbody>{''.join(corpo)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def fmt_pct(x: float, casas: int = 2) -> str:
    """Percentual com vírgula decimal, o resto da tela é todo pt-BR."""
    return f"{x:.{casas}f}%".replace(".", ",")


def fmt_curto(centavos: int) -> str:
    """Valor sem centavos e sem 'R$', para caber como rótulo em cima da barra."""
    return f"{centavos / 100:,.0f}".replace(",", ".")


def barras(df, x, y, rotulo=None, rotulo_x="", rotulo_y="", tooltip=None, altura=250):
    """Barras em hue único da marca, com o valor escrito em cima.

    Feito à mão em vez de st.bar_chart por três motivos: aquele liga pan/zoom
    no hover, usa escala contínua no eixo x, o que deixa as barras com
    larguras e espaçamentos irregulares quando há dias sem venda, e ignora a
    paleta. Aqui o x é ordinal (uma faixa por categoria, todas iguais),
    sem interação de zoom.

    `rotulo` é a coluna com o texto já formatado. Quando ela existe, o eixo Y
    sai junto com a grade: ler o número escrito e estimar a mesma coisa pela
    altura da barra é informação duplicada.
    """
    tem_rotulo = rotulo is not None
    base = alt.Chart(df)

    eixo_y = (
        alt.Y(f"{y}:Q", title=None, axis=None,
              scale=alt.Scale(domainMax=float(df[y].max()) * 1.18, nice=False))
        if tem_rotulo
        else alt.Y(f"{y}:Q", title=rotulo_y or None, axis=alt.Axis(tickCount=5))
    )
    eixo_x = alt.X(
        f"{x}:N",
        title=rotulo_x or None,
        sort=None,
        scale=alt.Scale(paddingInner=0.35, paddingOuter=0.2),
        axis=alt.Axis(labelAngle=0, labelLimit=90),
    )

    marcas = base.mark_bar(
        color=MARROM, cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
    ).encode(x=eixo_x, y=eixo_y, tooltip=tooltip or [])

    if tem_rotulo:
        texto = base.mark_text(
            dy=-9, font="Poppins", fontSize=10, color=MARROM,
        ).encode(x=eixo_x, y=eixo_y, text=f"{rotulo}:N")
        grafico = alt.layer(marcas, texto)
    else:
        grafico = marcas

    grafico = (
        # width="container" junto com use_container_width: sem isso o Vega fixa
        # a largura padrão e o gráfico não acompanha a coluna ao redimensionar.
        # O fundo branco é explícito porque o Streamlit repassa a cor do app
        # (creme) para o Vega, que a pintaria por cima do cartão branco.
        grafico.properties(height=altura, width="container", background="#FFFFFF")
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelFont="Poppins", titleFont="Poppins",
            labelFontSize=11, titleFontSize=11,
            labelColor=MARROM_CLARO, titleColor=MARROM_CLARO,
            domainColor=LINHA, tickColor=LINHA, labelPadding=6,
        )
        .configure_axisX(grid=False)
    )
    if not tem_rotulo:
        grafico = grafico.configure_axisY(
            grid=True, gridColor=LINHA, gridDash=[2, 3], domain=False, ticks=False
        )
    return grafico


def _so_digitos(t: str) -> str:
    return "".join(c for c in (t or "") if c.isdigit())


def _link_whatsapp(telefone: str, texto: str) -> str:
    """Link do WhatsApp com a mensagem já escrita.

    É link, não integração: abre a conversa preenchida e ela revisa antes de
    enviar. Resolve o caso de uso sem API de WhatsApp, template aprovado nem
    custo por mensagem.

    Vai direto para web.whatsapp.com, sem passar por wa.me nem por
    api.whatsapp.com, por dois motivos.

    O wa.me reencoda a URL ao redirecionar (troca %20 por +) e nessa passagem
    destrói caracteres de 4 bytes: o emoji 🤎 chegava como losango de
    interrogação. O api.whatsapp.com preserva a URL, mas é só uma página
    intermediária cujo botão "Continuar para o WhatsApp Web" abre uma aba nova
    por conta própria, fora do nosso controle.
    """
    return _links_whatsapp(telefone, texto)[0]


def _links_whatsapp(telefone: str, texto: str) -> tuple:
    """Devolve (computador, Android, iPhone) para o mesmo contato.

    São três porque cada aparelho falha de um jeito diferente:

    - **Computador**: `web.whatsapp.com`, que é o que sempre funciona mesmo
      sem o aplicativo instalado.
    - **Android**: `whatsapp://`, que entrega a conversa direto ao aplicativo
      sem passar por página nenhuma. Testado no aparelho do Pedro.
    - **iPhone**: `api.whatsapp.com`, porque o WebKit se recusa a abrir
      aplicativo a partir de um iframe isolado, que é onde estes botões vivem.
      No iPhone da Ana o `whatsapp://` não fez nada, enquanto no Android
      funcionou. Como este é `https`, o bloqueio não se aplica.

    O `wa.me` está fora dos três de propósito: ele reencoda a URL ao
    redirecionar e destrói caracteres de 4 bytes, então o 🤎 chega como
    losango de interrogação. Medido de novo em 11/08/2026, continua quebrando.
    O `api.whatsapp.com` preserva o emoji intacto.
    """
    from urllib.parse import quote
    num = _so_digitos(telefone)
    if num and not num.startswith("55"):
        num = "55" + num
    texto_url = quote(texto)
    return (
        f"https://web.whatsapp.com/send?phone={num}&text={texto_url}",
        f"whatsapp://send?phone={num}&text={texto_url}",
        f"https://api.whatsapp.com/send?phone={num}&text={texto_url}",
    )


def _link_recuperacao(url: str) -> str:
    """URL do carrinho em português, sem mexer em cupom.

    Nada de `?discount=` aqui de propósito: 11 dos 25 carrinhos já vêm com
    FRETEGRATIS, que em compra pequena vale mais que 5% (R$ 71 num pedido de
    R$ 528). Como os dois não acumulam, forçar o cupom pioraria a oferta na
    maioria dos casos. O código vai no texto da mensagem e a cliente escolhe.
    """
    if not url:
        return url
    return url if "locale=" in url else f"{url}{'&' if '?' in url else '?'}locale=pt-BR"


def _nome_curto(titulo: str) -> str:
    """'Top de Jacquard Azul - Giverny' vira 'Top Giverny'.

    O título do catálogo é feito para busca; na mensagem ele soa robótico.
    Tipo da peça + coleção é como a cliente chama o produto.
    """
    titulo = (titulo or "").strip()
    if " - " in titulo:
        tipo, colecao = titulo.split(" - ", 1)
        return f"{tipo.split()[0]} {colecao.strip()}"
    return titulo


def _artigo(nome: str) -> str:
    """Heurística de gênero: peça terminada em 'a' é feminina."""
    primeira = (nome or "").split()[0] if nome else ""
    return "uma" if primeira.lower().endswith("a") else "um"


def _lista_produtos(itens_txt: str) -> tuple:
    """Devolve (frase com artigos, coleção comum, pronome de retomada).

    O pronome existe para a frase concordar: uma peça só vira "ela ficou
    esperando"; duas viram "eles ficaram". Sem isso a mensagem sai errada
    justamente no caso mais comum, que é carrinho de item único.
    """
    partes = []
    for pedaco in (itens_txt or "").split(", "):
        titulo = pedaco.split("x ", 1)[-1] if "x " in pedaco else pedaco
        curto = _nome_curto(titulo)
        if curto:
            partes.append(curto)
    if not partes:
        return "as peças que separou", None, "elas ficaram"

    colecoes = {p.split()[-1] for p in partes}
    colecao = colecoes.pop() if len(colecoes) == 1 else None

    com_artigo = [f"{_artigo(p)} {p}" for p in partes]
    if len(com_artigo) == 1:
        frase = com_artigo[0]
        pronome = "ela ficou" if com_artigo[0].startswith("uma") else "ele ficou"
    else:
        frase = ", ".join(com_artigo[:-1]) + " e " + com_artigo[-1]
        pronome = "eles ficaram"
    return frase, colecao, pronome


def _botoes_acao(a: dict, texto: str, rotulo_link: str = "Ver o carrinho",
                 url_link: str = None):
    """Botões de contato num componente isolado.

    Precisa ser componente e não markdown porque o Streamlit injeta
    rel="noopener noreferrer" em todo link de markdown. Aqui o HTML é nosso.

    O clique abre uma aba nova a cada pessoa contatada. Não tem contorno: o
    navegador só consegue mirar uma aba que ele mesmo abriu e nomeou, e o
    Chrome apaga esse nome na primeira navegação para outro domínio, que é
    justamente a ida para o whatsapp.com. Alvo nomeado, window.open e link de
    markdown esbarram todos nisso.
    """
    import html as _h
    import streamlit.components.v1 as componentes

    def link(rotulo, href, ativo=True):
        if not ativo:
            return f'<span class="b off">{rotulo}</span>'
        return f'<a class="b" href="{_h.escape(href, quote=True)}" target="_blank">{rotulo}</a>'

    tem_fone = bool(_so_digitos(a.get("telefone", "")))
    web, app, ios = _links_whatsapp(a.get("telefone", ""), texto)
    if tem_fone:
        zap = (
            f'<a class="b zap" href="{_h.escape(web, quote=True)}" target="_blank" '
            f'data-app="{_h.escape(app, quote=True)}" '
            f'data-ios="{_h.escape(ios, quote=True)}">Abrir no WhatsApp</a>'
        )
    else:
        zap = '<span class="b off">Sem telefone</span>'
    url = a.get("url_recuperacao", "") if url_link is None else url_link
    carrinho = link(rotulo_link, url, bool(url))

    componentes.html(
        "<style>"
        "*{box-sizing:border-box}"
        "body{margin:0;font-family:Poppins,-apple-system,sans-serif;background:transparent}"
        ".linha{display:flex;gap:0.6rem}"
        ".b{flex:1;display:block;text-align:center;padding:0.55rem 0.3rem;"
        "border:1px solid rgba(104,56,10,0.35);border-radius:2px;color:#68380A;"
        "text-decoration:none;font-family:inherit;font-size:0.7rem;"
        "text-transform:uppercase;letter-spacing:0.08em;background:#fff;"
        "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}"
        ".b:hover{background:#FFF3EA}"
        ".b.off{opacity:0.4;border-style:dashed;cursor:default}"
        "</style>"
        f'<div class="linha">{zap}{carrinho}</div>'
        "<script>"
        # No celular não existe WhatsApp Web, então o botão troca de destino.
        # A escolha é feita aqui, no navegador, porque o servidor não sabe de
        # que aparelho veio a página.
        "(function(){var a=document.querySelector('a.zap');if(!a)return;"
        "var ua=navigator.userAgent||'';"
        "var ios=/iPad|iPhone|iPod/.test(ua)"
        "||(navigator.maxTouchPoints>1&&/Mac/.test(navigator.platform));"
        "var android=/Android/i.test(ua);"
        # iPhone fica no https e mantém a aba nova: o WebKit bloqueia abrir
        # aplicativo direto de dentro de um iframe isolado como este.
        "if(ios){a.href=a.dataset.ios}"
        # Android abre o aplicativo direto. Sem target, porque aba nova para
        # esquema de aplicativo deixa uma página em branco para trás.
        "else if(android){a.href=a.dataset.app;a.removeAttribute('target')}})();"
        "</script>",
        height=46,
    )


def _card_recuperar(a: dict):
    """Uma pessoa da fila, com o texto pronto e o link que restaura o carrinho."""
    primeiro_nome = (a.get("cliente") or "").split()[0] if a.get("cliente") else ""
    saudacao = f"Oi, {primeiro_nome}! Tudo bem? 🤎" if primeiro_nome else "Oi! Tudo bem? 🤎"
    itens = a.get("itens") or ""
    produtos, colecao, pronome = _lista_produtos(itens)
    url = _link_recuperacao(a.get("url_recuperacao", ""))

    if a["situacao"] == "Tentou e não passou":
        # Três decisões aqui, todas para não repetir o que já deu errado:
        # sem cupom, porque quem tentou pagar já aceitou o preço; sem
        # especular o motivo da recusa, que soa como se a cliente não tivesse
        # limite; e sem link para o mesmo checkout que acabou de falhar, a
        # saída é conversar, não tentar de novo sozinha.
        texto = (
            f"{saudacao}\n\n"
            f"Vimos que você tentou finalizar a compra de {produtos}, "
            "mas o pagamento não foi concluído.\n\n"
            "Seu carrinho continua guardadinho aqui com a gente. "
            "Se quiser, posso te ajudar a fechar por outra forma de pagamento. "
            "É só me responder por aqui que eu cuido do resto. 🤎\n\n"
            "Com carinho,\nAnnis"
        )
        cor, rotulo = "#8C2F0D", "Tentou e não passou"
    else:
        fecho = (
            f"Se ainda estiver apaixonada pelo {colecao}, é só finalizar por aqui:"
            if colecao else "Se ainda quiser, é só finalizar por aqui:"
        )
        # O cupom vai no texto e nunca na URL, mesmo para quem já tinha
        # FRETEGRATIS aplicado. Os dois não acumulam, então forçar um na URL
        # tiraria o outro sem avisar. Escrito na mensagem, a oferta aparece
        # inteira e a cliente escolhe qual usar.
        texto = (
            f"{saudacao}\n\n"
            f"Vimos que você deixou {produtos} no seu carrinho, "
            f"e {pronome} esperando por você!\n\n"
            f"Preparamos um desconto especial: {DESCONTO_RECUPERACAO} para sua "
            f"compra com o cupom {CUPOM_RECUPERACAO}.\n\n"
            f"{fecho}\n{url}\n\n"
            "Com carinho,\nAnnis"
        )
        cor, rotulo = MARROM, "Não tentou pagar"
    if a["situacao"] == "Já comprou":
        cor, rotulo = "#4A7C46", "Já comprou, não contatar"

    # O tempo desde o abandono fica na etiqueta, junto da situação, porque é
    # com ele que se decide se ainda vale mandar mensagem. Enterrado na linha
    # de baixo, junto de "primeira compra", ele passava batido.
    dias = ""
    try:
        d = (date.today() - date.fromisoformat((a["criado_em"] or "")[:10])).days
        dias = "hoje" if d == 0 else ("ontem" if d == 1 else f"há {d} dias")
    except Exception:
        pass

    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(
                f"<div style='font-family:Poppins;font-size:0.6rem;letter-spacing:0.12em;"
                f"text-transform:uppercase;color:{cor}'>{rotulo}"
                + (f" · {dias}" if dias else "")
                + "</div>"
                f"<div style='font-family:Newsreader,serif;font-size:1.3rem;color:{MARROM};"
                f"padding-top:0.1rem'>{a.get('cliente') or 'Sem cadastro'}</div>"
                f"<div style='font-family:Poppins;font-size:0.8rem;color:#4A2C0F;"
                f"padding-top:0.35rem'>{itens}</div>"
                f"<div style='font-family:Poppins;font-size:0.75rem;color:{MARROM_CLARO};"
                f"padding-top:0.25rem'>"
                + ("Já comprou {}x antes".format(a["pedidos_anteriores"])
                   if a.get("pedidos_anteriores") else "Primeira compra")
                + (f" · {a['tentativas']} tentativa(s) de pagamento" if a.get("tentativas") else "")
                + "</div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div style='text-align:right;font-family:Newsreader,serif;"
                f"font-size:1.5rem;color:{MARROM}'>{fmt_brl(a['valor'])}</div>",
                unsafe_allow_html=True,
            )

        if a["situacao"] != "Já comprou":
            # A mensagem é editável antes de enviar: cada cliente tem contexto
            # que o painel não sabe. O texto sugerido é ponto de partida, não
            # roteiro, e os botões abaixo usam sempre o que estiver na caixa.
            texto_final = st.text_area(
                "Mensagem",
                value=texto,
                height=210,
                key=f"msg_{a['id']}",
                label_visibility="collapsed",
            )

            _botoes_acao(a, texto_final)
            if texto_final != texto:
                st.caption("Texto editado. Os botões acima já usam a sua versão.")


def _dia_br(iso: str) -> str:
    """'2025-12-30' vira '30/12/2025'."""
    try:
        return date.fromisoformat((iso or "")[:10]).strftime("%d/%m/%Y")
    except Exception:
        return iso or ""


def _normalizar_busca(t: str) -> str:
    """Busca que ignora acento e caixa: 'andrea' acha 'Andréa'."""
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower().strip()


_METODOS = {
    "credit_card": "Cartão de crédito",
    "debit_card": "Cartão de débito",
    "pix": "Pix",
    "boleto": "Boleto",
}


def _detalhe_cliente(c: dict):
    """Ficha da cliente: contato, o que já gastou e cada compra que fez."""
    with st.container(border=True):
        e1, e2, e3 = st.columns(3)
        e1.metric("Já gastou", fmt_brl(c["total"]))
        e2.metric("Vezes que comprou", c["compras"])
        e3.metric("Sem comprar há", f"{c['dias_sem_comprar']} dias"
                  if c["dias_sem_comprar"] is not None else "—")

        fone = _so_digitos(c.get("telefone", ""))
        bonito = (f"+{fone[:2]} ({fone[2:4]}) {fone[4:-4]}-{fone[-4:]}"
                  if len(fone) >= 12 else fone)
        cidade = f"{c['cidade']}/{c['uf']}" if c.get("cidade") else ""
        st.markdown(
            f"<div style='font-family:Poppins;font-size:0.8rem;color:#4A2C0F;"
            f"padding-top:0.3rem'>{c.get('email') or 'sem e-mail'}"
            + (f" · {bonito}" if fone else "")
            + (f" · {cidade}" if cidade else "")
            + f" · primeira compra em {_dia_br(c['primeira'])}</div>",
            unsafe_allow_html=True,
        )

        linhas = []
        faltando = 0
        for p in sorted(c["pedidos"], key=lambda p: p["dia"], reverse=True):
            if p.get("itens"):
                pecas = p["itens"]
            elif p.get("fora_do_alcance"):
                # Não é pedido vazio: é pedido que a Shopify se recusa a
                # devolver. Dizer "sem itens" seria mentira por omissão.
                dias = (date.today() - date.fromisoformat(p["dia"])).days
                pecas = f"Compra de {dias} dias atrás, a loja não devolve as peças"
                faltando += 1
            else:
                pecas = "Sem peças registradas"
            linhas.append({
                "Data": _dia_br(p["dia"]),
                "Pedido": p.get("numero") or "—",
                "Peças": pecas,
                "Valor": fmt_brl(p["valor"]),
                "Pagamento": _METODOS.get(p["metodo"], p["metodo"] or "—")
                + (f" em {p['parcelas']}x" if p["parcelas"] > 1 else ""),
            })
        tabela(pd.DataFrame(linhas), num=("Valor",))

        if faltando:
            st.caption(
                f"{faltando} compra(s) sem detalhe. A Shopify só devolve pedidos "
                f"a partir de {_dia_br(c.get('limite_loja', ''))} enquanto o app "
                "não tiver o escopo `read_all_orders`."
            )


def _card_cliente(c: dict):
    """Uma cliente que sumiu, com o convite pronto para voltar.

    Sem cupom de propósito: quem já comprou pagou o preço cheio uma vez, e
    abrir desconto para todo mundo que some ensina a base a esperar desconto.
    """
    primeiro_nome = (c.get("nome") or "").split()[0] if c.get("nome") else ""
    saudacao = f"Oi, {primeiro_nome}! Tudo bem? 🤎" if primeiro_nome else "Oi! Tudo bem? 🤎"
    texto = (
        f"{saudacao}\n\n"
        "Passando para dizer que a gente lembra de você por aqui. "
        "Chegaram peças novas na loja e algumas têm a sua cara.\n\n"
        "Se quiser dar uma olhada, é por aqui:\nhttps://annis.store\n\n"
        "Com carinho,\nAnnis"
    )

    dias = c.get("dias_sem_comprar")
    etiqueta = f"Sem comprar há {dias} dias" if dias else "Comprou hoje"

    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(
                f"<div style='font-family:Poppins;font-size:0.6rem;letter-spacing:0.12em;"
                f"text-transform:uppercase;color:{MARROM_CLARO}'>{etiqueta}</div>"
                f"<div style='font-family:Newsreader,serif;font-size:1.3rem;color:{MARROM};"
                f"padding-top:0.1rem'>{c.get('nome') or 'Sem cadastro'}</div>"
                f"<div style='font-family:Poppins;font-size:0.75rem;color:{MARROM_CLARO};"
                f"padding-top:0.35rem'>"
                + (f"{c['compras']} compras" if c["compras"] > 1 else "1 compra")
                + f" · última em {_dia_br(c['ultima'])}"
                + (f" · {c['cidade']}/{c['uf']}" if c.get("cidade") else "")
                + (f" · primeira em {_dia_br(c['primeira'])}" if c["compras"] > 1 else "")
                + "</div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div style='text-align:right;font-family:Newsreader,serif;"
                f"font-size:1.5rem;color:{MARROM}'>{fmt_brl(c['total'])}</div>"
                f"<div style='text-align:right;font-family:Poppins;font-size:0.65rem;"
                f"color:{MARROM_CLARO}'>já gastou</div>",
                unsafe_allow_html=True,
            )

        texto_final = st.text_area(
            "Mensagem", value=texto, height=170,
            key=f"cli_{c['email'] or c['nome']}", label_visibility="collapsed",
        )
        _botoes_acao(c, texto_final, "Abrir a loja", "https://annis.store")
        if texto_final != texto:
            st.caption("Texto editado. Os botões acima já usam a sua versão.")


def para_brt(serie):
    """A API devolve tudo em UTC; o Dash exibe em horário de Brasília.
    Sem converter, operações da madrugada caem no dia anterior."""
    return serie.dt.tz_convert("America/Sao_Paulo")


def to_df_ops(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "created_at" in df.columns:
        df["created_at"] = para_brt(pd.to_datetime(df["created_at"], errors="coerce", utc=True))
    if "amount" in df.columns:
        df["amount_brl"] = df["amount"].apply(lambda x: fmt_brl(x or 0))
    if "fee" in df.columns:
        df["fee_brl"] = df["fee"].apply(lambda x: fmt_brl(x or 0))
    if "amount" in df.columns and "fee" in df.columns:
        df["net"] = df["amount"].fillna(0) - df["fee"].fillna(0)
        df["net_brl"] = df["net"].apply(lambda x: fmt_brl(int(x)))
    return df


# Nos recebíveis a taxa de antecipação é cobrada à parte do `fee`; ignorá-la
# infla o líquido. Nas operações de saldo isso não existe, elas já vêm
# líquidas de antecipação.
def to_df_pay(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for col in ("created_at", "payment_date"):
        if col in df.columns:
            df[col] = para_brt(pd.to_datetime(df[col], errors="coerce", utc=True))
    if "amount" in df.columns:
        df["amount_brl"] = df["amount"].apply(lambda x: fmt_brl(x or 0))
    if "fee" in df.columns:
        df["fee_brl"] = df["fee"].apply(lambda x: fmt_brl(x or 0))
    if "anticipation_fee" in df.columns:
        df["antec_brl"] = df["anticipation_fee"].apply(lambda x: fmt_brl(x or 0))
    if "amount" in df.columns and "fee" in df.columns:
        antec = df["anticipation_fee"].fillna(0) if "anticipation_fee" in df.columns else 0
        df["net"] = df["amount"].fillna(0) - df["fee"].fillna(0) - antec
        df["net_brl"] = df["net"].apply(lambda x: fmt_brl(int(x)))
    return df


# "available" primeiro: é o que corresponde ao extrato oficial do Dash.
# "transferred"/"waiting_funds" são lançamentos contábeis (contrapartida de
# transferências e recebíveis futuros), úteis como visão avançada.
STATUS_OP_LABEL = {
    "available": "Disponível (= extrato do Dash)",
    "waiting_funds": "Aguardando fundos",
    "transferred": "Transferido",
    "": "Todos (visão contábil)",
}

STATUS_PAY_LABEL = {
    "": "Todos",
    "paid": "Pago",
    "prepaid": "Antecipado",
    "waiting_funds": "Aguardando",
}

TYPE_PAY_LABEL = {
    "": "Todos",
    "credit": "Crédito",
    "refund": "Reembolso",
    "chargeback": "Chargeback",
    "chargeback_refund": "Estorno chargeback",
}

# Traduções para exibição nas tabelas (o Dash mostra em português).
TIPO_OP_PT = {
    "payable": "Venda",
    "external_settlement": "Liquidação de recebíveis",
    "transfer": "Transferência",
    "fee_collection": "Tarifa",
    "refund": "Estorno",
    "refund_reversal": "Reversão de estorno",
}

STATUS_OP_PT = {
    "available": "Disponível",
    "waiting_funds": "Aguardando",
    "transferred": "Transferido",
}

STATUS_PAY_PT = {
    "paid": "Pago",
    "prepaid": "Antecipado",
    "waiting_funds": "Aguardando",
}

METODO_PT = {
    "credit_card": "Cartão de crédito",
    "debit_card": "Cartão de débito",
    "pix": "Pix",
    "boleto": "Boleto",
}

# Condições comerciais lidas de Configurações › Taxas e prazos no Dash em
# 07/08/2026. Não existe endpoint de API para isso, se a taxa for
# renegociada, é preciso atualizar aqui à mão.
TAXAS_CONTRATADAS = {
    "lidas_em": "07/08/2026",
    "meios": {
        # meio: (rótulo da taxa contratada, valor de referência em % ou None)
        "credit_card": ("a partir de 4,70%", 4.70),
        "pix": ("0,99%", 0.99),
        "boleto": ("R$ 2,99 por transação", None),
    },
    "antecipacao": ("1,44% ao mês", 1.44),
    "prazo_credito": "7 dias corridos",
    "avulsas": [
        ("Processamento", "R$ 0,50 por transação aprovada"),
        ("Transferência para outra conta", "R$ 3,67"),
        ("Antifraude", "R$ 0,40 por transação de crédito"),
    ],
}

STATUS_CHG_PT = {
    "paid": "Paga",
    "pending": "Pendente",
    "failed": "Falha",
    "canceled": "Cancelada",
    "overpaid": "Paga a maior",
    "underpaid": "Paga a menor",
}


def traduzir(df, mapas: dict):
    """Aplica traduções PT-BR nas colunas indicadas, preservando o original."""
    for coluna, mapa in mapas.items():
        if coluna in df.columns:
            df[coluna] = df[coluna].apply(lambda v: mapa.get(v, v))
    return df

# ── Sidebar ──────────────────────────────────────────────────────────────────

def rotulo_lateral(texto: str):
    """Título de seção da barra lateral.

    Serif grande igual ao dos títulos de página competia com o conteúdo numa
    coluna de 256px; aqui vale a caixa alta espaçada do menu do site.
    """
    st.markdown(
        f"<div style='font-family:Poppins;font-size:0.62rem;letter-spacing:0.18em;"
        f"text-transform:uppercase;color:{MARROM_CLARO};"
        f"padding:0.2rem 0 0.35rem;border-bottom:1px solid {LINHA};"
        f"margin-bottom:0.7rem'>{texto}</div>",
        unsafe_allow_html=True,
    )


with st.sidebar:
    hoje = date.today()
    rotulo_lateral("Período")

    # Lista suspensa em vez de radio: com 5 opções o radio horizontal quebrava
    # em duas fileiras desalinhadas na largura da barra lateral.
    PRESETS = {
        "Hoje": 0,
        "Últimos 7 dias": 7,
        "Últimos 30 dias": 30,
        "Últimos 90 dias": 90,
        "Personalizado": None,
    }
    preset = st.selectbox(
        "Período", list(PRESETS.keys()), index=2, label_visibility="collapsed"
    )
    if PRESETS[preset] is None:
        c_de, c_ate = st.columns(2)
        data_ini = c_de.date_input("De", value=hoje - timedelta(days=30), key="g_ini")
        data_fim = c_ate.date_input("Até", value=hoje, key="g_fim")
    else:
        data_ini = hoje - timedelta(days=PRESETS[preset])
        data_fim = hoje
        st.caption(f"{data_ini.strftime('%d/%m/%Y')} até {data_fim.strftime('%d/%m/%Y')}")

    st.write("")
    rotulo_lateral("Recebedor")
    recipient_id = st.text_input(
        "Recebedor",
        value="",
        placeholder="Conta principal",
        label_visibility="collapsed",
        help="Informe um recipient_id (re_...) para filtrar por recebedor.",
    )

    st.write("")
    rotulo_lateral("Dados")

    # Um botão só: ter duas noções de data na mesma barra (uma para filtrar,
    # outra para sincronizar) confunde. Baixa sempre a mesma janela ampla; o
    # recorte de leitura é o filtro de período acima.
    if st.button("Atualizar dados", use_container_width=True, type="primary"):
        aviso = st.empty()
        try:
            n = sincronizar(hoje, avisar=lambda m: aviso.caption(m))
            aviso.empty()
            st.success(
                f"{n['vendas']} vendas · {n['operacoes']} operações · "
                f"{n['recebiveis']} recebíveis"
            )
            st.rerun()
        except Exception as e:
            aviso.empty()
            st.error(f"Falha ao atualizar: {e}")


    st.caption(f"Baixa os últimos {JANELA_SYNC} dias, mais os recebíveis ainda pendentes.")

    # Contadores locais
    counts = db.get_db_counts()
    st.caption(
        f"Local: {counts['charges']} vendas · "
        f"{counts['balance_operations']} operações · {counts['payables']} recebíveis"
    )

# ── Abas principais ──────────────────────────────────────────────────────────

# Duas naturezas de trabalho na mesma tela cansavam a leitura: Recuperar e
# Clientes são fila de contato, as outras são conferência de dinheiro. Elas
# não se misturam no dia da Ana, então também não se misturam no menu.
TRABALHO = ["Recuperar", "Clientes"]
FINANCEIRO = ["Vendas", "A receber", "Extrato", "Conciliação", "Histórico"]

secao = st.segmented_control(
    "Seção", ["Financeiro", "Trabalho"], default="Financeiro",
    key="secao", label_visibility="collapsed",
) or "Financeiro"

nomes = FINANCEIRO if secao == "Financeiro" else TRABALHO
abas = dict(zip(nomes, st.tabs(nomes)))

date_from_str = str(data_ini)
date_to_str = str(data_fim)
recip = recipient_id or None

# ════════════════════════════════════════════════════════════════════════════
# ABA 1: VENDAS: quanto vendeu
# ════════════════════════════════════════════════════════════════════════════
if "Vendas" in abas:
  with abas["Vendas"]:
    st.header("Quanto vendeu")

    chg_rows = db.query_charges(date_from=date_from_str, date_to=date_to_str)
    df_chg = pd.DataFrame(chg_rows)

    if df_chg.empty:
        st.info("ℹ️ Sem vendas no período. Use **Atualizar dados** na barra lateral.")
    else:
        df_chg["created_at"] = para_brt(pd.to_datetime(df_chg["created_at"], errors="coerce", utc=True))
        pagas = df_chg[df_chg["status"] == "paid"]
        vendido = int(pagas["amount"].sum())
        qtd = len(pagas)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Vendido", fmt_brl(vendido))
        c2.metric("Vendas", qtd)
        c3.metric("Ticket médio", fmt_brl(int(vendido / qtd)) if qtd else "—")
        aprov = qtd / len(df_chg) * 100
        c4.metric("Aprovação", fmt_pct(aprov, 0))
        perdidas = df_chg[df_chg["status"] != "paid"]
        c4.caption(f"{len(perdidas)} de {len(df_chg)} não converteram")

        if not perdidas.empty:
            valor_perdido = int(perdidas["amount"].sum())
            with st.expander(f"Ver as {len(perdidas)} vendas que não entraram ({fmt_brl(valor_perdido)})"):
                pd_ = perdidas.copy()
                pd_["valor"] = pd_["amount"].apply(fmt_brl)
                pd_["quando"] = pd_["created_at"].dt.strftime("%d/%m/%Y %H:%M")
                pd_ = traduzir(pd_, {"status": STATUS_CHG_PT, "payment_method": METODO_PT})
                tabela(
                    pd_[["quando", "customer_name", "valor", "status", "payment_method"]]
                    .rename(columns={
                        "quando": "Quando", "customer_name": "Cliente", "valor": "Valor",
                        "status": "Situação", "payment_method": "Meio",
                    }),
                    num=("Valor",), altura_max=320,
                )
                st.caption(
                    "Pendente ainda pode virar venda; falha e cancelada, não. "
                    "Cliente repetido com valor igual costuma ser nova tentativa da mesma compra."
                )

        st.divider()

        # A ponte: o que foi vendido não é o que entra na conta.
        st.subheader("Do que vendeu, quanto sobra")
        custo = db.custo_das_vendas(date_from_str, date_to_str)
        if custo["bruto"]:
            b1, b2, b3 = st.columns(3)
            b1.metric("− Taxa", fmt_brl(-custo["mdr"]))
            b2.metric("− Antecipação", fmt_brl(-custo["antecipacao"]))
            b3.metric("= Fica com você", fmt_brl(custo["liquido"]))
            st.caption(
                f"Custo total de **{fmt_pct(custo['custo_pct'])}** sobre o que vendeu, "
                f"distribuído em {custo['parcelas']} parcelas a receber."
            )
        else:
            st.caption("Sem vendas com recebíveis no período.")

        # Taxa contratada × taxa efetivamente paga
        meios = db.custo_por_meio(date_from_str, date_to_str)
        if meios:
            st.markdown("###### Taxa contratada × taxa paga")
            if True:
                linhas = []
                for m in meios:
                    contratada, _ = TAXAS_CONTRATADAS["meios"].get(
                        m["meio"], ("não informada", None)
                    )
                    linhas.append({
                        "Meio": METODO_PT.get(m["meio"], m["meio"]),
                        "Contratada": contratada,
                        "Taxa paga": fmt_pct(m["mdr_pct"]),
                        "Antecipação paga": fmt_pct(m["antec_pct"]),
                        "Custo total": fmt_pct(m["total_pct"]),
                        "Bruto": fmt_brl(m["bruto"]),
                    })
                tabela(pd.DataFrame(linhas),
                       num=("Contratada", "Taxa paga", "Antecipação paga", "Custo total", "Bruto"))
                st.caption(
                    f"Condições lidas do Dash em {TAXAS_CONTRATADAS['lidas_em']}: "
                    f"antecipação {TAXAS_CONTRATADAS['antecipacao'][0]}, "
                    f"crédito recebido em {TAXAS_CONTRATADAS['prazo_credito']}. "
                    "A taxa de crédito varia por bandeira e parcelamento, então a paga "
                    "fica naturalmente acima do piso contratado. A comparação serve "
                    "para achar desvio grande, não para bater exato. "
                    "Tarifas por transação (processamento, antifraude, transferência) "
                    "não entram nestes percentuais."
                )
                st.caption(
                    " · ".join(f"**{n}**: {md(v)}" for n, v in TAXAS_CONTRATADAS["avulsas"])
                )

        st.divider()

        g1, g2 = st.columns([2, 1], gap="large")
        with g1.container(border=True):
            st.subheader("Vendas por dia")
            ts = pagas.copy()
            ts["dia"] = ts["created_at"].dt.date
            agg = ts.groupby("dia")["amount"].sum().reset_index().sort_values("dia")
            agg["Dia"] = pd.to_datetime(agg["dia"]).dt.strftime("%d/%m")
            agg["Valor"] = agg["amount"] / 100
            agg["Vendido"] = agg["amount"].apply(fmt_brl)
            agg["Rotulo"] = agg["amount"].apply(fmt_curto)
            st.altair_chart(
                barras(agg, "Dia", "Valor", rotulo="Rotulo", tooltip=["Dia", "Vendido"]),
                use_container_width=True,
            )
        with g2.container(border=True):
            st.subheader("Por meio")
            mix = pagas.groupby("payment_method")["amount"].sum().reset_index()
            mix["Meio"] = mix["payment_method"].apply(lambda v: METODO_PT.get(v, v))
            mix["Valor"] = mix["amount"] / 100
            mix["Vendido"] = mix["amount"].apply(fmt_brl)
            st.altair_chart(
                barras(mix.sort_values("Valor", ascending=False), "Meio", "Valor",
                       rotulo="Vendido", tooltip=["Meio", "Vendido"]),
                use_container_width=True,
            )

        with st.expander(f"Ver as {len(df_chg)} cobranças do período"):
            det = df_chg.copy()
            det["valor"] = det["amount"].apply(fmt_brl)
            det["quando"] = det["created_at"].dt.strftime("%d/%m/%Y %H:%M")
            det = traduzir(det, {"status": STATUS_CHG_PT, "payment_method": METODO_PT})
            tabela(
                det[["quando", "customer_name", "valor", "status", "payment_method", "installments"]]
                .rename(columns={
                    "quando": "Quando", "customer_name": "Cliente", "valor": "Valor",
                    "status": "Situação", "payment_method": "Meio", "installments": "Parcelas",
                }),
                num=("Valor", "Parcelas"), altura_max=420,
            )

# ════════════════════════════════════════════════════════════════════════════
# ABA 2: RECUPERAR: fila de trabalho dos checkouts abandonados
# ════════════════════════════════════════════════════════════════════════════
if "Recuperar" in abas:
  with abas["Recuperar"]:
    st.header("Quem quase comprou")
    st.caption(
        "Carrinhos abandonados na loja, cruzados com as cobranças da Pagar.me. "
        "Não usa o filtro de período da barra lateral: é uma fila de trabalho, "
        "não um relatório."
    )

    if not shopify_client.configurado():
        st.info(
            "Shopify não configurada. Defina `SHOPIFY_LOJA`, `SHOPIFY_CLIENT_ID` "
            "e `SHOPIFY_CLIENT_SECRET` para esta aba funcionar."
        )
    else:
        abandonos = db.abandonados_classificados(dias=180)
        if not abandonos:
            st.info("Nenhum carrinho abandonado. Use **Atualizar dados** na barra lateral.")
        else:
            # Os filtros vêm antes dos números porque os números obedecem a
            # eles. Cartão que muda por causa de um controle que está abaixo
            # dele é exatamente o que fazia a conta de cima não bater com a
            # lista de baixo.
            f1, f2 = st.columns([2, 1])
            with f1:
                st.caption("Mostrar")
                m1, m2, m3 = st.columns(3)
                marcadas = []
                if m1.checkbox("Não tentou pagar", value=True, key="rec_lead"):
                    marcadas.append("Não tentou pagar")
                if m2.checkbox("Tentou e não passou", value=True, key="rec_falhou"):
                    marcadas.append("Tentou e não passou")
                if m3.checkbox("Já comprou", value=False, key="rec_comprou"):
                    marcadas.append("Já comprou")
            with f2:
                dias_max = st.selectbox(
                    "Abandonados nos últimos", [7, 15, 30, 60, 90, 180], index=2, key="rec_dias"
                )

            corte = (date.today() - timedelta(days=dias_max)).isoformat()
            janela = [a for a in abandonos if (a["criado_em"] or "")[:10] >= corte]

            leads = [a for a in janela if a["situacao"] == "Não tentou pagar"]
            falhou = [a for a in janela if a["situacao"] == "Tentou e não passou"]
            comprou = [a for a in janela if a["situacao"] == "Já comprou"]

            k1, k2, k3 = st.columns(3)
            k1.metric("Não tentaram pagar", len(leads))
            k1.caption(md(fmt_brl(sum(a["valor"] for a in leads))) + " em carrinho")
            k2.metric("Tentaram e não passou", len(falhou))
            k2.caption("O pagamento não foi concluído")
            k3.metric("Já compraram", len(comprou))
            k3.caption("Não contatar. A Shopify ainda lista")

            if comprou and "Já comprou" not in marcadas:
                st.success(
                    f"{len(comprou)} pessoas deste período compraram depois de abandonar. "
                    "Elas estão fora da lista para você não cobrar quem já pagou."
                )

            st.divider()

            escolhidos = [a for a in janela if a["situacao"] in marcadas]
            if not marcadas:
                st.info("Marque ao menos uma situação em **Mostrar**.")
            elif not escolhidos:
                st.info("Ninguém nesse recorte.")
            else:
                st.caption(f"{len(escolhidos)} pessoas · mais recentes primeiro")
                for a in escolhidos:
                    _card_recuperar(a)


# ════════════════════════════════════════════════════════════════════════════
# ABA 3: CLIENTES: quem já comprou, e quem não volta
# ════════════════════════════════════════════════════════════════════════════
if "Clientes" in abas:
  with abas["Clientes"]:
    st.header("Clientes")
    st.caption(
        "Todo mundo que já comprou, desde a primeira venda da loja. Não usa o "
        "filtro de período da barra lateral: é a base de clientes, não um "
        "relatório do mês."
    )

    cli = db.clientes()
    if not cli:
        st.info("Nenhuma compra paga ainda. Use **Atualizar dados** na barra lateral.")
    else:
        compras = sum(c["compras"] for c in cli)
        total = sum(c["total"] for c in cli)
        voltaram = [c for c in cli if c["compras"] > 1]

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Clientes", len(cli))
        k1.caption(f"{compras} compras no total")
        k2.metric("Gasto por cliente", fmt_brl(total // len(cli)))
        k2.caption("Média do que cada uma já deixou")
        k3.metric("Ticket médio", fmt_brl(total // compras))
        k3.caption("Por compra")
        k4.metric("Voltaram a comprar", len(voltaram))
        k4.caption(fmt_pct(len(voltaram) / len(cli) * 100, 0) + " da base")

        st.divider()

        # A tabela vem antes da fila porque responde "quem são minhas
        # clientes", que é a pergunta da aba. A fila embaixo é o trabalho.
        st.subheader("A base inteira")

        ORDENS = {
            "Quanto gastou": lambda c: -c["total"],
            "Vezes que comprou": lambda c: (-c["compras"], -c["total"]),
            "Compra mais recente": lambda c: c["dias_sem_comprar"] or 0,
            "Há mais tempo sem comprar": lambda c: -(c["dias_sem_comprar"] or 0),
        }
        t1, t2 = st.columns([1, 1])
        with t1:
            busca = st.text_input("Buscar pelo nome ou e-mail", key="cli_busca",
                                  placeholder="comece a digitar")
        with t2:
            ordem = st.selectbox("Ordenar por", list(ORDENS), key="cli_ordem")

        alvo = _normalizar_busca(busca)
        vistas = [c for c in cli
                  if not alvo
                  or alvo in _normalizar_busca(c["nome"])
                  or alvo in _normalizar_busca(c["email"])
                  or alvo in _normalizar_busca(c["cidade"])]
        vistas = sorted(vistas, key=ORDENS[ordem])

        if not vistas:
            st.info("Nenhuma cliente com esse nome.")
        else:
            st.caption(f"{len(vistas)} de {len(cli)} clientes")
            tabela(
                pd.DataFrame([{
                    "Cliente": c["nome"] or c["email"],
                    "Cidade": f"{c['cidade']}/{c['uf']}" if c["cidade"] else "—",
                    "Vezes que comprou": c["compras"],
                    "Total gasto": fmt_brl(c["total"]),
                    "Última compra": _dia_br(c["ultima"]),
                    "Dias sem comprar": c["dias_sem_comprar"] if c["dias_sem_comprar"] is not None else "",
                } for c in vistas]),
                num=("Vezes que comprou", "Total gasto", "Dias sem comprar"),
                altura_max=420,
            )

            # Clicar na linha da tabela exigiria recarregar a página, e como o
            # login vive na sessão isso jogaria a Ana de volta para a senha.
            # Por isso o detalhe abre por seleção, sem sair da página.
            rotulos = {f"{c['nome'] or c['email']} · {fmt_brl(c['total'])}": c for c in vistas}
            escolha = st.selectbox("Ver os dados de", ["Ninguém selecionada"] + list(rotulos),
                                   key="cli_detalhe")
            if escolha in rotulos:
                _detalhe_cliente(rotulos[escolha])

        st.divider()

        st.subheader("Quem não volta")
        st.caption(
            "Já comprou, gostou o bastante para pagar, e sumiu. Custa menos "
            "trazer de volta do que achar cliente nova."
        )
        # Campos digitáveis em vez de lista fechada: o corte útil muda com a
        # conversa (às vezes é 45 dias, às vezes R$ 1.500) e lista pronta
        # obriga a escolher o número errado mais próximo.
        q1, q2, q3 = st.columns(3)
        with q1:
            corte_dias = st.number_input(
                "Sem comprar há mais de (dias)", min_value=0, max_value=3650,
                value=90, step=15, key="cli_corte",
            )
        with q2:
            piso_reais = st.number_input(
                "Já gastou pelo menos (R$)", min_value=0, max_value=1_000_000,
                value=0, step=100, key="cli_faixa",
            )
        with q3:
            min_compras = st.number_input(
                "Comprou pelo menos (vezes)", min_value=1, max_value=50,
                value=1, step=1, key="cli_quantas",
            )

        q4, q5, q6 = st.columns(3)
        with q4:
            cidade_f = st.text_input(
                "Cidade contém", key="cli_cidade", placeholder="são paulo, rio…"
            )
        with q5:
            peca_f = st.text_input(
                "Comprou peça que contém", key="cli_peca", placeholder="giverny, saia…"
            )
        with q6:
            ordem_fila = st.selectbox(
                "Chamar primeiro quem", ["Gastou mais", "Sumiu há mais tempo",
                                         "Comprou mais vezes"],
                key="cli_ordem_fila",
            )

        alvo_cidade = _normalizar_busca(cidade_f)
        alvo_peca = _normalizar_busca(peca_f)
        sumidas = [
            c for c in cli
            if (c["dias_sem_comprar"] or 0) > corte_dias
            and c["total"] >= piso_reais * 100
            and c["compras"] >= min_compras
            and (not alvo_cidade or alvo_cidade in _normalizar_busca(c["cidade"]))
            and (not alvo_peca or any(
                alvo_peca in _normalizar_busca(p.get("itens", "")) for p in c["pedidos"]))
        ]
        sumidas = sorted(sumidas, key={
            "Gastou mais": ORDENS["Quanto gastou"],
            "Sumiu há mais tempo": ORDENS["Há mais tempo sem comprar"],
            "Comprou mais vezes": ORDENS["Vezes que comprou"],
        }[ordem_fila])

        if alvo_cidade or alvo_peca:
            st.caption(
                "Cidade e peça só existem para quem comprou de "
                + _dia_br(db.alcance_pedidos()) + " para cá, que é até onde a "
                "Shopify devolve pedido. Quem comprou antes fica de fora deste "
                "filtro mesmo que se encaixe."
            )

        if not sumidas:
            st.info("Ninguém nesse recorte.")
        else:
            st.caption(
                f"{len(sumidas)} pessoas · "
                + md(fmt_brl(sum(c["total"] for c in sumidas)))
                + " já gastos aqui"
            )
            for c in sumidas:
                _card_cliente(c)


# ════════════════════════════════════════════════════════════════════════════
# ABA 4: A RECEBER: quanto e quando
# ════════════════════════════════════════════════════════════════════════════
if "A receber" in abas:
  with abas["A receber"]:
    st.header("Quanto tem a receber, e quando")

    try:
        bal = pagarme_client.get_balance(recip)
        avail = bal.get("available_amount", 0)
        waiting = bal.get("waiting_funds_amount", 0)
    except Exception as e:
        st.warning(f"Não foi possível buscar saldo da API: {e}")
        avail = waiting = 0

    ar = db.compute_a_receber(recip)
    c1, c2 = st.columns(2)
    c1.metric("Já disponível para sacar", fmt_brl(avail))
    c2.metric("Ainda a receber", fmt_brl(waiting))

    st.divider()
    st.subheader("Agenda de recebimentos")

    agenda = db.agenda_recebimentos(recip)
    if not agenda:
        st.info("Nada a receber no momento, tudo já liquidado.")
    else:
        df_ag = pd.DataFrame(agenda)
        df_ag["Data"] = pd.to_datetime(df_ag["dia"]).dt.strftime("%d/%m/%Y")
        df_ag["Valor"] = df_ag["liquido"].apply(lambda x: fmt_brl(int(x)))
        df_ag["Acumulado"] = df_ag["liquido"].cumsum().apply(lambda x: fmt_brl(int(x)))
        tabela(
            df_ag[["Data", "Valor", "parcelas", "Acumulado"]].rename(columns={"parcelas": "Parcelas"}),
            num=("Valor", "Parcelas", "Acumulado"),
        )
        graf = df_ag.copy()
        graf["Entra"] = graf["liquido"] / 100
        with st.container(border=True):
            st.altair_chart(
                barras(graf, "Data", "Entra", rotulo="Valor",
                       tooltip=["Data", "Valor", "parcelas"], altura=200),
                use_container_width=True,
            )
        st.caption(
            "Valores líquidos, já descontadas taxa e antecipação. "
            f"Tarifas pendentes de {md(fmt_brl(ar['tarifas']))} são cobradas na liquidação."
        )

    # Drill-down: cada parcela com a venda que a originou
    pend_rows = db.a_receber_detalhado(recip)
    if pend_rows:
        st.divider()
        st.subheader("De onde vem cada parcela")

        df_pend = pd.DataFrame(pend_rows)
        df_pend["Cai em"] = para_brt(
            pd.to_datetime(df_pend["payment_date"], errors="coerce", utc=True)
        ).dt.strftime("%d/%m/%Y")
        df_pend["Vendido em"] = para_brt(
            pd.to_datetime(df_pend["created_at"], errors="coerce", utc=True)
        ).dt.strftime("%d/%m/%Y")
        df_pend["Cliente"] = df_pend["customer_name"].fillna("").replace("", "—")
        df_pend["Venda"] = df_pend["venda_total"].apply(
            lambda x: fmt_brl(int(x)) if pd.notna(x) else "—"
        )
        df_pend["Parcela"] = df_pend.apply(
            lambda r: f"{int(r['installment'])}/{int(r['venda_parcelas'])}"
            if pd.notna(r["venda_parcelas"]) else str(int(r["installment"] or 1)),
            axis=1,
        )
        df_pend["Meio"] = df_pend["payment_method"].apply(lambda v: METODO_PT.get(v, v))
        for col, orig in [("Bruto", "amount"), ("Taxa", "fee"),
                          ("Antecipação", "anticipation_fee"), ("Líquido", "liquido")]:
            df_pend[col] = df_pend[orig].apply(lambda x: fmt_brl(int(x or 0)))

        datas = ["Todas"] + sorted(df_pend["Cai em"].unique().tolist())
        escolha = st.selectbox("Filtrar por data de recebimento", datas, key="ar_data")
        visao = df_pend if escolha == "Todas" else df_pend[df_pend["Cai em"] == escolha]

        tabela(
            visao[["Cai em", "Cliente", "Venda", "Parcela", "Vendido em",
                   "Meio", "Bruto", "Taxa", "Antecipação", "Líquido"]],
            num=("Venda", "Bruto", "Taxa", "Antecipação", "Líquido"), altura_max=460,
        )
        if (df_pend["customer_name"].isna() | (df_pend["customer_name"] == "")).any():
            st.caption(
                "Parcelas com cliente “—” vêm de vendas anteriores à janela "
                "sincronizada. Clique em **Atualizar dados** para trazê-las."
            )
        csv_p = visao.to_csv(index=False).encode("utf-8")
        st.download_button("Exportar CSV", data=csv_p,
                           file_name=f"a_receber_{date_to_str}.csv", mime="text/csv")

# ════════════════════════════════════════════════════════════════════════════
# ABA 6: EXTRATO: no formato de extrato bancário, com saldo corrido
# ════════════════════════════════════════════════════════════════════════════
if "Extrato" in abas:
  with abas["Extrato"]:
    st.header("Extrato")

    try:
        saldo_api = pagarme_client.get_balance(recip).get("available_amount", 0)
    except Exception as e:
        st.warning(f"Não foi possível ler o saldo da API: {e}")
        saldo_api = None

    todos = db.extrato_bancario(recip)
    if not todos:
        st.info("Sem lançamentos. Use **Atualizar dados** na barra lateral.")
    else:
        # Saldo corrido somando do primeiro lançamento para frente, partindo de
        # zero na abertura da conta.
        #
        # Ancorar no saldo atual da API e voltar seria o inverso natural, mas
        # não fecha: a soma de todos os lançamentos dá um valor diferente do
        # available_amount, e ancorar no fim joga essa diferença para o começo,
        # produzindo saldos negativos no histórico inteiro. O teste que mostra
        # isso: o primeiro par de lançamentos da conta é uma entrada de
        # R$ 214,46 seguida de uma transferência de exatamente R$ 214,46 —
        # começando do zero, o saldo sobe e volta a zero, como tem que ser.
        # A soma dos lançamentos não chega ao saldo que a Pagar.me informa, a
        # API é inconsistente aqui: os lançamentos disponíveis somam mais que o
        # available_amount. Essa diferença vira o saldo de ABERTURA, não uma
        # linha de ajuste no fim: extrato se lê como "saldo anterior →
        # movimentos → saldo atual", e um ajuste na última linha pareceria uma
        # transação recém-ocorrida. Como abertura, ele cai dentro de um conceito
        # que todo extrato já tem, e o saldo final fica igual ao da conta.
        soma_lancamentos = sum(l["valor"] or 0 for l in todos)
        abertura_global = (saldo_api - soma_lancamentos) if saldo_api is not None else 0

        saldo = abertura_global
        for linha in todos:
            linha["saldo_antes"] = saldo
            saldo += linha["valor"] or 0
            linha["saldo_depois"] = saldo

        def descrever(r):
            """Frase legível para cada lançamento, no lugar de um id solto."""
            cliente = r.get("customer_name") or ""
            parcela = ""
            if r.get("installment") and r.get("venda_parcelas"):
                parcela = f" · parcela {int(r['installment'])}/{int(r['venda_parcelas'])}"
            venda = ""
            if r.get("venda_total"):
                venda = f" · venda de {fmt_brl(int(r['venda_total']))}"
            tipo = r.get("type")

            if tipo in ("payable", "external_settlement"):
                antecipada = tipo == "external_settlement"
                if r.get("tipo_recebivel") == "refund":
                    base = "Estorno"
                else:
                    base = "Antecipação recebida" if antecipada else "Liquidação"
                if cliente:
                    return f"{base} de {cliente}{parcela}{venda}"
                bandeira = (r.get("bandeira") or "").title()
                return f"{base}{' (' + bandeira + ')' if bandeira else ''}{parcela}"
            if tipo == "transfer":
                return "Transferência para sua conta bancária"
            if tipo == "fee_collection":
                # A própria API descreve a tarifa; é melhor que um rótulo genérico.
                return r.get("descricao_tarifa") or "Tarifa"
            if tipo == "refund":
                return f"Estorno{' de ' + cliente if cliente else ''}"
            return TIPO_OP_PT.get(tipo, tipo or "Lançamento")

        for r in todos:
            r["descricao"] = descrever(r)

        df_ext = pd.DataFrame(todos)
        df_ext["quando"] = para_brt(
            pd.to_datetime(df_ext["created_at"], errors="coerce", utc=True)
        )
        # Recorte do período depois de calcular o saldo, para o saldo corrido
        # continuar verdadeiro mesmo olhando uma janela curta.
        ini = pd.Timestamp(date_from_str, tz="America/Sao_Paulo")
        fim = pd.Timestamp(date_to_str, tz="America/Sao_Paulo") + pd.Timedelta(days=1)
        janela = df_ext[(df_ext["quando"] >= ini) & (df_ext["quando"] < fim)].copy()

        if janela.empty:
            st.info("Nenhuma movimentação nesse período.")
        else:
            busca = st.text_input("Buscar por cliente ou descrição", key="ext_busca")
            if busca:
                janela = janela[
                    janela["descricao"].str.contains(busca, case=False, na=False)
                ]

            entradas = int(janela[janela["valor"] > 0]["valor"].sum())
            saidas = int(janela[janela["valor"] < 0]["valor"].sum())
            abertura = int(janela.iloc[0]["saldo_antes"])
            fechamento = int(janela.iloc[-1]["saldo_depois"])

            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Saldo em " + ini.strftime("%d/%m"), fmt_brl(abertura))
            e2.metric("Entradas", fmt_brl(entradas))
            e3.metric("Saídas", fmt_brl(saidas))
            e4.metric("Saldo final", fmt_brl(fechamento))
            if abertura_global and janela.iloc[0]["id"] == todos[0]["id"]:
                st.caption(
                    f"O saldo anterior traz {md(fmt_brl(abs(abertura_global)))} que a "
                    "Pagar.me não detalha em lançamentos: a soma do extrato dela "
                    "não fecha com o saldo que ela mesma informa. Vale perguntar "
                    "ao suporte da Stone o que compõe esse valor."
                )

            st.divider()

            visao = janela.iloc[::-1].copy()   # mais recente primeiro, como banco
            linhas_ext = pd.DataFrame({
                "Data": visao["quando"].dt.strftime("%d/%m/%Y"),
                "Descrição": visao["descricao"],
                "Entrada": visao["valor"].apply(lambda v: fmt_brl(int(v)) if v > 0 else ""),
                "Saída": visao["valor"].apply(lambda v: fmt_brl(int(v)) if v < 0 else ""),
                "Saldo": visao["saldo_depois"].apply(lambda v: fmt_brl(int(v))),
            })
            # Fecha o extrato por baixo com o saldo de onde a leitura parte.
            linhas_ext = pd.concat([
                linhas_ext,
                pd.DataFrame([{
                    "Data": ini.strftime("%d/%m/%Y"),
                    "Descrição": "Saldo anterior",
                    "Entrada": "", "Saída": "",
                    "Saldo": fmt_brl(abertura),
                }]),
            ], ignore_index=True)
            tabela(linhas_ext, num=("Entrada", "Saída", "Saldo"), altura_max=500)
            st.caption(
                f"{len(visao)} lançamentos · valores líquidos, já descontadas as taxas."
            )
            st.download_button(
                "Exportar CSV",
                data=linhas_ext.to_csv(index=False).encode("utf-8"),
                file_name=f"extrato_{date_from_str}_{date_to_str}.csv",
                mime="text/csv",
            )

        with st.expander("Ver lançamentos contábeis (recebíveis futuros e contrapartidas)"):
            st.caption(
                "Estes não mexem no saldo: são o registro do recebível que ainda "
                "vai cair e a contrapartida das transferências. Aparecem aqui só "
                "para conferência."
            )
            cont = db.query_balance_operations(
                date_from=date_from_str, date_to=date_to_str, recipient_id=recip
            )
            df_cont = to_df_ops([o for o in cont if o["status"] != "available"])
            if df_cont.empty:
                st.caption("Nada no período.")
            else:
                disp = traduzir(
                    df_cont[["created_at", "type", "status", "amount_brl", "fee_brl", "net_brl"]].copy(),
                    {"type": TIPO_OP_PT, "status": STATUS_OP_PT},
                )
                disp["created_at"] = disp["created_at"].dt.strftime("%d/%m/%Y %H:%M")
                tabela(
                    disp.rename(columns={
                        "created_at": "Data/Hora", "type": "Tipo", "status": "Status",
                        "amount_brl": "Bruto", "fee_brl": "Taxa", "net_brl": "Líquido",
                    }),
                    num=("Bruto", "Taxa", "Líquido"), altura_max=360,
                )
# ════════════════════════════════════════════════════════════════════════════
# ABA 5: CONCILIAÇÃO
# ════════════════════════════════════════════════════════════════════════════
if "Conciliação" in abas:
  with abas["Conciliação"]:
    st.header("Conciliação")
    st.caption(
        "Confere se cada recebível apareceu no extrato pelo valor certo. "
        "O que é (venda ou estorno) fica separado de em que pé está. "
        "Misturar as duas coisas num rótulo só era o que confundia."
    )

    pays_rows = db.query_payables(date_from=date_from_str, date_to=date_to_str, recipient_id=recip)
    ops_rows  = db.query_balance_operations(date_from=date_from_str, date_to=date_to_str, recipient_id=recip)

    df_p = to_df_pay(pays_rows)
    df_o = to_df_ops(ops_rows)

    if df_p.empty or df_o.empty:
        st.info("Sincronize dados de recebíveis e extrato para ver a conciliação.")
    else:
        # Um recebível antecipado gera DOIS lançamentos no extrato: o original e
        # a reversão. Somar os dois dá zero e faria todo antecipado parecer
        # divergente, por isso a comparação usa o lançamento original.
        ops_agg = (
            df_o.sort_values("created_at")
            .groupby("movement_object_id")
            .agg(valor_extrato=("amount", "first"), qtd_ops=("id", "count"))
            .reset_index()
            .rename(columns={"movement_object_id": "id"})
        )
        merged = df_p.merge(ops_agg, on="id", how="left")

        # Nome do cliente, para a linha dizer de quem é.
        chg = pd.DataFrame(db.query_charges())
        if not chg.empty:
            merged = merged.merge(
                chg[["id", "customer_name"]].rename(columns={"id": "charge_id"}),
                on="charge_id", how="left",
            )
        else:
            merged["customer_name"] = None

        def situacao(row):
            """Em que pé está, só isso, sem dizer o que o registro é.

            O status cru da API não serve de rótulo: um estorno de venda
            cancelada volta como `prepaid`, que soa como dinheiro recebido
            adiantado quando na verdade é uma dívida sendo descontada antes do
            prazo original. Aqui `prepaid` e `waiting_funds` viram a mesma
            coisa, pendente, e o sinal do valor diz o resto.
            """
            if pd.isna(row.get("valor_extrato")):
                return "Sem lançamento"
            if abs((row.get("amount") or 0) - (row.get("valor_extrato") or 0)) > 5:
                return "Divergência"
            return "Liquidado" if row.get("status") == "paid" else "Pendente"

        merged["Situação"] = merged.apply(situacao, axis=1)
        merged["Tipo"] = merged["amount"].apply(lambda v: "Venda" if v >= 0 else "Estorno")

        # Resumo: quantidade e valor por situação, que é o que importa conferir.
        resumo = (
            merged.groupby(["Situação", "Tipo"])
            .agg(qtd=("id", "count"), total=("amount", "sum"))
            .reset_index()
        )
        resumo_disp = pd.DataFrame({
            "Situação": resumo["Situação"],
            "Tipo": resumo["Tipo"],
            "Qtd": resumo["qtd"],
            "Valor": resumo["total"].apply(lambda v: fmt_brl(int(v))),
        })
        tabela(resumo_disp, num=("Qtd", "Valor"))

        divergentes = int((merged["Situação"] == "Divergência").sum())
        sem_lanc = int((merged["Situação"] == "Sem lançamento").sum())
        if divergentes or sem_lanc:
            st.warning(
                f"{divergentes} com valor divergente e {sem_lanc} sem lançamento "
                "no extrato. São os que valem investigar."
            )
        else:
            st.success("Nenhuma divergência: todos os recebíveis do período batem com o extrato.")

        st.caption(
            "**Liquidado**: já caiu na conta. "
            "**Pendente**: ainda vai cair (venda) ou ainda vai ser descontado (estorno). "
            "**Divergência**: valor no extrato diferente do recebível. "
            "**Sem lançamento**: recebível que não apareceu no extrato."
        )

        st.divider()

        f1, f2 = st.columns(2)
        with f1:
            op_sit = ["Todas"] + sorted(merged["Situação"].unique().tolist())
            f_sit = st.selectbox("Situação", op_sit, key="conc_sit")
        with f2:
            f_tipo = st.selectbox("Tipo", ["Todos", "Venda", "Estorno"], key="conc_tipo")

        df_show = merged
        if f_sit != "Todas":
            df_show = df_show[df_show["Situação"] == f_sit]
        if f_tipo != "Todos":
            df_show = df_show[df_show["Tipo"] == f_tipo]

        if df_show.empty:
            st.info("Nada com esses filtros.")
        else:
            det = pd.DataFrame({
                "Cliente": df_show["customer_name"].fillna("—"),
                "Tipo": df_show["Tipo"],
                "Valor": df_show["amount"].apply(lambda v: fmt_brl(int(v))),
                "No extrato": df_show["valor_extrato"].apply(
                    lambda v: fmt_brl(int(v)) if pd.notna(v) else "—"
                ),
                "Cai em": df_show["payment_date"].dt.strftime("%d/%m/%Y"),
                "Situação": df_show["Situação"],
            })
            tabela(det, num=("Valor", "No extrato"), altura_max=460)
            st.download_button(
                "Exportar conciliação CSV",
                data=det.to_csv(index=False).encode("utf-8"),
                file_name=f"conciliacao_{date_from_str}_{date_to_str}.csv",
                mime="text/csv",
            )

# ════════════════════════════════════════════════════════════════════════════
# ABA 7: HISTÓRICO: visão gerencial de todos os meses
# ════════════════════════════════════════════════════════════════════════════
if "Histórico" in abas:
  with abas["Histórico"]:
    st.header("Histórico mês a mês")
    st.caption(
        "Todo o período disponível, independente do filtro da barra lateral."
    )

    hist = db.resumo_mensal()
    if not hist:
        st.info("Sem histórico. Use **Atualizar dados** na barra lateral.")
    else:
        dfh = pd.DataFrame(hist)
        MES_PT = ["jan", "fev", "mar", "abr", "mai", "jun",
                  "jul", "ago", "set", "out", "nov", "dez"]
        dfh["Mês"] = dfh["mes"].apply(
            lambda m: f"{MES_PT[int(m[5:7]) - 1]}/{m[2:4]}"
        )

        # Totais do período inteiro
        fat = int(dfh["faturamento"].sum())
        vendas = int(dfh["vendas"].sum())
        tent = int(dfh["tentativas"].sum())
        custo = int(dfh["custo"].sum())
        # Em duas fileiras: cinco cards numa linha só cortam os valores em reais.
        t1, t2, t3 = st.columns(3)
        t1.metric("Faturado", fmt_brl(fat))
        t1.caption(f"em {len(dfh)} meses")
        t2.metric("Vendas", f"{vendas}")
        t2.caption(f"de {tent} tentativas")
        t3.metric("Ticket médio", fmt_brl(int(fat / vendas)) if vendas else "—")

        t4, t5, t6 = st.columns(3)
        t4.metric("Aprovação", fmt_pct(vendas / tent * 100, 0) if tent else "—")
        t5.metric("Custo", fmt_pct(custo / fat * 100) if fat else "—")
        t5.caption(f"{md(fmt_brl(custo))} em taxas e antecipação")
        t6.metric("Líquido", fmt_brl(fat - custo))

        st.divider()

        cartao_fat = st.container(border=True)
        st.write("")
        g = dfh.copy()
        g["Faturamento"] = g["faturamento"] / 100
        g["Valor"] = g["faturamento"].apply(fmt_brl)
        g["Rotulo"] = g["faturamento"].apply(fmt_curto)
        g["Vendas "] = g["vendas"]
        with cartao_fat:
            st.subheader("Faturamento por mês")
            st.altair_chart(
                barras(g, "Mês", "Faturamento", rotulo="Rotulo",
                       tooltip=["Mês", "Valor", "Vendas "], altura=280),
                use_container_width=True,
            )

        c_esq, c_dir = st.columns(2, gap="large")
        with c_esq.container(border=True):
            st.subheader("Ticket médio")
            g["Ticket"] = g["ticket"] / 100
            g["Médio"] = g["ticket"].apply(fmt_brl)
            g["RotTicket"] = g["ticket"].apply(fmt_curto)
            st.altair_chart(
                barras(g, "Mês", "Ticket", rotulo="RotTicket",
                       tooltip=["Mês", "Médio"], altura=220),
                use_container_width=True,
            )
        with c_dir.container(border=True):
            st.subheader("Aprovação")
            g["Aprovação"] = g["aprovacao"].round(1)
            g["Tentativas"] = g["tentativas"]
            g["RotAprov"] = g["aprovacao"].apply(lambda x: fmt_pct(x, 0))
            st.altair_chart(
                barras(g, "Mês", "Aprovação", rotulo="RotAprov",
                       tooltip=["Mês", "Aprovação", "Vendas ", "Tentativas"], altura=220),
                use_container_width=True,
            )

        st.divider()
        st.subheader("Tabela")
        linhas_hist = pd.DataFrame({
            "Mês": dfh["Mês"],
            "Faturamento": dfh["faturamento"].apply(fmt_brl),
            "Vendas": dfh["vendas"],
            "Ticket médio": dfh["ticket"].apply(fmt_brl),
            "Tentativas": dfh["tentativas"],
            "Aprovação": dfh["aprovacao"].apply(lambda x: fmt_pct(x, 0)),
            "Custo": dfh["custo"].apply(fmt_brl),
            "Custo %": dfh["custo_pct"].apply(lambda x: fmt_pct(x)),
            "Líquido": dfh["liquido"].apply(fmt_brl),
        })
        tabela(linhas_hist,
               num=("Faturamento", "Vendas", "Ticket médio", "Tentativas",
                    "Aprovação", "Custo", "Custo %", "Líquido"))
        st.download_button(
            "Exportar CSV",
            data=linhas_hist.to_csv(index=False).encode("utf-8"),
            file_name="historico_mensal.csv",
            mime="text/csv",
        )
        st.caption(
            "Faturamento e ticket consideram apenas cobranças pagas. Custo é a "
            "soma de taxa e antecipação dos recebíveis dessas vendas. Meses "
            "anteriores à primeira sincronização de recebíveis aparecem com "
            "custo zerado."
        )
