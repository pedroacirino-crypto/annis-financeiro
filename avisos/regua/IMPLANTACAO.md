# Régua pós-compra: checklist de implantação (13/10/2026)

Legenda: [x] feito · [ ] falta · (você) depende do Pedro

## 1. Já pronto
- [x] Domínio: apelido pedidos@annis.store, DKIM, DMARC, domínio na GoDaddy da Annis
- [x] Envio pelo Gmail: senha de app da contato@, pedidos@ em "Enviar e-mail como" (avisos/gmail.py)
- [x] Modelos dos e-mails 0 a 9 (blocos.py, modelos.py), textos em textos.yaml, prévia (previa.py)
- [x] Regras: plural, misto, acessório, tamanho só para uma peça de roupa, cuidados do campo Manutenção
- [x] Backtest com 7 pedidos reais e 3 simulados (Code/ANNIS/regua-backtest, fora do Git)
- [x] Banco das páginas: regua.sql (pix, pesquisa, responder, rastreio), instalado e testado

## 2. Páginas do site (tema Atelier)
- [x] templates/page.pix.liquid: QR, valor, validade, botão que copia; só abre com token do e-mail
- [x] templates/page.rastreio.liquid: eventos dos Correios, previsão, link para o site dos Correios
- [x] templates/page.sua-opiniao.liquid: grava o toque do e-mail, completa a outra pergunta, comentário
- [x] Criar as 3 páginas no admin (visíveis, fora do menu, ocultas da busca)
- [x] Comentário e nota de 0 a 6 avisam no Telegram
- [x] Testar as 3 no computador e no celular com tokens de teste
- [x] templates/page.ir.liquid + página "Ir" (14/10): todo link dos e-mails passa por ela; grava o clique em avisos.regua_cliques e redireciona

## 3. Motor de envio (avisos/regua/motor.py)
- [x] Ler pedidos recentes da Shopify e decidir o que vence agora (mesma lógica do backtest)
- [x] Só pedidos criados depois da entrada no ar (REGUA_INICIO): ninguém antigo recebe nada
- [x] Anotar cada envio em avisos.enviados (regua:e2:1160), nunca repetir
- [-] Mesmo fio: o Gmail só junta e-mails de mesmo assunto; fica sem (respostas chegam na contato@ do mesmo jeito)
- [x] Cópia oculta para a contato@ (filtro no Gmail: marcador Régua/Enviados)
- [x] E-mail 1: Pix da Pagar.me + token da página do Pix; não sai se o pedido já foi pago
- [x] E-mails 3, 4, 5, 9 só em horário comercial (9h a 20h)
- [x] E-mail 4 no Telegram (danfe.mensagem)
- [x] Rastreio dos pacotes em trânsito no máximo 1 vez por hora por código
- [x] E-mail 6: anexar a DANFE em PDF (guardar o PDF quando danfe.py gerar) e o XML se a Olist der
- [x] E-mail 7: atrasada, tentativa, retirada (+ aviso no Telegram); devolução só Telegram
- [x] E-mail 9: token da pesquisa
- [x] Pedido cancelado ou reembolsado: para tudo
- [x] Erro em um pedido não trava os outros; erro vai para o Telegram
- [x] Cliques: cada link vira annis.store/pages/ir?p=<token> (regua_links tipo link); abertura não é medida
- [x] Modo sombra por variável (REGUA_MODO=sombra manda só para contato@), padrão: no ar

## 4. Rodar sozinho (NO AR desde 13/10/2026 22h26; sombra de 22h02 a 22h26)
- [x] Workflow .github/workflows/regua.yml
- [x] Segredos no GitHub: GMAIL_SENHA_APP, PAGARME_SECRET_KEY (os outros já existem)
- [x] Supabase: pg_cron a cada 10 minutos disparando o regua.yml
- [ ] Resumo da régua no fechamento das 23h no Telegram (depois)
- [x] Virada: REGUA_MODO=no_ar, REGUA_INICIO=2026-10-13T22:26:33-03:00

## 5. Shopify (virada em 13/10/2026)
- [x] E-mail 0: shopify-confirmacao-pedido.liquid em "Confirmação de pedido", assunto "Recebemos seu pedido {{ order_name }}"
- [x] Remetente da Shopify: pedidos@annis.store, verificado; 6 CNAMEs da Shopify na GoDaddy (Domain Connect)
- [x] "Saiu para entrega" e "Entregue" desligados; "Confirmação de envio" só sai com notificar cliente
- [x] "Pagamento pendente realizado": shopify-pagamento-realizado.liquid, assunto "Pagamento do pedido {{ order_name }} confirmado"
- [ ] (você) Baixa manual no app: sempre desmarcar "notificar cliente"

## 6. Depois de ligar
- [ ] Acompanhar o CCO nos primeiros pedidos
- [ ] Rodar o backtest de novo na semana que vem (#1134, #1139, 9 do #1132)
- [x] Botão do rastreio: Correios (Pedro, 13/10: se os Correios falharem, não associam à Annis)
