-- Luna Vox Studio - Fase 2: publicacoes com versao e aprovacao, destinos, fila
-- de tarefas com tentativas e trava, registro de tentativas e contas/conexoes.
-- Rodar depois da 0001, no SQL Editor do Supabase; pode rodar de novo.
--
-- Como as imagens ficam no computador, Instagram e TikTok sao publicados a
-- mao (publish_mode = 'manual'): o sistema prepara, exige aprovacao, avisa no
-- horario e so marca como publicado quando o administrador confirma cada
-- destino. Nada e dado como publicado sem essa confirmacao.

-- Contas e conexoes (OAuth fica para quando houver publicacao automatica) ----------

create table if not exists public.social_accounts (
  id uuid primary key default gen_random_uuid(),
  network text not null check (network in ('instagram', 'tiktok', 'telegram')),
  handle text not null check (length(trim(handle)) between 1 and 100),
  display_name text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (network, handle)
);

create table if not exists public.platform_connections (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.social_accounts (id),
  mode text not null default 'manual' check (mode in ('manual', 'api')),
  status text not null default 'desconectado' check (status in ('desconectado', 'conectado', 'expirado', 'erro')),
  -- token da rede cifrado no servidor (nunca em texto puro, nunca volta ao navegador)
  token_ciphertext text,
  token_expires_at timestamptz,
  scopes text[] not null default '{}',
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (account_id)
);

-- Publicacoes ---------------------------------------------------------------------------

create table if not exists public.posts (
  id uuid primary key default gen_random_uuid(),
  network text not null check (network in ('instagram', 'tiktok', 'telegram')),
  account_id uuid references public.social_accounts (id) on delete set null,
  caption text not null default '' check (length(caption) <= 4000),
  hashtags text[] not null default '{}',
  scheduled_at timestamptz,
  status text not null default 'rascunho' check (status in (
    'rascunho', 'aguardando_aprovacao', 'aprovado', 'agendado', 'publicando', 'publicado', 'falhou', 'cancelado'
  )),
  publish_mode text not null default 'manual' check (publish_mode in ('manual', 'api')),
  -- versao do conteudo: muda quando midia, legenda, hashtags, rede ou destinos mudam
  version integer not null default 1 check (version >= 1),
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  approved_version integer,
  approved_hash text,
  failure_reason text,
  published_at timestamptz,
  cancelled_at timestamptz,
  legacy_id text unique, -- id da publicacao importada do luna-vox.json
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- aprovado/agendado/publicando/publicado so com a aprovacao da versao atual
  -- (is not null explicito: um CHECK com resultado nulo passaria)
  constraint posts_approval_is_current check (
    status in ('rascunho', 'aguardando_aprovacao', 'falhou', 'cancelado')
    or (approved_version is not null and approved_hash is not null
        and approved_version = version and approved_hash = content_hash)
  ),
  constraint posts_schedule_has_time check (status not in ('agendado', 'publicando') or scheduled_at is not null),
  constraint posts_published_has_time check (status <> 'publicado' or published_at is not null)
);

create index if not exists posts_status_idx on public.posts (status, scheduled_at);

create table if not exists public.post_media (
  post_id uuid not null references public.posts (id) on delete cascade,
  media_id uuid not null references public.media_assets (id),
  position integer not null check (position >= 0),
  primary key (post_id, position),
  unique (post_id, media_id)
);

create index if not exists post_media_media_idx on public.post_media (media_id);

-- Cada destino (Feed, Reels, Stories...) e confirmado separadamente.
create table if not exists public.post_destinations (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.posts (id) on delete cascade,
  format text not null check (length(format) between 1 and 40),
  status text not null default 'pendente' check (status in ('pendente', 'publicado', 'falhou', 'cancelado')),
  external_url text check (external_url is null or external_url ~ '^https://'),
  confirmed_at timestamptz,
  confirmed_by text check (confirmed_by in ('admin', 'api', 'importado')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (post_id, format),
  constraint destination_published_is_confirmed check (status <> 'publicado' or (confirmed_at is not null and confirmed_by is not null))
);

create table if not exists public.approvals (
  id bigint generated always as identity primary key,
  post_id uuid not null references public.posts (id) on delete cascade,
  version integer not null,
  content_hash text not null,
  decision text not null check (decision in ('aprovado', 'reprovado')),
  note text,
  decided_at timestamptz not null default now(),
  decided_by text not null default 'admin'
);

create index if not exists approvals_post_idx on public.approvals (post_id, decided_at desc);

-- Fila de tarefas ----------------------------------------------------------------------------

create table if not exists public.scheduled_jobs (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('lembrete_publicacao')),
  post_id uuid not null references public.posts (id) on delete cascade,
  post_version integer not null,
  run_at timestamptz not null,
  status text not null default 'pendente' check (status in ('pendente', 'executando', 'concluido', 'falhou', 'cancelado')),
  attempts integer not null default 0 check (attempts >= 0),
  max_attempts integer not null default 4 check (max_attempts between 1 and 10),
  locked_at timestamptz,
  last_error text,
  -- a mesma tarefa nunca entra duas vezes (ex.: lembrete:<post>:v<versao>:<horario>)
  idempotency_key text not null unique,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists scheduled_jobs_due_idx on public.scheduled_jobs (status, run_at);

create table if not exists public.publishing_attempts (
  id bigint generated always as identity primary key,
  post_id uuid not null references public.posts (id) on delete cascade,
  job_id uuid references public.scheduled_jobs (id) on delete set null,
  attempt integer not null default 1,
  channel text not null check (channel in ('lembrete_telegram', 'manual', 'api')),
  outcome text not null check (outcome in ('ok', 'erro')),
  detail text,
  started_at timestamptz not null default now(),
  finished_at timestamptz not null default now()
);

create index if not exists publishing_attempts_post_idx on public.publishing_attempts (post_id, started_at desc);

-- Ultima execucao do processador de tarefas (monitoramento no painel).
create table if not exists public.worker_status (
  id integer primary key default 1 check (id = 1),
  last_run_at timestamptz not null default now(),
  last_result jsonb not null default '{}'::jsonb
);

-- Pega tarefas vencidas com trava: duas execucoes ao mesmo tempo nunca pegam
-- a mesma tarefa (skip locked). Tarefa presa ha mais de 5 min volta a fila.
create or replace function public.claim_jobs(p_limit integer default 10)
returns setof public.scheduled_jobs
language sql
as $$
  with claimed as (
    update public.scheduled_jobs j
       set status = 'executando', locked_at = now(), attempts = j.attempts + 1, updated_at = now()
     where j.id in (
       select id from public.scheduled_jobs
        where (status = 'pendente' and run_at <= now())
           or (status = 'executando' and locked_at < now() - interval '5 minutes')
        order by run_at
        limit greatest(1, least(p_limit, 50))
        for update skip locked
     )
    returning j.*
  )
  select * from claimed order by run_at;
$$;

-- updated_at automatico (funcao criada na 0001) ------------------------------------------

drop trigger if exists social_accounts_updated_at on public.social_accounts;
create trigger social_accounts_updated_at before update on public.social_accounts
  for each row execute function public.set_updated_at();
drop trigger if exists platform_connections_updated_at on public.platform_connections;
create trigger platform_connections_updated_at before update on public.platform_connections
  for each row execute function public.set_updated_at();
drop trigger if exists posts_updated_at on public.posts;
create trigger posts_updated_at before update on public.posts
  for each row execute function public.set_updated_at();
drop trigger if exists post_destinations_updated_at on public.post_destinations;
create trigger post_destinations_updated_at before update on public.post_destinations
  for each row execute function public.set_updated_at();
drop trigger if exists scheduled_jobs_updated_at on public.scheduled_jobs;
create trigger scheduled_jobs_updated_at before update on public.scheduled_jobs
  for each row execute function public.set_updated_at();

-- Acesso ---------------------------------------------------------------------------------------

alter table public.social_accounts enable row level security;
alter table public.platform_connections enable row level security;
alter table public.posts enable row level security;
alter table public.post_media enable row level security;
alter table public.post_destinations enable row level security;
alter table public.approvals enable row level security;
alter table public.scheduled_jobs enable row level security;
alter table public.publishing_attempts enable row level security;
alter table public.worker_status enable row level security;

revoke all on table public.social_accounts, public.platform_connections, public.posts, public.post_media,
  public.post_destinations, public.approvals, public.scheduled_jobs, public.publishing_attempts, public.worker_status
  from anon, authenticated;

grant select, insert, update on table public.social_accounts, public.platform_connections, public.posts,
  public.post_destinations, public.scheduled_jobs, public.worker_status to service_role;
-- a lista de midias de uma publicacao e trocada inteira ao editar
grant select, insert, delete on table public.post_media to service_role;
grant select, insert on table public.approvals, public.publishing_attempts to service_role;
-- O app nunca apaga publicacoes (cancela); aprovacoes e tentativas sao historico.
revoke delete, truncate on table public.social_accounts, public.platform_connections, public.posts,
  public.post_destinations, public.scheduled_jobs, public.worker_status from service_role;
revoke update, delete, truncate on table public.approvals, public.publishing_attempts from service_role;
revoke truncate on table public.post_media from service_role;

-- funcoes nascem executaveis por todos: so o servidor pode pegar tarefas
revoke execute on function public.claim_jobs(integer) from public, anon, authenticated;
grant execute on function public.claim_jobs(integer) to service_role;
