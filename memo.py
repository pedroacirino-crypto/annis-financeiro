"""Cache em memória com prazo, para não bater no Supabase a cada função.

O Streamlit Cloud fica longe do banco (São Paulo) e cada consulta custa
centenas de milissegundos. Sem isto, montar a aba Resultado dispara centenas
de idas ao banco, porque cada bloco da conta relê extrato, fichas e regras.
Com isto, cada leitura acontece uma vez por hora por processo.
"""

import time
from functools import wraps

_GUARDADO = {}


def memo(segundos: int = 3600):
    def decorador(fn):
        @wraps(fn)
        def envolvida(*args, **kwargs):
            chave = (fn.__module__, fn.__name__, args, tuple(sorted(kwargs.items())))
            agora = time.time()
            if chave in _GUARDADO:
                valor, quando = _GUARDADO[chave]
                if agora - quando < segundos:
                    return valor
            valor = fn(*args, **kwargs)
            _GUARDADO[chave] = (valor, agora)
            return valor
        envolvida.limpar = lambda: [_GUARDADO.pop(k) for k in list(_GUARDADO) if k[1] == fn.__name__]
        return envolvida
    return decorador


def limpar_tudo() -> None:
    _GUARDADO.clear()
