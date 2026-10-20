-- Rastreio do site, 20/10/2026. Cada evento da loja e do checkout que o pixel
-- da Shopify (Configurações > Eventos de clientes, código em site/pixel.js)
-- recebe vira uma linha aqui. Sem dado pessoal: nada de nome, e-mail, CPF ou
-- endereço; a compra só leva o número do pedido.
--
-- Esquema `site`, o único exposto na API pública do Supabase. A chave do site
-- só INSERE, com valores conferidos pela política abaixo; ler, só o portal,
-- pela conexão do banco.

create schema if not exists site;
grant usage on schema site to anon;

create table if not exists site.eventos (
  id        bigserial primary key,
  em        timestamptz not null default now(),
  visitante text not null,          -- clientId do pixel: um por navegador
  evento    text not null,
  pagina    text,                   -- caminho, sem domínio
  busca     text,                   -- query string da página (utm, filtros)
  origem    text,                   -- domínio de onde veio (referrer)
  produto   text,
  variante  text,
  valor     numeric(10, 2),
  pedido    text,                   -- só no checkout_completed, ex. #1160
  aparelho  text,                   -- celular ou computador
  extra     text                    -- termo da busca, etapa, quantidade
);

create index if not exists eventos_em on site.eventos (em);
create index if not exists eventos_visitante on site.eventos (visitante, em);

alter table site.eventos enable row level security;

drop policy if exists site_eventos_so_grava on site.eventos;
create policy site_eventos_so_grava on site.eventos for insert to anon
  with check (
    evento in ('page_viewed', 'product_viewed', 'collection_viewed', 'search_submitted',
               'product_added_to_cart', 'product_removed_from_cart', 'cart_viewed',
               'checkout_started', 'checkout_contact_info_submitted',
               'checkout_address_info_submitted', 'checkout_shipping_info_submitted',
               'payment_info_submitted', 'checkout_completed', 'page_left')
    and length(visitante) between 8 and 80
    and coalesce(length(pagina), 0) <= 300
    and coalesce(length(busca), 0) <= 500
    and coalesce(length(origem), 0) <= 120
    and coalesce(length(produto), 0) <= 160
    and coalesce(length(variante), 0) <= 80
    and coalesce(length(pedido), 0) <= 20
    and coalesce(length(aparelho), 0) <= 12
    and coalesce(length(extra), 0) <= 160
    and coalesce(valor, 0) between 0 and 100000
    and em between now() - interval '5 minutes' and now() + interval '5 minutes'
  );

revoke all on site.eventos from anon, authenticated;
grant insert on site.eventos to anon;
grant usage on sequence site.eventos_id_seq to anon;
