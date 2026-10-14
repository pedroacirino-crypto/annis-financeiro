"""Instala no Supabase o que as páginas da régua usam (regua.sql).

    python -m avisos.regua.instalar

Grava no cofre as credenciais dos Correios (CORREIOS_USUARIO,
CORREIOS_CODIGO_ACESSO, CORREIOS_CARTAO do .env, como annis_correios_*) e roda
o regua.sql. Rodar de novo atualiza.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402,F401  (carrega o .env)
from sqlalchemy import text  # noqa: E402

SEGREDOS = {
    "correios_usuario": "CORREIOS_USUARIO",
    "correios_codigo": "CORREIOS_CODIGO_ACESSO",
    "correios_cartao": "CORREIOS_CARTAO",
}


def main() -> None:
    sql = open(os.path.join(os.path.dirname(__file__), "regua.sql"), encoding="utf-8").read()
    with nuvem._conectar().begin() as con:
        for nome, var in SEGREDOS.items():
            valor = os.environ.get(var)
            if not valor:
                raise SystemExit(f"falta {var} no .env")
            existe = con.execute(text("select id from vault.secrets where name = :n"),
                                 {"n": "annis_" + nome}).scalar()
            if existe:
                con.execute(text("select vault.update_secret(:id, :v)"), {"id": existe, "v": valor})
            else:
                con.execute(text("select vault.create_secret(:v, :n)"), {"v": valor, "n": "annis_" + nome})
        con.connection.cursor().execute(sql)
    print("régua instalada")


if __name__ == "__main__":
    main()
