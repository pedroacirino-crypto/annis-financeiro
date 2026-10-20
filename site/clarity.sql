-- Clarity no portal, 20/10/2026. O mapa de calor e as gravações ficam no
-- Clarity (não exportam); daqui vêm os números por página da API de
-- exportação: rolagem, tempo de atenção, cliques de raiva, cliques mortos,
-- volta rápida. A API só devolve as últimas 24 a 72 horas e aceita 10
-- chamadas por dia, então uma coleta diária guarda o dia anterior aqui e o
-- histórico se forma com o tempo. Token no cofre: annis_clarity_token.

create table if not exists public.clarity_coletas (
  id           bigserial primary key,
  coletado_em  timestamptz not null default now(),
  dias         int not null,
  dados        jsonb not null
);
alter table public.clarity_coletas enable row level security;
revoke all on public.clarity_coletas from anon, authenticated;

create or replace function avisos.clarity_coletar() returns void
language plpgsql security definer set search_path = extensions, public as $$
declare r http_response;
begin
  perform http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS', '10000');
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '60000');
  r := http((
    'GET',
    'https://www.clarity.ms/export-data/api/v1/project-live-insights?numOfDays=1&dimension1=URL',
    array[http_header('Authorization', 'Bearer ' || avisos.segredo('clarity_token'))],
    null, null
  )::http_request);
  if r.status <> 200 then
    raise exception 'Clarity: HTTP % %', r.status, left(r.content, 200);
  end if;
  insert into public.clarity_coletas (dias, dados) values (1, r.content::jsonb);
exception when others then
  perform avisos.anota_erro('clarity: ' || sqlerrm);
end $$;

-- Todo dia às 03h de Brasília: as 24 horas anteriores, um dia por linha.
select cron.schedule('annis-clarity', '0 6 * * *', 'select avisos.clarity_coletar()');
