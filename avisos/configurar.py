"""Instala os avisos do Telegram no Supabase.

    python avisos/configurar.py            grava os segredos e o SQL, sem agendar
    python avisos/configurar.py agendar    também liga o agendamento (pg_cron)

Os segredos saem do .env (SHOPIFY_LOJA, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) e vão para o cofre do Supabase (vault)
com nomes annis_*. Rodar de novo atualiza os valores.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nuvem  # noqa: E402
import shopify_client  # noqa: E402,F401  (carrega o .env)
from sqlalchemy import text  # noqa: E402

SEGREDOS = {
    "shopify_loja": "SHOPIFY_LOJA",
    "shopify_client_id": "SHOPIFY_CLIENT_ID",
    "shopify_client_secret": "SHOPIFY_CLIENT_SECRET",
    "telegram_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat": "TELEGRAM_CHAT_ID",
}


def main(agendar: bool) -> None:
    sql = open(os.path.join(os.path.dirname(__file__), "avisos.sql"), encoding="utf-8").read()
    corpo, _, agenda = sql.partition("-- Agenda.")
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
        # Cursor cru: o SQL tem % (mensagens de erro) e não leva parâmetros.
        cursor = con.connection.cursor()
        cursor.execute(corpo)
        if agendar:
            cursor.execute("-- Agenda." + agenda)
    print("avisos instalados" + (" e agendados" if agendar else " (sem agenda)"))


if __name__ == "__main__":
    main(len(sys.argv) > 1 and sys.argv[1] == "agendar")
