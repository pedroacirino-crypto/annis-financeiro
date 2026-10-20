// Annis, 20/10/2026. Pixel próprio de navegação: Shopify > Configurações >
// Eventos de clientes > "Rastreio Annis" (pixel personalizado). Cola-se este
// arquivo inteiro lá. Ele recebe os eventos da loja e do checkout (o tema não
// enxerga o checkout) e grava cada um em site.eventos no Supabase, com a chave
// pública do site, que só consegue inserir (site/eventos.sql).
//
// Sem dado pessoal: nada de nome, e-mail, telefone, CPF ou endereço. A compra
// leva só o id do pedido, que o portal troca pelo número.

const BANCO = 'https://mkgrmljousyvggpxmofj.supabase.co/rest/v1/eventos';
const CHAVE = 'sb_publishable_KMXewGoRe3rCgXEhknGzdQ_pP09F1YW';
const DA_LOJA = /(^|\.)annis\.store$|myshopify\.com$|shopify\.com$|shop\.app$/;

function corta(t, n) {
  return t == null ? null : String(t).slice(0, n);
}

function aparelho(ev) {
  const ua = (ev.context && ev.context.navigator && ev.context.navigator.userAgent) || '';
  return /iPhone|iPad|Android|Mobile/i.test(ua) ? 'celular' : 'computador';
}

function origem(ev) {
  try {
    const ref = ev.context.document.referrer;
    if (!ref) return null;
    const host = new URL(ref).hostname.replace(/^www\./, '');
    return DA_LOJA.test(host) ? null : host;   // navegação interna não é origem
  } catch (e) {
    return null;
  }
}

function grava(ev, nome, campos) {
  try {
    const loc = (ev.context && ev.context.document && ev.context.document.location) || {};
    fetch(BANCO, {
      method: 'POST',
      keepalive: true,
      headers: { apikey: CHAVE, 'Content-Profile': 'site',
                 'Content-Type': 'application/json', Prefer: 'return=minimal' },
      body: JSON.stringify(Object.assign({
        visitante: corta(ev.clientId, 80),
        evento: nome,
        pagina: corta(loc.pathname, 300),
        busca: corta(loc.search, 500),
        origem: corta(origem(ev), 120),
        aparelho: aparelho(ev),
      }, campos || {})),
    }).catch(function () {});
  } catch (e) {}
}

function variante(v) {
  if (!v) return {};
  return {
    produto: corta(v.product && v.product.title, 160),
    variante: corta(v.title, 80),
    valor: v.price ? Number(v.price.amount) : null,
  };
}

function totalCheckout(ev) {
  const c = ev.data && ev.data.checkout;
  return c && c.totalPrice ? Number(c.totalPrice.amount) : null;
}

analytics.subscribe('page_viewed', (ev) => grava(ev, 'page_viewed'));

analytics.subscribe('product_viewed', (ev) =>
  grava(ev, 'product_viewed', variante(ev.data.productVariant)));

analytics.subscribe('collection_viewed', (ev) =>
  grava(ev, 'collection_viewed', { extra: corta(ev.data.collection && ev.data.collection.title, 160) }));

analytics.subscribe('search_submitted', (ev) =>
  grava(ev, 'search_submitted', { extra: corta(ev.data.searchResult && ev.data.searchResult.query, 160) }));

['product_added_to_cart', 'product_removed_from_cart'].forEach((nome) =>
  analytics.subscribe(nome, (ev) => {
    const linha = ev.data.cartLine || {};
    grava(ev, nome, Object.assign(variante(linha.merchandise), {
      extra: linha.quantity != null ? 'qtd ' + linha.quantity : null,
    }));
  }));

analytics.subscribe('cart_viewed', (ev) => {
  const c = ev.data.cart;
  grava(ev, 'cart_viewed', {
    valor: c && c.cost && c.cost.totalAmount ? Number(c.cost.totalAmount.amount) : null,
    extra: c ? 'itens ' + (c.totalQuantity || 0) : null,
  });
});

['checkout_started', 'checkout_contact_info_submitted', 'checkout_address_info_submitted',
 'checkout_shipping_info_submitted', 'payment_info_submitted'].forEach((nome) =>
  analytics.subscribe(nome, (ev) => grava(ev, nome, { valor: totalCheckout(ev) })));

analytics.subscribe('checkout_completed', (ev) => {
  const c = ev.data.checkout || {};
  const id = c.order && c.order.id ? String(c.order.id).split('/').pop() : null;
  grava(ev, 'checkout_completed', { valor: totalCheckout(ev), pedido: corta(id, 20) });
});
