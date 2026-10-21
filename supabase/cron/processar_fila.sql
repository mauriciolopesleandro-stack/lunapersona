-- Luna Vox Studio - agenda o processamento da fila (lembretes de publicacao)
-- a cada minuto, no proprio Supabase. Assim os lembretes saem mesmo com o
-- computador desligado e sem depender do cron da Vercel (no plano Hobby ele
-- so roda 1 vez por dia).
--
-- Antes de rodar:
--  1. Na Vercel, cadastre JOBS_SECRET (so servidor) com um valor aleatorio de
--     pelo menos 32 caracteres, e faca um novo deploy.
--  2. No Supabase, em Database > Extensions, ative pg_cron e pg_net (se o seu
--     plano nao oferecer alguma delas, o painel avisa; nesse caso use o botao
--     "Processar agora" do estudio).
--  3. Troque COLE_AQUI_O_JOBS_SECRET abaixo pelo mesmo valor da Vercel. Ele fica
--     guardado cifrado no Vault do Supabase, nao no texto do agendamento.

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

-- guarda (ou troca) o segredo no Vault
do $$
begin
  if exists (select 1 from vault.secrets where name = 'luna_jobs_secret') then
    perform vault.update_secret(
      (select id from vault.secrets where name = 'luna_jobs_secret'),
      'COLE_AQUI_O_JOBS_SECRET'
    );
  else
    perform vault.create_secret('COLE_AQUI_O_JOBS_SECRET', 'luna_jobs_secret', 'Luna Vox: chave do processador da fila');
  end if;
end;
$$;

-- recria o agendamento (pode rodar de novo)
select cron.unschedule('luna-vox-fila') where exists (select 1 from cron.job where jobname = 'luna-vox-fila');

select cron.schedule(
  'luna-vox-fila',
  '* * * * *',
  $$
  select net.http_post(
    url := 'https://luna-ai-studio-frontend.vercel.app/api/studio?r=jobs',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'luna_jobs_secret')
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- Conferir:   select * from cron.job_run_details order by start_time desc limit 5;
-- Parar:      select cron.unschedule('luna-vox-fila');
