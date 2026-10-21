-- Fase 2: biblioteca de conteudo (imagens e videos enviados a mao).
-- Rodar uma vez no SQL Editor do Supabase. Pode rodar de novo sem estragar
-- nada (if not exists / on conflict).
--
-- Seguranca: o navegador nunca fala com o banco. So as funcoes da Vercel
-- (api/media.ts) acessam, com a chave secreta (papel service_role). RLS
-- ligado sem nenhuma politica: anon e authenticated nao leem nem gravam.

create table if not exists public.media (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  kind text not null check (kind in ('image', 'video')),
  -- uploading: linha criada, arquivo ainda subindo (some da lista se o
  -- envio falhar); ready: na biblioteca; archived: escondido, nao apagado.
  status text not null default 'uploading' check (status in ('uploading', 'ready', 'archived')),
  storage_path text not null unique,
  original_name text not null,
  mime_type text not null,
  size_bytes bigint not null check (size_bytes > 0),
  sha256 text,
  title text,
  notes text
);

create index if not exists media_status_created_idx on public.media (status, created_at desc);
create index if not exists media_sha256_idx on public.media (sha256);

-- Historico de operacoes (quem e sempre o dono: o login e de conta unica).
create table if not exists public.audit_log (
  id bigint generated always as identity primary key,
  at timestamptz not null default now(),
  action text not null,
  media_id uuid references public.media (id) on delete set null,
  details jsonb not null default '{}'::jsonb
);

create index if not exists audit_log_at_idx on public.audit_log (at desc);

alter table public.media enable row level security;
alter table public.audit_log enable row level security;

revoke all on table public.media from anon, authenticated;
revoke all on table public.audit_log from anon, authenticated;
grant select, insert, update on table public.media to service_role;
grant select, insert on table public.audit_log to service_role;
-- O Supabase da todos os direitos ao service_role por padrao; o app so
-- arquiva, entao apagar fica so para o dono, pelo painel.
revoke delete, truncate on table public.media from service_role;
revoke update, delete, truncate on table public.audit_log from service_role;

-- Bucket privado. 50 MB e o maximo do plano Free; no Pro, aumentar aqui e
-- em MEDIA_MAX_MB na Vercel.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'luna-media',
  'luna-media',
  false,
  52428800,
  array['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/quicktime', 'video/webm']
)
on conflict (id) do nothing;
