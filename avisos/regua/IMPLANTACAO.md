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
- [ ] templates/page.pix.liquid: QR, valor, validade, botão que copia; só abre com token do e-mail
- [ ] templates/page.rastreio.liquid: eventos dos Correios, previsão, link para o site dos Correios
- [ ] templates/page.sua-opiniao.liquid: grava o toque do e-mail, completa a outra pergunta, comentário
- [ ] Criar as 3 páginas no admin (visíveis, fora do menu, ocultas da busca)
- [ ] Comentário e nota de 0 a 6 avisam no Telegram
- [ ] Testar as 3 no computador e no celular com tokens de teste

## 3. Motor de envio (avisos/regua/motor.py)
- [ ] Ler pedidos recentes da Shopify e decidir o que vence agora (mesma lógica do backtest)
- [ ] Só pedidos criados depois da entrada no ar (REGUA_INICIO): ninguém antigo recebe nada
- [ ] Anotar cada envio em avisos.enviados (regua:e2:1160), nunca repetir
- [ ] Mesmo fio: todos os e-mails do pedido na mesma conversa (In-Reply-To do primeiro)
- [ ] CCO do Pedro em todos os envios
- [ ] E-mail 1: Pix da Pagar.me + token da página do Pix; não sai se o pedido já foi pago
- [ ] E-mails 3, 4, 5, 9 só em horário comercial (9h a 20h)
- [ ] E-mail 4 no Telegram (danfe.mensagem)
- [ ] Rastreio dos pacotes em trânsito no máximo 1 vez por hora por código
- [ ] E-mail 6: anexar a DANFE em PDF (guardar o PDF quando danfe.py gerar) e o XML se a Olist der
- [ ] E-mail 7: atrasada, tentativa, retirada (+ aviso no Telegram); devolução só Telegram
- [ ] E-mail 9: token da pesquisa
- [ ] Pedido cancelado ou reembolsado: para tudo
- [ ] Erro em um pedido não trava os outros; erro vai para o Telegram
- [ ] Modo sombra por variável (REGUA_MODO=sombra manda só para contato@), padrão: no ar

## 4. Rodar sozinho
- [ ] Workflow .github/workflows/regua.yml
- [ ] Segredos no GitHub: GMAIL_SENHA_APP, PAGARME_SECRET_KEY (os outros já existem)
- [ ] Supabase: pg_cron a cada 10 minutos disparando o regua.yml
- [ ] Resumo da régua no fechamento das 23h no Telegram

## 5. Shopify (no dia da virada)
- [ ] E-mail 0: HTML no modelo "Confirmação do pedido" (Liquid, versão pago e pendente)
- [ ] Remetente da Shopify: conferir se dá para sair como pedidos@ (domínio autenticado na Shopify)
- [ ] Desligar "Confirmação de envio", "Saiu para entrega" e "Entregue" da Shopify
- [ ] (você) Baixa manual no app: sempre desmarcar "notificar cliente"

## 6. Depois de ligar
- [ ] Acompanhar o CCO nos primeiros pedidos
- [ ] Rodar o backtest de novo na semana que vem (#1134, #1139, 9 do #1132)
- [ ] Mandar ao design os textos novos: 0 pago, 5 pronta entrega, 2 misto, 7 tentativa, 7 retirada
- [ ] Botão do rastreio: Correios (hoje) ou a página sem captcha (decisão do Pedro)
