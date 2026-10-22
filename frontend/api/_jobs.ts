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
import { NETWORK_RULES, UPLOAD_BUCKET, cleanupUploads, type PostRow } from "./_posts.js";
import { dbInsert, dbRpc, dbSelect, dbUpdate, dbUpdateWhere, dbUpsert, storageReadUrl, supabaseConfigured } from "./_supabase.js";
import { sendNotice, telegramConfig } from "./_telegram.js";

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
//
// Tarefa "publicar_instagram" (modo automatico): publica pela API oficial,
// destino a destino, usando a copia temporaria da versao aprovada. O conteiner
// de cada destino fica guardado: uma nova tentativa consulta o status dele em
// vez de publicar de novo. Video em processamento reagenda em 1 min sem gastar
// tentativa. Terminou: apaga as copias e avisa no Telegram. Falhou de vez:
// marca "falhou" e avisa para publicar a mao.

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
  const label = NETWORK_RULES[post.network].label;
  const dests = await dbSelect<{ format: string }>("post_destinations", `select=format&post_id=eq.${post.id}&status=eq.pendente`);
  const text = [
    `⏰ Hora de publicar no ${label} (${dests.map((d) => d.format).join(", ") || "sem destino"}).`,
    post.caption ? `Legenda: ${post.caption.slice(0, 300)}${post.caption.length > 300 ? "…" : ""}` : "Sem legenda.",
    `Depois de publicar, confirme no estúdio${url ? `: ${url}` : "."}`,
  ].join("\n\n");
  try {
    await sendNotice(await telegramConfig(), text);
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

async function retryOrFail(job: JobRow, post: PostRow, error: string, retryable: boolean): Promise<Outcome> {
  await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "instagram_api", outcome: "erro", detail: error.slice(0, 300) });
  if (retryable && job.attempts < job.max_attempts) {
    const retryAt = new Date(Date.now() + 2 ** job.attempts * 60_000).toISOString();
    await finish(job, { status: "pendente", run_at: retryAt, last_error: error.slice(0, 300) });
    return "nova_tentativa";
  }
  await finish(job, { status: "falhou", finished_at: new Date().toISOString(), last_error: error.slice(0, 300) });
  await dbUpdate("posts", `id=eq.${post.id}&version=eq.${post.version}`, { status: "falhou", failure_reason: `Publicação automática falhou: ${error}`.slice(0, 500) });
  await cleanupUploads(post.id);
  await notify(`⚠️ Não consegui publicar no Instagram: ${error}\n\nPublique à mão pelo app e marque no estúdio.`);
  return "falhou";
}

// Cria o conteiner de um destino (Feed: foto, video ou carrossel; Reels; Stories).
async function createContainer(conn: IgConnection, format: string, uploads: UploadRow[], caption: string): Promise<string> {
  const urls = await Promise.all(uploads.map((u) => storageReadUrl(UPLOAD_BUCKET, u.storage_path, 6 * 3600)));
  const isVideo = (u: UploadRow) => u.mime_type.startsWith("video/");
  if (format === "Stories") {
    return igCreateContainer(conn.userId, conn.token, { media_type: "STORIES", [isVideo(uploads[0]) ? "video_url" : "image_url"]: urls[0] });
  }
  if (format === "Reels" || (uploads.length === 1 && isVideo(uploads[0]))) {
    return igCreateContainer(conn.userId, conn.token, { media_type: "REELS", video_url: urls[0], caption });
  }
  if (uploads.length === 1) return igCreateContainer(conn.userId, conn.token, { image_url: urls[0], caption });
  const children: string[] = [];
  for (const [i, u] of uploads.entries()) {
    children.push(
      await igCreateContainer(conn.userId, conn.token, isVideo(u) ? { media_type: "VIDEO", video_url: urls[i], is_carousel_item: "true" } : { image_url: urls[i], is_carousel_item: "true" })
    );
  }
  return igCreateContainer(conn.userId, conn.token, { media_type: "CAROUSEL", children: children.join(","), caption });
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
        const status = await igWaitReady(container, conn.token, 3);
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
        container = await createContainer(conn, d.format, uploads, caption);
        await dbUpdateWhere("post_destinations", `id=eq.${d.id}`, { external_container_id: container, last_error: null });
        // foto e video: espera o Instagram processar antes de publicar
        const status = await igWaitReady(container, conn.token);
        if (status === "ERROR" || status === "EXPIRED") {
          await dbUpdateWhere("post_destinations", `id=eq.${d.id}`, { external_container_id: null, last_error: `contêiner ${status}` });
          throw new InstagramError(`o Instagram recusou a mídia de ${d.format} (${status})`, true);
        }
        if (status !== "FINISHED") return waitMore(job, post);
      }
      const mediaId = await igPublish(conn.userId, conn.token, container);
      const permalink = await igPermalink(mediaId, conn.token);
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
        job.kind === "lembrete_publicacao" ? await runReminder(job, url) : job.kind === "publicar_instagram" ? await runInstagram(job) : "obsoleta";
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
