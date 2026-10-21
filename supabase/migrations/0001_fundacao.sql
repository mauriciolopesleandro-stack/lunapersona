-- Luna Vox Studio - Fase 1 (fundacao): personagens, campanhas, biblioteca de
-- conteudo e auditoria. Rodar uma vez no SQL Editor do Supabase; pode rodar
-- de novo sem estragar nada (if not exists / on conflict / or replace).
--
-- Os arquivos (imagens e videos) ficam no computador do usuario. Aqui ficam
-- so os dados sobre eles; local_path e o caminho dentro da pasta conteudos/.
--
-- Seguranca: o navegador nunca fala com o banco. So as funcoes da Vercel
-- acessam, com a chave secreta (papel service_role). RLS ligado sem nenhuma
-- politica: anon e authenticated nao leem nem gravam nada.

create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Personagens ------------------------------------------------------------------

create table if not exists public.characters (
  id uuid primary key default gen_random_uuid(),
  name text not null unique check (length(trim(name)) between 1 and 80),
  description text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into public.characters (name, description)
values ('Luna Vox', 'Influenciadora virtual')
on conflict (name) do nothing;

-- Campanhas --------------------------------------------------------------------

create table if not exists public.campaigns (
  id uuid primary key default gen_random_uuid(),
  name text not null unique check (length(trim(name)) between 1 and 120),
  description text,
  starts_on date,
  ends_on date,
  status text not null default 'ativa' check (status in ('ativa', 'pausada', 'encerrada')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ends_on is null or starts_on is null or ends_on >= starts_on)
);

-- Biblioteca de conteudo ---------------------------------------------------------

create table if not exists public.media_assets (
  id uuid primary key default gen_random_uuid(),
  local_path text not null unique check (length(local_path) between 1 and 500),
  sha256 text check (sha256 ~ '^[0-9a-f]{64}$'),
  kind text not null check (kind in ('image', 'video')),
  mime_type text not null,
  size_bytes bigint not null check (size_bytes >= 0),
  file_modified_at timestamptz,
  title text not null check (length(trim(title)) between 1 and 200),
  description text,
  character_id uuid references public.characters (id) on delete set null,
  category text,
  campaign_id uuid references public.campaigns (id) on delete set null,
  caption text,
  hashtags text[] not null default '{}',
  -- onde o conteudo pode ser usado
  allowed_destinations text[] not null default '{}'
    check (allowed_destinations <@ array['instagram', 'tiktok', 'telegram']),
  -- livre: qualquer rede; sensivel: revisar antes; adulto: so Telegram (+18)
  content_rating text not null default 'livre' check (content_rating in ('livre', 'sensivel', 'adulto')),
  -- promocional (divulgacao) ou exclusivo para assinantes / packs pagos
  audience text not null default 'promocional' check (audience in ('promocional', 'assinantes')),
  status text not null default 'rascunho' check (status in ('rascunho', 'aprovado', 'arquivado')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- Instagram e TikTok proibem conteudo adulto: o banco recusa a combinacao.
  constraint media_adult_only_telegram check (
    content_rating <> 'adulto' or not (allowed_destinations && array['instagram', 'tiktok'])
  )
);

create index if not exists media_assets_status_idx on public.media_assets (status, created_at desc);
create index if not exists media_assets_sha256_idx on public.media_assets (sha256);
create index if not exists media_assets_character_idx on public.media_assets (character_id);
create index if not exists media_assets_campaign_idx on public.media_assets (campaign_id);

-- Historico de utilizacao de cada arquivo (post, pack, uso manual).
create table if not exists public.media_usage (
  id bigint generated always as identity primary key,
  media_id uuid not null references public.media_assets (id),
  used_at timestamptz not null default now(),
  channel text not null check (channel in ('instagram', 'tiktok', 'telegram', 'outro')),
  context text not null check (context in ('post', 'pack', 'manual')),
  reference text,
  note text
);

create index if not exists media_usage_media_idx on public.media_usage (media_id, used_at desc);

-- Auditoria ---------------------------------------------------------------------------

create table if not exists public.audit_logs (
  id bigint generated always as identity primary key,
  at timestamptz not null default now(),
  actor text not null default 'admin',
  action text not null,
  entity text not null,
  entity_id text,
  details jsonb not null default '{}'::jsonb
);

create index if not exists audit_logs_at_idx on public.audit_logs (at desc);
create index if not exists audit_logs_entity_idx on public.audit_logs (entity, entity_id);

-- updated_at automatico ------------------------------------------------------------------

drop trigger if exists characters_updated_at on public.characters;
create trigger characters_updated_at before update on public.characters
  for each row execute function public.set_updated_at();
drop trigger if exists campaigns_updated_at on public.campaigns;
create trigger campaigns_updated_at before update on public.campaigns
  for each row execute function public.set_updated_at();
drop trigger if exists media_assets_updated_at on public.media_assets;
create trigger media_assets_updated_at before update on public.media_assets
  for each row execute function public.set_updated_at();

-- Acesso ------------------------------------------------------------------------------------

alter table public.characters enable row level security;
alter table public.campaigns enable row level security;
alter table public.media_assets enable row level security;
alter table public.media_usage enable row level security;
alter table public.audit_logs enable row level security;

revoke all on table public.characters, public.campaigns, public.media_assets, public.media_usage, public.audit_logs
  from anon, authenticated;

grant select, insert, update on table public.characters, public.campaigns, public.media_assets to service_role;
grant select, insert on table public.media_usage, public.audit_logs to service_role;
-- O Supabase da todos os direitos ao service_role por padrao. O app so
-- arquiva; apagar fica para o dono, pelo painel do Supabase. Historico e
-- auditoria nao se alteram.
revoke delete, truncate on table public.characters, public.campaigns, public.media_assets from service_role;
revoke update, delete, truncate on table public.media_usage, public.audit_logs from service_role;
