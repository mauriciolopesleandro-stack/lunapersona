-- Luna Vox Studio - varias personas no mesmo estudio (10/11/2026: Cleuza).
-- Rodar uma vez no SQL Editor do Supabase; pode rodar de novo sem estragar nada.
--
-- So ACRESCENTA: cada tabela de conteudo, publicacao e venda ganha a coluna
-- persona, com 'luna' como padrao - tudo o que ja existe continua sendo da Luna
-- e o estudio antigo (que nao conhece a coluna) segue funcionando igual.
-- O estudio novo filtra e grava a persona sozinho (_supabase.ts).
--
-- Desfazer (rollback), se precisar: rodar o bloco "desfazer" no fim deste arquivo.

do $$
declare
  t text;
begin
  foreach t in array array[
    'campaigns', 'media_assets', 'media_usage',
    'social_accounts', 'platform_connections', 'posts', 'post_media', 'post_destinations',
    'approvals', 'scheduled_jobs', 'publishing_attempts', 'post_uploads',
    'content_plans', 'content_plan_items',
    'sales_products', 'sales_product_media', 'fans', 'sales_orders', 'sales_broadcasts'
  ] loop
    execute format('alter table public.%I add column if not exists persona text not null default %L', t, 'luna');
    execute format('alter table public.%I drop constraint if exists %I', t, t || '_persona_check');
    execute format('alter table public.%I add constraint %I check (persona ~ %L)', t, t || '_persona_check', '^[a-z0-9-]{2,30}$');
    execute format('create index if not exists %I on public.%I (persona)', t || '_persona_idx', t);
  end loop;
end $$;

-- O mesmo fa pode seguir as duas personas (um registro por persona) e cada
-- persona tem os seus pacotes com o mesmo slug (ensaio, album-vip, vip).
alter table public.fans drop constraint if exists fans_telegram_id_key;
alter table public.fans drop constraint if exists fans_persona_telegram_id_key;
alter table public.fans add constraint fans_persona_telegram_id_key unique (persona, telegram_id);

alter table public.sales_products drop constraint if exists sales_products_slug_key;
alter table public.sales_products drop constraint if exists sales_products_persona_slug_key;
alter table public.sales_products add constraint sales_products_persona_slug_key unique (persona, slug);

insert into public.characters (name, description)
values ('Cleuza', 'Coroa nordestina, influenciadora virtual')
on conflict (name) do nothing;

-- desfazer (so se precisar voltar atras; apaga o que for da Cleuza):
--   delete from public.fans where persona <> 'luna'; delete from public.sales_products where persona <> 'luna';
--   alter table public.fans drop constraint fans_persona_telegram_id_key;
--   alter table public.fans add constraint fans_telegram_id_key unique (telegram_id);
--   alter table public.sales_products drop constraint sales_products_persona_slug_key;
--   alter table public.sales_products add constraint sales_products_slug_key unique (slug);
--   (as colunas persona podem ficar: o estudio antigo nao as usa)
