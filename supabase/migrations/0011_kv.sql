-- 25/11/2026: configuracoes pequenas (antes no Upstash) numa tabela do Supabase.
create table if not exists public.kv (
  key text primary key,
  value text not null,
  expires_at timestamptz
);
alter table public.kv enable row level security;

create or replace function public.kv_set_nx(p_key text, p_value text, p_ttl integer)
returns boolean language plpgsql as $$
declare ok boolean;
begin
  delete from public.kv where key = p_key and expires_at is not null and expires_at <= now();
  insert into public.kv(key, value, expires_at)
  values (p_key, p_value, case when p_ttl > 0 then now() + make_interval(secs => p_ttl) end)
  on conflict (key) do nothing;
  ok := found;
  if random() < 0.01 then
    delete from public.kv where expires_at is not null and expires_at <= now();
  end if;
  return ok;
end $$;

create or replace function public.kv_incr(p_key text, p_ttl integer)
returns bigint language plpgsql as $$
declare n bigint;
begin
  delete from public.kv where key = p_key and expires_at is not null and expires_at <= now();
  insert into public.kv(key, value, expires_at)
  values (p_key, '1', case when p_ttl > 0 then now() + make_interval(secs => p_ttl) end)
  on conflict (key) do update set value = (coalesce(nullif(public.kv.value, ''), '0')::bigint + 1)::text
  returning value::bigint into n;
  return n;
end $$;
