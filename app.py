"""
Dashboard de Conciliação Financeira sobre Pagar.me
"""

import hmac
import os
import time

import streamlit as st
import pandas as pd
import altair as alt
import json
from datetime import date, timedelta, datetime

import aba_resultado
import db
import nuvem
import pagarme_client
import shopify_client
from ui import tabela, versao_publicada

# Precisa ser o primeiro comando Streamlit do arquivo.
st.set_page_config(
    page_title="Conciliação Pagar.me",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

import ui as _ui
_ui.reiniciar()   # chave estável para o botão de tela cheia das tabelas


JANELA_SYNC = 90  # dias de extrato e recebíveis baixados a cada sincronização


def _esquecer_derivados():
    """Apaga o que foi calculado em cima do banco.

    Sem isto, "Atualizar dados" baixava o dado novo e a tela continuava
    mostrando o cruzamento antigo por até dez minutos: foi o que fez a cidade
    da venda recusada seguir vazia mesmo depois da sincronização.

    A aba Resultado entra aqui porque o cache dela dura uma hora e sobrevive
    a publicação nova: em 30/09/2026 a correção da competência subiu e a
    tabela continuou mostrando a agência zerada em agosto, porque `_dados`
    guardava o PnL calculado pelo código antigo.
    """
    for fn in (globals().get("_pedido_por_cobranca"), globals().get("_estoque_atual"),
               getattr(aba_resultado, "_dados", None)):
        try:
            fn.clear()
        except Exception:
            pass
    try:
        import memo
        memo.limpar_tudo()
    except Exception:
        pass


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
            pedidos_loja = shopify_client.listar_pedidos(limite=500)
            conta["pedidos_loja"] = db.upsert_pedidos(pedidos_loja)
            # Copia para o banco que sobrevive ao reinício. Assim o histórico
            # deixa de depender da janela de 60 dias da Shopify: o que passou
            # por aqui uma vez fica guardado para sempre, sem CSV nenhum.
            if nuvem.configurado():
                try:
                    conta["pedidos_guardados"] = nuvem.salvar_pedidos(
                        db.pedidos_para_nuvem()
                    )
                    # Localizar no mapa faz parte de sincronizar, não é tarefa
                    # de ninguém. O limite existe para um lote grande não
                    # segurar a tela: o que sobrar entra na próxima.
                    if avisar:
                        avisar("Localizando as compras no mapa…")
                    conta["localizados"] = nuvem.geocodificar_pendentes(
                        limite=40
                    )["achados"]
                except Exception as e:
                    conta["erro_nuvem"] = str(e)[:200]
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
# Endereço público do painel, usado para montar o atalho de acesso. O app
# não consegue ler a URL de cima sozinho: ele roda dentro de um iframe.
ENDERECO_APP = "https://annis-financeiro-vzk7lcvic78hfk7s8rngdw.streamlit.app"

# Cupom de recuperação, lido de Descontos no admin da loja. Só é aplicado a
# quem abandonou sem tentar pagar, ver _card_recuperar. Se o código mudar ou
# expirar, atualize aqui; não há endpoint que descubra sozinho qual usar.
# Desde 04/10/2026 o cupom é pessoal: nome da pessoa mais 10, 10% por 48
# horas, criado na Shopify na hora em que o card da fila é montado. O VOLTE5
# genérico ficou de reserva para quando a loja não deixar criar cupom.
CUPOM_RECUPERACAO = "VOLTE5"
DESCONTO_RECUPERACAO = "5% OFF"
PERCENTUAL_CUPOM_PESSOAL = 0.10
HORAS_CUPOM_PESSOAL = 48
# Validade do Pix do checkout. Era 30 minutos (medido no pedido #1118: gerado
# 10:53, expirou 11:23). Subiu para 120 em 04/10/2026, no Hub da Stone, em
# Minhas Integrações > Shopify. Se mudar lá, é só trocar aqui.
MINUTOS_PIX = 120

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
/* Enquanto o app processa, a Streamlit desenha um bonequinho correndo no
   cabeçalho. Não dá para trocar o ícone sem recompilar a Streamlit, mas dá
   para escondê-lo e desenhar outro no lugar do pai: um ponto de costura
   girando, feito com borda tracejada. Combina com a marca e não vira
   ilustração de academia. */
[data-testid="stStatusWidgetRunningManIcon"] {{ display: none !important; }}
[data-testid="stStatusWidgetRunningIcon"] {{ width: 1.1rem; height: 1.1rem; border-radius: 50%; border: 1.5px dashed rgba(255,255,255,0.85); animation: annis-costura 1.6s linear infinite; }}
@keyframes annis-costura {{ to {{ transform: rotate(360deg); }} }}
/* O "Stop" ao lado ganha o mesmo tom do cabeçalho em vez do vermelho padrão. */
[data-testid="stStatusWidget"] button {{ font-family: 'Poppins', sans-serif !important; font-size: 0.65rem !important; text-transform: uppercase; letter-spacing: 0.08em; }}
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
.tbl-box {{ position:relative; }}
.tbl-wrap {{ background:#FFFFFF; border:1px solid rgba(104,56,10,0.12); border-radius:2px; overflow:auto; }}
/* O diálogo de tela cheia ocupa quase a janela inteira, senão não vale a pena. */
div[data-testid="stDialog"] div[role="dialog"] {{ width:96vw !important; max-width:96vw !important; height:92vh !important; max-height:92vh !important; }}
div[data-testid="stDialog"] div[role="dialog"] .tbl-wrap {{ max-height:78vh !important; }}
.tbl {{ width:100%; border-collapse:collapse; font-family:'Poppins',sans-serif; font-size:0.82rem; }}
.tbl thead th {{ position:sticky; top:0; z-index:2; background:#FFFFFF; text-align:left; font-weight:400; font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em; color:{MARROM_CLARO}; padding:0.85rem 0.9rem 0.5rem; border-bottom:1px solid {LINHA}; white-space:nowrap; }}
.tbl tbody td {{ padding:0.6rem 0.9rem; border-bottom:1px solid rgba(232,218,203,0.55); color:#4A2C0F; white-space:nowrap; }}
/* Cabeçalho e primeira coluna ficam parados enquanto o resto rola: sem isso
   a tabela larga vira adivinhação, some o mês da linha e o nome da coluna. */
.tbl tbody td:first-child {{ position:sticky; left:0; z-index:1; background:#FFFFFF; border-right:1px solid {LINHA}; }}
.tbl thead th:first-child {{ z-index:3; left:0; border-right:1px solid {LINHA}; }}
/* Colunas presas na direita: o mês que está correndo não some ao rolar. */
.tbl td.presa, .tbl th.presa {{ position:sticky; background:#FFF8F2; min-width:8.5rem; }}
.tbl td.presa {{ z-index:1; }}
.tbl th.presa {{ z-index:3; }}
.tbl .presa0 {{ right:0; }}
.tbl .presa1 {{ right:8.5rem; border-left:1px solid {LINHA}; }}
.tbl tbody tr:hover td.presa {{ background:#FFF3EA; }}
/* Linhas presas embaixo, para a tabela em que o mês é linha. */
.tbl td.presaL {{ position:sticky; z-index:2; background:#FFF8F2; }}
.tbl td.presaL0 {{ bottom:0; }}
.tbl td.presaL1 {{ bottom:2.35rem; border-top:1px solid {LINHA}; }}
.tbl td.presaL.presa {{ z-index:3; }}
.tbl tbody tr:last-child td {{ border-bottom:none; }}
.tbl tbody tr:hover td {{ background:#FFFBF7; }}
.tbl tbody tr:hover td:first-child {{ background:#FFFBF7; }}
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

def _segredo(nome: str):
    """Valor vindo do cofre do host (hospedado) ou do .env (local)."""
    try:
        v = st.secrets.get(nome)
        if v:
            return v
    except Exception:
        pass
    return os.environ.get(nome)


def _senha_configurada():
    """Senha vinda do cofre do host (hospedado) ou do .env (local)."""
    try:
        s = st.secrets.get("APP_PASSWORD")
        if s:
            return s
    except Exception:
        pass
    return os.environ.get("APP_PASSWORD")


# Quanto tempo o atalho de acesso vale sem digitar a senha de novo. O
# incômodo que isto resolve: o st.session_state morre a cada F5 e a cada
# reinício do container, e o Streamlit Cloud reinicia sozinho.
#
# Por que na URL e não em cookie: medido em 29/09/2026, o proxy da Streamlit
# Cloud **descarta o cabeçalho Cookie** antes de entregar ao app, e
# `st.context.cookies` vem sempre vazio em produção (na máquina local vem
# certo, que foi o que me enganou na primeira tentativa). A query string é o
# único canal que atravessa, e o console repassa o parâmetro da URL de cima
# para o iframe do app, então o atalho sobrevive ao F5.
PARAM_SESSAO = "s"
DIAS_DE_SESSAO = 30


def _assinar(ate: int, senha: str) -> str:
    """Token = validade + assinatura da validade com a senha.

    A senha é a chave e nunca aparece no token. Trocar a APP_PASSWORD no
    cofre invalida todos os atalhos de uma vez, que é o botão de pânico se
    um link vazar.
    """
    import hashlib
    sig = hmac.new(senha.encode(), str(ate).encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ate}.{sig}"


def _token_valido(token: str, senha: str) -> bool:
    try:
        ate, _ = (token or "").split(".", 1)
        if int(ate) < int(time.time()):
            return False
    except Exception:
        return False
    return hmac.compare_digest(token, _assinar(int(ate), senha))


PESSOAS = ["Pedro", "Ana", "Isa"]


def _cabecalho(nome: str) -> str:
    try:
        return (st.context.headers or {}).get(nome, "") or ""
    except Exception:
        return ""


def _descrever_aparelho() -> dict:
    """O que dá para saber de quem está do outro lado.

    Medido em produção: chegam `User-Agent` e `X-Forwarded-For`. O
    `X-Streamlit-User` vem vazio em app público, então a Streamlit não
    entrega identidade nenhuma. A impressão do aparelho é o hash do
    User-Agent: é o que existe de estável. Atualização de navegador muda a
    versão e cria um aparelho "novo", o que gera um alarme falso de vez em
    quando, resolvido com um clique em "Fui eu".
    """
    import hashlib
    ua = _cabecalho("User-Agent")
    ip = (_cabecalho("X-Forwarded-For").split(",")[0] or "").strip()
    nav, sis = "desconhecido", "desconhecido"
    for marca, rotulo in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Chrome/", "Chrome"),
                          ("Firefox/", "Firefox"), ("Safari/", "Safari")):
        if marca in ua:
            nav = rotulo
            break
    for marca, rotulo in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                          ("Macintosh", "Mac"), ("Windows", "Windows"), ("Linux", "Linux")):
        if marca in ua:
            sis = rotulo
            break
    return {
        "aparelho": hashlib.sha1(ua.encode()).hexdigest()[:16] if ua else "sem-user-agent",
        "ip": ip, "navegador": nav, "sistema": sis,
    }


def _registrar_entrada(via: str, quem: str = "") -> None:
    """Uma linha no log por sessão. `via` é 'senha' ou 'atalho'."""
    if st.session_state.get("_acesso_registrado"):
        return
    d = _descrever_aparelho()
    if not quem:
        quem = next((a.get("quem") or "" for a in nuvem.ler_aparelhos()
                     if a["aparelho"] == d["aparelho"]), "")
    conhecido = nuvem.registrar_acesso(d["aparelho"], quem, d["ip"],
                                       d["navegador"], d["sistema"], via)
    st.session_state["_acesso_registrado"] = True
    st.session_state["_aparelho"] = d
    st.session_state["_quem"] = quem
    st.session_state["_aparelho_novo"] = not conhecido


def aviso_aparelho_novo() -> None:
    """Faixa no topo quando entra alguém de um aparelho nunca visto."""
    if not st.session_state.get("_aparelho_novo"):
        return
    d = st.session_state.get("_aparelho") or {}
    st.warning(
        f"**Acesso de um aparelho que nunca entrou aqui.** "
        f"{d.get('navegador', '?')} no {d.get('sistema', '?')}, rede {d.get('ip') or 'desconhecida'}. "
        f"Se não foi você, a Ana nem a Isa, troque a APP_PASSWORD no cofre agora: "
        f"ela derruba todos os acessos."
    )
    c1, c2, c3 = st.columns([1, 1, 3])
    quem = c1.selectbox("Quem", PESSOAS, label_visibility="collapsed", key="quem_novo")
    if c2.button("Fui eu", use_container_width=True):
        nuvem.conhecer_aparelho(d.get("aparelho", ""), f"{d.get('navegador')} no {d.get('sistema')}", quem)
        st.session_state["_aparelho_novo"] = False
        st.session_state["_quem"] = quem
        st.rerun()
    if c3.button("Não fui eu, marcar como suspeito", use_container_width=True):
        nuvem.conhecer_aparelho(d.get("aparelho", ""), f"{d.get('navegador')} no {d.get('sistema')}",
                                "", suspeito=True)
        st.session_state["_aparelho_novo"] = False
        st.rerun()


def _guardar_token(esperada: str) -> None:
    """Põe o token na URL, de onde ele sobrevive ao recarregar."""
    token = _assinar(int(time.time()) + DIAS_DE_SESSAO * 86400, esperada)
    st.session_state["_token"] = token
    try:
        st.query_params[PARAM_SESSAO] = token
    except Exception:
        pass


def sair():
    """Esquece este navegador: tira o token da URL e da sessão."""
    for chave in ("_autenticado", "_token"):
        st.session_state.pop(chave, None)
    try:
        del st.query_params[PARAM_SESSAO]
    except Exception:
        pass
    st.rerun()


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
        _registrar_entrada(st.session_state.get("_via", "atalho"))
        # Mantém o token na URL mesmo se algo o tiver apagado no caminho.
        if st.session_state.get("_token") and not st.query_params.get(PARAM_SESSAO):
            try:
                st.query_params[PARAM_SESSAO] = st.session_state["_token"]
            except Exception:
                pass
        return

    # Atalho de acesso: token assinado na URL dispensa a senha até vencer.
    token = st.query_params.get(PARAM_SESSAO) or ""
    if token and _token_valido(token, esperada):
        st.session_state["_autenticado"] = True
        st.session_state["_token"] = token
        st.session_state["_via"] = "atalho"
        _registrar_entrada("atalho")
        return
    if token:
        # Token presente e recusado: venceu, ou a senha mudou no cofre.
        st.session_state["_sessao_expirou"] = True

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
        if st.session_state.get("_sessao_expirou"):
            st.caption("Sua sessão venceu ou a senha mudou. Entre de novo.")
        with st.form("entrar"):
            senha = st.text_input("Senha", type="password", label_visibility="collapsed",
                                  placeholder="Senha de acesso")
            quem_entra = st.selectbox("Quem está entrando", PESSOAS + ["Outra pessoa"],
                                      index=0, help="Etiqueta para o log de acessos. "
                                                    "É declaração, não identificação.")
            lembrar = st.checkbox(f"Continuar conectada neste aparelho por {DIAS_DE_SESSAO} dias",
                                  value=True)
            entrou = st.form_submit_button("Entrar", use_container_width=True, type="primary")
        st.caption(f"versão {versao_publicada()}")
        if entrou:
            # compare_digest evita vazar o tamanho da senha pelo tempo de resposta
            if hmac.compare_digest(senha, esperada):
                st.session_state["_autenticado"] = True
                st.session_state["_via"] = "senha"
                st.session_state["_quem_declarado"] = quem_entra
                st.session_state.pop("_sessao_expirou", None)
                if lembrar:
                    _guardar_token(esperada)
                _registrar_entrada("senha", quem_entra)
                st.rerun()
            else:
                st.error("Senha incorreta.")
    st.stop()


db.init_db()

# Dado velho é pior que dado ausente: a tela parece certa e está errada. Foi
# assim que 12 vendas pagas ficaram 20 dias fora do painel. Agora, se o último
# download passou de meio dia, o app baixa sozinho ao abrir.
#
# Isto roda **antes** da senha de propósito, e é o que permite a rotina diária
# atualizar tudo apenas abrindo a página, sem token nem chave em lugar nenhum
# além do cofre. Quem não tem a senha continua sem ver dado nenhum: só provoca
# o download, e no máximo duas vezes por dia, por causa da janela de 12 horas.
HORAS_ATE_ENVELHECER = 12
_idade = db.horas_desde_sincronizacao()
if db.get_db_counts()["charges"] == 0 or _idade is None or _idade > HORAS_ATE_ENVELHECER:
    try:
        with st.spinner("Buscando o que entrou desde a última vez…"):
            sincronizar(date.today())
            _esquecer_derivados()
    except Exception as _e:
        st.warning(f"Não foi possível atualizar os dados automaticamente: {_e}")

exigir_senha()

# ── Helpers ──────────────────────────────────────────────────────────────────

def fmt_brl(centavos: int) -> str:
    return f"R$ {centavos / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def md(texto: str) -> str:
    """Escapa o cifrão, em markdown o Streamlit trata `$...$` como LaTeX."""
    return texto.replace("$", r"\$")


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


# A loja usa dois formatos de título. Em metade, o traço separa a coleção
# ("Top de Jacquard Azul - Giverny"); na outra, separa a cor ("Vestido de
# Jacquard Monet - Dia", "Colete de Jacquard Trama - Vinho"). Em 07/10/2026 o
# Monet Dia saiu na mensagem como "Vestido Dia": é a cor depois do traço que
# diz qual dos dois formatos é.
_CORES = {"azul", "bege", "branca", "branco", "cinza", "cru", "dia", "grafite",
          "marrom", "mostarda", "noite", "oliva", "preta", "preto", "rosa", "rosé",
          "vermelha", "vermelho", "vinho"}


def _partes_titulo(titulo: str) -> tuple:
    """(tipo, coleção, cor) do título; cor é None no formato com coleção no fim."""
    titulo = (titulo or "").replace(" — ", " - ").strip()
    if " - " not in titulo:
        return titulo, None, None
    antes, depois = (p.strip() for p in titulo.split(" - ", 1))
    tipo = antes.split()[0]
    if depois.lower() in _CORES:
        return tipo, antes.split()[-1], depois
    return tipo, depois, None


def _nome_curto(titulo: str) -> str:
    """'Top de Jacquard Azul - Giverny' vira 'Top Giverny'; 'Vestido de
    Jacquard Monet - Dia' vira 'Vestido Monet Dia'.

    O título do catálogo é feito para busca; na mensagem ele soa robótico.
    Tipo da peça + coleção é como a cliente chama o produto. Quando a cor é
    o que diferencia as versões da coleção (Monet Dia e Noite), ela fica.
    """
    tipo, colecao, cor = _partes_titulo(titulo)
    if not colecao:
        return tipo
    return f"{tipo} {colecao} {cor}" if cor else f"{tipo} {colecao}"


def _artigo(nome: str) -> str:
    """Heurística de gênero: peça terminada em 'a' é feminina."""
    primeira = (nome or "").split()[0] if nome else ""
    return "uma" if primeira.lower().endswith("a") else "um"


# Peças feitas depois do pedido. O Pedro explicou em 02/09/2026: não há 15
# Loulou em estoque, há tecido para produzir até 15.
SOB_ENCOMENDA = ("Loulou",)


def _tem_sob_encomenda(itens_txt: str) -> bool:
    return any(p.lower() in (itens_txt or "").lower() for p in SOB_ENCOMENDA)


def _nome_sob_encomenda(itens_txt: str) -> str:
    """"o Vestido Loulou", a partir da primeira peça sob encomenda do carrinho."""
    for pedaco in (itens_txt or "").split(", "):
        titulo = pedaco.split("x ", 1)[-1] if "x " in pedaco else pedaco
        if _tem_sob_encomenda(titulo):
            curto = _nome_curto(titulo) or titulo.strip()
            return f"{_artigo(curto)} {curto}"
    return "o vestido"


def _lista_produtos(itens_txt: str) -> tuple:
    """Devolve (frase com artigos, coleção comum, pronome de retomada).

    O pronome existe para a frase concordar: uma peça só vira "ela ficou
    esperando"; duas viram "eles ficaram". Sem isso a mensagem sai errada
    justamente no caso mais comum, que é carrinho de item único.
    """
    partes, colecoes = [], set()
    for pedaco in (itens_txt or "").split(", "):
        titulo = pedaco.split("x ", 1)[-1] if "x " in pedaco else pedaco
        curto = _nome_curto(titulo)
        if curto:
            partes.append(curto)
            colecoes.add(_partes_titulo(titulo)[1])
    if not partes:
        return "as peças que separou", None, "elas ficaram"

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
    # Sem rótulo, sem segundo botão: pedido não pago não tem carrinho para
    # restaurar, e um botão apagado do lado só ocupa espaço.
    carrinho = link(rotulo_link, url, bool(url)) if rotulo_link else ""

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


def _dias_desde(quando) -> str:
    """'hoje', 'ontem' ou 'há N dias', a partir de data ou datetime."""
    try:
        d = (date.today() - (quando.date() if hasattr(quando, "date") else
                             date.fromisoformat(str(quando)[:10]))).days
    except Exception:
        return ""
    return "hoje" if d == 0 else ("ontem" if d == 1 else f"há {d} dias")


def _codigo_pessoal(cliente: str, usados: set) -> str:
    """LETICIA10; se já existir, LETICIAZ10 com a inicial do sobrenome, e
    por fim LETICIA10B. Só letras, sem acento, como a Shopify aceita."""
    import unicodedata
    partes = [p for p in unicodedata.normalize("NFKD", cliente or "").encode("ascii", "ignore")
              .decode().upper().split() if p.isalpha()]
    if not partes:
        partes = ["CLIENTE"]
    base = partes[0]
    candidatos = [f"{base}10"] + [f"{base}{p[0]}10" for p in partes[1:]] + [f"{base}10{l}" for l in "BCDEFGH"]
    for c in candidatos:
        if c in usados:
            continue
        try:
            if shopify_client.codigo_existe(c):
                usados.add(c)
                continue
        except Exception:
            pass
        return c
    return f"{base}10X"


def _cupom_pessoal(a: dict):
    """Cupom da pessoa: o que já existe, ou um novo criado agora na Shopify.

    Devolve (codigo, expira_em) ou (None, motivo). Criado uma vez por
    carrinho e guardado na nuvem, para a mensagem sair sempre igual e para
    contar no fim do mês. Falha vira texto sem cupom, nunca card quebrado.
    """
    pronto = nuvem.cupom_do_carrinho(a["id"])
    if pronto:
        return pronto["codigo"], pronto["expira_em"]
    if not shopify_client.configurado():
        return None, "loja não conectada"
    try:
        codigo = _codigo_pessoal(a.get("cliente") or "", nuvem.codigos_de_cupom())
        r = shopify_client.criar_cupom_pessoal(
            codigo, f"Recuperação · {a.get('cliente') or a['id']}", a.get("email") or "",
            PERCENTUAL_CUPOM_PESSOAL, HORAS_CUPOM_PESSOAL)
        nuvem.guardar_cupom(a["id"], codigo, a.get("cliente") or "", a.get("email") or "",
                            r["id"], r["expira"])
        return codigo, r["expira"]
    except Exception as e:
        if "_discounts" in str(e):
            return None, "a loja ainda não liberou a permissão de descontos para o app"
        return None, str(e)[:160]


def _card_recuperar(a: dict, enviado_em=None):
    """Uma pessoa da fila, com o texto pronto e o link que restaura o carrinho.

    `enviado_em` é a data em que alguém clicou em "Já enviei" para esta
    pessoa. O painel não detecta envio: o WhatsApp abre numa janela que ele
    não enxerga. O que existe é a confirmação de quem atendeu.
    """
    primeiro_nome = (a.get("cliente") or "").split()[0] if a.get("cliente") else ""
    saudacao = f"Oi, {primeiro_nome}! Tudo bem? 🤎" if primeiro_nome else "Oi! Tudo bem? 🤎"
    itens = a.get("itens") or ""
    produtos, colecao, pronome = _lista_produtos(itens)
    url = _link_recuperacao(a.get("url_recuperacao", ""))

    # Peça sob encomenda não é carrinho abandonado comum: o Loulou é feito
    # depois do pedido, com prazo e medida da cliente. Quem o deixou no
    # carrinho provavelmente parou numa dúvida, não no preço. A abordagem é
    # abrir a conversa, sem cupom e sem link, como o Pedro pediu em
    # 04/10/2026. Quem tentou pagar e não passou continua com o texto de
    # pagamento, que é o problema dela naquele momento.
    if _tem_sob_encomenda(itens) and a["situacao"] != "Tentou e não passou":
        nome_peca = _nome_sob_encomenda(itens)
        texto = (
            f"{saudacao}\n\n"
            f"Vi que você colocou {nome_peca} no carrinho. Ele é um vestido feito "
            "sob encomenda, do seu jeito, então queria saber se ficou alguma "
            "dúvida sobre ele: prazo, medidas, cores, o que for.\n\n"
            "Me conta por aqui que eu te ajudo. 🤎\n\n"
            "Com carinho,\nAnnis"
        )
        cor, rotulo = "#5B4A8A", "Sob encomenda"
    elif a["situacao"] == "Gerou o pedido e não pagou":
        # Curto de propósito: no caso comum a cliente sabe que não pagou, e
        # explicar o e-mail de confirmação ou oferecer abertura de chamado
        # levanta um problema que ela não tem. Quem reclamar de cobrança
        # indevida recebe essa explicação na conversa, não no primeiro
        # contato. Sem cupom, porque ela já aceitou o preço, e sem link,
        # porque o Pix daquele pedido morreu.
        pedido = a.get("numero") or ""
        texto = (
            f"{saudacao}\n\n"
            f"Seu pedido{(' ' + pedido) if pedido else ''}, de {produtos}, "
            "ficou sem pagamento. O Pix gerado no site expira rápido, em "
            f"{MINUTOS_PIX} minutos, e o seu venceu antes de ser pago.\n\n"
            "Se ainda quiser, te mando um novo Pix por aqui, sem prazo para "
            "expirar. É só me responder. 🤎\n\n"
            "Com carinho,\nAnnis"
        )
        cor, rotulo = "#B8860B", "Gerou o pedido e não pagou"
    elif a["situacao"] == "Tentou e não passou":
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
        codigo, expira = _cupom_pessoal(a)
        if codigo:
            ate = (expira - timedelta(hours=3)).strftime("%d/%m") if hasattr(expira, "strftime") else ""
            oferta = (f"Preparamos um cupom só seu: {codigo}, com 10% de desconto, "
                      f"válido até {ate}.")
            aviso_cupom = ""
        else:
            oferta = (f"Preparamos um desconto especial: {DESCONTO_RECUPERACAO} para sua "
                      f"compra com o cupom {CUPOM_RECUPERACAO}.")
            aviso_cupom = f"Sem cupom pessoal ({expira}). A mensagem saiu com o {CUPOM_RECUPERACAO}."
        texto = (
            f"{saudacao}\n\n"
            f"Vimos que você deixou {produtos} no seu carrinho, "
            f"e {pronome} esperando por você!\n\n"
            f"{oferta}\n\n"
            f"{fecho}\n{url}\n\n"
            "Com carinho,\nAnnis"
        )
        if aviso_cupom:
            st.caption(aviso_cupom)
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
                # O contador de pedidos é da Shopify, que segue marcando o
                # pedido estornado como pago. Sem este aviso a tela afirma uma
                # compra que a loja devolveu.
                + (f" · {a['compras_estornadas']} estornada(s)"
                   if a.get("compras_estornadas") else "")
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

            if a.get("url_recuperacao"):
                _botoes_acao(a, texto_final)
            else:
                # Pedido não pago não tem link de carrinho para restaurar: o
                # checkout virou pedido e o Pix daquele pedido morreu.
                _botoes_acao(a, texto_final, rotulo_link="", url_link="")
            if texto_final != texto:
                st.caption("Texto editado. Os botões acima já usam a sua versão.")

            if enviado_em:
                e1, e2 = st.columns([3, 1])
                e1.caption(f"Marcada como enviada {_dias_desde(enviado_em)}.")
                if e2.button("Desfazer", key=f"desf_{a['id']}", use_container_width=True):
                    if nuvem.desmarcar_contato(a["id"]):
                        st.rerun()
                    else:
                        st.error("Não consegui gravar. O banco na nuvem não respondeu.")
            elif st.button("Já enviei", key=f"env_{a['id']}", use_container_width=True):
                if nuvem.marcar_contato(a["id"], a.get("cliente") or "",
                                        a.get("situacao") or "", a.get("valor") or 0):
                    st.rerun()
                else:
                    st.error(
                        "Não consegui gravar. Sem o banco na nuvem a marcação não "
                        "sobrevive ao reinício, então prefiro não fingir que salvou."
                    )


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
    import html as _h

    with st.container(border=True):
        e1, e2, e3 = st.columns(3)
        e1.metric("Já gastou", fmt_brl(c["total"]))
        e2.metric("Vezes que comprou", c["compras"])
        e3.metric("Sem comprar há", f"{c['dias_sem_comprar']} dias"
                  if c["dias_sem_comprar"] is not None else "—")
        if c["estornos"]:
            e1.caption(md(fmt_brl(c["total_estornado"])) + " estornados")

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

        # Cada compra é um bloco, e não linha de tabela. Numa tabela a lista de
        # peças vira uma linha só, empurra rolagem lateral e some da vista.
        faltando = 0
        for p in sorted(c["pedidos"], key=lambda p: p["dia"], reverse=True):
            cabecalho = " · ".join(x for x in [
                _dia_br(p["dia"]),
                p.get("numero") or "",
                fmt_brl(p["valor"]),
                (_METODOS.get(p["metodo"], p["metodo"] or "")
                 + (f" em {p['parcelas']}x" if p["parcelas"] > 1 else "")),
            ] if x)
            if p.get("estornada"):
                cabecalho += (" · estornada em " + _dia_br(p["estornada_em"])
                              if p.get("estornada_em") else " · estornada")

            if p.get("itens"):
                pecas = [f"<div style='padding-left:0.9rem'>{_h.escape(i.strip())}</div>"
                         for i in p["itens"].split(", ") if i.strip()]
            elif p.get("fora_do_alcance"):
                # Não é pedido vazio: é pedido que a loja não devolve mais.
                # Dizer "sem itens" seria mentira por omissão.
                dias_atras = (date.today() - date.fromisoformat(p["dia"])).days
                faltando += 1
                pecas = [f"<div style='padding-left:0.9rem;font-style:italic'>"
                         f"Compra de {dias_atras} dias atrás, sem detalhe guardado</div>"]
            else:
                pecas = ["<div style='padding-left:0.9rem;font-style:italic'>"
                         "Sem peças registradas</div>"]

            st.markdown(
                f"<div style='padding-top:0.7rem'>"
                f"<div style='font-family:Poppins;font-size:0.72rem;"
                f"letter-spacing:0.04em;color:{MARROM_CLARO}'>{cabecalho}</div>"
                f"<div style='font-family:Poppins;font-size:0.85rem;color:#4A2C0F;"
                f"padding-top:0.2rem;line-height:1.6'>{''.join(pecas)}</div></div>",
                unsafe_allow_html=True,
            )

        if faltando:
            st.caption(
                f"{faltando} compra(s) sem detalhe, anteriores ao que a loja "
                "guardou. Importar a exportação de pedidos preenche."
            )


@st.cache_data(ttl=600)
@st.cache_data(ttl=600, show_spinner=False)
def _pedido_por_cobranca() -> dict:
    """Casamento cobrança ↔ pedido, guardado por 10 minutos: a tela redesenha
    a cada clique e isso lê a nuvem."""
    try:
        return db.pedido_de_cada_cobranca()
    except Exception:
        return {}


def _estoque_atual() -> dict:
    """Estoque por (produto, tamanho), direto da Shopify.

    Guardado por 10 minutos: a lista de espera consulta isso a cada abertura,
    e sem cache seria uma varredura na API por clique.
    """
    if not shopify_client.configurado():
        return {}
    consulta = """query($cursor: String) {
      productVariants(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { title inventoryQuantity product { title status } } } }"""
    saida, cursor = {}, None
    try:
        while True:
            d = shopify_client._graphql(consulta, {"cursor": cursor})
            bloco = d["productVariants"]
            for v in bloco["nodes"]:
                prod = v.get("product") or {}
                if prod.get("status") != "ACTIVE":
                    continue
                saida[(prod.get("title", ""), v.get("title") or "")] = \
                    v.get("inventoryQuantity") or 0
            if not bloco["pageInfo"]["hasNextPage"]:
                break
            cursor = bloco["pageInfo"]["endCursor"]
    except Exception:
        return {}
    return saida


def _card_espera(r: dict, pronta: bool):
    """Uma pessoa da fila, com a mensagem pronta e o botão de WhatsApp."""
    primeiro = (r.get("email") or "").split("@")[0].split(".")[0].title()
    peca = r["produto"] + (f", tamanho {r['variante']}" if r.get("variante") else "")
    if pronta:
        texto = (
            f"Oi! Tudo bem? 🤎\n\n"
            f"Você pediu para avisarmos quando {peca} voltasse, e ele está "
            "disponível de novo na loja.\n\n"
            "Separei aqui para você dar uma olhada:\nhttps://annis.store\n\n"
            "Com carinho,\nAnnis"
        )
    else:
        texto = (
            f"Oi! Tudo bem? 🤎\n\n"
            f"Passando para dizer que não esquecemos: você está na lista de "
            f"{peca}.\n\n"
            "Assim que voltar, você é uma das primeiras a saber.\n\n"
            "Com carinho,\nAnnis"
        )

    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            dias = ""
            try:
                dias = f" · há {(date.today() - r['criado_em'].date()).days} dias"
            except Exception:
                pass
            st.markdown(
                f"<div style='font-family:Poppins;font-size:0.6rem;letter-spacing:0.12em;"
                f"text-transform:uppercase;color:{'#4A7C46' if pronta else MARROM_CLARO}'>"
                + ("Já pode avisar" if pronta else "Aguardando reposição") + dias
                + "</div>"
                f"<div style='font-family:Newsreader,serif;font-size:1.15rem;"
                f"color:{MARROM};padding-top:0.1rem'>{peca}</div>"
                f"<div style='font-family:Poppins;font-size:0.78rem;color:#4A2C0F;"
                f"padding-top:0.25rem'>"
                + (r.get("email") or "") + (" · " + r["telefone"] if r.get("telefone") else "")
                + "</div>",
                unsafe_allow_html=True,
            )
        with c2:
            if st.button("Marcar avisada", key=f"av_{r['id']}",
                         use_container_width=True):
                nuvem.marcar_avisado([r["id"]])
                st.rerun()
    if r.get("telefone"):
        _botoes_acao({"telefone": r["telefone"]}, texto, "Abrir a loja",
                     "https://annis.store")


def _mapa_das_compras():
    """De onde vêm as compras: mapa por cidade e ranking por estado.

    A coordenada sai do CEP da entrega, convertida uma vez e guardada. Se
    nenhum pedido tiver coordenada ainda, o bloco não aparece, em vez de
    mostrar um mapa vazio do oceano.
    """
    try:
        pedidos = nuvem.ler_pedidos()
    except Exception:
        return
    com_local = [p for p in pedidos if p.get("lat") and p.get("lon")]
    if not com_local:
        return

    st.divider()
    st.subheader("De onde vêm as compras")

    por_cidade = {}
    for p in com_local:
        chave = (round(p["lat"], 4), round(p["lon"], 4))
        d = por_cidade.setdefault(chave, {
            "lat": p["lat"], "lon": p["lon"],
            "cidade": p.get("cidade") or "", "uf": p.get("uf") or "",
            "pedidos": 0, "total": 0,
        })
        d["pedidos"] += 1
        d["total"] += p.get("total") or 0

    mapa = pd.DataFrame(por_cidade.values())
    # O raio cresce com o valor, com piso para o ponto pequeno não sumir.
    maior = max(mapa["total"]) or 1
    mapa["raio"] = 12000 + (mapa["total"] / maior) * 45000

    por_uf = {}
    for p in com_local:
        uf = (p.get("uf") or "?").upper()
        d = por_uf.setdefault(uf, {"UF": uf, "Pedidos": 0, "bruto": 0})
        d["Pedidos"] += 1
        d["bruto"] += p.get("total") or 0

    ranking = sorted(por_uf.values(), key=lambda d: -d["bruto"])
    total_geral = sum(d["bruto"] for d in ranking) or 1

    c1, c2 = st.columns([2, 1])
    with c1:
        st.map(mapa, latitude="lat", longitude="lon", size="raio",
               color="#68380ACC", height=420)
        st.caption(
            f"{len(mapa)} lugares · ponto maior é onde entrou mais dinheiro"
        )
    with c2:
        tabela(
            pd.DataFrame([{
                "UF": d["UF"],
                "Pedidos": d["Pedidos"],
                "Faturamento": fmt_brl(d["bruto"]),
                "Fatia": fmt_pct(d["bruto"] / total_geral * 100, 0),
            } for d in ranking]),
            num=("Pedidos", "Faturamento", "Fatia"),
            altura_max=420,
        )

    fora = len(pedidos) - len(com_local)
    if fora:
        st.caption(
            f"{fora} pedido(s) ainda sem localização. Eles entram na próxima "
            "vez que você atualizar os dados."
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
                + (f"{c['compras']} compras" if c["compras"] > 1
                   else ("1 compra" if c["compras"] == 1 else "nenhuma compra que ficou"))
                + f" · última em {_dia_br(c['ultima'])}"
                + (f" · {c['cidade']}/{c['uf']}" if c.get("cidade") else "")
                + (f" · primeira em {_dia_br(c['primeira'])}" if c["compras"] > 1 else "")
                + (f" · {c['estornos']} compra(s) estornada(s)" if c["estornos"] else "")
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
    # "Este mês" é o padrão: a conversa do dia a dia é sobre o mês corrente,
    # e "últimos 30 dias" misturava fim do mês passado com o atual.
    PRESETS = {
        "Hoje": 0,
        "Últimos 7 dias": 7,
        "Este mês": "mes",
        "Últimos 30 dias": 30,
        "Últimos 90 dias": 90,
        "Personalizado": None,
    }
    preset = st.selectbox(
        "Período", list(PRESETS.keys()), index=2, label_visibility="collapsed"
    )
    if PRESETS[preset] is None:
        c_de, c_ate = st.columns(2)
        data_ini = c_de.date_input("De", value=hoje.replace(day=1), key="g_ini")
        data_fim = c_ate.date_input("Até", value=hoje, key="g_fim")
    elif PRESETS[preset] == "mes":
        data_ini = hoje.replace(day=1)
        data_fim = hoje
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
            _esquecer_derivados()
            aviso.empty()
            st.success(
                f"{n['vendas']} vendas · {n['operacoes']} operações · "
                f"{n['recebiveis']} recebíveis"
            )
            st.rerun()
        except Exception as e:
            aviso.empty()
            st.error(f"Falha ao atualizar: {e}")


    # A idade do dado fica à vista, sempre. Painel que mostra número velho com
    # cara de novo é pior que painel vazio: em agosto, 12 vendas passaram 20
    # dias fora da tela sem nada avisar.
    _h_idade = db.horas_desde_sincronizacao()
    if _h_idade is None:
        st.caption("Dados nunca baixados.")
    elif _h_idade < 1:
        st.caption("Dados de agora há pouco.")
    elif _h_idade < 24:
        st.caption(f"Dados de {int(_h_idade)}h atrás.")
    else:
        st.warning(f"Dados de {int(_h_idade / 24)} dia(s) atrás. "
                   "Clique em Atualizar dados.")

    st.caption(f"Baixa os últimos {JANELA_SYNC} dias, mais os recebíveis ainda pendentes.")

    # Contadores locais
    counts = db.get_db_counts()
    st.caption(
        f"Local: {counts['charges']} vendas · "
        f"{counts['balance_operations']} operações · {counts['payables']} recebíveis"
    )

    # Sair existe para o aparelho emprestado ou perdido: apaga o cookie deste
    # navegador. Para derrubar todos de uma vez, troque a APP_PASSWORD no
    # cofre, porque ela é a chave que assina os tokens.
    # O atalho vive na URL, então o caminho seguro é o favorito do navegador.
    # Sem isto o token some na primeira vez que alguém digitar o endereço
    # limpo, e a senha volta a ser pedida sem explicação.
    if st.session_state.get("_token"):
        with st.expander(f"Atalho de acesso · {DIAS_DE_SESSAO} dias"):
            st.caption(
                "Salve este endereço nos favoritos e entre por ele: não pede senha. "
                "Quem tiver o link entra, então não compartilhe. Trocar a APP_PASSWORD "
                "no cofre derruba todos os atalhos de uma vez."
            )
            st.code(f"{ENDERECO_APP}/?{PARAM_SESSAO}={st.session_state['_token']}",
                    language=None)

    if st.button("Sair", use_container_width=True):
        sair()

# ── Abas principais ──────────────────────────────────────────────────────────

# Duas naturezas de trabalho na mesma tela cansavam a leitura: Recuperar e
# Clientes são fila de contato, as outras são conferência de dinheiro. Elas
# não se misturam no dia da Ana, então também não se misturam no menu.
# O Plano fica por último de propósito: é consulta de vez em quando, não
# trabalho do dia, e não precisa estar na frente de quem abre a seção.
# Lista de espera escondida em 04/10/2026 a pedido do Pedro, para voltar
# depois; o código da aba continua aqui e basta devolver o nome à lista.
TRABALHO = ["Recuperar", "Disparos", "Clientes", "Acessos", "Plano"]
FINANCEIRO = ["Vendas", "A receber", "Extrato", "Conciliação", "Histórico", "Resultado"]

# O aviso de aparelho novo vem antes de tudo, inclusive do menu: é a única
# coisa da tela que pode significar que alguém de fora está aqui dentro.
aviso_aparelho_novo()

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
        # Compra, não cobrança: quatro Pix da mesma pessoa pelo mesmo valor em
        # um minuto são uma compra perdida, e quem falhou e pagou em seguida
        # não perdeu nada. Contando cobrança, a aprovação de setembro dava
        # 70% em vez de 88% e a perda dava mais que o dobro.
        perdidas = db.vendas_perdidas(date_from_str, date_to_str)
        tentativas_extras = sum(p["tentativas"] for p in perdidas) - len(perdidas)
        aprov = qtd / (qtd + len(perdidas)) * 100 if (qtd + len(perdidas)) else 0
        c4.metric("Aprovação", fmt_pct(aprov, 0))
        c4.caption(f"{len(perdidas)} de {qtd + len(perdidas)} não converteram")

        if perdidas:
            valor_perdido = int(sum(p["amount"] for p in perdidas))
            with st.expander(f"Ver as {len(perdidas)} vendas que não entraram ({fmt_brl(valor_perdido)})"):
                pd_ = pd.DataFrame(perdidas)
                pd_["valor"] = pd_["amount"].apply(fmt_brl)
                pd_["quando"] = para_brt(
                    pd.to_datetime(pd_["created_at"], errors="coerce", utc=True)
                ).dt.strftime("%d/%m/%Y %H:%M")
                pd_["tent"] = pd_["tentativas"].apply(lambda n: "" if n == 1 else f"{n}x")
                pd_ = traduzir(pd_, {"status": STATUS_CHG_PT, "payment_method": METODO_PT})
                tabela(
                    pd_[["quando", "customer_name", "valor", "status", "payment_method", "tent"]]
                    .rename(columns={
                        "quando": "Quando", "customer_name": "Cliente", "valor": "Valor",
                        "status": "Situação", "payment_method": "Meio", "tent": "Tentativas",
                    }),
                    num=("Valor",), altura_max=320,
                )
                st.caption(
                    "Uma linha por compra, não por cobrança: tentativas da mesma "
                    "pessoa pelo mesmo valor em até 24 horas contam uma vez, e quem "
                    "pagou nos 7 dias seguintes saiu da lista."
                    + (f" No período foram {tentativas_extras} tentativas repetidas."
                       if tentativas_extras else "")
                    + " Pendente ainda pode virar venda; falha e cancelada, não."
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
            # A Pagar.me não sabe o que foi vendido nem para onde: cidade e
            # peças vêm do pedido da Shopify e, quando não houve pedido
            # (cartão recusado não gera um), do carrinho abandonado daquela
            # tentativa, que guarda as peças e o endereço.
            casados = _pedido_por_cobranca()
            det["cidade"] = det["id"].map(
                lambda i: (lambda p: f"{p['cidade']}/{p['uf']}" if p and p.get("cidade") else "")(casados.get(i))
            )
            det["levou"] = det["id"].map(
                lambda i: (casados.get(i) or {}).get("itens") or ""
            )
            det = traduzir(det, {"status": STATUS_CHG_PT, "payment_method": METODO_PT})
            tabela(
                det[["quando", "customer_name", "cidade", "levou", "valor",
                     "status", "payment_method", "installments"]]
                .rename(columns={
                    "quando": "Quando", "customer_name": "Cliente", "cidade": "Cidade",
                    "levou": "O que levou", "valor": "Valor",
                    "status": "Situação", "payment_method": "Meio", "installments": "Parcelas",
                }),
                num=("Valor", "Parcelas"), altura_max=420,
            )
            st.caption(
                "Cidade e peças vêm do pedido da loja e, quando o cartão foi recusado "
                "e não houve pedido, do carrinho daquela tentativa. Fica vazio quando "
                "a pessoa comprou depois, e aí a peça aparece na cobrança que valeu, "
                "ou quando o carrinho é antigo demais para a Shopify ainda guardar."
            )

# ════════════════════════════════════════════════════════════════════════════
# ABA 2: RECUPERAR: fila de trabalho dos checkouts abandonados
# ════════════════════════════════════════════════════════════════════════════
if "Recuperar" in abas:
  with abas["Recuperar"]:
    st.header("Quem quase comprou")
    st.caption(
        "Carrinhos abandonados e pedidos que ficaram sem pagamento, cruzados com "
        "as cobranças da Pagar.me. Não usa o filtro de período da barra lateral: "
        "é uma fila de trabalho, não um relatório."
    )

    if not shopify_client.configurado():
        st.info(
            "Shopify não configurada. Defina `SHOPIFY_LOJA`, `SHOPIFY_CLIENT_ID` "
            "e `SHOPIFY_CLIENT_SECRET` para esta aba funcionar."
        )
    else:
        # Pedido criado e nunca pago não é carrinho abandonado para a Shopify,
        # então nunca chegava aqui, e por não estar pago também não aparecia
        # em Vendas. Entra na mesma fila, com etiqueta própria.
        abandonos = db.abandonados_classificados(dias=180) + db.pedidos_nao_pagos(dias=180)
        abandonos.sort(key=lambda a: a.get("criado_em") or "", reverse=True)
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
                m1, m2, m3, m4 = st.columns(4)
                marcadas = []
                if m1.checkbox("Não tentou pagar", value=True, key="rec_lead"):
                    marcadas.append("Não tentou pagar")
                if m2.checkbox("Tentou e não passou", value=True, key="rec_falhou"):
                    marcadas.append("Tentou e não passou")
                if m3.checkbox("Gerou e não pagou", value=True, key="rec_pedido"):
                    marcadas.append("Gerou o pedido e não pagou")
                if m4.checkbox("Já comprou", value=False, key="rec_comprou"):
                    marcadas.append("Já comprou")
            with f2:
                dias_max = st.selectbox(
                    "Abandonados nos últimos", [7, 15, 30, 60, 90, 180], index=2, key="rec_dias"
                )
                ver_enviadas = st.checkbox(
                    "Mostrar já enviadas", value=False, key="rec_enviadas",
                    help="Quem foi marcada como enviada sai da fila. Marque aqui "
                         "para revisar ou desfazer.",
                )

            # Quem já foi chamada sai da fila para a Ana não mandar duas vezes.
            # A marcação vem da nuvem; sem ela o dicionário é vazio e a aba se
            # comporta como antes.
            enviadas = nuvem.ler_contatos()

            corte = (date.today() - timedelta(days=dias_max)).isoformat()
            janela = [a for a in abandonos if (a["criado_em"] or "")[:10] >= corte]

            leads = [a for a in janela if a["situacao"] == "Não tentou pagar"]
            falhou = [a for a in janela if a["situacao"] == "Tentou e não passou"]
            pedido = [a for a in janela if a["situacao"] == "Gerou o pedido e não pagou"]
            comprou = [a for a in janela if a["situacao"] == "Já comprou"]

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Não tentaram pagar", len(leads))
            k1.caption(md(fmt_brl(sum(a["valor"] for a in leads))) + " em carrinho")
            k2.metric("Tentaram e não passou", len(falhou))
            k2.caption("O pagamento não foi concluído")
            k3.metric("Geraram e não pagaram", len(pedido))
            k3.caption(md(fmt_brl(sum(a["valor"] for a in pedido))) + " em pedido vencido")
            k4.metric("Já compraram", len(comprou))
            k4.caption("Não contatar. A Shopify ainda lista")

            if pedido:
                st.warning(
                    f"{len(pedido)} pessoa(s) fecharam o pedido, geraram o Pix e não pagaram. "
                    "Elas receberam o e-mail de confirmação da Shopify, que sai antes do "
                    "pagamento, então podem achar que compraram. São a fila mais quente: "
                    "escolheram tamanho e preencheram endereço."
                )

            if comprou and "Já comprou" not in marcadas:
                st.success(
                    f"{len(comprou)} pessoas deste período compraram depois de abandonar. "
                    "Elas estão fora da lista para você não cobrar quem já pagou."
                )

            st.divider()

            escolhidos = [a for a in janela if a["situacao"] in marcadas]
            ja_enviadas = [a for a in escolhidos if a["id"] in enviadas]
            if not ver_enviadas:
                escolhidos = [a for a in escolhidos if a["id"] not in enviadas]

            if not marcadas:
                st.info("Marque ao menos uma situação em **Mostrar**.")
            elif not escolhidos:
                if ja_enviadas:
                    st.success(
                        f"Fila zerada. As {len(ja_enviadas)} pessoas deste recorte já "
                        "foram marcadas como enviadas."
                    )
                else:
                    st.info("Ninguém nesse recorte.")
            else:
                aviso = f"{len(escolhidos)} pessoas · mais recentes primeiro"
                if ja_enviadas and not ver_enviadas:
                    aviso += f" · {len(ja_enviadas)} já enviada(s), fora da fila"
                st.caption(aviso)
                for a in escolhidos:
                    _card_recuperar(a, enviado_em=enviadas.get(a["id"]))


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
        estornadas = [c for c in cli if c["estornos"]]
        total_estornado = sum(c["total_estornado"] for c in estornadas)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Clientes", len(cli))
        k1.caption(f"{compras} compras no total")
        # As médias dividem só o dinheiro que ficou, mas pelo total de gente,
        # inclusive quem teve a compra estornada. Tirar essas pessoas da conta
        # inflaria a média com uma base que não existe.
        k2.metric("Gasto por cliente", fmt_brl(total // len(cli)))
        k2.caption("Média do que cada uma já deixou")
        k3.metric("Ticket médio", fmt_brl(total // compras) if compras else "—")
        k3.caption("Por compra paga")
        k4.metric("Voltaram a comprar", len(voltaram))
        k4.caption(fmt_pct(len(voltaram) / len(cli) * 100, 0) + " da base")

        if estornadas:
            st.warning(
                f"{len(estornadas)} pessoas tiveram a compra estornada, "
                + md(fmt_brl(total_estornado))
                + " que voltaram. Elas aparecem na lista marcadas, e não entram "
                "no faturamento nem no ticket médio."
            )

        _mapa_das_compras()

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
            ordem = st.selectbox("Ordenar por", list(ORDENS), key="cli_ordem",
                                 index=list(ORDENS).index("Compra mais recente"))

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
            # Mostra um punhado e deixa a busca fazer o trabalho. A lista
            # inteira numa caixa de rolagem não ajuda ninguém a achar alguém:
            # é longa demais para ler e curta demais para navegar.
            PEDACO = 12
            todas = st.toggle(
                f"Ver as {len(vistas)} de uma vez", value=False, key="cli_ver_todas",
            ) if len(vistas) > PEDACO else True
            mostradas = vistas if todas else vistas[:PEDACO]

            st.caption(
                f"{len(vistas)} de {len(cli)} clientes"
                + ("" if todas else f", mostrando as {len(mostradas)} primeiras")
            )
            tabela(
                pd.DataFrame([{
                    "Cliente": c["nome"] or c["email"],
                    "Cidade": f"{c['cidade']}/{c['uf']}" if c["cidade"] else "—",
                    "Compras": c["compras"],
                    "Total gasto": fmt_brl(c["total"]),
                    "Estornado": fmt_brl(c["total_estornado"]) if c["estornos"] else "—",
                    "Última compra": _dia_br(c["ultima"]),
                    "Dias sem comprar": c["dias_sem_comprar"] if c["dias_sem_comprar"] is not None else "",
                } for c in mostradas]),
                num=("Compras", "Total gasto", "Estornado", "Dias sem comprar"),
                altura_max=520 if todas else None,
            )

            # Clicar na linha da tabela exigiria recarregar a página, e como o
            # login vive na sessão isso jogaria a Ana de volta para a senha.
            # Por isso o detalhe abre por seleção, sem sair da página.
            rotulos = {
                f"{c['nome'] or c['email']} · {fmt_brl(c['total'])}"
                + (" · estornada" if c["estornos"] else ""): c
                for c in vistas
            }
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

    # A base de cima é só o site. Quem compra no balcão não tem cadastro,
    # mas deixa rastro no cartão e no Pix, e é lá que a recompra acontece.
    st.divider()
    st.subheader("Fora do site: quem voltou")
    st.caption(
        "Venda de maquininha e Pix não tem cadastro, mas tem rastro: o cartão "
        "mascarado no relatório de vendas da Stone e o nome de quem pagou o Pix. "
        "Dá para medir quem voltou, não para entrar em contato. É um piso: quem "
        "trocou de cartão aparece como duas pessoas. Compras a menos de 7 dias "
        "uma da outra contam como a mesma ocasião."
    )
    import financeiro as _fin
    _bf = _fin.base_fisica()
    if _bf["resumo"].empty:
        st.info("Relatório de vendas da Stone ainda não carregado.")
    else:
        _r = _bf["resumo"]
        _site_voltou = (len([c for c in cli if c["compras"] > 1]) / len(cli) * 100) if cli else 0.0
        _fis = _r[_r.canal != "Cartão no site (Stone)"]
        f1, f2, f3 = st.columns(3)
        f1.metric("Identidades fora do site", int(_fis.identidades.sum()))
        f1.caption("Cartões na maquininha e pagadores de Pix")
        f2.metric("Voltaram a comprar", int(_fis.voltaram.sum()))
        f2.caption(fmt_pct(_fis.voltaram.sum() / _fis.identidades.sum() * 100, 1)
                   + f" da base, contra {fmt_pct(_site_voltou, 1)} no site")
        f3.metric("Receita de recompra", fmt_brl(int(round(_fis.receita_recompra.sum() * 100))))
        f3.caption(fmt_pct(_fis.receita_recompra.sum() / _fis.receita.sum() * 100, 1) + " do que entrou fora do site")

        tabela(pd.DataFrame({
            "Canal": _r.canal,
            "Identidades": _r.identidades,
            "Voltaram": _r.voltaram,
            "Taxa": _r.taxa.map(lambda v: fmt_pct(v, 1)),
            "Receita": _r.receita.map(lambda v: fmt_brl(int(round(v * 100)))),
            "Recompra": _r.receita_recompra.map(lambda v: fmt_brl(int(round(v * 100)))),
            "% recompra": _r.fatia_recompra.map(lambda v: fmt_pct(v, 1)),
        }), num=["Identidades", "Voltaram", "Taxa", "Receita", "Recompra", "% recompra"],
            titulo="Recompra por canal")

        _v = _bf["clientes"]
        _v = _v[_v.ocasioes > 1]
        if not _v.empty:
            st.markdown("**Quem voltou**")
            tabela(pd.DataFrame({
                "Quem": _v.quem,
                "Canal": _v.canal,
                "Ocasiões": _v.ocasioes,
                "Total": _v.total.map(lambda v: fmt_brl(int(round(v * 100)))),
                "Primeira": _v.primeira.map(lambda d: d.strftime("%d/%m/%Y")),
                "Última": _v.ultima.map(lambda d: d.strftime("%d/%m/%Y")),
                "Dias entre compras": _v.intervalos,
            }), num=["Ocasiões", "Total"], titulo="Quem voltou, fora do site")


# ════════════════════════════════════════════════════════════════════════════
# ABA: PLANO DO MÊS: o que foi combinado fazer, e o que já foi feito
# ════════════════════════════════════════════════════════════════════════════
def _marcar_item_do_plano(id_: str):
    """Callback da caixa: grava na hora, sem botão de salvar."""
    nuvem.marcar_plano(id_, bool(st.session_state.get(f"plano_{id_}")))


_MESES_PLANO = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                "setembro", "outubro", "novembro", "dezembro"]


def _data_do_prazo(prazo: str, mes: str):
    """Prazo do plano como data. "16/10" é o dia; "outubro" vale até o fim do
    mês; "backlog", "1 semana após o disparo" e afins não têm data."""
    import calendar
    import re as _re
    from datetime import date
    ano, mes_plano = int(mes[:4]), int(mes[5:7])
    t = (prazo or "").strip().lower()
    m = _re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)
    if m:
        dia, mm = int(m.group(1)), int(m.group(2))
        return date(ano + (1 if mm < mes_plano - 6 else 0), mm, dia)
    if t in _MESES_PLANO:
        mm = _MESES_PLANO.index(t) + 1
        a = ano + (1 if mm < mes_plano - 6 else 0)
        return date(a, mm, calendar.monthrange(a, mm)[1])
    return None


def _painel_de_prazos(itens: list, mes: str) -> None:
    """Atrasadas e próximas 7 dias, no topo do plano."""
    hoje = (datetime.utcnow() - timedelta(hours=3)).date()
    abertos = [(i, _data_do_prazo(i["prazo"], mes)) for i in itens if not i["feito_em"]]
    atrasadas = sorted([(i, d) for i, d in abertos if d and d < hoje], key=lambda x: x[1])
    proximas = sorted([(i, d) for i, d in abertos if d and hoje <= d <= hoje + timedelta(days=7)],
                      key=lambda x: x[1])
    sem_data = sum(1 for _, d in abertos if d is None)

    def linha(i, d):
        if d < hoje:
            quando = f"{(hoje - d).days} dia{'s' if (hoje - d).days > 1 else ''} de atraso"
        elif d == hoje:
            quando = "hoje"
        else:
            quando = d.strftime("%d/%m")
        return f"- **{quando}** · {i['texto']}  \n  <span style='opacity:.6'>{i['frente']}</span>"

    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown(f"**Atrasadas** · {len(atrasadas)}")
        if atrasadas:
            st.markdown(md("\n".join(linha(i, d) for i, d in atrasadas)), unsafe_allow_html=True)
        else:
            st.caption("Nada atrasado.")
    with c2.container(border=True):
        st.markdown(f"**Próximos 7 dias** · {len(proximas)}")
        if proximas:
            st.markdown(md("\n".join(linha(i, d) for i, d in proximas)), unsafe_allow_html=True)
        else:
            st.caption("Nada com prazo nos próximos 7 dias.")
    if sem_data:
        st.caption(f"{sem_data} em aberto sem data definida (backlog ou que depende de outra ação). "
                   "Prazo só com o mês vale até o fim dele.")


# Fragmento: marcar uma caixa refaz só esta aba. Sem isso o app inteiro
# rodava de novo a cada clique e o contador levava uns vinte segundos
# para acompanhar a caixa.
@st.fragment
def _aba_plano():
    _meses = nuvem.meses_com_plano()
    if not _meses:
        st.header("Plano do mês")
        st.info("Nenhum plano carregado ainda.")
    else:
        _atual = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m")
        _mes = _atual if _atual in _meses else _meses[0]
        if len(_meses) > 1:
            _mes = st.selectbox("Mês", _meses, index=_meses.index(_mes), key="plano_mes")
        _itens = nuvem.ler_plano(_mes)
        _feitos = sum(1 for i in _itens if i["feito_em"])
        _nome_mes = _MESES_PLANO[int(_mes[5:7]) - 1]

        st.header(f"Plano de {_nome_mes}")
        st.caption("O que foi combinado fazer no mês. Marcar ou desmarcar grava na hora, para todo mundo.")
        p1, p2 = st.columns([1, 3])
        p1.metric("Feito", f"{_feitos} de {len(_itens)}")
        with p2:
            st.write("")
            st.progress(_feitos / len(_itens) if _itens else 0.0)
        _painel_de_prazos(_itens, _mes)

        _frente = None
        for i in _itens:
            if i["frente"] != _frente:
                _frente = i["frente"]
                _da_frente = [x for x in _itens if x["frente"] == _frente]
                st.divider()
                st.subheader(_frente)
                st.caption(md(f"{sum(1 for x in _da_frente if x['feito_em'])} de {len(_da_frente)} · {i['alvo']}"))
            c1, c2 = st.columns([5, 1])
            with c1:
                st.checkbox(md(i["texto"]), value=bool(i["feito_em"]), key=f"plano_{i['id']}",
                            on_change=_marcar_item_do_plano, args=(i["id"],))
            with c2:
                if i["feito_em"]:
                    st.caption(f"feito em {(i['feito_em'] - timedelta(hours=3)).strftime('%d/%m')}")
                elif i["prazo"]:
                    st.caption(i["prazo"])


if "Plano" in abas:
  with abas["Plano"]:
    _aba_plano()


# ════════════════════════════════════════════════════════════════════════════
# ABA: DISPAROS: campanha para a base, uma pessoa por vez, com "Já enviei"
# ════════════════════════════════════════════════════════════════════════════
#
# Uma campanha é um texto com um link, mandado para quem já comprou. O cupom
# da campanha vive na Shopify; aqui fica só o nome dele. A marcação de
# enviada usa a mesma tabela da fila de carrinho, com id próprio por
# campanha e pessoa, então não se mistura com a recuperação.
CAMPANHAS = {
    "entretempos": {
        "titulo": "Nova coleção · Entretempos",
        "ate": "25/10",  # cupons estendidos de 18 para 25/10 em 08/10/2026
        # Catálogo online nominal (08/10/2026): a página lê o primeiro nome do
        # ?n= e todos os botões dela já aplicam o ENTRETEMPOS10. Só abre por
        # link: fora de menu, da busca e do sitemap da loja.
        "link": "annis.store/pages/entretempos",
        "cupom_frete": "ENTRETEMPOSFRETE",
        "cupom": "ENTRETEMPOS10",
        # O PDF vai anexado à mão em cada conversa: o link do WhatsApp só leva texto.
        "pdf": "catalogos/ANNIS_ENTRETEMPOS_2026.pdf",
        # Quem comprou há pouco não recebe: o desconto em cima de uma compra
        # recente soa como "paguei mais caro". Janela móvel, conta do dia do
        # envio, então nos repiques quem comprar com o cupom também sai.
        "pausa_dias": 15,
    },
}


def _link_campanha(c: dict, camp: dict) -> str:
    """Link do catálogo com o primeiro nome da cliente, só letras."""
    primeiro = (c.get("nome") or "").split()[0] if c.get("nome") else ""
    primeiro = "".join(ch for ch in primeiro if ch.isalpha() or ch == "-").strip("-")
    primeiro = "-".join(p.capitalize() for p in primeiro.split("-") if p)
    return f"{camp['link']}?n={primeiro}" if len(primeiro) >= 2 else camp["link"]


def _texto_campanha(c: dict, camp: dict) -> str:
    # O desconto é uma cortesia para celebrar o lançamento com quem já conhece
    # a Annis, não um agradecimento (texto revisto pelo Pedro em 09/10/2026).
    # A mensagem sai do número novo da loja, por isso o pedido para salvar o
    # contato. O nome sai igual ao do link.
    link = _link_campanha(c, camp)
    primeiro = link.split("?n=", 1)[1] if "?n=" in link else ""
    # A mensagem sai de um número que a cliente ainda não tem salvo, então a
    # primeira frase já diz de quem é (Pedro, 09/10/2026).
    abertura = (f"{primeiro}, a Entretempos, nova coleção da Annis, chegou." if primeiro
                else "A Entretempos, nova coleção da Annis, chegou.")
    # Quem devolveu peça recebe a frase que reconhece isso. É a chance de
    # recuperar quem saiu frustrada, e fingir que não aconteceu soa pior.
    if c.get("estornos"):
        meio = ("Sei que a última peça não ficou do jeito que você queria, e queremos muito te mostrar "
                "o que criamos de novo. Para celebrar o lançamento com quem já faz parte da história "
                "da Annis, preparamos uma cortesia para você: 10% OFF em toda a coleção Entretempos.")
    else:
        meio = ("Para celebrar o lançamento, quisemos dividir este momento com quem já faz parte da "
                "história da Annis. Por isso, preparamos uma cortesia para você: 10% OFF em toda a "
                "coleção Entretempos.")
    return (
        f"{abertura}\n\n{meio}\n\n"
        # Primeiro disparo sem o frete grátis (decisão de 08/10/2026): o
        # ENTRETEMPOSFRETE fica guardado para os repiques.
        "Fizemos uma página especial para você conhecer a coleção, com os 10% já aplicados no checkout:\n"
        f"{link}\n\n"
        f"A cortesia vale até {camp['ate']}.\n\n"
        "Junto com esta mensagem vai também o catálogo em PDF.\n\n"
        "Este é o novo número oficial da Annis. Salve o contato e, qualquer dúvida de tamanho ou "
        "prazo, é só responder aqui.\n\n"
        "Com carinho,\nAnnis"
    )


def _usos_do_cupom(codigo: str):
    """Quantas vezes o cupom foi usado, direto da Shopify. None se não der."""
    try:
        d = shopify_client._graphql(
            '{ codeDiscountNodeByCode(code: "%s") { codeDiscount { ... on DiscountCodeBasic { asyncUsageCount } '
            '... on DiscountCodeFreeShipping { asyncUsageCount } } } }' % codigo)
        return d["codeDiscountNodeByCode"]["codeDiscount"]["asyncUsageCount"]
    except Exception:
        return None


def _card_disparo(c: dict, camp: dict, chave: str, enviado_em=None):
    texto = _texto_campanha(c, camp)
    etiqueta = []
    if c.get("estornos"):
        etiqueta.append("devolveu peça")
    if c.get("compras", 0) > 1:
        etiqueta.append(f"{c['compras']} compras")
    etiqueta.append(f"última em {_dia_br(c['ultima'])}")
    if c.get("cidade"):
        etiqueta.append(f"{c['cidade']}/{c['uf']}")
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(
                f"<div style='font-family:Newsreader,serif;font-size:1.3rem;color:{MARROM}'>{c.get('nome') or 'Sem nome'}</div>"
                f"<div style='font-family:Poppins;font-size:0.75rem;color:{MARROM_CLARO};padding-top:0.3rem'>"
                + " · ".join(etiqueta) + "</div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div style='text-align:right;font-family:Newsreader,serif;font-size:1.5rem;color:{MARROM}'>{fmt_brl(c['total'])}</div>"
                f"<div style='text-align:right;font-family:Poppins;font-size:0.65rem;color:{MARROM_CLARO}'>já gastou</div>",
                unsafe_allow_html=True,
            )
        _outra = db.quem_recebe_diferente(c.get("email"), c.get("nome"))
        if _outra:
            st.warning(f"Comprou no nome de {c.get('nome')}, mas o pedido foi entregue para {_outra}. "
                       f"O e-mail e o telefone podem ser de {_outra.split()[0]}: confira para quem vai a "
                       "mensagem e ajuste o nome no texto se for o caso.")
        texto_final = st.text_area("Mensagem", value=texto, height=230, key=f"disp_{chave}",
                                   label_visibility="collapsed")
        _botoes_acao(c, texto_final, "Abrir o catálogo", "https://" + _link_campanha(c, camp))
        if camp.get("pdf"):
            st.caption("Antes de enviar, anexe o PDF do catálogo na conversa.")
        if texto_final != texto:
            st.caption("Texto editado. Os botões acima já usam a sua versão.")
        if enviado_em:
            e1, e2 = st.columns([3, 1])
            e1.caption(f"Marcada como enviada {_dias_desde(enviado_em)}.")
            if e2.button("Desfazer", key=f"disp_desf_{chave}", use_container_width=True):
                if nuvem.desmarcar_contato(chave):
                    st.rerun()
        elif st.button("Já enviei", key=f"disp_env_{chave}", use_container_width=True):
            if nuvem.marcar_contato(chave, c.get("nome") or "", "disparo", c.get("total") or 0):
                st.rerun()
            else:
                st.error("Não consegui gravar. O banco na nuvem não respondeu.")


if "Disparos" in abas:
  with abas["Disparos"]:
    st.header("Disparos")
    st.caption(
        "Campanha para quem já comprou, uma pessoa por vez: o texto já vem "
        "pronto, o botão abre o WhatsApp, e \"Já enviei\" tira a pessoa da fila."
    )
    _camp_id = st.selectbox("Campanha", list(CAMPANHAS), format_func=lambda k: CAMPANHAS[k]["titulo"],
                            key="disp_campanha")
    _camp = CAMPANHAS[_camp_id]
    if _camp.get("pdf") and os.path.exists(_camp["pdf"]):
        p1, p2 = st.columns([3, 1])
        p1.info("Em cada conversa, anexe o catálogo em PDF depois de colar a mensagem. "
                "O WhatsApp não deixa o link levar o arquivo junto.")
        with open(_camp["pdf"], "rb") as _f:
            p2.download_button("Baixar o PDF", _f.read(), file_name=os.path.basename(_camp["pdf"]),
                               mime="application/pdf", use_container_width=True, key="disp_pdf")
    _base = db.clientes()
    if not _base:
        st.info("Nenhuma cliente na base ainda. Use **Atualizar dados** na barra lateral.")
    else:
        _pausa = _camp.get("pausa_dias")
        _recentes = [c for c in _base if _pausa and c.get("dias_sem_comprar") is not None
                     and c["dias_sem_comprar"] <= _pausa]
        _base = [c for c in _base if c not in _recentes]
        _base = sorted(_base, key=lambda c: c["ultima"] or "", reverse=True)
        _enviadas = nuvem.ler_contatos()
        _chave = lambda c: f"disparo:{_camp_id}:{(c.get('email') or c.get('nome') or '').lower()}"
        _feitas = [c for c in _base if _chave(c) in _enviadas]
        _sem_fone = [c for c in _base if not _so_digitos(c.get("telefone", ""))]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Na base", len(_base))
        m2.metric("Enviadas", len(_feitas))
        m3.metric("Faltam", len(_base) - len(_feitas))
        _u1, _u2 = _usos_do_cupom(_camp["cupom"]), _usos_do_cupom(_camp["cupom_frete"])
        m4.metric("Usos do cupom", "—" if _u1 is None else f"{_u1} · frete {_u2 if _u2 is not None else '—'}",
                  help="Direto da Shopify: quantos pedidos usaram o cupom de 10%, e quantos o de frete.")
        if _recentes:
            st.caption(f"{len(_recentes)} fora da campanha por terem comprado nos últimos {_pausa} dias.")
        if _sem_fone:
            st.caption(f"{len(_sem_fone)} sem telefone na base; aparecem no fim, com o botão apagado.")

        f1, f2 = st.columns([1, 1])
        _so_devolveu = f1.checkbox("Só quem devolveu peça", value=False, key="disp_devolveu")
        _ver_enviadas = f2.checkbox("Mostrar já enviadas", value=False, key="disp_ver_enviadas")

        _fila = [c for c in _base if (not _so_devolveu or c.get("estornos"))]
        _fila = sorted(_fila, key=lambda c: (0 if _so_digitos(c.get("telefone", "")) else 1))
        if not _ver_enviadas:
            _fila = [c for c in _fila if _chave(c) not in _enviadas]
        st.caption(f"{len(_fila)} na fila")
        for c in _fila:
            _card_disparo(c, _camp, _chave(c), enviado_em=_enviadas.get(_chave(c)))


# ════════════════════════════════════════════════════════════════════════════
# ABA 4: LISTA DE ESPERA: quem quis e não tinha
# ════════════════════════════════════════════════════════════════════════════
if "Lista de espera" in abas:
  with abas["Lista de espera"]:
    st.header("Quem quis e não tinha")
    st.caption(
        "Cadastros de aviso de reposição. É a única parte do funil que não "
        "aparecia em lugar nenhum: quem bate em página esgotada não chega ao "
        "checkout, então nem a aba Recuperar enxergava."
    )

    if not nuvem.configurado():
        st.info("Banco não configurado. A lista de espera vive fora do Streamlit.")
    else:
        fila = nuvem.ler_espera()
        if not fila:
            st.info(
                "Ninguém na fila ainda. Assim que o formulário estiver no site, "
                "cada pessoa que pedir aviso aparece aqui."
            )
        else:
            # Cruza com o estoque de hoje: quem já pode ser avisada vem primeiro,
            # porque é a única parte da fila que vira venda agora.
            estoque = _estoque_atual()

            def tem_estoque(r):
                return estoque.get((r["produto"], r.get("variante") or ""), 0) > 0

            prontas = [r for r in fila if tem_estoque(r)]
            aguardando = [r for r in fila if not tem_estoque(r)]

            k1, k2, k3 = st.columns(3)
            k1.metric("Na fila", len(fila))
            k2.metric("Já dá para avisar", len(prontas))
            k2.caption("A peça voltou ao estoque")
            k3.metric("Peças diferentes", len({(r["produto"], r.get("variante")) for r in fila}))

            if prontas:
                st.success(f"{len(prontas)} pessoa(s) esperando peça que já voltou.")
                for r in prontas:
                    _card_espera(r, pronta=True)

            st.divider()
            st.subheader("Demanda represada")
            st.caption("Ordenado por quantas pessoas esperam a mesma peça")
            agrupado = {}
            for r in aguardando:
                ch = (r["produto"], r.get("variante") or "")
                agrupado.setdefault(ch, []).append(r)
            for (prod, var), gente in sorted(agrupado.items(), key=lambda kv: -len(kv[1])):
                with st.expander(
                    f"{len(gente)} esperando · {prod}" + (f" [{var}]" if var else ""),
                    expanded=False,
                ):
                    for r in gente:
                        _card_espera(r, pronta=False)


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
    c1, c2, c3 = st.columns(3)
    c1.metric("Já disponível para sacar", fmt_brl(avail))
    c2.metric("Ainda a receber", fmt_brl(waiting))
    c3.metric("Com tudo que está previsto", fmt_brl(avail + waiting),
              help="O que já dá para sacar mais tudo que ainda vai cair, "
                   "sem contar venda que ainda não aconteceu.")

    st.divider()
    st.subheader("Agenda de recebimentos")

    agenda = db.agenda_recebimentos(recip)
    if not agenda:
        st.info("Nada a receber no momento, tudo já liquidado.")
    else:
        df_ag = pd.DataFrame(agenda)
        df_ag["Data"] = pd.to_datetime(df_ag["dia"]).dt.strftime("%d/%m/%Y")
        df_ag["Valor"] = df_ag["liquido"].apply(lambda x: fmt_brl(int(x)))
        # Duas informações, não uma: Valor é o que entra naquele dia, Saldo
        # previsto é o que se acumula por cima do saldo de hoje. Não é "saldo
        # na conta" porque um saque derruba o número sem que nada tenha
        # mudado na previsão.
        df_ag["Saldo previsto"] = (avail + df_ag["liquido"].cumsum()).apply(lambda x: fmt_brl(int(x)))
        tabela(
            df_ag[["Data", "Valor", "parcelas", "Saldo previsto"]].rename(columns={"parcelas": "Parcelas"}),
            num=("Valor", "Parcelas", "Saldo previsto"),
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
            f"**Valor** é o que entra no dia. **Saldo previsto** soma isso aos "
            f"{md(fmt_brl(avail))} que já estão na conta hoje, e supõe que nada "
            "seja sacado no meio do caminho. Valores líquidos, já descontadas "
            f"taxa e antecipação. Tarifas pendentes de {md(fmt_brl(ar['tarifas']))} "
            "são cobradas na liquidação."
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

            # O que ainda vai cair, para o extrato não parar no saldo de hoje.
            # Só faz sentido quando a janela alcança hoje: olhando um mês
            # fechado do passado, previsão futura não tem o que fazer ali.
            hoje_brt = pd.Timestamp.now(tz="America/Sao_Paulo").normalize()
            previsto = db.agenda_recebimentos(recip) if fim > hoje_brt else []
            total_previsto = int(sum(p["liquido"] for p in previsto))

            if previsto:
                e1, e2, e3, e4, e5 = st.columns(5)
            else:
                e1, e2, e3, e4 = st.columns(4)
            e1.metric("Saldo em " + ini.strftime("%d/%m"), fmt_brl(abertura))
            e2.metric("Entradas", fmt_brl(entradas))
            e3.metric("Saídas", fmt_brl(saidas))
            e4.metric("Saldo final", fmt_brl(fechamento))
            if previsto:
                e5.metric("Com o previsto", fmt_brl(fechamento + total_previsto),
                          delta=fmt_brl(total_previsto),
                          help="Saldo de hoje mais os recebíveis que ainda vão cair, "
                               "supondo que nada seja sacado. Não entra venda que "
                               "ainda não aconteceu.")
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
            # Previsão por cima, na mesma ordem do resto (mais recente primeiro),
            # com o saldo correndo a partir do fechamento de hoje.
            if previsto:
                corrido, futuras = fechamento, []
                for p in previsto:
                    corrido += int(p["liquido"])
                    futuras.append({
                        "Data": pd.to_datetime(p["dia"]).strftime("%d/%m/%Y"),
                        "Descrição": f"A receber · {p['parcelas']} "
                                     + ("parcela" if p["parcelas"] == 1 else "parcelas"),
                        "Entrada": fmt_brl(int(p["liquido"])), "Saída": "",
                        "Saldo": fmt_brl(corrido),
                    })
                linhas_ext = pd.concat(
                    [pd.DataFrame(futuras[::-1]), linhas_ext], ignore_index=True
                )
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
                + (f" As {len(previsto)} primeiras linhas são previsão: recebível "
                   "confirmado que ainda não caiu. O saldo delas supõe que nada "
                   "seja sacado até lá." if previsto else "")
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
        perd = int(dfh["perdidas"].sum())
        custo = int(dfh["custo"].sum())
        # Em duas fileiras: cinco cards numa linha só cortam os valores em reais.
        t1, t2, t3 = st.columns(3)
        t1.metric("Faturado", fmt_brl(fat))
        t1.caption(f"em {len(dfh)} meses")
        t2.metric("Vendas", f"{vendas}")
        t2.caption(f"de {tent} compras tentadas")
        t3.metric("Ticket médio", fmt_brl(int(fat / vendas)) if vendas else "—")

        t4, t5, t6 = st.columns(3)
        t4.metric("Aprovação", fmt_pct(vendas / tent * 100, 0) if tent else "—")
        t4.caption(f"{perd} não entraram")
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
            g["Não entraram"] = g["perdidas"]
            g["RotAprov"] = g["aprovacao"].apply(lambda x: fmt_pct(x, 0))
            st.altair_chart(
                barras(g, "Mês", "Aprovação", rotulo="RotAprov",
                       tooltip=["Mês", "Aprovação", "Vendas ", "Não entraram"], altura=220),
                use_container_width=True,
            )

        st.divider()
        st.subheader("Tabela")
        linhas_hist = pd.DataFrame({
            "Mês": dfh["Mês"],
            "Faturamento": dfh["faturamento"].apply(fmt_brl),
            "Vendas": dfh["vendas"],
            "Ticket médio": dfh["ticket"].apply(fmt_brl),
            "Não entraram": dfh["perdidas"],
            "Aprovação": dfh["aprovacao"].apply(lambda x: fmt_pct(x, 0)),
            "Custo": dfh["custo"].apply(fmt_brl),
            "Custo %": dfh["custo_pct"].apply(lambda x: fmt_pct(x)),
            "Líquido": dfh["liquido"].apply(fmt_brl),
        })
        tabela(linhas_hist,
               num=("Faturamento", "Vendas", "Ticket médio", "Não entraram",
                    "Aprovação", "Custo", "Custo %", "Líquido"))
        st.download_button(
            "Exportar CSV",
            data=linhas_hist.to_csv(index=False).encode("utf-8"),
            file_name="historico_mensal.csv",
            mime="text/csv",
        )
        st.caption(
            "Faturamento e ticket consideram apenas cobranças pagas. Aprovação é "
            "venda sobre compra tentada, com o mesmo agrupamento da aba Vendas: "
            "quem tentou duas vezes conta uma, e quem falhou e pagou em seguida "
            "não conta como perda. Custo é a soma de taxa e antecipação dos "
            "recebíveis dessas vendas. Meses anteriores à primeira sincronização "
            "de recebíveis aparecem com custo zerado."
        )


if "Resultado" in abas:
    with abas["Resultado"]:
        aba_resultado.render()


# ════════════════════════════════════════════════════════════════════════════
# ABA: ACESSOS: quem entrou no painel, e de onde
# ════════════════════════════════════════════════════════════════════════════
if "Acessos" in abas:
  with abas["Acessos"]:
    st.header("Quem entrou aqui")
    st.caption(
        "Uma linha por entrada no painel. O nome é etiqueta declarada por quem "
        "entra, não identificação: a senha é uma só. O que o painel afirma de "
        "verdade é o aparelho e a rede."
    )

    if not nuvem.configurado():
        st.info("Sem o banco na nuvem não há log: ele precisa sobreviver ao reinício.")
    else:
        acessos = nuvem.ler_acessos(limite=300)
        aparelhos = nuvem.ler_aparelhos()
        if not acessos:
            st.info("Nenhum acesso registrado ainda.")
        else:
            desconhecidos = [a for a in acessos if not a["conhecido"]]
            suspeitos = [a for a in aparelhos if a["suspeito"]]
            k1, k2, k3 = st.columns(3)
            k1.metric("Entradas registradas", len(acessos))
            k2.metric("Aparelhos conhecidos", len([a for a in aparelhos if not a["suspeito"]]))
            k3.metric("Marcados como suspeitos", len(suspeitos))

            if suspeitos:
                st.error(
                    f"{len(suspeitos)} aparelho(s) marcado(s) como suspeito. "
                    "Troque a APP_PASSWORD no cofre: ela derruba todos os acessos."
                )
            elif desconhecidos:
                st.warning(
                    f"{len(desconhecidos)} entrada(s) de aparelho ainda não reconhecido. "
                    "Confirme abaixo quais são de vocês."
                )

            st.subheader("Aparelhos")
            st.caption(
                "Atualização de navegador muda a impressão do aparelho e cria um "
                "registro novo. Por isso aparece um desconhecido de vez em quando "
                "sem ninguém estranho ter entrado."
            )
            if aparelhos:
                tabela(pd.DataFrame({
                    "Aparelho": [a["apelido"] or a["aparelho"][:8] for a in aparelhos],
                    "Quem": [a["quem"] or "não identificado" for a in aparelhos],
                    "Último acesso": [a["visto_em"].strftime("%d/%m/%Y %H:%M") for a in aparelhos],
                    "Situação": ["suspeito" if a["suspeito"] else "conhecido" for a in aparelhos],
                }))
            else:
                st.caption("Nenhum aparelho confirmado ainda.")

            st.subheader("Entradas")
            tabela(pd.DataFrame({
                "Quando": [a["quando"].strftime("%d/%m/%Y %H:%M") for a in acessos],
                "Quem disse ser": [a["quem"] or "não disse" for a in acessos],
                "Aparelho": [a["apelido"] or f"{a['navegador']} no {a['sistema']}" for a in acessos],
                "Rede": [a["ip"] or "desconhecida" for a in acessos],
                "Entrou por": [a["via"] for a in acessos],
                "Reconhecido": ["sim" if a["conhecido"] else "NÃO" for a in acessos],
            }), altura_max=520)
