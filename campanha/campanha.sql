-- Novembro Annis 2026: tabelas da campanha (sale, cashback, cópias para reverter).
-- Fica num esquema próprio, fora do public (que é trancado) e fora do site.
create schema if not exists campanha;

-- Preço de cada variante da sale. Antes = como estava ao congelar; sale = o
-- que entra à meia-noite de 01/11. O reverter usa o "antes".
create table if not exists campanha.sale_precos (
  variante_id   text primary key,
  produto_id    text not null,
  produto       text not null,
  tamanho       text not null,
  preco_antes   numeric(10,2) not null,
  riscado_antes numeric(10,2),
  preco_sale    numeric(10,2) not null,
  riscado_sale  numeric(10,2) not null,
  desconto      numeric(5,3) not null,
  congelado_em  timestamptz not null default now()
);

-- Como cada cupom estava antes de a campanha restringi-lo à coleção Fora da sale.
create table if not exists campanha.cupons_antes (
  cupom_id   text primary key,
  codigo     text not null,
  estado     jsonb not null,
  guardado_em timestamptz not null default now()
);

-- Um crédito de cashback por pedido.
create table if not exists campanha.cashback (
  pedido        text primary key,          -- #1160
  pedido_id     text not null,
  cliente       text,
  email         text not null,
  base          numeric(10,2) not null,    -- valor pago nas peças, sem frete
  valor         numeric(10,2) not null,    -- crédito atual (10% da base, menos devolução)
  codigo        text unique,
  desconto_id   text,                      -- id do código na Shopify
  emitido_em    timestamptz not null default now(),
  vence_em      timestamptz not null,
  situacao      text not null default 'ativo',   -- ativo, usado, cancelado, vencido
  usado_em      timestamptz,
  pedido_uso    text,
  atualizado_em timestamptz not null default now()
);
create index if not exists cashback_email on campanha.cashback (email);

-- Agenda: aplicar às 00h de 01/11 (03h UTC) e reverter às 23h59 de 30/11 (02h59 UTC de 01/12).
-- O workflow tem trava de data; mesmo assim, desagendar depois da campanha.
select cron.schedule('annis-campanha-aplicar', '0 3 1 11 *',
  $$select avisos.disparar_workflow('campanha.yml', '{"acao": "aplicar"}')$$);
select cron.schedule('annis-campanha-reverter', '59 2 1 12 *',
  $$select avisos.disparar_workflow('campanha.yml', '{"acao": "reverter"}')$$);
