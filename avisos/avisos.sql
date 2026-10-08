-- Avisos da loja no Telegram (grupo ANNIS), 04/10/2026.
--
-- Roda dentro do Supabase, sem depender do GitHub (o agendador de lá já
-- atrasou de 3 a 4 horas) nem de computador ligado:
--   - a cada 2 minutos, pedido novo, Pix vencido sem pagamento e carrinho
--     abandonado novo viram mensagem;
--   - todo dia às 23h de Brasília, fechamento com hoje, semana e mês;
--   - na mesma volta de 2 minutos, enquanto houver pedido esperando nota
--     fiscal, consulta a Olist e manda a DANFE em PDF no grupo (avisos.notas
--     e o workflow danfe.yml).
--
-- As credenciais (Shopify, Telegram, Olist, GitHub) ficam no cofre do Supabase (vault),
-- com nomes annis_*, gravadas por avisos/configurar.py a partir do .env.
-- Nada de segredo neste arquivo, que é público no GitHub.
--
-- O esquema "avisos" não é exposto pela API do Supabase.

create extension if not exists http with schema extensions;
create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;

create schema if not exists avisos;

create table if not exists avisos.enviados (
  id     text primary key,
  tipo   text not null,
  criado timestamptz not null default now()
);

create table if not exists avisos.estado (
  chave  text primary key,
  valor  text,
  expira timestamptz,
  em     timestamptz not null default now()
);

create or replace function avisos.segredo(nome text) returns text
language sql stable security definer set search_path = '' as $$
  select decrypted_secret from vault.decrypted_secrets where name = 'annis_' || nome
$$;

-- R$ 1.234,56
create or replace function avisos.brl(v numeric) returns text
language sql immutable as $$
  select 'R$ ' || translate(to_char(coalesce(v, 0), 'FM999,999,990.00'), ',.', '.,')
$$;

create or replace function avisos.shopify_token() returns text
language plpgsql security definer set search_path = extensions, public as $$
declare
  t text; e timestamptz; r http_response; j jsonb;
begin
  select valor, expira into t, e from avisos.estado where chave = 'shopify_token';
  if t is not null and e > now() + interval '10 minutes' then
    return t;
  end if;
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '20000');
  r := http_post(
    'https://' || avisos.segredo('shopify_loja') || '.myshopify.com/admin/oauth/access_token',
    'grant_type=client_credentials'
      || '&client_id=' || urlencode(avisos.segredo('shopify_client_id'))
      || '&client_secret=' || urlencode(avisos.segredo('shopify_client_secret')),
    'application/x-www-form-urlencoded');
  if r.status <> 200 then
    raise exception 'token da Shopify: HTTP %', r.status;
  end if;
  j := r.content::jsonb;
  t := j ->> 'access_token';
  insert into avisos.estado (chave, valor, expira, em)
  values ('shopify_token', t, now() + make_interval(secs => coalesce((j ->> 'expires_in')::int, 86399)), now())
  on conflict (chave) do update set valor = excluded.valor, expira = excluded.expira, em = now();
  return t;
end $$;

create or replace function avisos.shopify(consulta text, variaveis jsonb default '{}') returns jsonb
language plpgsql security definer set search_path = extensions, public as $$
declare r http_response; j jsonb;
begin
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '20000');
  r := http((
    'POST',
    'https://' || avisos.segredo('shopify_loja') || '.myshopify.com/admin/api/2026-07/graphql.json',
    array[http_header('X-Shopify-Access-Token', avisos.shopify_token())],
    'application/json',
    jsonb_build_object('query', consulta, 'variables', variaveis)::text
  )::http_request);
  if r.status <> 200 then
    raise exception 'Shopify: HTTP %', r.status;
  end if;
  j := r.content::jsonb;
  if j ? 'errors' then
    raise exception 'Shopify: %', left(j ->> 'errors', 300);
  end if;
  return j -> 'data';
end $$;

create or replace function avisos.telegram(texto text) returns void
language plpgsql security definer set search_path = extensions, public as $$
declare r http_response;
begin
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '15000');
  r := http_post(
    'https://api.telegram.org/bot' || avisos.segredo('telegram_token') || '/sendMessage',
    jsonb_build_object('chat_id', avisos.segredo('telegram_chat'), 'text', texto,
                       'disable_web_page_preview', true)::text,
    'application/json');
  if r.status <> 200 then
    raise exception 'Telegram: HTTP % %', r.status, left(r.content, 200);
  end if;
end $$;

-- Itens: "- Colete de Jacquard Trama - Vinho · M" (quantidade quando passa de 1)
create or replace function avisos.itens(linhas jsonb) returns text
language sql immutable as $$
  select coalesce(string_agg(
    '- ' || (l ->> 'title')
      || case when coalesce(l ->> 'variantTitle', '') not in ('', 'Default Title')
              then ' · ' || (l ->> 'variantTitle') else '' end
      || case when (l ->> 'quantity')::int > 1 then ' · ' || (l ->> 'quantity') || ' un' else '' end,
    E'\n'), '')
  from jsonb_array_elements(coalesce(linhas, '[]')) l
$$;

create or replace function avisos.lugar(endereco jsonb) returns text
language sql immutable as $$
  select nullif(concat_ws('/', nullif(endereco ->> 'city', ''), nullif(endereco ->> 'provinceCode', '')), '')
$$;

create or replace function avisos.anota_erro(msg text) returns void
language sql security definer as $$
  insert into avisos.estado (chave, valor, em) values ('ultimo_erro', left(msg, 500), now())
  on conflict (chave) do update set valor = excluded.valor, em = now()
$$;

-- Cada aviso é enviado e anotado num bloco próprio: se o Telegram falhar no
-- meio, o que já foi enviado fica anotado e não se repete na próxima volta.
create or replace function avisos.checar() returns void
language plpgsql security definer set search_path = extensions, public as $$
declare
  desde text := to_char((now() - interval '2 days') at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
  semear boolean := not exists (select 1 from avisos.estado where chave = 'semeado');
  d jsonb; n jsonb; texto text; nome text; lugar text; contato text; compras int;
begin
  -- Pedidos novos
  d := avisos.shopify($q$
    query($q: String!) {
      orders(first: 30, sortKey: CREATED_AT, reverse: true, query: $q) {
        nodes {
          id name test
          displayFinancialStatus
          totalPriceSet { shopMoney { amount } }
          discountCodes
          customer { displayName numberOfOrders phone }
          shippingAddress { city provinceCode phone }
          billingAddress { phone }
          lineItems(first: 20) { nodes { title variantTitle quantity } }
        }
      }
    }$q$, jsonb_build_object('q', 'created_at:>=' || desde));
  for n in select * from jsonb_array_elements(d -> 'orders' -> 'nodes') loop
    continue when exists (select 1 from avisos.enviados where id = n ->> 'id');
    if not semear then
      nome := coalesce(nullif(n -> 'customer' ->> 'displayName', ''), 'Cliente sem nome');
      lugar := avisos.lugar(n -> 'shippingAddress');
      compras := (n -> 'customer' ->> 'numberOfOrders')::int;
      texto := 'Novo pedido ' || (n ->> 'name') || ' · ' || avisos.brl((n -> 'totalPriceSet' -> 'shopMoney' ->> 'amount')::numeric)
        || case when (n ->> 'test')::boolean then ' (teste)' else '' end
        || E'\n' || nome || coalesce(' · ' || lugar, '')
        || case when compras > 1 then ' · ' || compras || 'ª compra' else ' · primeira compra' end
        || E'\n' || avisos.itens(n -> 'lineItems' -> 'nodes')
        || E'\n' || case n ->> 'displayFinancialStatus'
                      when 'PAID' then 'Pago'
                      when 'PENDING' then 'Aguardando pagamento'
                      when 'AUTHORIZED' then 'Pagamento autorizado'
                      else coalesce(n ->> 'displayFinancialStatus', '') end
        || case when jsonb_array_length(coalesce(n -> 'discountCodes', '[]')) > 0
                then ' · Cupom: ' || (select string_agg(x, ', ') from jsonb_array_elements_text(n -> 'discountCodes') x)
                else '' end;
    end if;
    begin
      if not semear then perform avisos.telegram(texto); end if;
      insert into avisos.enviados (id, tipo) values (n ->> 'id', 'pedido') on conflict do nothing;
      -- Pedido novo passa a esperar a nota fiscal (ver avisos.notas).
      if not semear and n ->> 'displayFinancialStatus' <> 'EXPIRED' then
        insert into avisos.enviados (id, tipo) values ('aguarda_nf:' || ltrim(n ->> 'name', '#'), 'aguarda nota')
        on conflict do nothing;
      end if;
    exception when others then
      perform avisos.anota_erro(sqlerrm);
      return;
    end;
  end loop;

  -- Pix que venceu sem pagamento (a Shopify marca o pedido como EXPIRED).
  -- Venda quase fechada: a cliente escolheu, preencheu e só não pagou.
  for n in select * from jsonb_array_elements(d -> 'orders' -> 'nodes')
           where value ->> 'displayFinancialStatus' = 'EXPIRED' loop
    continue when exists (select 1 from avisos.enviados where id = 'pix:' || (n ->> 'id'));
    if not semear then
      nome := coalesce(nullif(n -> 'customer' ->> 'displayName', ''), 'Cliente sem nome');
      contato := case
        when coalesce(n -> 'customer' ->> 'phone', n -> 'shippingAddress' ->> 'phone', n -> 'billingAddress' ->> 'phone') is not null
          then 'tem telefone' else 'sem telefone' end;
      texto := 'Pix do pedido ' || (n ->> 'name') || ' venceu sem pagar · '
        || avisos.brl((n -> 'totalPriceSet' -> 'shopMoney' ->> 'amount')::numeric)
        || E'\n' || nome || ' · ' || contato
        || E'\n' || avisos.itens(n -> 'lineItems' -> 'nodes');
    end if;
    begin
      if not semear then perform avisos.telegram(texto); end if;
      insert into avisos.enviados (id, tipo) values ('pix:' || (n ->> 'id'), 'pix vencido') on conflict do nothing;
      delete from avisos.enviados where id = 'aguarda_nf:' || ltrim(n ->> 'name', '#');
    exception when others then
      perform avisos.anota_erro(sqlerrm);
      return;
    end;
  end loop;

  -- Carrinhos abandonados novos
  d := avisos.shopify($q$
    query($q: String!) {
      abandonedCheckouts(first: 30, sortKey: CREATED_AT, reverse: true, query: $q) {
        nodes {
          id
          totalPriceSet { shopMoney { amount } }
          customer { displayName email phone numberOfOrders }
          shippingAddress { city provinceCode phone }
          billingAddress { phone }
          lineItems(first: 20) { nodes { title variantTitle quantity } }
        }
      }
    }$q$, jsonb_build_object('q', 'created_at:>=' || desde));
  for n in select * from jsonb_array_elements(d -> 'abandonedCheckouts' -> 'nodes') loop
    continue when exists (select 1 from avisos.enviados where id = n ->> 'id');
    if not semear then
      nome := coalesce(nullif(n -> 'customer' ->> 'displayName', ''), 'Sem nome');
      lugar := avisos.lugar(n -> 'shippingAddress');
      contato := case
        when coalesce(n -> 'customer' ->> 'phone', n -> 'shippingAddress' ->> 'phone', n -> 'billingAddress' ->> 'phone') is not null
          then 'tem telefone'
        when n -> 'customer' ->> 'email' is not null then 'só email'
        else 'sem contato' end;
      texto := 'Carrinho abandonado · ' || avisos.brl((n -> 'totalPriceSet' -> 'shopMoney' ->> 'amount')::numeric)
        || E'\n' || nome || coalesce(' · ' || lugar, '') || ' · ' || contato
        || E'\n' || avisos.itens(n -> 'lineItems' -> 'nodes')
        || E'\nNa fila da aba Recuperar do portal.';
    end if;
    begin
      if not semear then perform avisos.telegram(texto); end if;
      insert into avisos.enviados (id, tipo) values (n ->> 'id', 'carrinho') on conflict do nothing;
    exception when others then
      perform avisos.anota_erro(sqlerrm);
      return;
    end;
  end loop;

  if semear then
    insert into avisos.estado (chave, valor) values ('semeado', now()::text) on conflict do nothing;
  end if;
  delete from avisos.estado where chave = 'ultimo_erro';

  -- Notas fiscais dos pedidos que estão esperando nota. Num bloco próprio:
  -- problema na Olist não pode atrasar os avisos de pedido.
  begin
    perform avisos.notas();
  exception when others then
    perform avisos.anota_erro('notas: ' || sqlerrm);
  end;
exception when others then
  perform avisos.anota_erro(sqlerrm);
end $$;

-- Olist ERP, API 2.0. Parâmetros já no formato do corpo do POST (a=1&b=2).
create or replace function avisos.olist(metodo text, parametros text) returns jsonb
language plpgsql security definer set search_path = extensions, public as $$
declare r http_response; j jsonb;
begin
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '20000');
  r := http_post(
    'https://api.tiny.com.br/api2/' || metodo || '.php',
    'token=' || urlencode(avisos.segredo('olist_token')) || '&formato=json&' || parametros,
    'application/x-www-form-urlencoded');
  if r.status <> 200 then
    raise exception 'Olist %: HTTP %', metodo, r.status;
  end if;
  j := r.content::jsonb -> 'retorno';
  -- Pesquisa sem resultado volta como erro na API 2.0 ("A consulta não retornou registros").
  if j ->> 'status' <> 'OK' and coalesce(j ->> 'codigo_erro', '') <> '20' then
    raise exception 'Olist %: %', metodo, left(j ->> 'erros', 300);
  end if;
  return j;
end $$;

-- Dispara o workflow danfe.yml, que imprime a DANFE em PDF e manda no grupo.
create or replace function avisos.disparar_danfe() returns void
language plpgsql security definer set search_path = extensions, public as $$
declare r http_response;
begin
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '15000');
  r := http((
    'POST',
    'https://api.github.com/repos/pedroacirino-crypto/annis-financeiro/actions/workflows/danfe.yml/dispatches',
    array[http_header('Authorization', 'Bearer ' || avisos.segredo('github_token')),
          http_header('Accept', 'application/vnd.github+json'),
          http_header('User-Agent', 'annis-avisos')],
    'application/json',
    '{"ref":"main"}'
  )::http_request);
  if r.status <> 204 then
    raise exception 'GitHub: HTTP % %', r.status, left(r.content, 200);
  end if;
end $$;

-- Nota fiscal dos pedidos novos, 07/10/2026. A Olist puxa o pedido da Shopify
-- e gera a nota sozinha, alguns minutos depois do pedido. Enquanto houver
-- pedido esperando nota ("aguarda_nf:<número>", anotado por avisos.checar),
-- esta função pergunta à Olist; sem pedido esperando, não consulta nada.
--   - nota autorizada: dispara o workflow que manda o PDF no grupo (ele tira a
--     marca do pedido depois de enviar);
--   - nota rejeitada pela SEFAZ: avisa uma vez;
--   - nota parada em "pendente" há 30 minutos: avisa uma vez (a autorização
--     automática não está confirmada no plano da Olist);
--   - pedido há 3 dias sem nota: avisa e para de esperar.
create or replace function avisos.notas() returns void
language plpgsql security definer set search_path = extensions, public as $$
declare
  fuso constant text := 'America/Sao_Paulo';
  ret jsonb; n jsonb; pedido text; idn text; precisa_pdf boolean := false; ultimo timestamptz;
begin
  if not exists (select 1 from avisos.enviados where id like 'aguarda_nf:%') then
    return;
  end if;

  for pedido in delete from avisos.enviados
                where id like 'aguarda_nf:%' and criado < now() - interval '3 days'
                returning substr(id, 12) loop
    perform avisos.telegram('Pedido #' || pedido || ' está há 3 dias sem nota fiscal autorizada na Olist.');
  end loop;

  ret := avisos.olist('notas.fiscais.pesquisa',
    'tipoNota=S&dataInicial=' || to_char((now() at time zone fuso)::date - 3, 'DD/MM/YYYY'));

  for n in select value -> 'nota_fiscal' from jsonb_array_elements(coalesce(ret -> 'notas_fiscais', '[]')) loop
    pedido := n ->> 'numero_ecommerce';
    idn := n ->> 'id';
    continue when coalesce(pedido, '') = ''
      or not exists (select 1 from avisos.enviados where id = 'aguarda_nf:' || pedido);

    if (n ->> 'situacao')::int in (6, 7) then
      precisa_pdf := true;

    elsif (n ->> 'situacao')::int = 5
          and not exists (select 1 from avisos.enviados where id = 'nfrej:' || idn) then
      perform avisos.telegram('NF ' || (n ->> 'numero') || ' do pedido #' || pedido
        || ' foi rejeitada pela SEFAZ. Abrir a nota na Olist para ver o motivo e corrigir.');
      insert into avisos.enviados (id, tipo) values ('nfrej:' || idn, 'nota rejeitada');

    elsif (n ->> 'situacao')::int = 1 then
      insert into avisos.enviados (id, tipo) values ('nfpend:' || idn, 'nota pendente') on conflict do nothing;
      if exists (select 1 from avisos.enviados where id = 'nfpend:' || idn and criado < now() - interval '30 minutes')
         and not exists (select 1 from avisos.enviados where id = 'nfpend_aviso:' || idn) then
        perform avisos.telegram('NF ' || (n ->> 'numero') || ' do pedido #' || pedido
          || ' está pendente há 30 minutos. Falta autorizar na Olist: Vendas > Notas fiscais > autorizar pendentes.');
        insert into avisos.enviados (id, tipo) values ('nfpend_aviso:' || idn, 'nota pendente avisada');
      end if;
    end if;
  end loop;

  -- Um disparo cobre todas as notas autorizadas; espera 10 minutos antes de
  -- disparar de novo, para o GitHub ter tempo de mandar e tirar as marcas.
  if precisa_pdf then
    select em into ultimo from avisos.estado where chave = 'danfe_disparo';
    if ultimo is null or ultimo < now() - interval '10 minutes' then
      perform avisos.disparar_danfe();
      insert into avisos.estado (chave, valor, em) values ('danfe_disparo', now()::text, now())
      on conflict (chave) do update set valor = excluded.valor, em = now();
    end if;
  end if;
end $$;

-- Fechamento do dia, só site (vendas da loja física não passam pela Shopify).
create or replace function avisos.fechamento() returns text
language plpgsql security definer set search_path = extensions, public as $$
declare
  fuso constant text := 'America/Sao_Paulo';
  hoje date := (now() at time zone fuso)::date;
  ini_semana date := date_trunc('week', hoje)::date;
  ini_mes date := date_trunc('month', hoje)::date;
  -- A semana pode começar no mês anterior: busca desde o que vier primeiro.
  desde text := to_char((least(ini_mes, ini_semana)::timestamp at time zone fuso) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
  d jsonb; texto text;
  dias text[] := array['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo'];
  meses text[] := array['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
                        'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
  h_n int; h_v numeric; s_n int; s_v numeric; m_n int; m_v numeric; carrinhos int;
begin
  d := avisos.shopify($q$
    query($q: String!) {
      orders(first: 250, sortKey: CREATED_AT, reverse: true, query: $q) {
        nodes { createdAt test cancelledAt currentTotalPriceSet { shopMoney { amount } } }
      }
    }$q$, jsonb_build_object('q', 'created_at:>=' || desde));
  with p as (
    select ((n ->> 'createdAt')::timestamptz at time zone fuso)::date as dia,
           (n -> 'currentTotalPriceSet' -> 'shopMoney' ->> 'amount')::numeric as valor
    from jsonb_array_elements(d -> 'orders' -> 'nodes') n
    where n ->> 'cancelledAt' is null and not coalesce((n ->> 'test')::boolean, false)
  )
  select count(*) filter (where dia = hoje), coalesce(sum(valor) filter (where dia = hoje), 0),
         count(*) filter (where dia >= ini_semana), coalesce(sum(valor) filter (where dia >= ini_semana), 0),
         count(*) filter (where dia >= ini_mes), coalesce(sum(valor) filter (where dia >= ini_mes), 0)
    into h_n, h_v, s_n, s_v, m_n, m_v
  from p;

  d := avisos.shopify($q$
    query($q: String!) {
      abandonedCheckouts(first: 100, query: $q) { nodes { createdAt } }
    }$q$, jsonb_build_object('q', 'created_at:>=' || to_char((hoje::timestamp at time zone fuso) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')));
  carrinhos := jsonb_array_length(coalesce(d -> 'abandonedCheckouts' -> 'nodes', '[]'));

  texto := 'Fechamento de ' || dias[extract(isodow from hoje)::int] || ', ' || to_char(hoje, 'DD/MM') || ' (site)'
    || E'\n\nHoje: ' || h_n || case when h_n = 1 then ' pedido' else ' pedidos' end || ' · ' || avisos.brl(h_v)
    || E'\nSemana (desde segunda ' || to_char(ini_semana, 'DD/MM') || '): ' || s_n
      || case when s_n = 1 then ' pedido' else ' pedidos' end || ' · ' || avisos.brl(s_v)
    || E'\nMês (' || meses[extract(month from hoje)::int] || '): ' || m_n
      || case when m_n = 1 then ' pedido' else ' pedidos' end || ' · ' || avisos.brl(m_v)
      || case when m_n > 0 then ' · ticket ' || avisos.brl(round(m_v / m_n, 2)) else '' end
    || E'\n\nCarrinhos abandonados hoje: ' || carrinhos;
  perform avisos.telegram(texto);
  return texto;
end $$;

-- Agenda. 02h UTC é 23h em Brasília (sem horário de verão).
select cron.schedule('annis-avisos', '*/2 * * * *', 'select avisos.checar()');
select cron.schedule('annis-fechamento', '0 2 * * *', 'select avisos.fechamento()');
