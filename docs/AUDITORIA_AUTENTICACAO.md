# Auditoria da autenticação: Luna Vox Studio sem RunPod

21/10/2026. Só leitura: nenhum código, variável de ambiente ou deploy foi alterado.

## 1. Como o login funciona hoje

É um login de **conta única** (um dono, sem tabela de usuários), feito em funções serverless da Vercel (`frontend/api/`).

| Peça | Como funciona | Arquivo |
|---|---|---|
| Credenciais | E-mail em `APP_LOGIN_EMAIL`; senha em `APP_LOGIN_PASSWORD`. Depois da primeira troca pelo "Meu perfil", passa a valer um hash **scrypt** (sal aleatório) guardado no Upstash Redis, e a variável deixa de ser usada | `api/login.ts`, `api/_password.ts` |
| Sessão | Cookie `luna_session` = `versão.expira.tipo.assinatura`, assinado com **HMAC-SHA256** (`SESSION_SECRET`). `HttpOnly`, `Secure`, `SameSite=Lax`. Dura 1 dia, ou 30 dias com "lembrar" | `api/_auth.ts` |
| Derrubar sessões | Versão da sessão no Upstash (`luna:session_version`). Trocar a senha ou "sair de todos os aparelhos" aumenta a versão e invalida todos os cookies | `api/_auth.ts`, `api/logout.ts`, `api/change-password.ts` |
| Limite de tentativas | 5 erros por IP (guardado como hash) a cada 15 min, no Upstash | `api/_ratelimit.ts` |
| Troca de senha | Exige sessão válida **e** a senha atual; mínimo de 8 caracteres | `api/change-password.ts` |
| Comparação de segredos | Em tempo constante (`timingSafeEqual`) | `api/_auth.ts` |
| Controle de acesso | Cada função confere `isAuthenticated(req)`. O app chama `/api/session` ao abrir e mostra a tela de login se não houver sessão | `api/*.ts`, `src/App.tsx` |
| Armazenamento | Upstash Redis via REST (`KV_REST_API_*` ou `UPSTASH_REDIS_REST_*`), sem biblioteca nova | `api/_store.ts` |

**Supabase:** não existe. Não há nenhuma referência no código, nas dependências nem nas variáveis. Também não dá para saber, pelo código, se você tem um projeto Supabase criado na sua conta.

**Avaliação:** para um dono só, o login é sólido:
- senha com hash forte;
- cookie assinado e inacessível ao JavaScript;
- limite de tentativas;
- revogação de sessões;
- troca de senha protegida.

## 2. O que depende do sistema antigo (RunPod/backend)

### Dependências diretas encontradas

1. **A tela de login liga a GPU sozinha.** Logo depois de um login certo, `LoginPage.tsx` chama `startStudio()` → `/api/runpod-wake`, e isso **cria ou religa um pod pago** (linhas 187–188).
   - Explica parte da queda de saldo: todo login ligava uma GPU.
   - É o primeiro ponto a desligar.
2. **Token do backend antigo.** `backendApiToken()` (em `_auth.ts`) deriva do mesmo `SESSION_SECRET` o token `X-Luna-Token` que o pod usa. Só serve ao backend antigo; o login não depende dele.
3. **Aviso do pod no Telegram.** `api/notify.ts` aceita mensagens assinadas com uma chave derivada de `RUNPOD_API_KEY` (é o pod que chama).
4. **Funções do RunPod:** `runpod-status`, `runpod-wake`, `runpod-stop` e `runpod-bootstrap`, mais `_runpod.ts`, `_s3.ts` e `_external.ts`.
5. **Dados do app.** Personas, galeria, histórico, voz e configurações vêm do backend do pod (`src/api/client.ts`, com `apiBase` vindo de `/api/runpod-status`). Sem o pod, o app entra, mas fica vazio.

### Não depende do RunPod

- `login`, `logout`, `session`, `change-password` e `profile`;
- `_auth`, `_password`, `_ratelimit` e `_store`;
- a configuração do bot do Telegram (`telegram.ts`, `_telegram.ts`), que guarda o token no Upstash e pode ser reaproveitada no bot de vendas.

## 3. Variáveis de ambiente (encontradas no código; os valores ficam na Vercel)

| Variável | Uso | Ao tirar o RunPod |
|---|---|---|
| `APP_LOGIN_EMAIL` | e-mail do login, perfil | **manter** |
| `APP_LOGIN_PASSWORD` | senha inicial (até a primeira troca) | **manter** |
| `SESSION_SECRET` | assinatura do cookie (+ token do pod) | **manter** (trocar o valor derruba todas as sessões) |
| `KV_REST_API_URL` / `KV_REST_API_TOKEN` (ou `UPSTASH_REDIS_REST_*`) | hash da senha, versão de sessão, limite de tentativas, Telegram | **manter**; confirmar na Vercel que está conectado |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | bot do Telegram (alternativa ao token salvo no app) | manter |
| `RUNPOD_API_KEY` | pods + chave do aviso do pod | manter até desligar o RunPod de vez; depois avaliar |
| `RUNPOD_S3_ACCESS_KEY` / `RUNPOD_S3_SECRET_KEY` | sincronização dos volumes | idem |
| `RUNPOD_SSH_PRIVATE_KEY` / `RUNPOD_SSH_USER` | bootstrap do pod | idem |
| `LUNA_MAX_GPU_PRICE`, `LUNA_BACKEND_URL` | escolha de GPU / backend externo | idem |
| `VITE_API_BASE_URL` (build) | reserva do endereço do backend | idem |

Nenhuma variável deve ser removida agora.

## 4. Precisamos trocar a autenticação?

**Não, por enquanto.** O login atual já é independente do RunPod e seguro para um dono só. As funcionalidades previstas (biblioteca, calendário, aprovação, Instagram/TikTok, bot do Telegram, relatórios) não exigem trocar o login se for só você usando.

**Trocar para Supabase Auth passa a valer quando houver:**
- mais de uma pessoa entrando (equipe, editor, aprovador) com permissões diferentes;
- segurança por usuário no banco (RLS por `auth.uid()`);
- verificação em duas etapas (MFA) ou login social;
- recuperação de senha por e-mail.

| | Login atual | Supabase Auth |
|---|---|---|
| Custo | sem custo novo (Vercel + Upstash grátis no uso atual) | grátis no plano Free; o Pro custa ~US$ 25/mês (conferir o preço atual) |
| Usuários | 1 | vários, com papéis |
| Risco de migrar | nenhum | médio: muda cookies, telas, sessões e todas as funções que conferem login |
| MFA / recuperação de senha | não | sim |

## 5. Arquitetura recomendada (a menor mudança segura)

**Fase 1: desligar o RunPod do caminho, sem apagar nada**
- Login, sessão e funções de auth ficam **como estão**.
- A tela de login para de ligar a GPU; o app entra direto.
- As telas de geração ficam escondidas por uma chave de configuração (ex.: `VITE_STUDIO_MODE=manual`). O código antigo continua no repositório (rollback = desligar a chave).

**Fase 2: dados e arquivos no Supabase (Postgres + Storage), acessados só pelas funções da Vercel**
- O navegador **nunca** recebe chave do Supabase. As funções da Vercel usam a `service_role` e só respondem com a sessão atual válida (`isAuthenticated`).
- Bucket **privado**. O upload vai por URL assinada de upload, direto do navegador para o Storage: o corpo das funções da Vercel é limitado, e vídeo não cabe nele. A leitura é por URL assinada de curta duração.
- RLS ligado em todas as tabelas, sem política para `anon`/`authenticated` (só a `service_role` acessa). Assim fica pronto para o multiusuário depois.
- Tabelas iniciais:
  - `media`: arquivo, tipo, tamanho, hash, status;
  - `posts`: legenda, hashtags, destino, data;
  - `approvals`;
  - `audit_log`: histórico de operações.

**Fase 3 (só se houver equipe):** migrar o login para Supabase Auth com RLS por usuário.

**Alternativa sem Supabase:** Vercel Postgres (Neon) + Vercel Blob, tudo dentro da Vercel. Vale comparar preço de armazenamento e tráfego de vídeo antes de decidir. Como você citou o Supabase e ele junta banco + arquivos + auth futura, ele continua sendo a recomendação.

## 6. Arquivos que pretendo modificar (depois da sua validação)

**Fase 1 (não destrutiva)**
- `frontend/src/components/LoginPage.tsx`: depois do login, entrar direto, sem `startStudio()`.
- `frontend/src/App.tsx`: não carregar personas/config do pod no modo manual; esconder as abas de geração.
- `frontend/src/components/Sidebar.tsx`: menu do modo manual.
- `frontend/src/api/client.ts`: não procurar o pod no modo manual.

**Fase 2 (novos, sem tocar no login)**
- `frontend/api/_supabase.ts`: cliente do servidor.
- `frontend/api/media-upload-url.ts`, `media-list.ts`, `media-url.ts`, `media-archive.ts`.
- `supabase/migrations/0001_init.sql`: tabelas, RLS e bucket privado.
- Variáveis novas na Vercel (só servidor): `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`.

**Não mexo:** `_auth.ts`, `_password.ts`, `_ratelimit.ts`, `_store.ts`, `login.ts`, `logout.ts`, `session.ts`, `change-password.ts`, `profile.ts`, nem nas variáveis atuais.

## 7. O que preciso que você confirme

1. Você já tem um projeto Supabase criado? Se sim, em qual plano (Free ou Pro)?
2. Na Vercel, o Upstash Redis está conectado ao projeto? (Se a troca de senha já funcionou pelo "Meu perfil", está.)
3. Só você vai usar o sistema, ou haverá outras pessoas (com permissões diferentes)?
4. Volume esperado de vídeos e imagens por mês: define se o plano grátis de armazenamento basta.

## 8. Riscos e observações

- **O mesmo segredo serve a duas coisas:** o `SESSION_SECRET` assina os cookies e também gera o token do pod. Quando o pod for desligado de vez, essa derivação pode sair do código.
- **Sem o Upstash** não há limite de tentativas nem revogação de sessões: o código cai para o modo antigo sem avisar.
- **Conta única:** quem tiver a senha tem acesso total. Não há MFA.
- **Integrações oficiais (Instagram, TikTok, Telegram para vendas):** cada plataforma tem regras próprias sobre o tipo de conteúdo e sobre pagamentos. Vale conferir as políticas antes de construir essas integrações.
