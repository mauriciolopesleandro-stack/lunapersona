-- Luna Vox Studio - mensagens que chamam o fa para comprar (CTA) no bot de
-- vendas. Rodar depois da 0008; pode rodar de novo. So acrescenta colunas.
--
--   fans.cta_sent_at / cta_count   ultima mensagem diaria mandada e quantas ja foram
--                                  (no maximo uma por dia; para quando vira VIP ou /parar)
--   sales_orders.reminded_at       lembrete do Pix gerado e nao pago (uma vez)

alter table public.fans add column if not exists cta_sent_at timestamptz;
alter table public.fans add column if not exists cta_count integer not null default 0;
alter table public.sales_orders add column if not exists reminded_at timestamptz;

create index if not exists fans_cta_idx on public.fans (cta_sent_at nulls first);
