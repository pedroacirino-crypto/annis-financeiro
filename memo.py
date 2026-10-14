"""Cache em memória para não bater no Supabase a cada clique.

O Streamlit Cloud fica longe do banco (São Paulo) e cada consulta custa
centenas de milissegundos. Sem isto, montar a aba Resultado dispara centenas
de idas ao banco, porque cada bloco da conta relê extrato, fichas e regras.

Desde 14/10/2026 o que fica guardado não vence com o tempo: o Pedro pediu o
portal congelado até "Atualizar dados". Dado novo entra por três caminhos, e
os três limpam isto: o botão (e a sincronização automática de 12 horas), a
gravação no banco dentro do próprio processo (dados_fin._upsert) e a marca
de "dado novo" que carga.py e a rotina da Stone deixam no Supabase, conferida
uma vez por clique (ver `mudou`).
"""

import time
from functools import wraps

_GUARDADO = {}
_MARCA_VISTA = {"valor": None, "lida": False}


def _hoje() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 3600))  # Brasília


def _copia(valor):
    # Quem chama às vezes acrescenta coluna no DataFrame que recebeu; sem a
    # cópia, isso alteraria o que está guardado para o próximo clique.
    try:
        import pandas as pd
        if isinstance(valor, (pd.DataFrame, pd.Series)):
            return valor.copy()
    except Exception:
        pass
    return valor


def memo(segundos: int = None):
    """Guarda o resultado até alguém limpar. Com `segundos`, vence sozinho."""
    def decorador(fn):
        @wraps(fn)
        def envolvida(*args, **kwargs):
            # O dia entra na chave: muita conta depende de "hoje" (mês corrente,
            # dia do mês), e virar o dia com o cache velho erraria o mês.
            chave = (fn.__module__, fn.__name__, args, tuple(sorted(kwargs.items())), _hoje())
            agora = time.time()
            if chave in _GUARDADO:
                valor, quando = _GUARDADO[chave]
                if segundos is None or agora - quando < segundos:
                    return _copia(valor)
            valor = fn(*args, **kwargs)
            _GUARDADO[chave] = (valor, agora)
            return _copia(valor)
        envolvida.limpar = lambda: [_GUARDADO.pop(k) for k in list(_GUARDADO) if k[1] == fn.__name__]
        return envolvida
    return decorador


def limpar_tudo() -> None:
    _GUARDADO.clear()


def mudou(marca) -> bool:
    """True quando a marca de dado novo do banco é diferente da última vista
    por este processo. A primeira leitura só memoriza: o cache ainda está vazio."""
    if not _MARCA_VISTA["lida"]:
        _MARCA_VISTA.update(valor=marca, lida=True)
        return False
    if marca != _MARCA_VISTA["valor"]:
        _MARCA_VISTA["valor"] = marca
        return True
    return False
