-- Luna Vox Studio - uma publicacao para varias redes. Rodar depois da 0004;
-- pode rodar de novo.
--
-- O compositor cria uma publicacao por rede (cada uma com a sua fila, versao e
-- aprovacao, como antes) ligadas pelo mesmo group_id; a aprovacao e feita de
-- uma vez para o grupo. As "flags" sao opcoes neutras (feito com IA, musica
-- sugerida, comentarios desligados, capa) que o estudio traduz para cada rede.
-- Nada existente muda: publicacoes antigas ficam sem grupo e sem flags.

alter table public.posts add column if not exists group_id uuid;
alter table public.posts add column if not exists flags jsonb not null default '{}'::jsonb;

alter table public.posts drop constraint if exists posts_flags_is_object;
alter table public.posts add constraint posts_flags_is_object check (jsonb_typeof(flags) = 'object');

create index if not exists posts_group_idx on public.posts (group_id) where group_id is not null;
