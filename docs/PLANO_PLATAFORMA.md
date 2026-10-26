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
