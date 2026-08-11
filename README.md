# Financeiro ANNIS

Painel de conciliação financeira sobre a API da Pagar.me. Responde três
perguntas: quanto vendeu, quanto tem a receber e quando, e se tudo bate.

Somente leitura, nenhuma rota de escrita da API é usada.

## Abas

- **Vendas**, faturamento do período, ticket médio, aprovação, e a ponte
  entre o que foi vendido e o que sobra depois de taxa e antecipação.
- **A receber**, agenda por data de liquidação, com a venda que originou
  cada parcela.
- **Conciliação**, confere cada recebível contra o extrato.
- **Extrato**, no formato de extrato bancário, com saldo corrido.
- **Histórico**, série mensal de todo o período, fora do filtro de data.

## Rodar local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
printf 'PAGARME_SECRET_KEY=sua_chave\nAPP_PASSWORD=uma_senha\n' > .env
streamlit run app.py
```

## Publicar na Streamlit Cloud

1. Suba este repositório no GitHub (privado).
2. Em share.streamlit.io, conecte o repositório e aponte para `app.py`.
3. Em **Advanced settings › Secrets**, cole:

   ```toml
   PAGARME_SECRET_KEY = "sua_chave"
   APP_PASSWORD = "a_senha_de_acesso"
   ```

A Streamlit Community Cloud só publica apps **públicos**, app privado exige
plano pago com Snowflake. Por isso o painel exige senha própria e falha
fechado: sem `APP_PASSWORD` configurada ele não abre. Aqui aparecem nome de
cliente, faturamento e agenda de recebimentos; um esquecimento de
configuração não pode virar vazamento.

A chave nunca vai para o repositório: local ela é lida do `.env`, hospedada
vem do cofre de secrets. Ambos estão no `.gitignore`.

## Notas que economizam tempo

- **O banco é cache, não fonte.** `dados.db` é reconstruído da API; na
  Streamlit Cloud o disco é efêmero e o app faz a primeira carga sozinho.
- **Datas em horário de Brasília.** A API responde em UTC; sem converter,
  operações da madrugada caem no dia anterior.
- **Taxa de antecipação é cobrada à parte do MDR.** O líquido de um
  recebível é `amount - fee - anticipation_fee`; ignorar o terceiro campo
  infla o resultado.
- **O extrato abre com saldo anterior.** A soma dos lançamentos da API não
  fecha com o `available_amount` que ela mesma informa, a diferença entra
  como saldo de abertura, para o saldo final bater com o da conta.
- **Python fica em 3.12** (`runtime.txt`). Em 3.14 o Altair quebra no import:
  ele declara `TypedDict(..., closed=True)`, sintaxe que o `typing` dessa
  versão ainda não aceita. A Streamlit Cloud oferece 3.14 por padrão, então
  sem o `runtime.txt` um app novo já nasce quebrado.
- **Taxas contratadas são digitadas à mão** em `TAXAS_CONTRATADAS` no
  `app.py`, lidas de Configurações › Taxas e prazos no Dash. Não há endpoint
  de API para elas; se a taxa for renegociada, atualize lá.
