-- Régua pós-compra: o que as páginas do site leem e gravam, 13/10/2026.
--
--   annis.store/pages/pix?p=<token>          QR e copia e cola do Pix (e-mail 1)
--   annis.store/pages/rastreio?c=<código>    andamento do pacote, sem captcha (e-mails 6 e 7)
--   annis.store/pages/sua-opiniao?p=<token>  caimento, nota e comentário (e-mail 9)
--   annis.store/pages/ir?p=<token>           todo link dos e-mails: registra o clique e
--                                            redireciona (só para destino gravado por nós)
--
-- As tabelas ficam no esquema avisos, fora da API pública. O site, com a chave
-- publicável, só chama as funções do esquema site, e cada uma devolve só o
-- necessário para a página:
--   - pix e pesquisa só abrem com o token do e-mail (aleatório, criado no
--     envio); a página do Pix nunca mostra um código que não seja nosso, para
--     ninguém usar o endereço da loja com um Pix de golpe;
--   - rastreio só consulta código de etiqueta emitida por nós (o portal usa
--     avisos.rastro, sem essa trava, pela conexão do banco)
--     (avisos.enviados), e guarda a resposta por 10 minutos.
--
-- Credenciais dos Correios no cofre (annis_correios_*), gravadas por
-- avisos/regua/instalar.py a partir do .env. Nada de segredo aqui.

create table if not exists avisos.regua_links (
  token  text primary key,
  pedido text not null,
  tipo   text not null check (tipo in ('pix', 'pesquisa')),
  dados  jsonb not null default '{}',
  criado timestamptz not null default now()
);

-- Link de e-mail (tipo 'link'): o destino fica gravado; a página /pages/ir só
-- redireciona para ele. Abertura não é medida (o Mail do iPhone abre tudo sozinho).
alter table avisos.regua_links drop constraint if exists regua_links_tipo_check;
alter table avisos.regua_links add constraint regua_links_tipo_check check (tipo in ('pix', 'pesquisa', 'link'));

create table if not exists avisos.regua_cliques (
  id     bigserial primary key,
  token  text not null references avisos.regua_links (token),
  pedido text not null,
  email  text,
  botao  text,
  em     timestamptz not null default now()
);

create table if not exists avisos.regua_respostas (
  token      text primary key references avisos.regua_links (token),
  pedido     text not null,
  tamanho    text,
  nota       int,
  comentario text,
  criado     timestamptz not null default now(),
  atualizado timestamptz not null default now()
);

-- Nota de cada pedido, guardada por avisos/danfe.py, para ir anexada no e-mail 6.
create table if not exists avisos.regua_notas (
  pedido text primary key,
  numero text not null,
  pdf    bytea,
  xml    text,
  criado timestamptz not null default now()
);

create or replace function avisos.correios_token() returns text
language plpgsql security definer set search_path = extensions, public as $$
declare t text; e timestamptz; r http_response;
begin
  select valor, expira into t, e from avisos.estado where chave = 'correios_token';
  if t is not null and e > now() then
    return t;
  end if;
  perform http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS', '10000');
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '20000');
  r := http((
    'POST', 'https://api.correios.com.br/token/v1/autentica/cartaopostagem',
    array[http_header('Authorization', 'Basic ' || replace(encode(convert_to(
      avisos.segredo('correios_usuario') || ':' || avisos.segredo('correios_codigo'), 'UTF8'), 'base64'), E'\n', ''))],
    'application/json',
    jsonb_build_object('numero', avisos.segredo('correios_cartao'), 'contrato', '9912705946')::text
  )::http_request);
  if r.status not in (200, 201) then
    raise exception 'token dos Correios: HTTP %', r.status;
  end if;
  t := r.content::jsonb ->> 'token';
  -- O token vale um dia; renova bem antes.
  insert into avisos.estado (chave, valor, expira, em) values ('correios_token', t, now() + interval '6 hours', now())
  on conflict (chave) do update set valor = excluded.valor, expira = excluded.expira, em = now();
  return t;
end $$;

create schema if not exists site;
grant usage on schema site to anon;

-- Página do Pix
create or replace function site.pix(p text) returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('pedido', l.pedido, 'codigo', l.dados ->> 'codigo', 'valor', l.dados ->> 'valor',
                            'expira', l.dados ->> 'expira')
  from avisos.regua_links l where l.token = p and l.tipo = 'pix'
$$;

-- Página da pesquisa: a peça que ela está avaliando e o que já respondeu
create or replace function site.pesquisa(p text) returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('pedido', l.pedido, 'nome', l.dados ->> 'nome', 'peca', l.dados ->> 'peca',
                            'peca_nome', l.dados ->> 'peca_nome', 'img', l.dados ->> 'img',
                            'o_seu', l.dados ->> 'o_seu', 'roupa', coalesce((l.dados ->> 'roupa')::boolean, true),
                            'tamanho', r.tamanho, 'nota', r.nota, 'comentou', r.comentario is not null)
  from avisos.regua_links l left join avisos.regua_respostas r on r.token = l.token
  where l.token = p and l.tipo = 'pesquisa'
$$;

create or replace function site.responder(p text, tamanho text default null, nota int default null,
                                          comentario text default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
#variable_conflict use_variable
declare l avisos.regua_links;
begin
  select * into l from avisos.regua_links x where x.token = p and x.tipo = 'pesquisa';
  if not found then
    return jsonb_build_object('ok', false);
  end if;
  if tamanho is not null and tamanho not in ('pequeno', 'certo', 'grande') then
    return jsonb_build_object('ok', false);
  end if;
  if nota is not null and (nota < 0 or nota > 10) then
    return jsonb_build_object('ok', false);
  end if;
  comentario := nullif(left(btrim(comentario), 2000), '');
  insert into avisos.regua_respostas as r (token, pedido, tamanho, nota, comentario)
  values (p, l.pedido, tamanho, nota, comentario)
  on conflict (token) do update set
    tamanho = coalesce(excluded.tamanho, r.tamanho),
    nota = coalesce(excluded.nota, r.nota),
    comentario = coalesce(excluded.comentario, r.comentario),
    atualizado = now();
  -- Comentário e nota baixa (0 a 6) avisam no grupo; falha no Telegram não perde a resposta.
  if comentario is not null or (nota is not null and nota <= 6) then
    begin
      perform avisos.telegram('Pesquisa do pedido ' || l.pedido
        || coalesce(' · nota ' || nota, '') || coalesce(E'\n' || comentario, ''));
    exception when others then
      null;
    end;
  end if;
  return jsonb_build_object('ok', true);
end $$;

-- Página /pages/ir: registra o clique e devolve o destino.
create or replace function site.ir(p text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare l avisos.regua_links;
begin
  select * into l from avisos.regua_links x where x.token = p and x.tipo = 'link';
  if not found then
    return null;
  end if;
  insert into avisos.regua_cliques (token, pedido, email, botao)
  values (p, l.pedido, l.dados ->> 'email', l.dados ->> 'botao');
  return jsonb_build_object('destino', l.dados ->> 'destino');
end $$;

-- Rastreio de qualquer código, direto dos Correios, guardado por 10 minutos.
-- Fora da API pública: quem chama é a página do site (abaixo, só com etiqueta
-- nossa) e a aba Rastreio do portal, pela conexão do banco.
create or replace function avisos.rastro(c text) returns jsonb
language plpgsql security definer set search_path = extensions, public as $$
declare cod text := upper(btrim(c)); o jsonb; r http_response; j jsonb;
begin
  if cod !~ '^[A-Z]{2}[0-9]{9}[A-Z]{2}$' then
    return jsonb_build_object('ok', false, 'motivo', 'codigo');
  end if;
  select valor::jsonb into o from avisos.estado where chave = 'rastro:' || cod and expira > now();
  if o is not null then
    return o;
  end if;
  perform http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS', '10000');
  perform http_set_curlopt('CURLOPT_TIMEOUT_MS', '20000');
  r := http((
    'GET', 'https://api.correios.com.br/srorastro/v1/objetos/' || cod || '?resultado=T',
    array[http_header('Authorization', 'Bearer ' || avisos.correios_token())], null, null
  )::http_request);
  if r.status <> 200 then
    return jsonb_build_object('ok', false, 'motivo', 'correios');
  end if;
  j := r.content::jsonb -> 'objetos' -> 0;
  o := jsonb_build_object(
    'ok', true, 'codigo', cod, 'previsao', j ->> 'dtPrevista', 'consultado', now(),
    'eventos', coalesce((
      select jsonb_agg(jsonb_build_object(
        'quando', e ->> 'dtHrCriado', 'descricao', e ->> 'descricao', 'detalhe', e ->> 'detalhe',
        'cidade', e -> 'unidade' -> 'endereco' ->> 'cidade', 'uf', e -> 'unidade' -> 'endereco' ->> 'uf',
        'para_cidade', e -> 'unidadeDestino' -> 'endereco' ->> 'cidade',
        'para_uf', e -> 'unidadeDestino' -> 'endereco' ->> 'uf') order by ord)
      from jsonb_array_elements(j -> 'eventos') with ordinality as ev(e, ord)), '[]'::jsonb));
  insert into avisos.estado (chave, valor, expira, em) values ('rastro:' || cod, o::text, now() + interval '10 minutes', now())
  on conflict (chave) do update set valor = excluded.valor, expira = excluded.expira, em = now();
  return o;
exception when others then
  return jsonb_build_object('ok', false, 'motivo', 'erro');
end $$;

-- Página do rastreio
create or replace function site.rastreio(c text) returns jsonb
language plpgsql security definer set search_path = extensions, public as $$
declare cod text := upper(btrim(c));
begin
  if cod !~ '^[A-Z]{2}[0-9]{9}[A-Z]{2}$' then
    return jsonb_build_object('ok', false, 'motivo', 'codigo');
  end if;
  if not exists (select 1 from avisos.enviados where id like 'etq:%' and split_part(tipo, ' ', 2) = cod) then
    return jsonb_build_object('ok', false, 'motivo', 'desconhecido');
  end if;
  return avisos.rastro(cod);
end $$;

-- Funções nascem executáveis por qualquer um; aqui só o site (anon) e o banco.
revoke all on function site.pix(text), site.pesquisa(text), site.responder(text, text, int, text), site.rastreio(text), site.ir(text) from public;
grant execute on function site.pix(text), site.pesquisa(text), site.responder(text, text, int, text), site.rastreio(text), site.ir(text) to anon;
revoke all on function avisos.correios_token(), avisos.rastro(text) from public;

-- Motor da régua (workflow regua.yml), a cada 10 minutos. Agenda, rodada uma vez:
--   select cron.schedule('annis-regua', '*/10 * * * *', 'select avisos.disparar_regua()'); Erro ao disparar
-- vai para avisos.anota_erro, como os outros disparos.
create or replace function avisos.disparar_regua() returns void
language plpgsql security definer as $$
begin
  perform avisos.disparar_workflow('regua.yml', null);
exception when others then
  perform avisos.anota_erro('regua: ' || sqlerrm);
end $$;
