-- Luna Vox Studio - Fase 2b: publicacao automatica no Instagram (API oficial,
-- Instagram Login, conta profissional do proprio administrador).
-- Rodar depois da 0002; pode rodar de novo.
--
-- As imagens continuam no computador. A excecao, autorizada pelo usuario em
-- 21/10/2026: no momento da APROVACAO de um post automatico do Instagram, o
-- navegador envia uma copia das midias daquele post para o bucket privado
-- "publicar-temp". O Instagram busca por link assinado na hora de publicar e a
-- copia e apagada assim que a publicacao termina (ou e cancelada / falha).

-- Copias temporarias, presas a versao aprovada -----------------------------------------

create table if not exists public.post_uploads (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.posts (id) on delete cascade,
  post_version integer not null,
  position integer not null check (position >= 0),
  storage_path text not null unique,
  mime_type text not null check (mime_type in ('image/jpeg', 'video/mp4', 'video/quicktime')),
  size_bytes bigint not null default 0,
  uploaded_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  unique (post_id, post_version, position)
);

create index if not exists post_uploads_live_idx on public.post_uploads (post_id) where deleted_at is null;

-- Destino publicado pela API: guarda o conteiner do Instagram para que uma nova
-- tentativa consulte o status em vez de publicar de novo.
alter table public.post_destinations add column if not exists external_container_id text;
alter table public.post_destinations add column if not exists external_id text;
alter table public.post_destinations add column if not exists last_error text;

-- Conexao: conta do Instagram ligada e quando o token foi renovado.
alter table public.platform_connections add column if not exists external_user_id text;
alter table public.platform_connections add column if not exists token_refreshed_at timestamptz;

-- Novos tipos de tarefa -----------------------------------------------------------------------

alter table public.scheduled_jobs drop constraint if exists scheduled_jobs_kind_check;
alter table public.scheduled_jobs add constraint scheduled_jobs_kind_check
  check (kind in ('lembrete_publicacao', 'publicar_instagram'));

alter table public.publishing_attempts drop constraint if exists publishing_attempts_channel_check;
alter table public.publishing_attempts add constraint publishing_attempts_channel_check
  check (channel in ('lembrete_telegram', 'manual', 'api', 'instagram_api'));

-- Bucket privado (so o servidor gera links assinados). 50 MB = maximo do plano Free.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('publicar-temp', 'publicar-temp', false, 52428800, array['image/jpeg', 'video/mp4', 'video/quicktime'])
on conflict (id) do nothing;

-- Acesso --------------------------------------------------------------------------------------

alter table public.post_uploads enable row level security;
revoke all on table public.post_uploads from anon, authenticated;
grant select, insert, update on table public.post_uploads to service_role;
revoke delete, truncate on table public.post_uploads from service_role;
