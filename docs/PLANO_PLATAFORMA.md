# Luna Vox Studio: auditoria e plano da plataforma

21/10/2026. Auditoria feita só por leitura do repositório, do deploy e da documentação oficial. Nenhuma alteração em produção.

## 1. Arquitetura existente

| Parte | Hoje | Onde |
|---|---|---|
| Frontend | React 18 + Vite + TypeScript (não é Next.js) | `frontend/src` |
| Painel | Luna Vox Studio no modo manual (do mockup), na produção desde 21/10 | `frontend/src/studio/` |
| Dados do painel | Pasta no computador do usuário (File System Access API): `conteudos/` + `luna-vox.json` | `studio/localFolder.ts`, `studio/store.tsx` |
| Funções de servidor | 11 funções da Vercel (Node, `api/*.ts`) | `frontend/api` |
| Login | Conta única: cookie HttpOnly assinado (HMAC), hash scrypt da senha, limite de tentativas e revogação de sessões no Upstash Redis | `api/_auth.ts`, `_password.ts`, `_ratelimit.ts`, `_store.ts` |
| Banco | Não há. Só Upstash Redis (chave/valor) para login e Telegram | `api/_store.ts` |
| Telegram | Bot de avisos para o celular do dono (token no Redis) | `api/telegram.ts`, `api/_telegram.ts` |
| Estúdio antigo (GPU) | Backend FastAPI no pod RunPod + ComfyUI. Desligado no site (`VITE_STUDIO_MODE=manual`); o código continua no repositório | `backend/`, `api/runpod-*`, `workflows/` |
| Deploy | Vercel, projeto `luna-ai-studio-frontend`, push na `main` vai para produção. CI no GitHub Actions (pytest + build) | `.github/workflows/ci.yml` |
| Tentativa de biblioteca na nuvem | Supabase (tabelas `media` e `audit_log`, bucket privado, envio por URL assinada). Testada com Supabase falso, **sem merge** | branch `feature/biblioteca` |

## 2. O que dá para reaproveitar

- **Login inteiro**, sem mudança: é seguro para um administrador só. Vale migrar para Supabase Auth só quando houver equipe com papéis diferentes.
- **Telas do mockup** (`frontend/src/studio/`): troca só a fonte dos dados (pasta → banco).
- **Pasta local**: vira **"Importar da pasta"**, que leva para a nuvem os arquivos e os agendamentos já feitos.
- **Da branch `feature/biblioteca`:** o cliente Supabase do servidor, o envio direto por URL assinada, a migração com RLS e o bucket privado.
- **Telegram:** a função `telegram()` e a configuração do token servem de base para o bot de vendas.
- **CI** e o deploy pela `main`.

## 3. Dependências do RunPod e do backend antigo

- **No painel atual: nenhuma.** O login não liga GPU, e o modo manual não chama `/api/runpod-*`.
- **Ainda no repositório, sem uso em produção:**
  - funções `runpod-status`, `runpod-wake`, `runpod-stop`, `runpod-bootstrap`;
  - `_runpod.ts`, `_s3.ts`, `_external.ts`, `notify.ts`;
  - `backend/` inteiro, `workflows/`, `comfyui_nodes/`.
- **Variáveis da Vercel usadas só por eles:** `RUNPOD_API_KEY`, `RUNPOD_S3_*`, `RUNPOD_SSH_*`, `LUNA_MAX_GPU_PRICE`, `LUNA_BACKEND_URL`.
- **Proposta:** remover tudo isso numa limpeza separada, **só com sua autorização**. Até lá fica como está.

## 4. Login e banco

- **Login:** funciona e independe do RunPod. Proposta: manter. As funções novas exigem a mesma sessão (`isAuthenticated`).
- **Banco:** não existe. Precisa de um projeto Supabase criado por você. Eu não crio contas nem digito chaves.

## 5. Regras das plataformas (documentação oficial consultada em 21/10/2026)

**Telegram** (Bot API 10.3, payments-stars, termos para desenvolvedores):
- Bens e conteúdos digitais vendidos por bot: **somente Telegram Stars (XTR)**.
- **Fluxo de pagamento:** `pre_checkout_query` respondido em até 10 s. O conteúdo só é entregue depois do `successful_payment`, guardando o `telegram_payment_charge_id`.
- **Assinatura:** `createInvoiceLink` com `subscription_period` fixo de 30 dias e até 10.000 Stars.
  - A renovação chega como pagamento com `is_recurring` e `subscription_expiration_date`.
  - O cancelamento é feito com `editUserStarSubscription`.
- **Reembolso:** `refundStarPayment`. Disputas são responsabilidade do vendedor.
- **Obrigatório:** comando `/paysupport` e uma política de privacidade acessível.
- **Proibido:** spam e mensagens não solicitadas. Também é proibido coletar dados além do necessário.
- **Mídia paga:** `sendPaidMedia`, de 1 a 25.000 Stars, até 10 itens.
- **Webhook:** validado pelo cabeçalho `X-Telegram-Bot-Api-Secret-Token`.

**Instagram** (Content Publishing):
- **Conta:** precisa de conta **profissional**. Duas formas de login:
  - Instagram Login: `instagram_business_basic` + `instagram_business_content_publish`;
  - Facebook Login: com Página.
- **Acesso:** Advanced Access exige revisão do app pela Meta.
- **Publicação:** a mídia precisa estar em **URL pública** (a Meta busca o arquivo).
  - Imagens: **só JPEG**.
  - Reels, Stories e carrossel de até 20 itens.
- **Limite:** 100 posts por API em 24 h.
- **Sem agendamento nativo:** o nosso sistema precisa publicar na hora certa.
  - O container expira em 24 h.
  - O status é consultado com `status_code` (IN_PROGRESS / FINISHED / PUBLISHED / ERROR / EXPIRED).
- **Conteúdo:** as regras da Meta proíbem nudez e serviços sexuais. O conteúdo promocional precisa seguir isso, e o sistema deve barrar mídia marcada como adulta para Instagram e TikTok.

**TikTok** (Content Posting API):
- **Escopo:** `video.publish`.
- **Sem auditoria do app, tudo é publicado como privado:** a auditoria é obrigatória para posts públicos.
- **Antes de cada post:** é obrigatório consultar `creator_info` (privacidades permitidas, duração máxima).
- **Envio por URL:** só de domínio verificado. A alternativa é enviar o arquivo direto.

**Vercel:**
- **O plano Hobby (grátis) é só para uso pessoal não comercial:** qualquer cobrança ou venda exige o Pro.
- **Agendamento no Hobby:** cron 1x por dia, com ±59 min de imprecisão. Não serve para publicar na hora.
- **Duração máxima de função:** 300 s.

**Supabase:**
- **Planos:**
  - Free: 500 MB de banco, 1 GB de arquivos, arquivo de até 50 MB, **pausa após 1 semana sem uso**.
  - Pro: US$ 25/mês, 100 GB, sem pausa.
- **Supabase Cron** (pg_cron) roda até a cada segundo e chama endpoints HTTP.

## 6. Decisões do usuário (21/10/2026) e o que elas implicam

- **As imagens e os vídeos ficam só no computador.** O banco guarda só os dados sobre eles, com `local_path` apontando para o arquivo dentro de `conteudos/`.
- **Supabase para o resto:** dados, publicações, bot e financeiro.
- **Só planos grátis.**

O que isso significa:

| Item do pedido | Com as imagens só no computador e plano grátis |
|---|---|
| Biblioteca, calendário, aprovação, histórico | Funciona. A pasta precisa estar aberta no Chrome/Edge do computador para ver as miniaturas |
| Publicar no Instagram/TikTok com o PC desligado | **Não é possível**: as duas APIs buscam o arquivo num servidor. Fica o **fluxo manual** previsto no pedido: o estúdio prepara, aprova e avisa no horário (Telegram); você publica pelo app da rede e marca como publicado |
| Bot do Telegram entregando packs | Possível sem guardar arquivos num servidor nosso. Você envia os arquivos do pack para o bot uma vez (pelo Telegram do PC); o Telegram guarda e o bot entrega pelo `file_id`. Os arquivos ficam nos servidores do Telegram, o que é inevitável para entregar a quem compra |
| Vendas e assinaturas (Fase 4) | **Bloqueadas no Vercel Hobby**, que proíbe uso comercial. Antes da Fase 4: Vercel Pro (US$ 20/mês) ou mover a parte de vendas para um serviço grátis que permita uso comercial. Decidir antes de começar |
| Supabase Free | Pausa após 1 semana sem uso (reativa pelo painel). Limite de 500 MB de banco, suficiente para os dados (sem arquivos) |

## 7. Mudanças necessárias

1. **Arquivos no computador; dados no Supabase.** Feito na Fase 1: os dados e o histórico ficam no banco, e o `luna-vox.json` vira uma cópia local de segurança.
2. **Banco Supabase Postgres** com as tabelas do pedido. Ver o item 8.
3. **Fila persistente no banco** (`scheduled_jobs`) com tentativas, trava por job e chave de idempotência.
   - **Disparo:** Supabase Cron chama a cada minuto uma função da Vercel protegida por segredo.
   - **Execução:** a função publica, registra a tentativa e agenda a próxima se falhar.
   - Nada depende do navegador. Não fica nenhum processo permanente na Vercel.
4. **Bot do Telegram por webhook**, numa função da Vercel. Toda venda e assinatura é confirmada pelo evento do Telegram, nunca pela palavra do usuário.
5. **Aprovação ligada à versão:** cada post tem `version`. Mudar mídia, legenda ou destino aumenta a versão e invalida a aprovação.
6. **Não migrar para Next.js.** A stack atual (Vite + funções da Vercel) atende, e reescrever não traz ganho.

## 8. Modelo de dados

Uma migração por fase, sempre com:
- chaves estrangeiras, índices e `created_at`/`updated_at`;
- `CHECK` nos estados;
- RLS ligado sem políticas, de modo que só a chave secreta do servidor acessa;
- tokens das redes cifrados no servidor (AES-GCM com chave na Vercel), nunca devolvidos ao navegador.

| Fase | Tabelas |
|---|---|
| 1 | `characters`, `campaigns`, `media_assets`, `media_usage` (histórico de uso), `audit_logs`. Sem bucket: os arquivos ficam no computador |
| 2 | `social_accounts`, `platform_connections`, `posts`, `post_destinations`, `approvals`, `scheduled_jobs`, `publishing_attempts` |
| 3 | `telegram_users`, `telegram_products` (+ itens do pack), `message_templates`, `message_logs` |
| 4 | `orders`, `payment_transactions` (único por `telegram_payment_charge_id`), `telegram_subscriptions`, `access_grants`, `refunds` |
| 5 | `revenue_records` (ligado à transação, sem duplicidade), `expense_records` |

`users` fica para quando houver equipe; hoje o administrador é o login único.

## 9. Custos e serviços externos

| Serviço | Para quê | Custo |
|---|---|---|
| Vercel Pro | **Obrigatório quando houver vendas** (o Hobby proíbe uso comercial); cron por minuto | US$ 20/mês por membro |
| Supabase | Banco, arquivos privados, cron | Free para começar (com pausa e limite de 50 MB por arquivo). **Pro, US$ 25/mês, recomendado quando entrar dinheiro** |
| Meta (Instagram) | App no Meta for Developers, conta profissional, revisão do app | sem custo, depende de aprovação |
| TikTok | App no TikTok for Developers, auditoria para posts públicos | sem custo, depende de aprovação |
| Telegram | Bot (@BotFather), Stars | sem custo fixo; o Telegram fica com a parte dele na conversão das Stars |

## 10. Plano por fases (cada uma com entrega verificável)

| Fase | Entrega | Como verificar |
|---|---|---|
| **1. Fundação** | Supabase (migração 0001), biblioteca na nuvem com todos os campos pedidos, importação da pasta local, painel com números do banco, histórico de auditoria | Testes do servidor contra um Postgres real (PGlite) + Supabase falso; teste de ponta a ponta no navegador; depois, com o seu projeto Supabase, um envio real |
| 2. Redes sociais | Posts com versão e aprovação, fila com cron, conectores OAuth Instagram/TikTok, fluxo manual quando a API não permitir | Testes da fila (duplicidade, tentativas, trava); publicação real só depois de os apps serem aprovados |
| 3. Telegram | Bot por webhook, menu, catálogo de packs, prévias separadas, mensagens com opt-out, `/paysupport`, política | Bot de teste no Telegram |
| 4. Monetização | Stars: venda avulsa e assinatura de 30 dias, entrega só após `successful_payment`, reembolso, membros | Teste de pagamento real com poucas Stars |
| 5. Financeiro | Receitas confirmadas × pendentes, despesas, taxas, conciliação, CSV | Testes de conciliação |
| 6. Testes e expansão | Segurança, falhas, permissões, documentação | Checklist |

## 11. Riscos

- **Conteúdo adulto:**
  - Instagram e TikTok proíbem. O sistema bloqueia o envio de mídia classificada como adulta para essas redes.
  - No Telegram, valem os Termos de Serviço do Telegram, e o bot deve deixar claro que é só para maiores de 18 anos.
  - Toda imagem precisa respeitar consentimento e direito de imagem. Isso inclui fotos de pessoas reais usadas como base.
- **Aprovação das redes:** a Meta pode negar a revisão do app, e o TikTok pode negar a auditoria. Nesse caso fica o fluxo manual: o sistema prepara tudo e avisa no horário.
- **Arquivos pagos na nuvem:** ficam num bucket privado, acessados só por links que vencem.
- **Supabase Free pausa sem uso:** para produção com vendas, o Pro é recomendado.

## 12. Fase 1 entregue: como ligar o banco

**O que a Fase 1 entrega**

- **Biblioteca com todos os campos pedidos:** nome, descrição, personagem, categoria, campanha, legenda, hashtags, destinos permitidos, classificação (livre, sensível ou adulto), público (promocional ou assinantes), status, data e histórico de uso.
- **Filtros e busca:** por personagem, campanha e público. A busca cobre título, descrição, legenda, hashtags e categoria.
- **Regra do conteúdo adulto:** é recusado no Instagram e no TikTok em três camadas, a tela, a API e uma constraint no banco.
- **Personagens e campanhas:** criação e edição. Histórico de operações em Configurações.
- **Painel:** aguardando aprovação, publicações de hoje, publicados no mês e estado das conexões.
- **Sem o Supabase configurado, nada muda:** tudo continua no `luna-vox.json`, como hoje.
- **Com ele:** o estúdio importa sozinho os dados do json, as personagens e as campanhas, e passa a gravar no banco.

**Passo a passo para ligar o banco** (você faz; as chaves nunca passam por mim):

1. Em supabase.com, crie um projeto. Região sugerida: São Paulo. Plano Free.
2. No SQL Editor, cole o conteúdo de `supabase/migrations/0001_fundacao.sql` e clique em Run. Pode rodar de novo sem problema.
3. Em Project Settings → API Keys, copie a **Project URL** e uma **Secret key** (`sb_secret_...`).
4. Na Vercel, abra Settings → Environment Variables e cadastre:
   - `SUPABASE_URL`
   - `SUPABASE_SECRET_KEY`
   Sem o prefixo `VITE_`, para a chave nunca ir para o navegador.
5. Faça um novo deploy. Depois, abra o estúdio e reconecte a pasta. A importação acontece sozinha, e Configurações passa a mostrar "Banco de dados: Conectado".

**Como desfazer:** retire as duas variáveis da Vercel e faça um novo deploy. O estúdio volta a usar o `luna-vox.json`, que é mantido como cópia dos dados.
