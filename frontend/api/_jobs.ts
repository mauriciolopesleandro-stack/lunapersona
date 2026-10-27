import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated, safeEqual } from "./_auth.js";
import {
  InstagramError,
  igConnection,
  igCreateContainer,
  igPermalink,
  igPublish,
  igRefreshIfDue,
  igWaitReady,
  type IgConnection,
} from "./_instagram.js";
import type { PostFlags } from "./_flags.js";
import { NETWORK_RULES, UPLOAD_BUCKET, cleanupUploads, type PostRow } from "./_posts.js";
import { TikTokError, publishStatus, recentVideos, tiktokAccessToken, tiktokConnectionRow, uploadDraft, uploadPhotoDraft, type TikTokVideo } from "./_tiktok.js";
import { tiktokMediaUrl } from "./_tiktok_media.js";
import { sendMediaNotice, sendToChannel } from "./_tg_review.js";
import { dbInsert, dbRpc, dbSelect, dbUpdate, dbUpdateWhere, dbUpsert, storageReadUrl, supabaseConfigured } from "./_supabase.js";
import { sendNotice, telegramChannel, telegramConfig, type TelegramChannel } from "./_telegram.js";

// /api/studio?r=jobs - processa a fila (scheduled_jobs). Chamado:
//  - a cada minuto pelo Supabase Cron (POST com "Authorization: Bearer <JOBS_SECRET>"),
//    para funcionar com o computador desligado;
//  - ou pelo botao "Processar agora" do painel (com a sessao do administrador).
// Nada roda em segundo plano na Vercel: cada chamada pega as tarefas vencidas
// (com trava no banco), executa e termina.
//
// Tarefa "lembrete_publicacao": no horario, a publicacao aprovada passa para
// "publicando" e o administrador recebe o aviso no Telegram para publicar a
// mao e confirmar no estudio. Falha no aviso: tenta de novo em 2, 4, 8 min.
// Telegram +18 com o canal conectado (bot administrador do canal): no horario
// o bot posta no canal e a publicacao fica "publicado", sem lembrete.
//
// Tarefa "publicar_instagram" (modo automatico): publica pela API oficial,
// destino a destino, usando a copia temporaria da versao aprovada. O conteiner
// de cada destino fica guardado: uma nova tentativa consulta o status dele em
// vez de publicar de novo. Video em processamento reagenda em 1 min sem gastar
// tentativa. Terminou: apaga as copias e avisa no Telegram. Falhou de vez:
// marca "falhou" e avisa para publicar a mao.
//
// Tarefa "enviar_tiktok" (rascunho automatico): no horario envia o video (ou
// as fotos, que o TikTok baixa por link assinado no endereco verificado do
// site) da copia temporaria da versao aprovada para a caixa de entrada e
// guarda o publish_id no destino. Depois consulta o status: chegou na caixa de
// entrada -> apaga a copia e avisa; o criador poe a musica e publica no app;
// so quando o TikTok responde PUBLISH_COMPLETE o destino vira "publicado".
// Sem resposta em 72 h para de consultar (da para confirmar a mao no estudio).

type Outcome = "ok" | "obsoleta" | "nova_tentativa" | "falhou" | "aguardando";

interface DestinationRow {
  id: string;
  format: string;
  status: string;
  external_container_id: string | null;
}

interface UploadRow {
  position: number;
  storage_path: string;
  mime_type: string;
}

interface JobRow {
  id: string;
  kind: string;
  post_id: string;
  post_version: number;
  run_at: string;
  attempts: number;
  max_attempts: number;
}

const BATCH = 10;

function authorizedByCron(req: IncomingMessage): boolean {
  const secret = process.env.JOBS_SECRET;
  const header = req.headers.authorization ?? "";
  return Boolean(secret && secret.length >= 32 && header.startsWith("Bearer ") && safeEqual(header.slice(7), secret));
}

function appUrl(req: IncomingMessage): string {
  if (process.env.PUBLIC_APP_URL) return process.env.PUBLIC_APP_URL.replace(/\/+$/, "");
  const host = req.headers["x-forwarded-host"] ?? req.headers.host;
  return host ? `https://${host}` : "";
}

async function finish(job: JobRow, patch: Record<string, unknown>) {
  await dbUpdate("scheduled_jobs", `id=eq.${job.id}`, { ...patch, locked_at: null });
}

async function runReminder(job: JobRow, url: string): Promise<"ok" | "obsoleta" | "nova_tentativa" | "falhou"> {
  const [post] = await dbSelect<PostRow>("posts", `select=*&id=eq.${job.post_id}`);
  // Publicacao mudou, foi cancelada ou ja publicada: o lembrete perdeu o sentido.
  const current = post && post.version === job.post_version && (post.status === "agendado" || post.status === "publicando");
  if (!current) {
    await finish(job, { status: "cancelado", finished_at: new Date().toISOString(), last_error: "publicação mudou ou foi cancelada" });
    return "obsoleta";
  }
  if (post.status === "agendado") {
    // so passa se continuar agendada na mesma versao (outra execucao pode ter mexido)
    await dbUpdate("posts", `id=eq.${post.id}&status=eq.agendado&version=eq.${post.version}`, { status: "publicando" });
  }
  // +18 com o canal conectado: o bot posta sozinho, sem lembrete
  if (post.network === "telegram") {
    const channel = await telegramChannel().catch(() => null);
    if (channel) return runChannel(job, post, channel);
  }
  const label = NETWORK_RULES[post.network].label;
  const dests = await dbSelect<{ format: string }>("post_destinations", `select=format&post_id=eq.${post.id}&status=eq.pendente`);
  const text = [
    `⏰ Hora de publicar no ${label} (${dests.map((d) => d.format).join(", ") || "sem destino"}).`,
    post.caption ? `Legenda: ${post.caption.slice(0, 300)}${post.caption.length > 300 ? "…" : ""}` : "Sem legenda.",
    `Depois de publicar, confirme no estúdio${url ? `: ${url}` : "."}`,
  ].join("\n\n");
  try {
    // Telegram (+18): a midia aprovada no bot volta junto, pronta para postar no canal
    const copies =
      post.network === "telegram"
        ? await dbSelect<{ storage_path: string; mime_type: string }>(
            "post_uploads",
            `select=storage_path,mime_type&post_id=eq.${post.id}&post_version=eq.${post.version}&deleted_at=is.null&order=position.asc`
          )
        : [];
    if (copies.length) await sendMediaNotice(copies, text);
    else await sendNotice(await telegramConfig(), text);
    await finish(job, { status: "concluido", finished_at: new Date().toISOString(), last_error: null });
    await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "lembrete_telegram", outcome: "ok", detail: "aviso enviado" });
    return "ok";
  } catch (e) {
    const error = (e instanceof Error ? e.message : String(e)).slice(0, 300);
    await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "lembrete_telegram", outcome: "erro", detail: error });
    if (job.attempts >= job.max_attempts) {
      await finish(job, { status: "falhou", finished_at: new Date().toISOString(), last_error: error });
      return "falhou";
    }
    const retryAt = new Date(Date.now() + 2 ** job.attempts * 60_000).toISOString();
    await finish(job, { status: "pendente", run_at: retryAt, last_error: error });
    return "nova_tentativa";
  }
}

async function notify(text: string) {
  await sendNotice(await telegramConfig(), text).catch(() => undefined);
}

// Canal +18: posta a versao aprovada pelo bot e marca publicada. Falhou: tenta
// de novo em 2, 4, 8 min; sem tentativas, avisa para postar a mao (a
// publicacao fica "publicando", para confirmar no estudio).
async function runChannel(job: JobRow, post: PostRow, channel: TelegramChannel): Promise<"ok" | "nova_tentativa" | "falhou"> {
  try {
    const { messageId, url } = await sendToChannel(post, channel);
    const now = new Date().toISOString();
    await dbUpdateWhere("post_destinations", `post_id=eq.${post.id}&status=eq.pendente`, {
      status: "publicado",
      external_id: String(messageId),
      external_url: url,
      confirmed_at: now,
      confirmed_by: "api",
    });
    await dbUpdate("posts", `id=eq.${post.id}&version=eq.${post.version}`, { status: "publicado", published_at: now });
    await finish(job, { status: "concluido", finished_at: now, last_error: null });
    await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "api", outcome: "ok", detail: `postado no canal ${channel.title}` });
    for (const m of await dbSelect<{ media_id: string }>("post_media", `select=media_id&post_id=eq.${post.id}`)) {
      await dbInsert("media_usage", { media_id: m.media_id, channel: "telegram", context: "post", reference: post.id });
    }
    await cleanupUploads(post.id);
    await notify(`✅ Postado no canal “${channel.title}”.\n${url}`);
    return "ok";
  } catch (e) {
    const error = (e instanceof Error ? e.message : String(e)).slice(0, 300);
    await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "api", outcome: "erro", detail: `canal: ${error}` });
    if (job.attempts >= job.max_attempts) {
      await finish(job, { status: "falhou", finished_at: new Date().toISOString(), last_error: error });
      await notify(`⚠️ Não consegui postar no canal “${channel.title}”: ${error}\n\nPoste à mão e confirme no estúdio.`);
      return "falhou";
    }
    await finish(job, { status: "pendente", run_at: new Date(Date.now() + 2 ** job.attempts * 60_000).toISOString(), last_error: error });
    return "nova_tentativa";
  }
}

async function retryOrFail(job: JobRow, post: PostRow, error: string, retryable: boolean): Promise<Outcome> {
  const tiktok = job.kind === "enviar_tiktok";
  const channel = tiktok ? "tiktok_api" : "instagram_api";
  await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel, outcome: "erro", detail: error.slice(0, 300) });
  if (retryable && job.attempts < job.max_attempts) {
    const retryAt = new Date(Date.now() + 2 ** job.attempts * 60_000).toISOString();
    await finish(job, { status: "pendente", run_at: retryAt, last_error: error.slice(0, 300) });
    return "nova_tentativa";
  }
  await finish(job, { status: "falhou", finished_at: new Date().toISOString(), last_error: error.slice(0, 300) });
  await dbUpdate("posts", `id=eq.${post.id}&version=eq.${post.version}`, { status: "falhou", failure_reason: `Publicação automática falhou: ${error}`.slice(0, 500) });
  await cleanupUploads(post.id);
  await notify(
    tiktok
      ? `⚠️ Não consegui enviar o rascunho para o TikTok: ${error}\n\nPublique à mão pelo app e marque no estúdio.`
      : `⚠️ Não consegui publicar no Instagram: ${error}\n\nPublique à mão pelo app e marque no estúdio.`
  );
  return "falhou";
}

// Cria o conteiner de um destino (Feed: foto, video ou carrossel; Reels; Stories).
// Reels com musica da biblioteca do Instagram: audio_configuration (Audio API,
// so pela conexao do Facebook); volume da musica 90 e do som do video 40.
async function createContainer(conn: IgConnection, format: string, uploads: UploadRow[], caption: string, flags: PostFlags): Promise<string> {
  const urls = await Promise.all(uploads.map((u) => storageReadUrl(UPLOAD_BUCKET, u.storage_path, 6 * 3600)));
  const isVideo = (u: UploadRow) => u.mime_type.startsWith("video/");
  const create = (params: Record<string, string>) => igCreateContainer(conn.userId, conn.token, params, conn.host);
  if (format === "Stories") {
    return create({ media_type: "STORIES", [isVideo(uploads[0]) ? "video_url" : "image_url"]: urls[0] });
  }
  if (format === "Reels" || (uploads.length === 1 && isVideo(uploads[0]))) {
    const params: Record<string, string> = { media_type: "REELS", video_url: urls[0], caption };
    if (format === "Reels" && flags.igAudio && conn.provider === "facebook_login") {
      params.audio_configuration = JSON.stringify({ audio_id: flags.igAudio.id, audio_volume: 90, video_volume: 40 });
    }
    return create(params);
  }
  if (uploads.length === 1) return create({ image_url: urls[0], caption });
  const children: string[] = [];
  for (const [i, u] of uploads.entries()) {
    children.push(await create(isVideo(u) ? { media_type: "VIDEO", video_url: urls[i], is_carousel_item: "true" } : { image_url: urls[i], is_carousel_item: "true" }));
  }
  return create({ media_type: "CAROUSEL", children: children.join(","), caption });
}

// Midia ainda processando no Instagram: volta em 1 min sem gastar tentativa
// (ate o limite de 30 voltas, para nao ficar preso para sempre).
async function waitMore(job: JobRow, post: PostRow): Promise<Outcome> {
  const waits = (await dbSelect<{ id: number }>("publishing_attempts", `select=id&job_id=eq.${job.id}&channel=eq.instagram_api&detail=eq.aguardando%20processamento`)).length;
  if (waits >= 30) return retryOrFail(job, post, "o Instagram não terminou de processar a mídia em 30 minutos", true);
  await dbInsert("publishing_attempts", { post_id: job.post_id, job_id: job.id, attempt: job.attempts, channel: "instagram_api", outcome: "ok", detail: "aguardando processamento" });
  await finish(job, { status: "pendente", run_at: new Date(Date.now() + 60_000).toISOString(), attempts: job.attempts - 1 });
  return "aguardando";
}

async function runInstagram(job: JobRow): Promise<Outcome> {
  const [post] = await dbSelect<PostRow>("posts", `select=*&id=eq.${job.post_id}`);
  const current = post && post.version === job.post_version && (post.status === "agendado" || post.status === "publicando") && post.publish_mode === "api";
  if (!current) {
    await finish(job, { status: "cancelado", finished_at: new Date().toISOString(), last_error: "publicação mudou ou foi cancelada" });
    return "obsoleta";
  }
  const conn = await igConnection().catch(() => null);
  if (!conn) return retryOrFail(job, post, "a conta do Instagram não está conectada (ou o token expirou)", false);
  const uploads = await dbSelect<UploadRow>(
    "post_uploads",
    `select=position,storage_path,mime_type&post_id=eq.${post.id}&post_version=eq.${post.version}&deleted_at=is.null&order=position.asc`
  );
  if (!uploads.length) return retryOrFail(job, post, "as mídias desta versão não estão no armazenamento temporário", false);
  if (post.status === "agendado") {
    await dbUpdate("posts", `id=eq.${post.id}&status=eq.agendado&version=eq.${post.version}`, { status: "publicando" });
  }
  const caption = [post.caption, post.hashtags.join(" ")].filter(Boolean).join("\n\n");
  const dests = await dbSelect<DestinationRow>("post_destinations", `select=id,format,status,external_container_id&post_id=eq.${post.id}&status=eq.pendente`);
  try {
    for (const d of dests) {
      let container = d.external_container_id;
      if (container) {
        const status = await igWaitReady(container, conn.token, 3, 3000, conn.host);
        if (status === "PUBLISHED") {
          // publicou numa tentativa anterior e caiu antes de registrar: so registra
          await dbUpdateWhere("post_destinations", `id=eq.${d.id}&status=eq.pendente`, {
            status: "publicado",
            confirmed_at: new Date().toISOString(),
            confirmed_by: "api",
          });
          continue;
        }
        if (status === "ERROR" || status === "EXPIRED") {
          await dbUpdateWhere("post_destinations", `id=eq.${d.id}`, { external_container_id: null, last_error: `contêiner ${status}` });
          throw new InstagramError(`o Instagram recusou a mídia de ${d.format} (${status})`, true);
        }
        if (status !== "FINISHED") return waitMore(job, post);
      } else {
        container = await createContainer(conn, d.format, uploads, caption, post.flags ?? {});
        await dbUpdateWhere("post_destinations", `id=eq.${d.id}`, { external_container_id: container, last_error: null });
        // foto e video: espera o Instagram processar antes de publicar
        const status = await igWaitReady(container, conn.token, 8, 3000, conn.host);
        if (status === "ERROR" || status === "EXPIRED") {
          await dbUpdateWhere("post_destinations", `id=eq.${d.id}`, { external_container_id: null, last_error: `contêiner ${status}` });
          throw new InstagramError(`o Instagram recusou a mídia de ${d.format} (${status})`, true);
        }
        if (status !== "FINISHED") return waitMore(job, post);
      }
      const mediaId = await igPublish(conn.userId, conn.token, container, conn.host);
      const permalink = await igPermalink(mediaId, conn.token, conn.host);
      await dbUpdateWhere("post_destinations", `id=eq.${d.id}&status=eq.pendente`, {
        status: "publicado",
        external_id: mediaId,
        external_url: permalink && permalink.startsWith("https://") ? permalink : null,
        confirmed_at: new Date().toISOString(),
        confirmed_by: "api",
      });
    }
  } catch (e) {
    const err = e instanceof InstagramError ? e : new InstagramError(e instanceof Error ? e.message : String(e), true);
    // midia ainda processando: nao e falha, so volta daqui a pouco
    if (err.notReady) return waitMore(job, post);
    return retryOrFail(job, post, err.message, err.retryable);
  }
  const published = await dbSelect<{ external_url: string | null; format: string }>("post_destinations", `select=format,external_url&post_id=eq.${post.id}&status=eq.publicado`);
  const now = new Date().toISOString();
  await dbUpdate("posts", `id=eq.${post.id}&version=eq.${post.version}`, { status: "publicado", published_at: now });
  await finish(job, { status: "concluido", finished_at: now, last_error: null });
  await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "instagram_api", outcome: "ok", detail: `publicado: ${published.map((p) => p.format).join(", ")}` });
  for (const m of await dbSelect<{ media_id: string }>("post_media", `select=media_id&post_id=eq.${post.id}`)) {
    await dbInsert("media_usage", { media_id: m.media_id, channel: "instagram", context: "post", reference: post.id });
  }
  await cleanupUploads(post.id);
  await notify(`✅ Publicado no Instagram (${published.map((p) => p.format).join(", ")}).${published.find((p) => p.external_url) ? `\n${published.find((p) => p.external_url)!.external_url}` : ""}`);
  return "ok";
}

// Volta a consultar depois, sem gastar tentativa.
async function checkAgain(job: JobRow, minutes: number, detail?: string): Promise<Outcome> {
  if (detail) await dbInsert("publishing_attempts", { post_id: job.post_id, job_id: job.id, attempt: job.attempts, channel: "tiktok_api", outcome: "ok", detail });
  await finish(job, { status: "pendente", run_at: new Date(Date.now() + minutes * 60_000).toISOString(), attempts: job.attempts - 1, last_error: null });
  return "aguardando";
}

const TIKTOK_WAIT_HOURS = 72;
// lembretes do rascunho parado na caixa de entrada do TikTok (horas depois de chegar)
const TIKTOK_REMIND_HOURS = [3, 12, 24, 48];
const STATUS_LOG = "TikTok respondeu: ";

// Historico desta tarefa (poucas linhas: envio, respostas do TikTok, lembretes).
function jobLog(job: JobRow) {
  return dbSelect<{ detail: string | null; started_at: string }>("publishing_attempts", `select=detail,started_at&job_id=eq.${job.id}&order=started_at.asc`);
}

// Guarda a resposta do TikTok no historico, so quando muda (diagnostico: mostra
// se o TikTok chega a dizer PUBLISH_COMPLETE depois que o rascunho e publicado).
async function logTiktokStatus(job: JobRow, status: string) {
  const last = (await jobLog(job)).filter((a) => a.detail?.startsWith(STATUS_LOG)).at(-1);
  if (last?.detail === `${STATUS_LOG}${status}`) return;
  await dbInsert("publishing_attempts", { post_id: job.post_id, job_id: job.id, attempt: job.attempts, channel: "tiktok_api", outcome: "ok", detail: `${STATUS_LOG}${status}` });
}

// Destino do TikTok publicado (pela API ou achado no perfil): fecha a tarefa,
// registra o uso das midias e apaga as copias temporarias.
async function markTiktokPublished(job: JobRow, post: PostRow, destId: string, url: string | null, detail: string) {
  const now = new Date().toISOString();
  await dbUpdateWhere("post_destinations", `id=eq.${destId}&status=eq.pendente`, { status: "publicado", confirmed_at: now, confirmed_by: "api", ...(url ? { external_url: url } : {}) });
  await dbUpdate("posts", `id=eq.${post.id}&version=eq.${post.version}`, { status: "publicado", published_at: now });
  await finish(job, { status: "concluido", finished_at: now, last_error: null });
  await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "tiktok_api", outcome: "ok", detail });
  for (const m of await dbSelect<{ media_id: string }>("post_media", `select=media_id&post_id=eq.${post.id}`)) {
    await dbInsert("media_usage", { media_id: m.media_id, channel: "tiktok", context: "post", reference: post.id });
  }
  await cleanupUploads(post.id);
}

// Texto comparavel: minusculo, sem hashtag, emoji nem pontuacao.
function plainText(text: string): string {
  return text
    .toLowerCase()
    .replace(/#\S+/g, " ")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

// O rascunho ja virou post? Procura nos posts publicos recentes da conta um com
// a mesma legenda, criado depois de o rascunho chegar (escopo video.list; sem
// ele, ou se a consulta falhar, segue esperando e o "Já publiquei" resolve).
async function findOnProfile(token: string, job: JobRow, post: PostRow): Promise<TikTokVideo | null> {
  const key = plainText(post.caption).slice(0, 40);
  if (key.length < 4) return null;
  const conn = await tiktokConnectionRow();
  if (!conn?.scopes?.includes("video.list")) return null;
  const arrived = (await jobLog(job)).find((a) => a.detail === `${STATUS_LOG}SEND_TO_USER_INBOX`);
  const since = new Date(arrived?.started_at ?? post.scheduled_at ?? job.run_at).getTime() - 10 * 60_000;
  let videos: TikTokVideo[];
  try {
    videos = await recentVideos(token);
  } catch {
    return null;
  }
  for (const v of videos) {
    if (v.create_time * 1000 < since) continue;
    if (!plainText(`${v.video_description ?? ""} ${v.title ?? ""}`).includes(key)) continue;
    // o mesmo post do TikTok nao confirma duas publicacoes do estudio
    if (v.share_url && (await dbSelect("post_destinations", `select=id&external_url=eq.${encodeURIComponent(v.share_url)}`)).length) continue;
    return v;
  }
  return null;
}

// Rascunho na caixa de entrada ha muito tempo: avisa de novo (3 h, 12 h, 24 h, 48 h).
async function remindDraft(job: JobRow, post: PostRow) {
  const log = await jobLog(job);
  const arrived = log.find((a) => a.detail === `${STATUS_LOG}SEND_TO_USER_INBOX`);
  if (!arrived) return;
  const sent = log.filter((a) => a.detail?.startsWith("lembrete "));
  const hours = TIKTOK_REMIND_HOURS[sent.length];
  if (hours === undefined || Date.now() - new Date(arrived.started_at).getTime() < hours * 3600_000) return;
  await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "tiktok_api", outcome: "ok", detail: `lembrete ${sent.length + 1}: rascunho parado há ${hours} h` });
  const caption = post.caption.length > 60 ? `${post.caption.slice(0, 60)}…` : post.caption;
  await notify(`⏰ O rascunho do TikTok “${caption}” está há ${hours} h esperando você. Abra o app do TikTok e publique, ou toque em “Já publiquei” no estúdio.`);
}

async function runTiktok(job: JobRow, baseUrl: string): Promise<Outcome> {
  const [post] = await dbSelect<PostRow>("posts", `select=*&id=eq.${job.post_id}`);
  const current = post && post.version === job.post_version && (post.status === "agendado" || post.status === "publicando") && post.publish_mode === "api";
  if (!current) {
    await finish(job, { status: "cancelado", finished_at: new Date().toISOString(), last_error: "publicação mudou ou foi cancelada" });
    return "obsoleta";
  }
  const [dest] = await dbSelect<DestinationRow & { external_id: string | null }>(
    "post_destinations",
    `select=id,format,status,external_container_id,external_id&post_id=eq.${post.id}&status=eq.pendente&limit=1`
  );
  if (!dest) {
    // confirmado a mao no estudio enquanto esperava
    await finish(job, { status: "concluido", finished_at: new Date().toISOString(), last_error: null });
    return "ok";
  }
  let token: string | null;
  try {
    token = await tiktokAccessToken();
  } catch (e) {
    const err = e instanceof TikTokError ? e : new TikTokError(e instanceof Error ? e.message : String(e), true);
    return retryOrFail(job, post, err.message, err.retryable);
  }
  if (!token) return retryOrFail(job, post, "o TikTok não está conectado (ou a conexão expirou)", false);

  try {
    if (!dest.external_id && dest.format === "Foto") {
      const photos = await dbSelect<UploadRow>(
        "post_uploads",
        `select=position,storage_path,mime_type&post_id=eq.${post.id}&post_version=eq.${post.version}&deleted_at=is.null&order=position.asc`
      );
      if (!photos.length) return retryOrFail(job, post, "as fotos desta versão não estão no armazenamento temporário", false);
      if (!baseUrl.startsWith("https://")) return retryOrFail(job, post, "endereço do site desconhecido para o TikTok baixar as fotos", false);
      if (post.status === "agendado") {
        await dbUpdate("posts", `id=eq.${post.id}&status=eq.agendado&version=eq.${post.version}`, { status: "publicando" });
      }
      // o TikTok tem 1 h para baixar; o link vale 2 h
      const urls = photos.map((u) => tiktokMediaUrl(baseUrl, u.storage_path, 2 * 3600));
      const caption = [post.caption, post.hashtags.join(" ")].filter(Boolean).join("\n\n");
      const publishId = await uploadPhotoDraft(token, urls, caption, { aiGenerated: post.flags?.aiGenerated, coverIndex: post.flags?.coverIndex });
      await dbUpdateWhere("post_destinations", `id=eq.${dest.id}`, { external_id: publishId, last_error: null });
      return checkAgain(job, 1, "vídeo enviado ao TikTok");
    }
    if (!dest.external_id) {
      const [upload] = await dbSelect<UploadRow>(
        "post_uploads",
        `select=position,storage_path,mime_type&post_id=eq.${post.id}&post_version=eq.${post.version}&deleted_at=is.null&order=position.asc&limit=1`
      );
      if (!upload) return retryOrFail(job, post, "o vídeo desta versão não está no armazenamento temporário", false);
      const res = await fetch(await storageReadUrl(UPLOAD_BUCKET, upload.storage_path, 3600), { signal: AbortSignal.timeout(120_000) });
      if (!res.ok) throw new TikTokError(`não consegui ler a cópia temporária (HTTP ${res.status})`, true);
      const video = new Uint8Array(await res.arrayBuffer());
      if (post.status === "agendado") {
        await dbUpdate("posts", `id=eq.${post.id}&status=eq.agendado&version=eq.${post.version}`, { status: "publicando" });
      }
      const publishId = await uploadDraft(token, video, upload.mime_type);
      await dbUpdateWhere("post_destinations", `id=eq.${dest.id}`, { external_id: publishId, last_error: null });
      return checkAgain(job, 1, "vídeo enviado ao TikTok");
    }

    const st = await publishStatus(token, dest.external_id);
    await logTiktokStatus(job, st.status);
    if (st.status === "PUBLISH_COMPLETE") {
      await markTiktokPublished(job, post, dest.id, null, "publicado no TikTok");
      await notify("✅ O TikTok confirmou: o post foi publicado.");
      return "ok";
    }
    if (st.status === "FAILED") {
      // o video nao chegou: limpa o publish_id para a proxima tentativa enviar de novo
      await dbUpdateWhere("post_destinations", `id=eq.${dest.id}`, { external_id: null, last_error: `TikTok: ${st.fail_reason ?? "falhou"}` });
      return retryOrFail(job, post, `o TikTok recusou o conteúdo (${st.fail_reason ?? "sem motivo"})`, st.fail_reason === "internal");
    }
    if (st.status === "SEND_TO_USER_INBOX") {
      // primeira vez que chegou: manda as midias no Telegram (para ver o que
      // publicar), apaga a copia e avisa para por a musica e publicar
      const copies = await dbSelect<{ storage_path: string; mime_type: string }>(
        "post_uploads",
        `select=storage_path,mime_type&post_id=eq.${post.id}&deleted_at=is.null&order=position.asc`
      );
      if (copies.length) {
        const caption = post.caption.length > 80 ? `${post.caption.slice(0, 80)}…` : post.caption;
        const text = `📥 O rascunho chegou nas notificações do TikTok${caption ? `: “${caption}”` : ""}. Abra o app, coloque a música em alta e publique. O estúdio marca como publicado sozinho quando achar o post no seu perfil.`;
        await sendMediaNotice(copies, text).catch(() => notify(text));
        await cleanupUploads(post.id);
      }
      // o TikTok nao avisa quando o rascunho vira post: procura no perfil
      const found = await findOnProfile(token, job, post);
      if (found) {
        await markTiktokPublished(job, post, dest.id, found.share_url ?? null, `publicado no TikTok (achado no perfil: ${found.id})`);
        await notify(`✅ Achei no seu perfil do TikTok: o post “${post.caption.slice(0, 60)}” foi publicado.${found.share_url ? ` ${found.share_url}` : ""}`);
        return "ok";
      }
      const since = new Date(post.scheduled_at ?? job.run_at).getTime();
      if (Date.now() - since > TIKTOK_WAIT_HOURS * 3600_000) {
        await finish(job, { status: "concluido", finished_at: new Date().toISOString(), last_error: `sem confirmação do TikTok em ${TIKTOK_WAIT_HOURS} h` });
        await notify("⏳ O TikTok não confirmou a publicação do rascunho em 72 h. Se você publicou, marque no estúdio.");
        return "ok";
      }
      await remindDraft(job, post);
      return checkAgain(job, 15);
    }
    // PROCESSING_UPLOAD / PROCESSING_DOWNLOAD (preso por mais de 2 h: envia de novo)
    const [sent] = await dbSelect<{ started_at: string }>(
      "publishing_attempts",
      `select=started_at&job_id=eq.${job.id}&channel=eq.tiktok_api&detail=eq.${encodeURIComponent("vídeo enviado ao TikTok")}&order=started_at.desc&limit=1`
    );
    if (sent && Date.now() - new Date(sent.started_at).getTime() > 2 * 3600_000) {
      await dbUpdateWhere("post_destinations", `id=eq.${dest.id}`, { external_id: null, last_error: "TikTok não terminou de processar" });
      return retryOrFail(job, post, "o TikTok não terminou de processar o vídeo em 2 h", true);
    }
    return checkAgain(job, 1);
  } catch (e) {
    const err = e instanceof TikTokError ? e : new TikTokError(e instanceof Error ? e.message : String(e), true);
    return retryOrFail(job, post, err.message, err.retryable);
  }
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (req.method !== "POST") return reply(405, { detail: "Use POST." });
  const byCron = authorizedByCron(req);
  if (!byCron && !(await isAuthenticated(req))) return reply(401, { detail: "Não autorizado." });
  if (!supabaseConfigured()) return reply(501, { configured: false, detail: "Banco (Supabase) ainda não configurado na Vercel." });

  const result = {
    origem: byCron ? "cron" : "painel",
    processadas: 0,
    ok: 0,
    obsoletas: 0,
    novas_tentativas: 0,
    aguardando: 0,
    falhas: 0,
    token_instagram: null as string | null,
    erro: null as string | null,
  };
  try {
    const jobs = await dbRpc<JobRow[]>("claim_jobs", { p_limit: BATCH });
    const url = appUrl(req);
    for (const job of jobs) {
      result.processadas++;
      const outcome: Outcome =
        job.kind === "lembrete_publicacao"
          ? await runReminder(job, url)
          : job.kind === "publicar_instagram"
            ? await runInstagram(job)
            : job.kind === "enviar_tiktok"
              ? await runTiktok(job, url)
              : "obsoleta";
      if (outcome === "ok") result.ok++;
      else if (outcome === "obsoleta") result.obsoletas++;
      else if (outcome === "nova_tentativa") result.novas_tentativas++;
      else if (outcome === "aguardando") result.aguardando++;
      else result.falhas++;
    }
    // renova o token do Instagram de tempos em tempos (vale 60 dias)
    result.token_instagram = await igRefreshIfDue();
  } catch (e) {
    result.erro = (e instanceof Error ? e.message : String(e)).slice(0, 300);
  }
  await dbUpsert("worker_status", { id: 1, last_run_at: new Date().toISOString(), last_result: result }, "id").catch(() => undefined);
  return reply(result.erro ? 502 : 200, result);
}
