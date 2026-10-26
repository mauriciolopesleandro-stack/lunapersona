-- Luna Vox Studio - planejamento feito pelo chat (Assistente). Rodar depois da
-- 0006; pode rodar de novo. So acrescenta tabelas novas: nada existente muda.
--
-- Um plano (semana/mes/trimestre) tem itens: cada item e uma publicacao a
-- criar - dia e hora, rede e destino, se e foto ou video, o prompt para a
-- Naiaflow, a legenda e as hashtags. O administrador aprova o plano (vendo a
-- estimativa de creditos) e so entao a automacao local (Python + Playwright no
-- PC) gera a midia, cria a publicacao e manda para a analise no bot do
-- Telegram. Item que reaproveita a midia de outro (TikTok com a foto do Feed
-- do mesmo dia) aponta para ele em source_item_id e nao gasta credito.
-- Conteudo +18 so pode ir para o canal do Telegram.

create table if not exists public.content_plans (
  id uuid primary key default gen_random_uuid(),
  title text not null default '' check (length(title) <= 200),
  request jsonb not null default '{}'::jsonb,
  status text not null default 'rascunho' check (status in ('rascunho', 'aprovado', 'cancelado')),
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.content_plan_items (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.content_plans (id) on delete cascade,
  position integer not null check (position >= 0),
  scheduled_at timestamptz not null,
  network text not null check (network in ('instagram', 'tiktok', 'telegram')),
  format text not null check (length(format) between 1 and 40),
  kind text not null default 'image' check (kind in ('image', 'video')),
  adult boolean not null default false,
  prompt text not null default '' check (length(prompt) <= 2000),
  caption text not null default '' check (length(caption) <= 4000),
  hashtags text[] not null default '{}',
  source_item_id uuid references public.content_plan_items (id) on delete set null,
  status text not null default 'planejado' check (status in (
    'planejado', 'na_fila', 'gerando', 'em_analise', 'aprovado', 'reprovado', 'falhou', 'cancelado'
  )),
  attempts integer not null default 0 check (attempts >= 0),
  max_attempts integer not null default 2 check (max_attempts between 1 and 5),
  media_path text,
  post_id uuid references public.posts (id) on delete set null,
  note text check (length(note) <= 1000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (plan_id, position),
  -- +18 nunca vai para Instagram ou TikTok
  constraint content_plan_items_adult_only_telegram check (not adult or network = 'telegram')
);

create index if not exists content_plan_items_queue_idx on public.content_plan_items (status, scheduled_at);
create index if not exists content_plan_items_plan_idx on public.content_plan_items (plan_id, position);

alter table public.content_plans enable row level security;
alter table public.content_plan_items enable row level security;
