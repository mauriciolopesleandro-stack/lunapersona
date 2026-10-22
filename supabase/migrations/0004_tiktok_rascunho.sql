-- Luna Vox Studio - TikTok: video enviado como RASCUNHO para a caixa de entrada
-- do TikTok (Content Posting API, escopo video.upload). Rodar depois da 0003;
-- pode rodar de novo.
--
-- No horario, o estudio envia o video (copia temporaria da versao aprovada) e o
-- TikTok avisa o criador no app; ele poe a musica e publica. O estudio consulta
-- o status ate o TikTok responder PUBLISH_COMPLETE e so entao marca publicado.
-- O publish_id do TikTok fica em post_destinations.external_id.

alter table public.scheduled_jobs drop constraint if exists scheduled_jobs_kind_check;
alter table public.scheduled_jobs add constraint scheduled_jobs_kind_check
  check (kind in ('lembrete_publicacao', 'publicar_instagram', 'enviar_tiktok'));

alter table public.publishing_attempts drop constraint if exists publishing_attempts_channel_check;
alter table public.publishing_attempts add constraint publishing_attempts_channel_check
  check (channel in ('lembrete_telegram', 'manual', 'api', 'instagram_api', 'tiktok_api'));
