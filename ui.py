"""Tabela HTML no padrão da marca, usada pelo app e pela aba Resultado.

Estava só dentro do app.py, e por isso a aba Resultado desenhava tudo com
st.dataframe, que é canvas: não dá para buscar com ctrl+F, não dá para
copiar e não dá para conferir de fora. Movido para cá em 30/09/2026.
"""

import pandas as pd
import streamlit as st


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


def versao_publicada() -> str:
    """Commit que está rodando, lido do .git que a Streamlit Cloud clona.

    Existe para eu conseguir conferir o que foi publicado sem passar pela
    senha. Sem isto eu ficava adivinhando se um deploy tinha entrado, e
    cheguei a dizer que o Pedro estava vendo versão velha sem ter como
    saber. Aparece discreto na tela de entrada.
    """
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
