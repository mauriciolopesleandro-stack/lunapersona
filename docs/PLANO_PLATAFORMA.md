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

## 13. Fase 2 entregue: publicações com aprovação e lembretes

**Como funciona**

- Os estados são os do pedido: rascunho → aguardando aprovação → aprovado → agendado → publicando ("hora de publicar") → publicado. Há também falhou e cancelado.
- **A aprovação vale para uma versão exata.** Mudar mídia, legenda, hashtags, rede ou destinos cria a versão seguinte e devolve a publicação para aprovação. Mudar só o horário não.
  - O banco garante essa regra com uma constraint: nada fica aprovado, agendado ou publicado sem a aprovação da versão atual.
- **No horário**, o processador da fila (`/api/studio?r=jobs`):
  1. passa a publicação para "Hora de publicar";
  2. manda o aviso no Telegram, pelo bot já configurado em Meu perfil.
- **Se o Telegram falhar:**
  - o processador tenta de novo depois de 2, 4 e 8 minutos;
  - na 4ª tentativa, marca a falha e mostra um alerta no painel;
  - toda tentativa fica registrada.
- **Instagram e TikTok são publicados à mão** (as imagens ficam no computador). Você confirma cada destino (Feed, Reels...) com o link, se quiser. **Só vira "publicado" o que você confirmar.**
- **A fila tem trava** (`for update skip locked`) e uma chave de idempotência por publicação, versão e horário. Duas execuções nunca mandam o mesmo aviso.
- **As publicações antigas do `luna-vox.json` são importadas uma vez.** As agendadas voltam para aprovação; as publicadas ficam marcadas como "antes da Fase 2".

**Passo a passo para ligar** (depois da Fase 1 ligada):

1. No SQL Editor do Supabase, rode `supabase/migrations/0002_publicacoes.sql`.
2. Na Vercel, cadastre `JOBS_SECRET`, só para o servidor: um valor aleatório de pelo menos 32 caracteres. Depois faça um novo deploy.
3. No Supabase, em Database → Extensions, ative `pg_cron` e `pg_net`.
4. Em `supabase/cron/processar_fila.sql`, troque `COLE_AQUI_O_JOBS_SECRET` pelo mesmo valor e rode o arquivo no SQL Editor. O segredo fica no Vault, não no texto do agendamento.
5. Confira em Publicações: a linha "Lembretes automáticos" deve mostrar "última execução há 0–1 min (agendador do Supabase)".

**Se o plano Free não oferecer `pg_cron`/`pg_net`:** o botão "Processar agora", em Publicações, roda a fila na mão. Os lembretes não saem sozinhos com o computador desligado, e o painel avisa quando há publicação vencida sem execução da fila.

**Fica para depois:** publicação automática pela API do Instagram/TikTok. As tabelas `social_accounts` e `platform_connections` (com token cifrado) já existem, mas a automação depende de as mídias ficarem num servidor e de as redes aprovarem o app.

## 14. Publicação automática no Instagram (21/10/2026)

**Decisão do usuário:** as mídias de posts **aprovados** para publicação automática no Instagram podem ter uma cópia temporária num armazenamento privado. Essas mídias vão ficar públicas no Instagram de qualquer jeito. A cópia é apagada assim que o Instagram publica (ou quando o post é cancelado, falha ou muda de versão). Todo o resto continua só no computador.

**Como funciona**

- **Requisitos (fonte oficial: developers.facebook.com/docs/instagram-platform):**
  - API oficial com Instagram Login, versão v26.0;
  - conta profissional;
  - só a própria conta, então o Standard Access basta: **sem App Review e sem verificação de empresa**.
- **No compositor:** "Publicar automaticamente" aparece só para o Instagram. Trocar entre manual e automático cria uma nova versão, que precisa de nova aprovação.
- **Ao aprovar** ("Enviar mídias e aprovar"):
  - o navegador converte fotos para JPEG (exigência da API) e envia a cópia da **versão exata** em revisão, direto para o bucket privado `publicar-temp`;
  - o servidor só aprova se todas as cópias chegaram.
- **No horário**, o processador:
  - cria o contêiner de cada destino: Feed (foto, vídeo ou carrossel de até 20), Reels (1 vídeo) ou Stories (1 mídia), com um link assinado que vence;
  - espera o vídeo processar sem gastar tentativa;
  - publica e guarda o link do post;
  - apaga as cópias e avisa no Telegram.
- **Uma nova tentativa nunca publica duas vezes.** O identificador do contêiner fica guardado, e a nova tentativa consulta o status dele antes de publicar.
- **Erros:**
  - falha temporária: tenta de novo em 2, 4, 8 e 16 minutos, até 6 tentativas;
  - token inválido: falha na hora;
  - nos dois casos, quando falha de vez, avisa no Telegram para publicar à mão.
- **Token:**
  - o usuário gera o token no painel da Meta e cola no estúdio; ele vale 60 dias;
  - o estúdio guarda o token cifrado (AES-256-GCM, chave `TOKEN_ENCRYPTION_KEY`) e o renova sozinho toda semana;
  - o token nunca volta para o navegador.
- **TikTok:** ver a seção 15 (rascunho automático). O post manual continua com o botão "Copiar legenda e hashtags".

**Passo a passo para ligar** (com o banco e a fila da Fase 2 já ligados):

1. Rodar `supabase/migrations/0003_instagram_automatico.sql` no SQL Editor do Supabase.
2. Na Vercel, cadastrar `TOKEN_ENCRYPTION_KEY`, só para o servidor: 32 ou mais caracteres aleatórios. Fazer um novo deploy.
3. No Meta for Developers:
   - criar um app com o caso de uso de Instagram;
   - em "API do Instagram com login do Instagram" → "Gerar tokens de acesso", adicionar a conta e gerar o token.
4. No estúdio, abrir a página **Instagram** e colar o token.

## 15. Rascunho automático no TikTok (22/10/2026)

- **Como funciona:** o estúdio usa a Content Posting API oficial, no modo **upload** (escopo `video.upload`). No horário, o vídeo aprovado vai para a caixa de entrada do app do TikTok. O criador recebe a notificação, coloca a música em alta e publica no app. Esse modo não depende da auditoria do TikTok, que só é exigida para publicar direto.
- **Conexão:** botão "Conectar TikTok" (OAuth do Login Kit). O `state` vai num cookie HttpOnly que vale 10 minutos. Os tokens ficam cifrados (`TOKEN_ENCRYPTION_KEY`):
  - o acesso vale 24 h e é renovado sozinho antes de cada envio;
  - a conexão (refresh token) vale 365 dias.
  O botão Desconectar apaga os tokens.
- **Regras** (fotos adicionadas em 22/10: o TikTok baixa cada foto por um link assinado de 2 h no próprio site, `/api/studio?r=tiktok-media`, que é o endereço verificado no app; até 35 fotos JPEG de no máximo 4 MB, com a legenda junto):
  - automático com 1 vídeo MP4 ou MOV no destino "Vídeo", ou de 1 a 35 fotos no destino "Foto";
  - o site precisa estar verificado no app do TikTok (URL prefix, arquivo `tiktok....txt` em `frontend/public`);
  - Telegram não tem modo automático.
- **Fila** (tarefa `enviar_tiktok`):
  1. Envia o vídeo num pedaço só (até 64 MB; o bucket aceita até 50 MB) e guarda o `publish_id` no destino.
  2. Consulta o status a cada 1 minuto enquanto o TikTok processa, sem gastar tentativa.
  3. Quando o vídeo chega na caixa de entrada, apaga a cópia, avisa uma vez no Telegram e passa a consultar a cada 15 minutos, por até 72 horas.
  4. **Só marca publicado quando o TikTok responde `PUBLISH_COMPLETE`.** Se o TikTok não confirmar, dá para confirmar à mão no estúdio.
  5. Se o TikTok recusar o vídeo (`FAILED`), o post vai para "falhou" e o estúdio avisa no Telegram. Erro interno do TikTok tenta de novo.
- **Páginas públicas para a revisão do app:** `/termos.html` e `/privacidade.html`. O contato indicado é o perfil do Instagram, e nenhum e-mail é publicado.

**Passo a passo para ligar:**

1. Rodar `supabase/migrations/0004_tiktok_rascunho.sql` no SQL Editor do Supabase.
2. Em developers.tiktok.com (o próprio usuário entra e aceita os termos):
   - criar o app e adicionar os produtos Login Kit e Content Posting API (`video.upload`);
   - cadastrar o Redirect URI `https://luna-ai-studio-frontend.vercel.app/api/tiktok/callback`;
   - informar os Termos (`/termos.html`) e a Privacidade (`/privacidade.html`);
   - enviar para revisão.
3. Na Vercel, cadastrar `TIKTOK_CLIENT_KEY` e `TIKTOK_CLIENT_SECRET`, só para o servidor. Fazer um novo deploy.
4. No estúdio, abrir a página **TikTok** e clicar em "Conectar TikTok".

Rollback: desligar o modo automático nos posts (ou desconectar) faz tudo voltar ao modo manual com lembrete. A migração 0004 só amplia duas listas de valores permitidos.

## 16. Nova publicação em três passos, em várias redes (23/10/2026)

O fluxo é próprio do Luna Vox: segue o jeito do Instagram, mas sem copiar marca nem código do Instagram.

**Passos:**
1. **Selecionar:** escolher as mídias na ordem do carrossel.
2. **Editar:**
   - enquadramento das fotos (Original, 1:1, 4:5 ou 9:16); o recorte é feito no centro, no navegador, antes de enviar;
   - ordem das mídias e escolha da capa.
3. **Publicar:**
   - legenda e hashtags;
   - opções neutras ("flags");
   - as redes, cada uma com os seus destinos e com o modo automático ligado ou desligado.

**Grupo:** a mesma publicação vai para várias redes. Por baixo, cada rede continua sendo uma publicação própria (com a sua versão, fila e confirmação), e todas ficam ligadas pelo mesmo `group_id`. O botão **"Aprovar todas as redes"** aprova o grupo inteiro de uma vez.

**Flags:** a tradução fica num módulo único, `api/_flags.ts`, que o servidor e a tela usam. Mostra, rede a rede, o que vai automático e o que fica para fazer no app:

| Flag | Instagram | TikTok (fotos, automático) | TikTok (vídeo) / Telegram |
|---|---|---|---|
| Feito com IA | no app (a API não marca) | automático (`is_aigc`) | no app / na legenda |
| Música sugerida | no app (a API não põe música) | no app | no app / na legenda |
| Comentários desligados | no app, depois de publicado | no app, ao publicar | no app / grupo do canal |
| Enquadramento | automático (recorte) | automático (recorte) | automático (recorte) |
| Capa | primeira mídia (aviso) | automático (`photo_cover_index`) | primeira mídia (aviso) |

**Regra bloqueante:** Feed do Instagram com 9:16 não salva, porque o Instagram aceita no Feed fotos de 4:5 a 1,91:1.

**Banco:** migração `0005_grupos_e_flags.sql`. Ela só acrescenta `posts.group_id` e `posts.flags` (que começa vazio, `{}`); publicações antigas não mudam. Precisa rodar **antes** do merge desta parte, porque o servidor passa a gravar `flags`.

## 17. Prompt mestre, Fase 1: assistente Publicar em 4 etapas (23/10/2026)

Decisões do usuário:
- manter as cores atuais;
- uso só pelo próprio usuário (sem Supabase Auth nem várias contas por enquanto);
- geração com IA fica para depois (depende de provedor pago).

**Publicar** tem quatro etapas: Criar → Configurar → Revisar → Publicar. As configurações ficam guardadas ao voltar de uma etapa.

- **Criar:** mídias da biblioteca, ordem (que vale para o carrossel), capa do carrossel e recorte padrão.
- **Configurar:**
  - grade **mídia × formato** (Instagram Feed, Stories e Reels, TikTok, Telegram). Cada marcação vira **uma publicação própria**, com a sua rede, o seu formato, as suas mídias, o seu recorte e, se quiser, a sua legenda. Nada vai para uma rede que não foi marcada;
  - combinações impossíveis ficam bloqueadas na grade, com o motivo. Exemplos: Reels só com vídeo; Stories com 1 mídia; o TikTok não junta vídeo com fotos. Feed com 9:16 tem o botão "Adaptar para 4:5";
  - painel de cada plataforma: liga e desliga, estado da conta, modo automático, legenda e hashtags próprias, e localização.
- **Revisar:** prévias de Feed, Stories, Reels e TikTok, cada uma só com o que foi marcado para ela, na ordem e com o recorte de verdade. Os alertas aparecem num lugar só:
  - impedem publicar: conta desconectada, incompatível, legenda acima do limite, conteúdo adulto, horário passado;
  - só avisam: música não automática, TikTok em rascunho, publicações manuais.
- **Publicar:**
  - agora ou agendar, com o fuso horário à vista;
  - resumo de cada publicação (rede, formato, mídia, legenda, como sai) e confirmação antes de enviar;
  - cada publicação é registrada e enviada sozinha: um erro numa não impede as outras;
  - o botão principal mostra a ação possível: "Publicar agora", "Agendar publicação" ou "Corrigir pendências".

**Nomes dos status:**

| Status | Quando aparece |
|---|---|
| Rascunho | salvo, sem enviar para aprovação |
| Aguardando aprovação | enviado, falta aprovar |
| Aguardando publicação | aprovado, sem horário |
| Agendada | aprovada e com horário |
| Processando | o Instagram está publicando sozinho |
| Requer ação manual | é com você: modo manual, ou rascunho no app do TikTok |
| Publicada | a rede confirmou |
| Falhou | a rede recusou ou deu erro |
| Cancelada | cancelada antes de sair |

**Menu**, na ordem do fluxo: Publicar, Planejamento, Publicações, Biblioteca de mídia, Músicas e áudios, Contas conectadas (página nova, que dá acesso ao Instagram, ao TikTok e ao Telegram), Personagens e Configurações. Os módulos que ainda não existem ficam em "Em breve".

**Teste** (`test_assistente.mjs`, no computador e no celular): cobre os cenários 1, 2, 3, 4, 5, 9 e 13 do prompt.

## 18. Música do Instagram nos Reels (24/10/2026)

A Meta só libera música pela API (Instagram Audio API) com o **Login do Facebook para Empresas**, com o Instagram ligado a uma Página do Facebook. A música vai **só em Reels**: não há API para música em foto, carrossel ou Stories.

**O que foi configurado na Meta:**
- Página do Facebook "Luna Vox", ligada a @lunavoxlopes;
- no app "Luna Vox Studio", o login do Facebook com as 5 permissões e a configuração "Luna Studio" (ID 1788847055662416);
- URI de retorno: `https://luna-ai-studio-frontend.vercel.app/api/instagram/callback`.

**Como funciona:**
- **Conectar.** Em Contas conectadas → Instagram → "Conectar pelo Facebook". A conexão de hoje (token colado) fica guardada como reserva, cifrada.
- **Voltar.** O botão "Voltar para a conexão anterior (sem música)" restaura a reserva sem pedir nada de novo.
- **Escolher a música.** No passo 2 do Publicar, cada Reels tem "Música do Instagram": buscar por nome ou artista, ou "Em alta", depois "Usar". A plataforma guarda só o código, o nome e o artista da música, sem o áudio. A prévia mostra a música.
- **Publicar.** O Instagram aplica a música ao criar o Reels (`audio_configuration`: música 90, som do vídeo 40).
- **Regras:**
  - música do Instagram fora de Reels é recusada;
  - um Reels com música não é aprovado sem a conexão pelo Facebook.
- **Renovação.** O token do Facebook dura cerca de 60 dias e é renovado sozinho, como o do Instagram.

**Arquivos:**
- `api/_instagram.ts`: login do Facebook, busca de música e escolha do servidor (graph.instagram.com ou graph.facebook.com);
- `api/_instagram_api.ts`: rotas fb-connect, callback, audio e restore;
- `api/_flags.ts`: flag `igAudio`;
- `api/_jobs.ts`: envia `audio_configuration`;
- `src/studio/PostarPage.tsx`: componente `IgAudioPicker`;
- `vercel.json`: reescreve `/api/instagram/callback`.

**Banco:** migração `0006_instagram_facebook.sql`, só aditiva: colunas `provider`, `backup_ciphertext` e `backup_user_id` em `platform_connections`. As conexões existentes ficam como estão.

**Variáveis na Vercel:** `META_APP_ID`, `META_LOGIN_CONFIG_ID`, `META_APP_SECRET`. O segredo é colado pelo usuário. A opcional `INSTAGRAM_REDIRECT_URI` só é necessária se o domínio mudar.

**Para desfazer:**
- usar "Voltar para a conexão anterior";
- reverter o merge;
- as colunas novas podem ficar, porque o código antigo as ignora.

**Teste:** `test_igmusic.mjs`, com Facebook falso. Cobre:
- o diálogo com app, configuração e state;
- o token cifrado com a reserva;
- a busca de música;
- a tradução e a prévia;
- o Reels criado com `audio_configuration` via graph.facebook.com;
- a música recusada no Feed;
- a volta para a conexão anterior;
- o bloqueio sem a conexão pelo Facebook.

## 19. Não esquecer nenhuma publicação (25/10/2026)

O rascunho do TikTok é publicado por você no app. Em teste real (24 e 25/10), o TikTok não avisou o estúdio depois da publicação: a consulta a cada 15 minutos nunca recebeu `PUBLISH_COMPLETE`, embora a documentação diga que deveria. Para nada ficar esquecido:

- **Resposta do TikTok no histórico.** Cada mudança de status do rascunho é gravada no histórico da publicação, como "TikTok respondeu: SEND_TO_USER_INBOX". Assim dá para ver se o TikTok chega a confirmar. Quando confirma, o estúdio marca como publicado sozinho, como antes.
- **Lembretes.** Se o rascunho continua na caixa de entrada, o estúdio avisa pelo Telegram 3 h, 12 h, 24 h e 48 h depois de ele chegar. Cada aviso sai uma vez só e fica no histórico.
- **Faixa "Esperando você".** Aparece no topo de todas as telas sempre que há publicação em "Requer ação manual" (rascunho do TikTok ou publicação manual). Tem os botões **Abrir** e **Já publiquei**, que confirma todos os destinos pendentes e para as consultas ao TikTok. O menu Publicações mostra a mesma contagem.

**Arquivos:**
- `api/_jobs.ts`: `logTiktokStatus` e `remindDraft`;
- `src/studio/PostFlow.tsx`: `needsYou` e `WaitingForYou`;
- `src/studio/StudioApp.tsx`: faixa e contador no menu;
- `Icon.tsx`: ícone de sino;
- `studio.css`.

**Banco:** nenhuma migração. O histórico usa `publishing_attempts`.

**Leitura do perfil (25/10/2026):**
- **Como funciona.** O app do TikTok (Sandbox "Luna Vox Teste") ganhou a permissão `video.list`, e o estúdio passa a pedi-la ao conectar. Enquanto o rascunho está na caixa de entrada, a cada consulta o estúdio lê os 20 posts públicos mais recentes da conta (`/v2/video/list/`). Um post confirma o rascunho quando atende às três condições:
  - foi criado depois que o rascunho chegou;
  - a legenda bate, comparada sem hashtags, emoji nem pontuação (primeiros 40 caracteres);
  - ainda não confirmou outra publicação.
- **Resultado.** O estúdio marca "Publicado" com o link do post e avisa no Telegram.
- **Reconexão.** Conexões antigas, sem a permissão, mostram na página do TikTok o aviso "Reconectar agora".
- **Limites:**
  - pode ser que posts de foto não apareçam na lista. Nesse caso continuam valendo os lembretes e o "Já publiquei";
  - legenda vazia ou muito curta não é procurada.

**Teste:** `test_tt.mjs` cobre:
- a resposta gravada uma vez;
- o lembrete de 3 h e o de 12 h, sem repetir;
- a faixa e o contador;
- o "Abrir";
- o "Já publiquei", que confirma e cancela as consultas;
- a faixa sumindo quando não há nada pendente.

## 20. Assistente com IA (26/10/2026)

Um chat do administrador com uma IA, dentro do Luna Studio, no menu **Assistente**. Por enquanto a IA conversa só com o administrador. A conversa com os fãs fica para depois.

**Como funciona:**
- **Uma chave só:** IA pelo **OpenRouter**, com a chave `OPENROUTER_API_KEY` na Vercel. O administrador compra os créditos (US$ 5) e define o limite de gasto na própria chave. O orçamento combinado é de até R$ 30 por mês.
- **Dois "cérebros":**
  - **Redes:** `OPENROUTER_MODEL`, padrão `deepseek/deepseek-v4-flash`. Cuida do plano da semana, dos prompts para a Naiaflow, das legendas de Instagram e TikTok e de ideias.
  - **+18 Telegram:** `OPENROUTER_MODEL_ADULT`, padrão `cognitivecomputations/dolphin-mistral-24b-venice-edition`. Só para o canal privado. As conversas dos dois modos ficam separadas.
- **O que a IA recebe:**
  - a persona da Luna e a aparência fixa;
  - as regras: nada explícito no Instagram e TikTok, +18 só com a Luna e nunca com pessoa real;
  - a data de hoje;
  - a agenda dos próximos 8 dias e as últimas 15 legendas publicadas, para não repetir.
- **Onde fica a conversa:** neste navegador (`localStorage`). "Limpar conversa" apaga só o modo aberto.
- **Erros:** crédito acabado (402), chave inválida (401) e falta de resposta viram mensagens claras.
- **Tempo:** a função `api/studio.ts` passou a ter até 60 s (`vercel.json`).
- **Custo estimado:** menos de US$ 1 por mês com uso pesado, pelos preços de 26/10/2026.
- **Kaggle e Oracle foram descartados para +18:** as regras deles proíbem NSFW, e no Kaggle isso vale até para notebook privado.

**Arquivos:**
- `api/_assistant.ts`, a rota `r=assistant`;
- `src/studio/assistant.tsx`;
- o menu em `StudioApp.tsx`;
- `studio.css`.

**Teste:** `test_assistente_ia.mjs`, com o OpenRouter falso. Cobre:
- o 401 sem login;
- o pedido rápido;
- a persona, as regras e a agenda no contexto;
- o histórico;
- a conversa salva ao recarregar;
- o modo +18 separado, com o outro modelo;
- o erro de crédito;
- o "Limpar conversa";
- o celular sem rolagem lateral.

## 21. Aprovação pelo Telegram (26/10/2026)

O administrador aprova pelo celular. A publicação chega no Telegram dele com a foto ou o vídeo, a legenda, o horário e três botões: **✅ Aprovar**, **❌ Reprovar** e **✏️ Pedir correção**.

**Como funciona:**
- **Enviar para aprovar:** pelo botão **Aprovar pelo Telegram**, que aparece em dois lugares:
  - no passo 4 do Publicar;
  - na publicação que está aguardando aprovação.

  O estúdio sobe as cópias temporárias (as mesmas que a aprovação usa) e o servidor manda para o Telegram (ação `telegram-review`). Só para publicações automáticas.
- **Formato da mensagem:** com 1 mídia, foto ou vídeo com a legenda e os botões. Com várias, o álbum vai antes e os botões na mensagem seguinte.
- **Os botões voltam pelo webhook** `/api/telegram/webhook`, reescrito para `r=telegram-hook`.
  - O Telegram assina cada chamada com um segredo derivado do token. Chamadas sem o segredo recebem 401.
  - Só a conversa do administrador, a salva em Meu perfil, é atendida.
- **Mesmas regras do estúdio:** aprovar e reprovar usam a mesma função (`decidePost`), com versão exata, mídias no armazenamento e conta conectada. Aprovada com horário vira "Agendada" e entra na fila.
- **Botão velho:** se a versão mudou ou a publicação já foi decidida, o clique vira um aviso ⚠️ e nada muda.
- **Pedir correção:** o bot pergunta "O que precisa mudar?", com resposta obrigatória. A resposta reprova a versão com a nota "Correção pedida pelo Telegram: …", e a publicação volta para rascunho.
- **Conectar em Meu perfil** desliga o webhook, acha a conversa (getUpdates) e liga o webhook de novo. A ação `webhook` religa os botões manualmente.
- **Rascunho do TikTok:** quando chega no app, a foto ou o vídeo também vai para o Telegram, antes de a cópia ser apagada. Respeita a opção "mandar foto/vídeo junto".

**Arquivos:**
- `api/_tg_review.ts`;
- `decidePost` e a ação `telegram-review` em `api/_posts_api.ts`;
- `api/telegram.ts`;
- `api/studio.ts`;
- `vercel.json`;
- `api/_jobs.ts`;
- no estúdio: `store.tsx` (`sendToTelegram`), `PostFlow.tsx` e `PostarPage.tsx`.

**Teste:** `test_tg_aprovacao.mjs`, com o Telegram simulado. Cobre:
- o envio pelo estúdio;
- a foto com legenda, horário e botões;
- o segredo errado (401);
- a conversa de outra pessoa (ignorada);
- aprovar, que agenda, tira os botões e confirma;
- o botão velho;
- reprovar;
- pedir correção com a resposta.

`test_tt.mjs` confere o vídeo do rascunho indo para o Telegram.

## 22. Análise +18 pelo bot e prompt simples (26/10/2026)

- **O bot é a mesa de análise de todas as imagens, inclusive as +18.**
  - Posts do Telegram (canal +18) também sobem as cópias e vão para o bot com ✅ / ❌ / ✏️ (`usesCopies`: modo automático ou rede Telegram).
  - Aprovado, fica agendado. No horário, o lembrete manda a foto ou o vídeo aprovado de volta no bot, pronto para postar no canal.
  - A postagem automática no canal vem depois, quando conferirmos as regras do Telegram.
  - O +18 nunca vira publicação automática de Instagram ou TikTok: a rede Telegram não aceita o modo automático.
- **Prompt da Naiaflow simples:**
  - o Assistente escreve uma frase curta, "Luna + roupa + lugar + o que faz + clima", porque o clone luna já sabe a aparência dela;
  - nunca descreve rosto ou corpo e nunca escreve "uma mulher";
  - no máximo 5 hashtags comuns, sem inventar.
- **Teste:** `test_tg_aprovacao.mjs` cobre o caminho +18: bot, aprovar, lembrete com a foto, e o modo automático recusado.

## 23. Planejamento pelo chat (parte A do fluxo, 26/10/2026)

Aba **Planejamento** no Assistente. O chat transforma o pedido do administrador num plano. A geração das imagens (parte B) e a refação (parte C) vêm depois.

**Como funciona:**
1. **Formulário:**
   - período, até 92 dias (um trimestre);
   - quantas publicações por dia em cada destino: Instagram Feed, Stories, Reels (vídeo), TikTok e Telegram +18;
   - tema opcional;
   - "TikTok usa a mesma foto do Feed do dia".
2. **A grade é do estúdio, não da IA:** dias × quantidades, nos horários padrão de Brasília. Feed às 12:00 e 19:00; Stories às 09:00, 13:00 e 17:00; TikTok às 20:00; Telegram +18 às 22:00; etc.
3. **A IA escreve o criativo de cada item, 20 por vez** (ação `fill`; a tela repete até acabar), para caber no tempo da função:
   - prompt simples para a Naiaflow;
   - legenda;
   - até 5 hashtags.

   Os itens das redes usam o modelo normal. Os +18 usam o modelo sem censura, com as regras do modo +18. Hashtags estranhas são filtradas.
4. **TikTok que reaproveita** aponta para o Feed do mesmo dia (`source_item_id`). Copia o prompt e a legenda e **não gasta crédito**. Editar o Feed atualiza o TikTok.
5. **Revisar:** dá para editar o prompt e a legenda, tirar um item e ver a estimativa: fotos e vídeos a gerar, e créditos da Naiaflow (foto 1, vídeo ~6).
6. **Aprovar o plano:**
   - os itens vão para `na_fila`, a fila da automação local (parte B);
   - depois de aprovado, o plano não muda mais;
   - "Descartar" cancela o que ainda não foi gerado.

**Banco:** migração `0007_planejamento.sql`, só aditiva:
- tabelas `content_plans` e `content_plan_items`;
- trava no banco que impede item +18 fora do Telegram.

**Arquivos:**
- `api/_plans.ts`, a rota `r=plans`;
- `askAi` reaproveitável em `api/_assistant.ts`;
- `src/studio/plans.tsx`;
- a aba em `assistant.tsx`;
- `studio.css`.

**Testes:**
- `supabase/tests/planejamento.test.mjs`;
- `test_planejamento.mjs`, que cobre:
  - as regras da grade;
  - os 10 itens;
  - os horários;
  - o reaproveitamento do TikTok;
  - +18 só no Telegram e pela IA sem censura;
  - uma chamada por IA;
  - as hashtags limpas;
  - a estimativa;
  - editar;
  - tirar item;
  - aprovar;
  - o travamento depois de aprovado;
  - o celular.

## 24. Automação local da Naiaflow (parte B do fluxo, 26/10/2026)

Um programa no PC do administrador, em `worker/luna_worker.py` (Python, com Playwright e Pillow), transforma os itens de foto dos planos aprovados em publicações para aprovar no Telegram.

**Ciclo de cada item:**
1. **`next`:** pega na fila o próximo item de foto (`na_fila`, que não reaproveita outro) e marca "gerando". Item preso em "gerando" há mais de 30 min volta para a fila, para o caso de o PC ter caído.
2. **Gera na Naiaflow** pelo Playwright, que controla o Chrome do PC num perfil separado (`worker/.perfil-naiaflow`):
   - seleciona o clone "luna", cola o pedido e clica em Gerar;
   - espera a foto nova e baixa o original (`/public`).
   - Se a Naiaflow tiver menos de 2 créditos, o programa para e não gasta o resto.
3. **Salva** em `conteudos/AAAA-MM/` (o +18 em `conteudos/AAAA-MM/+18/`) como `plano-<data>-<destino>-<id>.jpg`.
4. **`done`:** o estúdio cadastra a mídia (+18 como "adulto") e cria **uma publicação por destino** do item e dos itens que reaproveitam a foto (o TikTok do mesmo dia). Instagram e TikTok ficam no modo automático; o Telegram +18 fica manual, com lembrete. Todas vão para "aguardando aprovação".
5. **`upload-url`, para cada destino:** o PC sobe uma cópia JPEG no formato do destino. Feed: 4:5, 1080×1350. Stories, TikTok e Telegram: 9:16, 1080×1920.
6. **`uploaded`:** cada publicação vai para o bot do Telegram com ✅ / ❌ / ✏️, e o item fica "em_analise".
7. **Decisão no bot:** aprovar marca o item "aprovado" e a publicação agendada. Reprovar ou pedir correção marca o item "reprovado", com a nota (a refação é a parte C).
8. **`fail`:** volta para a fila até o limite de 2 tentativas. Passou disso, fica "falhou" e chega um aviso no Telegram.

**Segurança:**
- O PC **nunca guarda a chave do banco**. Ele usa só `/api/studio?r=worker`, com a "chave do PC", que é derivada da `TOKEN_ENCRYPTION_KEY` e de uma geração guardada no Upstash.
- Em **Configurações → Automação do PC → Conectar este computador**, o estúdio troca a geração (a chave antiga para de valer) e baixa o `luna-worker.env`. Ninguém copia chave a mão.
- O `.gitignore` deixa de fora do git o `.env`, o perfil do Chrome e o registro (`luna-worker.log`).
- **Mudança em relação ao fluxograma:** não há FastAPI no PC. O programa só busca tarefas no estúdio, então nada fica exposto no computador.

**Ainda não automatizado:** itens de **vídeo** (Reels) ficam na fila; a geração de vídeo pela Naiaflow vem depois.

**Para usar:** baixar o `luna-worker.env` em Configurações, colocar em `worker/` e abrir **Iniciar automação.bat**. Na primeira vez, entrar na Naiaflow na janela do Chrome que abrir.

**Teste:** `test_worker.mjs` roda o **programa Python de verdade** (com `--imagem-teste` no lugar da Naiaflow) contra o estúdio de teste. Cobre:
- a fila, com 2 fotos (o TikTok reaproveita a do Feed);
- as 3 publicações certas;
- a pasta +18 e a classificação adulto;
- as cópias por destino;
- as 3 fotos no bot;
- aprovar, que atualiza o plano;
- a falha com limite e aviso;
- o item preso que volta para a fila;
- a chave errada (401).

## 25. Conversa com uma IA só e "Gerar esta foto" (26/10/2026)

**Uma IA só na Conversa:** a aba Conversa do Assistente não tem mais a escolha "Redes / +18 Telegram". É uma conversa só, sempre com o modelo sem restrição (`OPENROUTER_MODEL_ADULT`). O prompt de sistema (`CHAT_NOTE` em `api/_assistant.ts`) avisa que conteúdo explícito só vale para o canal +18 do Telegram e que Instagram e TikTok seguem as regras das redes. O **Planejamento** continua escolhendo a IA por destino.

**Gerar esta foto:** quando o administrador pede uma foto na Conversa, a IA termina a resposta com as linhas `PROMPT:`, `LEGENDA:`, `HASHTAGS:` e `DESTINO:`. O estúdio lê essas linhas (`src/studio/quickPhoto.tsx`) e mostra o botão **Gerar esta foto** embaixo da resposta.

Ao clicar, abre um formulário para ajustar:
- o destino (Feed, Stories, TikTok ou +18 Telegram);
- o horário de publicação (padrão: daqui a 2 h);
- o pedido, a legenda e as hashtags.

Ao confirmar, `POST /api/studio?r=plans` com `action: "quick"` cria um plano "Foto avulsa do chat", já aprovado, com 1 item direto em `na_fila`. A partir daí é o mesmo caminho dos planos:
1. o robô do PC gera a foto na Naiaflow (1 crédito);
2. cria a publicação;
3. manda para aprovar no Telegram.

Só fotos: vídeo continua fora da automação.

## 26. Parte C: correção pelo Telegram editando a foto (27/10/2026)

**Fluxo:**
1. No Telegram, **✏️ Pedir correção** e o texto do pedido (respondendo a pergunta ou direto no chat, até 30 min depois do botão).
2. A publicação é reprovada. Se veio de um item do plano (foto do robô), o item volta para a fila como correção: `status=na_fila`, nota `Correção: <pedido>`, `media_path` = foto atual.
3. O robô abre a foto na Naiaflow pelo link direto (`/generations/preview/job/<id>`), escolhe **Editar pelo texto**, escreve o pedido e clica em Criar (1 crédito).
4. A foto corrigida é salva com sufixo `-correcao-...`; a publicação antiga é cancelada e a nova vai para aprovar no Telegram. Só a publicação corrigida muda (o TikTok que reaproveitava a foto fica como está).

**Detalhes:**
- O robô anota em `worker/naia-jobs.json` qual job gerou cada foto. Foto gerada antes disso não tem como ser editada (a correção falha sem gastar crédito).
- A espera pela foto pronta só aceita imagem com horário (no link) depois do clique, para não pegar foto antiga da galeria.
- Publicação feita à mão (sem item do plano) continua como antes: volta para rascunho com o pedido.

**Banco:** nenhuma migração (usa `note` e `media_path` de `content_plan_items`).

**Robô no início do Windows:** atalho na pasta Inicializar, janela minimizada; tenta de novo em 2 min se cair; não abre duas vezes.

## 27. Reels automático a partir da foto (27/10/2026)

Os itens de **Reels** do plano agora passam pelo robô do PC, sem gastar crédito de vídeo:
1. o robô gera a **foto** na Naiaflow (1 crédito), como num item de foto;
2. monta no PC um **vídeo 9:16 de 7 s, 30 fps, com zoom leve de 6%** (Pillow + ffmpeg do pacote `imageio-ffmpeg`, trilha muda; ~1 min e ~1-2 MB);
3. o estúdio cadastra o **MP4** na biblioteca e cria a publicação de Reels (automática). A foto fica no item (`media_path`), ao lado do vídeo, para a correção editar a foto e remontar o vídeo;
4. **música:** o estúdio escolhe uma das 5 músicas em alta da biblioteca do Instagram (só com a conexão pelo Facebook; sem ela, o Reels sai sem música). A música aparece na mensagem de aprovação do Telegram (🎵).

**Mudanças:**
- `api/_worker.ts`: a fila inclui itens de vídeo; `done` recebe `video_path`/`video_size`; `upload-url` aceita `mime_type: video/mp4`; a correção vale também para Reels.
- `api/_plans.ts`: estimativa do Reels = 1 crédito; a IA escreve o prompt de uma foto que vira Reels.
- `api/_tg_review.ts`: linha 🎵 com a música.
- `worker/luna_worker.py`: `make_reels`; `requirements.txt` com `imageio-ffmpeg==0.5.1` (o `Iniciar automação.bat` instala sozinho).

**Teste:** `test_worker.mjs` ganhou o bloco do Reels (estimativa, fila, vídeo montado, MP4 na biblioteca e no armazenamento, música, vídeo no bot com 🎵, correção de Reels volta para a fila).

## 28. Canal +18 postando sozinho (27/10/2026)

As publicações +18 aprovadas no bot saem sozinhas no canal do Telegram, no horário marcado. Sem canal conectado, continua como antes: o lembrete com a foto chega no Telegram do administrador para postar à mão.

**Conectar (Meu perfil → Avisos no celular → Canal +18):**
1. **Preparar o bot**: o webhook passa a receber `my_chat_member`, o aviso de que o bot entrou num grupo ou canal.
2. No Telegram, num canal **privado**: Administradores → Adicionar administrador → o bot, com **Publicar mensagens** ligado.
3. O Telegram avisa o estúdio, que salva o canal (Upstash `luna:telegram-channel`, com id e nome) e manda "📢 Canal conectado" no chat do administrador.
- **Segurança:** só vale quando quem adicionou o bot é o próprio administrador (`from.id` = chat salvo). Se outra pessoa puser o bot no canal dela, nada muda.
- Sem a permissão de publicar, o canal não conecta e chega um aviso.
- Bot tirado do canal: o canal é esquecido e chega um aviso. Também dá para **Desligar canal** no estúdio.
- Trocar o token do bot ou desligar os avisos apaga o canal.

**Postar (`_jobs.ts`, tarefa `lembrete_publicacao`):**
- Publicação do Telegram com canal conectado: `sendToChannel` (`_tg_review.ts`) manda a foto ou o vídeo (ou o álbum, com a legenda na primeira mídia) com legenda e hashtags, até 1024 caracteres.
- Depois disso:
  - o destino e a publicação ficam "publicado", com o link `t.me/c/<id>/<mensagem>`;
  - registra o uso da mídia;
  - apaga a cópia temporária;
  - avisa "✅ Postado no canal" com o link.
- **Falha:** tenta de novo em 2, 4 e 8 min. Sem tentativas restantes, avisa para postar à mão; a publicação fica "publicando" para confirmar no estúdio.
- Sem migração: usa o `channel: 'api'` e o `confirmed_by: 'api'` que já existem.

**Regras do Telegram:**
- Conteúdo adulto só em canal **privado**: os termos proíbem pornografia em canais, bots e grupos públicos.
- Vendas, assinaturas e mídia paga (Stars) ficam para o próximo passo, depois de conferir as regras.

**Teste:** `test_canal18.mjs`. Cobre:
- preparar o bot;
- outra pessoa sendo ignorada;
- sem permissão de publicar;
- conectar;
- Meu perfil;
- postar no canal no horário (link, aviso, cópia apagada, uso);
- o canal fora do ar, que tenta de novo;
- o bot tirado do canal, que volta ao lembrete;
- desligar.

O harness ganhou um Upstash falso, que só liga quando o teste pede (`/__kv?on=1`).

## 29. Bot de vendas +18, etapa 1 (27/10/2026)

Um **bot só para os fãs**, separado do bot de aprovações, que continua só do administrador. Decisões do administrador (27/10):
- **Pacotes:** Ensaio R$ 9,90; Álbum VIP sem censura R$ 19,90; VIP completo R$ 29,90 (fotos, vídeos e ensaios, com conteúdo novo quase todo dia no canal VIP; assinatura de 30 dias).
- **Pagamento por Pix** (próxima etapa, falta o gateway). Ele sabe que o Telegram exige Stars para conteúdo digital vendido por bot e que o bot pode ser derrubado. Por isso a lista de fãs e de compras fica no banco, para dar para recomeçar num bot novo.
- **Disparo diário ligado** (etapa 2). Ele foi avisado do ECA Digital (Lei 15.211/2025, em vigor desde 17/03/2026), que proíbe verificar a idade só por autodeclaração, inclusive para prévias. No bot: confirmação de 18+ no /start; a prévia grátis vai borrada e com texto sugestivo, não explícito; o explícito só chega depois do pagamento.

**Banco:** migração `0008_vendas.sql`, só aditiva. Cria `sales_products` (com os 3 pacotes iniciais), `sales_product_media`, `fans`, `sales_orders` e `sales_broadcasts`.

**Bot dos fãs** (`api/_sales_bot.ts`; webhook `/api/vendas/telegram` → `r=sales-hook`, assinado):
- **/start:** registra o fã e pede a confirmação de 18+. Sem ela, nada de pacotes.
- **Menu:** botões só dos pacotes prontos. O ensaio e o álbum precisam ter conteúdo; o VIP precisa do canal +18 conectado.
- **Tocar num pacote:** por enquanto, "Pix quase pronto" e um aviso ao administrador ("fulano quis comprar…").
- **/parar e /voltar** desligam e religam as novidades. Bloqueou o bot: fica marcado, e as mensagens param.

**Página Vendas** (`src/studio/sales.tsx`, rota `r=sales`):
- ligar o bot de vendas: token de outro bot, que não pode ser o de aprovações;
- os números dos fãs;
- editar nome, preço, descrição, dias do VIP e "à venda";
- **colocar fotos e vídeos nos pacotes:**
  - o navegador pega o arquivo da pasta do PC (JPEG até 5 MB, MP4 até 20 MB) e sobe uma cópia temporária;
  - o bot de vendas manda essa cópia para a conversa do administrador, sem som, e apaga a mensagem;
  - o estúdio guarda só o `file_id` do Telegram e apaga a cópia. O conteúdo dos pacotes **não fica em servidor nenhum**;
  - para isso, o administrador precisa ter aberto o bot de vendas (Começar).
- **Ver como o fã vê:** o menu chega no Telegram do administrador.

**Teste:** `test_vendas.mjs`. Cobre:
- os pacotes iniciais;
- o token (formato e Ligar);
- o segredo do webhook;
- /start e 18+, sem pacote antes de confirmar;
- grupo ignorado;
- guardar a foto pela página (file_id, mensagem apagada);
- o preço;
- o menu só com os pacotes prontos;
- comprar, com o aviso ao fã e ao administrador;
- o VIP com o canal;
- /parar, /voltar e o bloqueio;
- Ver como o fã vê;
- o celular sem rolagem para o lado;
- desligar.

**Próximas etapas:**
- **(2)** a prévia borrada feita no PC e o disparo diário, com a aprovação do administrador;
- **(3)** o Pix com o gateway, a entrega automática do pacote (`file_id`, com proteção contra encaminhar) e o VIP com o convite do canal e a saída quando o prazo vence.

## 30. Pix pela PushinPay e entrega automática (27/10/2026)

**Gateway:** PushinPay, escolhida pelo administrador.
- R$ 0,35 por Pix, sem mensalidade, saque no mesmo dia.
- Os termos aceitam conteúdo adulto, desde que o vendedor colha a confirmação de 18+ do comprador (o /start do bot faz isso).
- A conta é aberta pelo administrador, com documentos e selfie.
- **Token:** fica no Upstash (`luna:pix-token`), colado em **Vendas → Pix**, com a escolha entre pagamentos de verdade e teste (sandbox). Ele nunca volta ao navegador.

**Fluxo** (`api/_sales_pay.ts`):
1. **O fã toca no pacote.**
   - Nasce o pedido "pendente" e a cobrança (`POST /api/pix/cashIn`, valor em centavos).
   - O link de aviso é assinado para aquele pedido: `/api/studio?r=sales-pix&o=<pedido>&s=<HMAC>`.
   - O bot manda o valor, o **Pix copia-e-cola** (texto fácil de copiar) e o botão **"Já paguei"**.
   - Se o fã tocar de novo no mesmo pacote em até 30 min, o mesmo Pix é reaproveitado.
2. **Aviso da PushinPay.** A assinatura errada é recusada. Com a assinatura certa, o estúdio ainda **confere na PushinPay** (`GET /api/transactions/{id}`) se está `paid` e se o valor bate. Um aviso falso não libera nada; com valor diferente, nada é entregue e o administrador é avisado.
3. **"Já paguei":** no máximo uma consulta por minuto por pedido, que é a regra da PushinPay (trava no Upstash).
4. **Entrega**, só uma vez (o pedido passa de pendente a pago com trava no banco):
   - **Ensaio e álbum:** as fotos e os vídeos saem pelo `file_id` do Telegram, em álbuns de até 10, com `protect_content`, que impede encaminhar e salvar.
   - **VIP:**
     - convite do canal para **1 pessoa**, válido por 24 h, criado pelo bot que administra o canal;
     - o acesso **soma** ao que ainda resta, então renovar antes de vencer não perde dias.
   - Para o administrador: "💰 Venda! fulano comprou X por R$ Y".
   - Se a entrega falhar, o fã recebe "já estou resolvendo" e o administrador é avisado.
5. **VIP vencido:** o processador da fila (a cada minuto, `salesTick`) tira o fã do canal (banir e desbanir, para ele poder voltar se renovar), manda "Seu VIP terminou" com o botão **Renovar** e marca o pedido "expirado".

**Canal:** ao conectar o canal +18, o bot avisa se faltarem as permissões "Convidar usuários via link" e "Banir usuários", que o VIP precisa.

**Página Vendas:**
- o cartão do **Pix** (ligar e desligar);
- vendas e valor recebido em 30 dias;
- Pix esperando pagamento;
- a lista dos pedidos (data, fã, pacote, valor, situação, ⚠ quando há observação).

**Teste:** `test_pix.mjs`, com a PushinPay falsa no harness. Cobre:
- o token;
- a cobrança e o link assinado;
- o Pix reaproveitado;
- o aviso com assinatura errada;
- o aviso falso (ainda `created`);
- "Já paguei" antes de pagar e no mesmo minuto;
- a entrega protegida das 2 fotos e o aviso da venda;
- a repetição sem entregar de novo;
- o valor diferente;
- o VIP com convite de 1 pessoa;
- a renovação somando 60 dias;
- o vencimento (sai do canal e oferece renovar);
- a página;
- desligar o Pix.

O harness agora compara `lt`/`gt` no tipo da coluna, como o PostgREST.

## 31. Regras de prazo: pacotes de 30 dias e VIP avulso ou mensal (27/10/2026)

Pedido do administrador:
- todo pacote vale 30 dias;
- o VIP de R$ 29,90 pode ser avulso ou mensal, porque o canal sempre tem conteúdo novo;
- não apagar mensagens: ao vencer, só tirar do canal.

**Pacotes (Ensaio R$ 9,90 e Álbum R$ 19,90):**
- **Ao pagar:** as fotos chegam no chat e ficam lá, protegidas contra encaminhar e salvar. O pedido ganha `access_until` = pagamento + `period_days` (30).
- **📂 Meus pacotes** (botão e `/pacotes`): abre de novo enquanto vale, no máximo a cada 10 min por pacote.
- **No dia 30:** o processador marca "expirado", o pacote não abre mais, e o fã recebe "Seu acesso terminou" com o botão **Comprar de novo**.
- Ninguém apaga mensagem nenhuma. O Telegram só deixa o bot apagar até 48 h, e o administrador não quer.

**VIP (canal):**
- **VIP completo 30 dias (avulso):**
  - paga uma vez e recebe o convite de 1 pessoa;
  - comprar de novo antes de vencer soma os dias;
  - no vencimento, sai do canal (banir e desbanir) e recebe o botão **Renovar**.
- **VIP completo mensal:**
  - **Pix Recorrente** da PushinPay (`POST /api/pix/cashIn/subscription`, `frequency` 2 = mensal, `pix_recurring_retry_policy` 2 = até 3 tentativas em 7 dias). Pagar o primeiro Pix autoriza os meses seguintes.
  - **Cada parcela paga** chega no mesmo link assinado do primeiro pedido (outro `id`, mesmo `subscription_id`) e é conferida na PushinPay. Vira um pedido novo, com `installment` + 1; a chave única da cobrança impede duplicar. Soma 30 dias, e o administrador recebe "renovou (parcela N)".
  - **Parcela atrasada:** o fã só sai do canal 3 dias depois do vencimento.
  - **/cancelar** cancela na PushinPay (`DELETE /api/pix/cashIn/subscription/{id}/cancel`). O acesso vai até o fim do mês pago; depois, sai do canal sem a folga.

**Banco (0008, ainda não rodada em produção):**
- `sales_products.period_days` agora é obrigatório para todos os pacotes (padrão 30);
- `billing` (`avulso` ou `mensal`; mensal só para VIP);
- 4 pacotes iniciais;
- `sales_orders.subscription_id` e `installment`.

**Página:**
- "Dias de acesso" em todos os pacotes;
- o cartão do VIP mensal;
- a situação "Acesso vencido" na lista de pedidos.

**Teste:** `test_pix.mjs` agora cobre também:
- os 30 dias do pacote;
- Meus pacotes (abrir de novo, não repetir em seguida);
- nenhuma mensagem apagada;
- o pacote vencido;
- o mensal: assinatura, parcela 2 somando dias (sem duplicar), a folga de 3 dias, /cancelar e a saída depois de cancelado e vencido.

## 32. Telegram por pastas no PC (27/10/2026)

Pedido do administrador: montar os pacotes e o canal VIP colocando e tirando arquivos de pastas no PC.

**Pastas** (dentro da pasta do estúdio, para o estúdio enxergar):
- `conteudos\vendas\ensaio\` → conteúdo do pacote **Ensaio**;
- `conteudos\vendas\album-vip\` → conteúdo do pacote **Álbum VIP sem censura**;
- `conteudos\vendas\vip\` → **postado no canal VIP**.

A pasta `vendas` tem um `LEIA-ME.txt`, e há um atalho na Área de Trabalho: **"Luna Vox - Pastas do Telegram"**.

**Página "Telegram (pastas)":**
- Abre por Contas conectadas → Telegram, ou pelo link em Vendas.
- Mostra cada pasta e **o que vai mudar**: "vai entrar" ou "vai postar" nos arquivos novos, e "vai sair do pacote" ou "vai apagar do canal" nos que saíram.
- **Ler as pastas de novo:** relê a pasta do PC.
- **Atualizar (N mudanças):** aplica.
  - **Pacotes:** usa o mesmo caminho da página Vendas (cópia temporária → bot de vendas → `file_id`), e quem saiu da pasta sai do pacote.
  - **VIP:** o bot do canal posta a foto ou o vídeo no canal. Quem saiu da pasta tem o post apagado do canal. Se o Telegram não deixar apagar um post antigo (o bot precisa de "Apagar mensagens"), aparece um aviso para apagar à mão.
- Quais arquivos já estão no canal fica no Upstash (`luna:vip-posts`, arquivo → mensagens). Não precisa de migração.

**Servidor** (`r=sales`): ações `vip-url`, `vip-post` e `vip-unpost`. O estado traz `vipPosts`, com o caminho do arquivo mesmo depois de ele sair da pasta.

**Teste:** `test_pastas.mjs`. Cobre:
- as 3 pastas e o que vai entrar ou postar;
- Atualizar (ensaio 2, álbum 1, VIP postado pelo bot do canal);
- tirar e pôr arquivos (sai do pacote, o post antigo é apagado e o novo postado);
- o VIP sem canal;
- o celular.

## 33. Aba Telegram: controlar pelo estúdio (27/10/2026)

Pedido do administrador: uma aba só do Telegram no menu, e controlar o conteúdo pelo estúdio, sem precisar mexer nas pastas pelo Explorer.

- **Menu:** o item **Telegram** fica logo abaixo de Vendas (antes estava escondido em Contas conectadas). A página se chama **Telegram**.
- **Em cada cartão** (Ensaio, Álbum VIP sem censura, VIP canal):
  - **Do computador:** escolhe arquivos do PC. O estúdio copia para a pasta (`conteudos/vendas/<pasta>/`) e **já atualiza** (guarda no pacote ou posta no canal).
  - **Da biblioteca:** escolhe da Biblioteca de mídia (fora das pastas de vendas). O estúdio copia para a pasta, o original continua onde estava, e já atualiza.
  - **✕ em cada foto:** pede confirmação, tira do pacote ou apaga o post do canal, e **move** o arquivo para `conteudos/vendas/_removidos/`. Nada é apagado de vez.
- **Atualizar** continua valendo para o que for mexido à mão pelo Explorer.
- **Estúdio** (`store.tsx`, `localFolder.ts`): `addFiles(files, subdir)` copia para qualquer subpasta de `conteudos/`, e `moveItem(path, subdir)` move (copia e só depois tira o original).
- **Teste:** `test_pastas.mjs` cobre também o item do menu, o ✕ (sai do pacote e vai para `_removidos`), "Do computador" no álbum e "Da biblioteca" no VIP (posta no canal e o original fica).

## 34. Trocar o token do bot de aprovações sem perder o canal (27/10/2026)

Depois de um `/revoke` no BotFather, o bot continua o mesmo (o número antes do ":" não muda). Agora, salvar o token novo de um bot **igual**:
- mantém a conversa do administrador e o canal +18 conectados;
- religa o webhook com o segredo novo, que é derivado do token.

O token de **outro** bot continua apagando a conversa e o canal (precisa conectar de novo).

Em **Meu perfil → Avisos no celular**, com o bot já conectado, há a opção **"Trocar o token (depois de um /revoke no @BotFather)"**.

**Teste:** `test_token_revoke.mjs`. O harness ganhou `/__kvset`, só para teste.

## 35. Mensagens que chamam o fã para comprar (CTA) (28/10/2026)

O administrador mostrou modelos de bots e canais do mercado: chamar a pessoa pelo nome, voltar a falar com quem não comprou, foto com as partes íntimas cobertas e botões de compra. Sem áudio (decisão dele).

**Mensagens** (`api/_sales_cta.ts`). Todas levam `{nome}` (o primeiro nome do fã), uma **prévia** e os botões dos pacotes:
1. **Boas-vindas:** logo depois do "Tenho 18". Conta como a mensagem do dia.
2. **Pix esquecido:** 20 min depois de gerar o Pix sem pagar, um lembrete só (até 1 dia), com `{pacote}`, "✅ Já paguei" e "🔁 Gerar outro Pix".
3. **Diária:**
   - uma por dia (intervalo mínimo de 20 h), a partir do horário escolhido (Brasília), para quem confirmou 18+;
   - não vai para quem deu /parar, bloqueou o bot ou tem **VIP ativo**;
   - a frase gira pela lista, uma por dia; a prévia gira e pode repetir;
   - sai em lotes de 25 por minuto, pelo processador da fila (`ctaTick` em `_jobs.ts`);
   - se o fã bloqueou o bot, ele fica marcado e para de receber.

**Prévias:** cartão **"Prévias (CTA)"** na aba Telegram (pasta `conteudos/vendas/previa/`). O conteúdo sobe para o Telegram como nos pacotes (`file_id`) e a lista fica no Upstash (`luna:sales-previews`). Use fotos com as partes íntimas **já cobertas**; cobrir automaticamente (NudeNet no PC) fica para depois.

**Textos:**
- Editáveis em **Vendas → Mensagens para os fãs (CTA)**: boas-vindas, Pix esquecido, liga/desliga, horário e a lista de frases diárias, com "Mandar um teste para mim".
- Ficam no Upstash (`luna:cta`). Os padrões são 10 frases provocantes, **sem descrição de sexo**: as mensagens grátis chegam para quem só disse "tenho 18" (ECA Digital, seção 29).

**Banco:** migração `0009_cta.sql`, só aditiva: `fans.cta_sent_at`, `fans.cta_count`, `sales_orders.reminded_at`. **Precisa rodar antes do merge.**

**Teste:** `test_cta.mjs`. Cobre:
- as boas-vindas (nome, prévia, botões);
- o Pix esquecido (só depois de 20 min, e uma vez);
- a diária (quem parou e o VIP ficam de fora, uma por dia, a frase muda);
- o horário e o desligado;
- o teste para o administrador;
- frase vazia recusada;
- editar e salvar pela tela;
- o cartão Prévias;
- o celular.

## 36. VIP mensal: nome e CPF de quem paga (28/10/2026)

Em produção, a PushinPay recusou a assinatura (Pix Recorrente) com **422 "Informe o customerId ou os dados do customer"**: o Pix automático exige os dados do pagador.

- Ao tocar no **VIP mensal**, o bot pede **nome completo e CPF numa mensagem só** (ex.: `Maria da Silva 123.456.789-00`). Quem não quiser informar fica com o VIP de 30 dias.
- O CPF é conferido pelos dígitos verificadores. A mensagem sem CPF ou com CPF inválido é recusada com um aviso. O pedido de dados vale 15 min (Upstash `luna:sub-data:<id>`).
- Os dados vão **só** para a PushinPay (`customer: { name, document: { type: "cpf", number } }`) e não são gravados no banco nem na auditoria.
- O erro da PushinPay agora vem completo (com a lista `errors` do 422) no aviso ao administrador e na nota do pedido, para ajustar se ela pedir outro campo.
- **Teste:** `test_pix.mjs`, com a PushinPay falsa recusando assinatura sem cliente.
