-- Luna Vox Studio - bot de vendas do Telegram (+18). Rodar depois da 0007;
-- pode rodar de novo. So acrescenta tabelas novas: nada existente muda.
--
-- Os fas falam com um bot proprio de vendas (separado do bot de aprovacoes do
-- administrador). No /start confirmam que tem 18 anos ou mais, veem os pacotes
-- e compram por Pix. O estudio guarda:
--   sales_products        pacotes a venda (ensaio, album, VIP por assinatura)
--   sales_product_media   fotos/videos de cada pacote; o arquivo fica no PC e
--                         no Telegram (file_id do bot de vendas), nunca aqui
--   fans                  quem abriu o bot (id do Telegram), 18+ confirmado,
--                         se parou de receber novidades ou bloqueou o bot
--   sales_orders          pedidos (Pix) e entregas; VIP com acesso ate a data
--   sales_broadcasts      disparo diario (texto + previa borrada), aprovado
--                         pelo administrador antes de sair para os fas

create table if not exists public.sales_products (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9-]{2,40}$'),
  kind text not null check (kind in ('ensaio', 'album', 'vip')),
  title text not null check (length(title) between 1 and 80),
  description text not null default '' check (length(description) <= 600),
  price_cents integer not null check (price_cents between 100 and 100000),
  -- VIP: dias de acesso ao canal por pagamento
  period_days integer check (period_days between 1 and 366),
  active boolean not null default true,
  position integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint sales_products_vip_period check ((kind = 'vip') = (period_days is not null))
);

create table if not exists public.sales_product_media (
  product_id uuid not null references public.sales_products (id) on delete cascade,
  media_id uuid not null references public.media_assets (id),
  position integer not null check (position >= 0),
  kind text not null check (kind in ('photo', 'video')),
  telegram_file_id text not null check (length(telegram_file_id) between 10 and 300),
  added_at timestamptz not null default now(),
  primary key (product_id, media_id)
);

create table if not exists public.fans (
  id uuid primary key default gen_random_uuid(),
  telegram_id bigint not null unique,
  username text check (length(username) <= 64),
  first_name text check (length(first_name) <= 128),
  adult_confirmed_at timestamptz,
  started_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  -- /parar: nao recebe o disparo diario (compras continuam valendo)
  opted_out_at timestamptz,
  -- bloqueou o bot (Telegram avisa): para de tentar mandar
  blocked_at timestamptz
);

create table if not exists public.sales_orders (
  id uuid primary key default gen_random_uuid(),
  fan_id uuid not null references public.fans (id),
  product_id uuid not null references public.sales_products (id),
  amount_cents integer not null check (amount_cents > 0),
  status text not null default 'pendente' check (status in ('pendente', 'pago', 'entregue', 'expirado', 'cancelado')),
  gateway text check (length(gateway) <= 40),
  gateway_charge_id text unique check (length(gateway_charge_id) <= 120),
  pix_code text check (length(pix_code) <= 1000),
  created_at timestamptz not null default now(),
  paid_at timestamptz,
  delivered_at timestamptz,
  -- VIP: ate quando o fa pode ficar no canal
  access_until timestamptz,
  note text check (length(note) <= 500)
);

create index if not exists sales_orders_fan_idx on public.sales_orders (fan_id, created_at desc);
create index if not exists sales_orders_status_idx on public.sales_orders (status, created_at);

create table if not exists public.sales_broadcasts (
  id uuid primary key default gen_random_uuid(),
  day date not null,
  text text not null default '' check (length(text) <= 1000),
  media_id uuid references public.media_assets (id),
  teaser_file_id text check (length(teaser_file_id) <= 300),
  status text not null default 'rascunho' check (status in ('rascunho', 'em_analise', 'aprovado', 'enviando', 'enviado', 'reprovado', 'cancelado')),
  -- envio em lotes (o processador da fila roda a cada minuto)
  cursor_telegram_id bigint,
  sent_count integer not null default 0,
  fail_count integer not null default 0,
  created_at timestamptz not null default now(),
  approved_at timestamptz,
  finished_at timestamptz
);

create index if not exists sales_broadcasts_day_idx on public.sales_broadcasts (day, created_at);

-- pacotes iniciais (o administrador muda nome, preco e conteudo no estudio)
insert into public.sales_products (slug, kind, title, description, price_cents, period_days, position) values
  ('ensaio', 'ensaio', 'Ensaio', 'Um ensaio completo da Luna.', 990, null, 0),
  ('album-vip', 'album', 'Álbum VIP sem censura', 'O álbum sem censura.', 1990, null, 1),
  ('vip', 'vip', 'VIP completo', 'Fotos, vídeos e ensaios, com conteúdo novo quase todo dia no canal VIP.', 2990, 30, 2)
on conflict (slug) do nothing;

alter table public.sales_products enable row level security;
alter table public.sales_product_media enable row level security;
alter table public.fans enable row level security;
alter table public.sales_orders enable row level security;
alter table public.sales_broadcasts enable row level security;
