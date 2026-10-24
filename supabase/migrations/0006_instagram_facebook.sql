-- Luna Vox Studio - Instagram tambem pela conexao do Facebook (API do
-- Instagram com login do Facebook), que libera a musica da biblioteca do
-- Instagram nos Reels (Instagram Audio API). Rodar depois da 0005; pode rodar
-- de novo.
--
-- provider: como a conexao foi feita (null = antiga, Instagram Login).
-- backup_*: a conexao anterior (token cifrado e id) fica guardada ao trocar
-- para o Facebook, para poder voltar sem gerar token de novo.

alter table public.platform_connections add column if not exists provider text;
alter table public.platform_connections drop constraint if exists platform_connections_provider_check;
alter table public.platform_connections add constraint platform_connections_provider_check
  check (provider is null or provider in ('instagram_login', 'facebook_login'));

alter table public.platform_connections add column if not exists backup_ciphertext text;
alter table public.platform_connections add column if not exists backup_user_id text;
