"""Tabela HTML no padrão da marca, usada pelo app e pela aba Resultado.

Estava só dentro do app.py, e por isso a aba Resultado desenhava tudo com
st.dataframe, que é canvas: não dá para buscar com ctrl+F, não dá para
copiar e não dá para conferir de fora. Movido para cá em 30/09/2026.
"""

import pandas as pd
import streamlit as st

def reiniciar():
    """Zera a contagem de tabelas do run. A chave do botão de tela cheia
    precisa ser a mesma em todo run, senão o Streamlit não reconhece o
    clique; o contador global sozinho cresceria para sempre."""
    st.session_state["_tbl_n"] = {}


def _chave(df) -> str:
    import hashlib
    base = hashlib.sha1(("|".join(map(str, df.columns)) + str(len(df))).encode()).hexdigest()[:8]
    n = st.session_state.setdefault("_tbl_n", {})
    n[base] = n.get(base, 0) + 1
    return f"tblfs_{base}_{n[base]}"



@st.dialog(" ", width="large")
def _tela_cheia(titulo: str, html: str):
    st.markdown(f"**{titulo}**")
    st.markdown(html, unsafe_allow_html=True)


def tabela(df, num=(), altura_max=None, titulo="Tabela", fixas_direita=0, fixas_baixo=0):
    """Tabela em HTML no padrão da marca.

    O st.dataframe desenha num canvas com grade em volta de cada célula, não
    dá para estilizar por CSS e destoa do resto. Aqui sai HTML de verdade:
    sem linhas verticais, cabeçalho em caixa alta discreta, régua fina entre
    as linhas. `num` são as colunas alinhadas à direita (valores).
    """
    import html as _html

    # As últimas colunas podem ficar paradas na direita, como a primeira fica
    # na esquerda: é onde mora o mês que está correndo, e ele não pode sumir
    # quando se rola o histórico.
    n_col = len(df.columns)
    presas = {n_col - 1 - i: i for i in range(min(fixas_direita, n_col - 1))}

    def classe(c, i, base=""):
        partes = [base] if base else []
        if c in num:
            partes.append("num")
        if i in presas:
            partes.append(f"presa presa{presas[i]}")
        return " ".join(partes)

    cab = "".join(
        f'<th class="{classe(c, i)}">{_html.escape(str(c))}</th>'
        for i, c in enumerate(df.columns)
    )
    # Linhas presas embaixo: nas tabelas em que o mês é linha, o mês corrente
    # fica parado no rodapé enquanto o histórico rola por cima.
    n_lin = len(df)
    presas_lin = {n_lin - 1 - i: i for i in range(min(fixas_baixo, n_lin))}
    corpo = []
    for n_r, (_, linha) in enumerate(df.iterrows()):
        celulas = []
        for i, c in enumerate(df.columns):
            v = "" if pd.isna(linha[c]) else str(linha[c])
            base = "neg" if (c in num and v.strip().startswith("-")) else ""
            if n_r in presas_lin:
                base = (base + f" presaL presaL{presas_lin[n_r]}").strip()
            cls = classe(c, i, base)
            celulas.append(f'<td class="{cls}">{_html.escape(v)}</td>' if cls
                           else f"<td>{_html.escape(v)}</td>")
        corpo.append("<tr>" + "".join(celulas) + "</tr>")

    def montar(limite):
        estilo = f' style="max-height:{limite}px"' if limite else ""
        # Sem quebras de linha: linha em branco encerraria o bloco no markdown.
        return (f'<div class="tbl-box"><div class="tbl-wrap"{estilo}><table class="tbl">'
                f"<thead><tr>{cab}</tr></thead><tbody>{''.join(corpo)}</tbody></table></div></div>")

    st.markdown(montar(altura_max), unsafe_allow_html=True)

    # Tela cheia pelo diálogo do Streamlit, e não por CSS: dentro de um
    # expander o <details> vira bloco de contenção e prende o position:fixed
    # no lugar da tabela, então a tabela "em tela cheia" ficava do tamanho da
    # janela mas ancorada no meio da página. O diálogo abre na raiz do app.
    if st.button("⤢", key=_chave(df), help="Ver em tela cheia", type="tertiary"):
        _tela_cheia(titulo, montar(None))


def versao_publicada() -> str:
    """Commit que o processo carregou, não o que está no disco.

    Existe para eu conseguir conferir o que foi publicado sem passar pela
    senha. Sem isto eu ficava adivinhando se um deploy tinha entrado, e
    cheguei a dizer que o Pedro estava vendo versão velha sem ter como
    saber. Aparece discreto na tela de entrada.

    Lido uma vez só, quando o módulo é importado. Em 01/10/2026 a Streamlit
    Cloud já tinha puxado o commit novo para o disco e ainda servia o código
    velho: a tela dizia uma versão e a conta vinha de outra. Lendo no
    import, o número que aparece é o do código que está de fato rodando.
    """
    return _VERSAO


def _ler_versao() -> str:
    import pathlib
    try:
        raiz = pathlib.Path(__file__).resolve().parent / ".git"
        cabeca = (raiz / "HEAD").read_text().strip()
        if cabeca.startswith("ref:"):
            alvo = raiz / cabeca.split(" ", 1)[1].strip()
            if alvo.exists():
                return alvo.read_text().strip()[:7]
            pacotes = (raiz / "packed-refs").read_text().splitlines()
            ref = cabeca.split(" ", 1)[1].strip()
            for linha in pacotes:
                if linha.endswith(" " + ref):
                    return linha.split(" ", 1)[0][:7]
            return "?"
        return cabeca[:7]
    except Exception:
        return "?"


_VERSAO = _ler_versao()
