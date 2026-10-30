-- E-mails de divulgação do Novembro Annis, 30/10/2026 (campanha/divulgacao.py).
--
-- Cada envio tem um token aleatório. Com ele:
--   - os links passam por annis.store/pages/ir (avisos.regua_links, pedido "div:<token>");
--   - a imagem de 1 pixel no fim do e-mail chama site.abriu, que grava a abertura.
--     O Mail do iPhone baixa as imagens sozinho, então abertura de iPhone conta
--     mesmo sem a cliente ter lido; clique é o sinal firme;
--   - annis.store/pages/sair?p=<token> tira a pessoa da lista (site.sair).
--
-- Nada de e-mail de cliente no repositório: só no banco.

create table if not exists campanha.divulgacao_envios (
  token      text primary key,
  email      text not null,
  nome       text,
  grupo      text not null,               -- cliente (já comprou) ou cadastro
  qual       text not null,               -- d1, d2a, d2b, d3, d4
  enviado_em timestamptz not null default now(),
  message_id text
);
create index if not exists divulgacao_envios_email on campanha.divulgacao_envios (email);

create table if not exists campanha.divulgacao_aberturas (
  id    bigserial primary key,
  token text not null references campanha.divulgacao_envios (token),
  em    timestamptz not null default now()
);

create table if not exists campanha.descadastro (
  email  text primary key,
  em     timestamptz not null default now(),
  origem text not null                    -- link, resposta, shopify
);

-- Abertura. GET do PostgREST roda em transação só de leitura, então a imagem
-- grava chamando o próprio PostgREST por POST (site.abriu_grava). Devolve um
-- GIF de 1 pixel; o tipo "*/*" deixa a função responder ao Accept da imagem.
do $$ begin
  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where t.typname = '*/*' and n.nspname = 'site') then
    create domain site."*/*" as bytea;
  end if;
end $$;

create or replace function site.abriu_grava(p text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  insert into campanha.divulgacao_aberturas (token)
  select e.token from campanha.divulgacao_envios e where e.token = p;
end $$;

create or replace function site.abriu(p text) returns site."*/*"
language plpgsql stable security definer set search_path = extensions, public as $$
begin
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '3000');
  begin
    perform http((
      'POST', 'https://mkgrmljousyvggpxmofj.supabase.co/rest/v1/rpc/abriu_grava',
      array[http_header('apikey', 'sb_publishable_KMXewGoRe3rCgXEhknGzdQ_pP09F1YW'),
            http_header('Content-Profile', 'site')],
      'application/json', jsonb_build_object('p', p)::text)::http_request);
  exception when others then
    null;  -- a imagem sai mesmo se a gravação falhar
  end;
  perform set_config('response.headers', '[{"Content-Type": "image/gif"}, {"Cache-Control": "no-store"}]', true);
  return decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');
end $$;

-- Sair da lista pela página annis.store/pages/sair.
create or replace function site.sair(p text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare e text;
begin
  select x.email into e from campanha.divulgacao_envios x where x.token = p;
  if e is null then
    return jsonb_build_object('ok', false);
  end if;
  insert into campanha.descadastro (email, origem) values (e, 'link') on conflict do nothing;
  return jsonb_build_object('ok', true);
end $$;

revoke all on function site.abriu_grava(text), site.abriu(text), site.sair(text) from public;
grant execute on function site.abriu_grava(text), site.abriu(text), site.sair(text) to anon;

-- Agenda (UTC; Brasília é UTC-3). O script só manda dentro da janela de cada
-- e-mail e nunca duas vezes para a mesma pessoa; desagendar depois de 30/11.
--   d1   dom 01/11 10h   todo mundo
--   d2   qua 04/11 19h   não clicou no d1 e não comprou (d2a não abriu, d2b abriu)
--   d3   sex 13/11 10h   não comprou (junto do lançamento; data a confirmar)
--   d4   qui 26/11 19h   não comprou
select cron.schedule('annis-divulgacao-d1', '0 13 1 11 *',
  $$select avisos.disparar_workflow('divulgacao.yml', '{"qual": "d1"}')$$);
select cron.schedule('annis-divulgacao-d2', '0 22 4 11 *',
  $$select avisos.disparar_workflow('divulgacao.yml', '{"qual": "d2"}')$$);
select cron.schedule('annis-divulgacao-d3', '0 13 13 11 *',
  $$select avisos.disparar_workflow('divulgacao.yml', '{"qual": "d3"}')$$);
select cron.schedule('annis-divulgacao-d4', '0 22 26 11 *',
  $$select avisos.disparar_workflow('divulgacao.yml', '{"qual": "d4"}')$$);

notify pgrst, 'reload schema';
