"""Baixa manual: pedido entregue em mãos, fora dos Correios (29/10/2026).

    python -m avisos.regua.entregue 1164

Grava "em_maos:1164" em avisos.enviados com a hora de agora. Na próxima rodada
(até 10 minutos) o motor manda o 8 ("Entregue em mãos", com os cuidados) e
agenda o 9 para 6 dias depois. Para de esperar a postagem: sem 5, 6 nem 7.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402,F401  (carrega o .env)
from sqlalchemy import text  # noqa: E402


def main(numero: str) -> None:
    numero = numero.lstrip("#")
    with nuvem._conectar().begin() as con:
        con.execute(text("insert into avisos.enviados (id, tipo) values (:i, 'entregue em mãos') on conflict do nothing"),
                    {"i": f"em_maos:{numero}"})
    print(f"#{numero} marcado como entregue em mãos")


if __name__ == "__main__":
    main(sys.argv[1])
