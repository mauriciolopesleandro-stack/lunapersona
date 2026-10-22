import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { igConnection } from "./_instagram.js";
import { tiktokConnectionRow } from "./_tiktok.js";
import { TIKTOK_PHOTO_MAX_BYTES } from "./_tiktok_media.js";
import {
  NETWORK_RULES,
  UPLOAD_BUCKET,
  cancelJobs,
  cleanupUploads,
  contentHash,
  mediaProblem,
  scheduleReminder,
  type Network,
  type PostRow,
  type PostStatus,
} from "./_posts.js";
import {
  audit,
  dbDelete,
  dbInsert,
  dbInsertIgnoringDuplicates,
  dbSelect,
  dbSelectAll,
  dbUpdate,
  dbUpdateWhere,
  storageObjectSize,
  storageUploadUrl,
  supabaseConfigured,
} from "./_supabase.js";

// /api/studio?r=posts - Fase 2: publicacoes com versao e aprovacao obrigatoria.
// Instagram/TikTok sao publicados a mao (as imagens ficam no computador): o
// sistema avisa no horario (api/jobs.ts) e o administrador confirma cada
// destino. Nada vira "publicado" sem essa confirmacao.
//
// GET -> { posts, worker }
// POST { action }:
//   save { id?, version?, network, formats, media_ids, caption, hashtags, scheduled_at }
//   submit { id }                    rascunho/falhou -> aguardando aprovacao
//   approve { id, version }          aprova exatamente a versao revisada
//   reject { id, version, note? }
//   schedule { id, scheduled_at }    aprovado/agendado -> agendado (lembrete no horario)
//   confirm { id, destinations: [{ format, url? }] }   publicado de verdade, destino a destino
//   fail { id, reason }              nao deu para publicar
//   cancel { id }
//   import { posts: [...] }          publicacoes antigas do luna-vox.json
//   upload-urls { id, version, items: [{ position, mime_type, size_bytes }] }
//                                    modo automatico: links para o navegador enviar a copia
//                                    temporaria das midias da versao em revisao

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NETWORKS: Network[] = ["instagram", "tiktok", "telegram"];

class Problem extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

interface DestinationRow {
  id: string;
  post_id: string;
  format: string;
  status: "pendente" | "publicado" | "falhou" | "cancelado";
  external_url: string | null;
  confirmed_at: string | null;
}

interface PostInput {
  mode: "manual" | "api";
  network: Network;
  formats: string[];
  mediaIds: string[];
  caption: string;
  hashtags: string[];
  scheduledAt: string | null;
}

function uuid(v: unknown, field = "Publicação"): string {
  if (typeof v !== "string" || !UUID.test(v)) throw new Problem(400, `${field} inválida.`);
  return v;
}

function when(v: unknown): string | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v !== "string" || Number.isNaN(Date.parse(v))) throw new Problem(400, "Data e hora inválidas.");
  return new Date(v).toISOString();
}

function parseInput(body: Record<string, unknown>): PostInput {
  const network = body.network as Network;
  if (!NETWORKS.includes(network)) throw new Problem(400, "Rede inválida.");
  const rules = NETWORK_RULES[network];
  const formats = Array.isArray(body.formats) ? [...new Set(body.formats.map(String))] : [];
  for (const f of formats) if (!rules.formats.includes(f)) throw new Problem(400, `Destino inválido para ${rules.label}: ${f}`);
  const mediaIds = Array.isArray(body.media_ids) ? body.media_ids.map((v) => uuid(v, "Mídia")) : [];
  if (new Set(mediaIds).size !== mediaIds.length) throw new Problem(400, "A mesma mídia aparece duas vezes.");
  if (mediaIds.length > rules.mediaMax) throw new Problem(400, `${rules.label}: no máximo ${rules.mediaMax} mídias.`);
  const caption = typeof body.caption === "string" ? body.caption : "";
  if (caption.length > rules.captionMax) throw new Problem(400, `A legenda passou do limite do ${rules.label} (${rules.captionMax}).`);
  const hashtags = Array.isArray(body.hashtags)
    ? [...new Set(body.hashtags.map((t) => `#${String(t).replace(/^#+/, "").replace(/\s+/g, "")}`).filter((t) => t.length > 1))]
    : [];
  if (hashtags.length > rules.hashtagMax) throw new Problem(400, `${rules.label}: no máximo ${rules.hashtagMax} hashtags.`);
  const mode = body.publish_mode === "api" ? "api" : "manual";
  if (mode === "api" && network === "telegram") throw new Problem(400, "Publicação automática ainda não existe para o Telegram.");
  return { mode, network, formats, mediaIds, caption, hashtags, scheduledAt: when(body.scheduled_at) };
}

async function getPost(id: string): Promise<PostRow> {
  const [post] = await dbSelect<PostRow>("posts", `select=*&id=eq.${id}`);
  if (!post) throw new Problem(404, "Publicação não encontrada.");
  return post;
}

async function postMediaIds(id: string): Promise<string[]> {
  const rows = await dbSelect<{ media_id: string; position: number }>("post_media", `select=media_id,position&post_id=eq.${id}&order=position.asc`);
  return rows.map((r) => r.media_id);
}

async function destinations(id: string): Promise<DestinationRow[]> {
  return dbSelect<DestinationRow>("post_destinations", `select=*&post_id=eq.${id}`);
}

function requireStatus(post: PostRow, allowed: PostStatus[], what: string) {
  if (!allowed.includes(post.status)) throw new Problem(409, `Não dá para ${what} uma publicação com status "${post.status}".`);
}

// Atualiza so se ninguem mexeu antes (mesma versao e mesmo status que lemos).
async function guardedUpdate(post: PostRow, patch: Record<string, unknown>): Promise<PostRow> {
  const row = await dbUpdate<PostRow>("posts", `id=eq.${post.id}&version=eq.${post.version}&status=eq.${post.status}`, patch);
  if (!row) throw new Problem(409, "A publicação mudou em outro lugar. Recarregue e tente de novo.");
  return row;
}

async function syncDestinations(postId: string, formats: string[]) {
  const current = await destinations(postId);
  for (const d of current) {
    if (!formats.includes(d.format) && d.status === "pendente") {
      await dbUpdateWhere("post_destinations", `id=eq.${d.id}`, { status: "cancelado" });
    }
    if (formats.includes(d.format) && d.status === "cancelado") {
      await dbUpdateWhere("post_destinations", `id=eq.${d.id}`, { status: "pendente" });
    }
  }
  const missing = formats.filter((f) => !current.some((d) => d.format === f));
  if (missing.length) await dbInsertIgnoringDuplicates("post_destinations", missing.map((format) => ({ post_id: postId, format })), "post_id,format");
}

async function replaceMedia(postId: string, mediaIds: string[]) {
  await dbDelete("post_media", `post_id=eq.${postId}`);
  if (mediaIds.length) {
    await dbInsertIgnoringDuplicates(
      "post_media",
      mediaIds.map((media_id, position) => ({ post_id: postId, media_id, position })),
      "post_id,position"
    );
  }
}

// Regras da publicacao automatica, destino a destino. TikTok: rascunho de
// 1 video (destino Video) ou de 1 a 35 fotos (destino Foto).
function problemWithAutoFormats(network: string, formats: string[], mimes: string[]) {
  const videos = mimes.filter((m) => m.startsWith("video/")).length;
  if (network === "tiktok") {
    const video = formats.length === 1 && formats[0] === "Vídeo" && mimes.length === 1 && videos === 1;
    const photos = formats.length === 1 && formats[0] === "Foto" && mimes.length >= 1 && mimes.length <= 35 && videos === 0;
    if (!video && !photos) throw new Problem(400, "TikTok automático: 1 vídeo no destino Vídeo, ou de 1 a 35 fotos no destino Foto (um destino só).");
    return;
  }
  if (formats.includes("Reels") && (mimes.length !== 1 || videos !== 1)) throw new Problem(400, "Reels automático precisa de exatamente 1 vídeo.");
  if (formats.includes("Stories") && mimes.length !== 1) throw new Problem(400, "Stories automático precisa de exatamente 1 mídia.");
  if (formats.includes("Feed") && (mimes.length < 1 || mimes.length > 20)) throw new Problem(400, "Feed: de 1 a 20 mídias.");
}

// Modo automatico: so aprova quando todas as copias da versao chegaram ao Storage.
async function checkUploads(post: PostRow) {
  if (post.network === "tiktok") {
    const row = await tiktokConnectionRow().catch(() => null);
    if (row?.status !== "conectado") throw new Problem(400, "Conecte o TikTok (página TikTok) antes de aprovar um envio automático.");
  } else {
    const conn = await igConnection().catch(() => null);
    if (!conn) throw new Problem(400, "Conecte a conta do Instagram (página Instagram) antes de aprovar uma publicação automática.");
  }
  const mediaCount = (await postMediaIds(post.id)).length;
  const rows = await dbSelect<{ id: string; storage_path: string }>(
    "post_uploads",
    `select=id,storage_path&post_id=eq.${post.id}&post_version=eq.${post.version}&deleted_at=is.null`
  );
  if (rows.length !== mediaCount) throw new Problem(400, "As mídias ainda não foram enviadas para a publicação automática.");
  for (const r of rows) {
    const size = await storageObjectSize(UPLOAD_BUCKET, r.storage_path);
    if (size === null) throw new Problem(400, "Uma das mídias não chegou ao armazenamento. Tente aprovar de novo.");
    await dbUpdateWhere("post_uploads", `id=eq.${r.id}`, { uploaded_at: new Date().toISOString(), size_bytes: size || undefined });
  }
}

async function readyToApprove(post: PostRow) {
  const mediaIds = await postMediaIds(post.id);
  if (!mediaIds.length) throw new Problem(400, "Escolha pelo menos uma mídia.");
  const dests = (await destinations(post.id)).filter((d) => d.status !== "cancelado");
  if (!dests.length) throw new Problem(400, "Marque pelo menos um destino.");
  const problem = await mediaProblem(post.network, mediaIds);
  if (problem) throw new Problem(400, problem);
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
  if (!supabaseConfigured()) return reply(501, { configured: false, detail: "Banco (Supabase) ainda não configurado na Vercel." });

  try {
    if (req.method === "GET") {
      const [posts, media, dests, approvals, jobs, attempts, worker] = await Promise.all([
        dbSelectAll<PostRow>("posts", "select=*&order=created_at.desc"),
        dbSelectAll<{ post_id: string; media_id: string; position: number }>("post_media", "select=post_id,media_id,position&order=position.asc"),
        dbSelectAll<DestinationRow>("post_destinations", "select=*"),
        dbSelect("approvals", "select=post_id,version,decision,note,decided_at&order=decided_at.desc&limit=500"),
        dbSelect("scheduled_jobs", "select=id,post_id,post_version,run_at,status,attempts,max_attempts,last_error,finished_at&status=in.(pendente,executando,falhou)&order=run_at.asc&limit=500"),
        dbSelect("publishing_attempts", "select=post_id,channel,outcome,detail,started_at&order=started_at.desc&limit=500"),
        dbSelect("worker_status", "select=last_run_at,last_result&id=eq.1"),
      ]);
      const by = <T extends { post_id: string }>(rows: T[], id: string) => rows.filter((r) => r.post_id === id);
      return reply(200, {
        posts: posts.map((p) => ({
          ...p,
          media_ids: by(media, p.id).map((m) => m.media_id),
          destinations: by(dests, p.id),
          approvals: by(approvals as { post_id: string }[], p.id),
          jobs: by(jobs as { post_id: string }[], p.id),
          attempts: by(attempts as { post_id: string }[], p.id).slice(0, 20),
        })),
        worker: worker[0] ?? null,
      });
    }
    if (req.method !== "POST") return reply(405, { detail: "Use GET ou POST." });

    let raw = "";
    for await (const chunk of req) raw += chunk;
    let body: Record<string, unknown>;
    try {
      body = JSON.parse(raw || "{}");
    } catch {
      return reply(400, { detail: "JSON inválido." });
    }
    const now = new Date().toISOString();

    if (body.action === "save") {
      const input = parseInput(body);
      const problem = await mediaProblem(input.network, input.mediaIds);
      if (problem) throw new Problem(400, problem);
      const hash = contentHash({ ...input, mode: input.mode });
      const fields = {
        network: input.network,
        caption: input.caption,
        hashtags: input.hashtags,
        scheduled_at: input.scheduledAt,
        publish_mode: input.mode,
      };

      if (!body.id) {
        const post = await dbInsert<PostRow>("posts", { ...fields, content_hash: hash });
        await replaceMedia(post.id, input.mediaIds);
        await syncDestinations(post.id, input.formats);
        await audit("criar", "posts", post.id, { network: post.network });
        return reply(200, { post });
      }

      const post = await getPost(uuid(body.id));
      if (body.version !== undefined && body.version !== post.version) {
        throw new Problem(409, "A publicação mudou em outro lugar. Recarregue e tente de novo.");
      }
      requireStatus(post, ["rascunho", "aguardando_aprovacao", "aprovado", "agendado", "falhou"], "editar");
      const changed = hash !== post.content_hash;
      const patch: Record<string, unknown> = { ...fields };
      if (changed) {
        // Conteudo mudou: nova versao. O que estava aprovado/agendado volta para aprovacao.
        patch.version = post.version + 1;
        patch.content_hash = hash;
        patch.status = post.status === "rascunho" || post.status === "falhou" ? "rascunho" : "aguardando_aprovacao";
        patch.failure_reason = null;
      } else if (post.status === "agendado" && !input.scheduledAt) {
        patch.status = "aprovado";
      }
      const saved = await guardedUpdate(post, patch);
      if (changed) {
        await replaceMedia(post.id, input.mediaIds);
        await syncDestinations(post.id, input.formats);
      }
      if (changed || saved.scheduled_at !== post.scheduled_at || saved.status !== "agendado") await cancelJobs(post.id);
      // copias temporarias da versao antiga nao servem mais
      if (changed) await cleanupUploads(post.id);
      if (saved.status === "agendado") await scheduleReminder(saved);
      await audit("editar", "posts", post.id, { version: saved.version, nova_aprovacao: changed && post.status !== "rascunho" });
      return reply(200, { post: saved, needsApproval: changed && ["aguardando_aprovacao", "aprovado", "agendado"].includes(post.status) });
    }

    if (body.action === "import") {
      const list = Array.isArray(body.posts) ? body.posts.slice(0, 500) : [];
      let imported = 0;
      for (const item of list as Record<string, unknown>[]) {
        const legacy = typeof item.legacy_id === "string" ? item.legacy_id.slice(0, 100) : null;
        if (!legacy) continue;
        const input = parseInput(item);
        const hash = contentHash({ ...input, mode: input.mode });
        const published = item.status === "publicado";
        const status: PostStatus = published ? "publicado" : item.status === "agendado" ? "aguardando_aprovacao" : "rascunho";
        const [post] = await dbInsertIgnoringDuplicates<PostRow>(
          "posts",
          [
            {
              network: input.network,
              caption: input.caption,
              hashtags: input.hashtags,
              scheduled_at: input.scheduledAt,
              content_hash: hash,
              status,
              // publicada antes da Fase 2: foi marcada a mao pelo administrador
              approved_version: published ? 1 : null,
              approved_hash: published ? hash : null,
              published_at: published ? input.scheduledAt ?? now : null,
              legacy_id: legacy,
            },
          ],
          "legacy_id"
        );
        if (!post) continue; // ja importada
        imported++;
        await replaceMedia(post.id, input.mediaIds);
        await syncDestinations(post.id, input.formats);
        if (published) {
          await dbInsert("approvals", { post_id: post.id, version: 1, content_hash: hash, decision: "aprovado", note: "importado (antes da Fase 2)" });
          await dbUpdateWhere("post_destinations", `post_id=eq.${post.id}&status=eq.pendente`, {
            status: "publicado",
            confirmed_at: post.published_at,
            confirmed_by: "importado",
          });
        }
      }
      if (imported) await audit("importar", "posts", null, { quantidade: imported });
      return reply(200, { imported });
    }

    const post = await getPost(uuid(body.id));

    if (body.action === "submit") {
      requireStatus(post, ["rascunho", "falhou"], "enviar para aprovação");
      await readyToApprove(post);
      const saved = await guardedUpdate(post, { status: "aguardando_aprovacao", failure_reason: null });
      await audit("enviar_aprovacao", "posts", post.id, { version: post.version });
      return reply(200, { post: saved });
    }

    if (body.action === "upload-urls") {
      requireStatus(post, ["aguardando_aprovacao"], "preparar o envio de");
      if (post.publish_mode !== "api") throw new Problem(400, "Esta publicação é manual.");
      if (body.version !== post.version) throw new Problem(409, `A publicação mudou (agora é a versão ${post.version}).`);
      const mediaIds = await postMediaIds(post.id);
      const formats = (await destinations(post.id)).filter((d) => d.status !== "cancelado").map((d) => d.format);
      const items = Array.isArray(body.items) ? (body.items as Record<string, unknown>[]) : [];
      if (items.length !== mediaIds.length) throw new Problem(400, "Envie todas as mídias da publicação.");
      const kinds = items.map((it) => String(it.mime_type));
      problemWithAutoFormats(post.network, formats, kinds);
      const urls: { position: number; url: string }[] = [];
      for (const it of items) {
        const position = Number(it.position);
        const mime = String(it.mime_type);
        const size = Number(it.size_bytes);
        if (!Number.isInteger(position) || position < 0 || position >= mediaIds.length) throw new Problem(400, "Posição de mídia inválida.");
        if (!["image/jpeg", "video/mp4", "video/quicktime"].includes(mime)) throw new Problem(400, "O Instagram aceita foto JPEG e vídeo MP4/MOV.");
        if (!Number.isFinite(size) || size <= 0 || size > 50 * 1024 * 1024) throw new Problem(400, "Cada arquivo pode ter no máximo 50 MB (limite do plano grátis).");
        if (post.network === "tiktok" && mime === "image/jpeg" && size > TIKTOK_PHOTO_MAX_BYTES) throw new Problem(400, "Cada foto para o TikTok pode ter no máximo 4 MB.");
        const ext = mime === "image/jpeg" ? "jpg" : mime === "video/mp4" ? "mp4" : "mov";
        const path = `${post.id}/v${post.version}/${position}.${ext}`;
        const [existing] = await dbSelect<{ id: string }>("post_uploads", `select=id&post_id=eq.${post.id}&post_version=eq.${post.version}&position=eq.${position}`);
        if (existing) await dbUpdateWhere("post_uploads", `id=eq.${existing.id}`, { storage_path: path, mime_type: mime, size_bytes: size, uploaded_at: null, deleted_at: null });
        else await dbInsert("post_uploads", { post_id: post.id, post_version: post.version, position, storage_path: path, mime_type: mime, size_bytes: size });
        urls.push({ position, url: await storageUploadUrl(UPLOAD_BUCKET, path) });
      }
      return reply(200, { uploads: urls });
    }

    if (body.action === "approve" || body.action === "reject") {
      requireStatus(post, ["aguardando_aprovacao"], body.action === "approve" ? "aprovar" : "reprovar");
      if (body.version !== post.version) {
        throw new Problem(409, `A publicação mudou (agora é a versão ${post.version}). Revise de novo antes de decidir.`);
      }
      if (body.action === "reject") {
        const note = typeof body.note === "string" ? body.note.slice(0, 500) : null;
        await dbInsert("approvals", { post_id: post.id, version: post.version, content_hash: post.content_hash, decision: "reprovado", note });
        const saved = await guardedUpdate(post, { status: "rascunho" });
        await audit("reprovar", "posts", post.id, { version: post.version });
        return reply(200, { post: saved });
      }
      await readyToApprove(post);
      if (post.publish_mode === "api") await checkUploads(post);
      await dbInsert("approvals", { post_id: post.id, version: post.version, content_hash: post.content_hash, decision: "aprovado" });
      const saved = await guardedUpdate(post, {
        status: post.scheduled_at ? "agendado" : "aprovado",
        approved_version: post.version,
        approved_hash: post.content_hash,
      });
      if (saved.status === "agendado") await scheduleReminder(saved);
      await audit("aprovar", "posts", post.id, { version: post.version, agendado: saved.status === "agendado" });
      return reply(200, { post: saved });
    }

    if (body.action === "schedule") {
      requireStatus(post, ["aprovado", "agendado"], "agendar");
      const at = when(body.scheduled_at);
      if (!at) throw new Problem(400, "Escolha o dia e a hora.");
      const saved = await guardedUpdate(post, { status: "agendado", scheduled_at: at });
      await cancelJobs(post.id);
      await scheduleReminder(saved);
      await audit("agendar", "posts", post.id, { scheduled_at: at });
      return reply(200, { post: saved });
    }

    if (body.action === "confirm") {
      requireStatus(post, ["aprovado", "agendado", "publicando"], "confirmar");
      const given = Array.isArray(body.destinations) ? (body.destinations as Record<string, unknown>[]) : [];
      if (!given.length) throw new Problem(400, "Marque o que foi publicado.");
      const dests = await destinations(post.id);
      const confirmed: string[] = [];
      for (const g of given) {
        const d = dests.find((x) => x.format === g.format && x.status === "pendente");
        if (!d) throw new Problem(400, `Destino ${String(g.format)} não está pendente.`);
        const url = typeof g.url === "string" && g.url.trim() ? g.url.trim() : null;
        if (url && !/^https:\/\/\S+$/.test(url)) throw new Problem(400, "O link precisa começar com https://");
        await dbUpdateWhere("post_destinations", `id=eq.${d.id}&status=eq.pendente`, {
          status: "publicado",
          external_url: url,
          confirmed_at: now,
          confirmed_by: "admin",
        });
        confirmed.push(d.format);
      }
      const remaining = (await destinations(post.id)).filter((d) => d.status === "pendente");
      const done = remaining.length === 0;
      // "publicando" exige horario: sem agendamento, vale a hora da confirmacao
      const saved = await guardedUpdate(
        post,
        done ? { status: "publicado", published_at: now } : { status: "publicando", scheduled_at: post.scheduled_at ?? now }
      );
      await dbInsert("publishing_attempts", { post_id: post.id, channel: "manual", outcome: "ok", detail: `confirmado: ${confirmed.join(", ")}` });
      if (done) {
        await cancelJobs(post.id);
        await cleanupUploads(post.id);
        for (const media_id of await postMediaIds(post.id)) {
          await dbInsert("media_usage", { media_id, channel: post.network, context: "post", reference: post.id });
        }
      }
      await audit("confirmar_publicacao", "posts", post.id, { destinos: confirmed, completo: done });
      return reply(200, { post: saved });
    }

    if (body.action === "fail") {
      requireStatus(post, ["aprovado", "agendado", "publicando"], "marcar falha em");
      const reason = typeof body.reason === "string" && body.reason.trim() ? body.reason.trim().slice(0, 500) : "Não foi possível publicar.";
      const saved = await guardedUpdate(post, { status: "falhou", failure_reason: reason });
      await cancelJobs(post.id);
      await cleanupUploads(post.id);
      await dbInsert("publishing_attempts", { post_id: post.id, channel: "manual", outcome: "erro", detail: reason });
      await audit("falhou", "posts", post.id, { reason });
      return reply(200, { post: saved });
    }

    if (body.action === "cancel") {
      requireStatus(post, ["rascunho", "aguardando_aprovacao", "aprovado", "agendado", "publicando", "falhou"], "cancelar");
      const saved = await guardedUpdate(post, { status: "cancelado", cancelled_at: now });
      await cancelJobs(post.id);
      await cleanupUploads(post.id);
      await dbUpdateWhere("post_destinations", `post_id=eq.${post.id}&status=eq.pendente`, { status: "cancelado" });
      await audit("cancelar", "posts", post.id, {});
      return reply(200, { post: saved });
    }

    return reply(400, { detail: "Ação desconhecida." });
  } catch (e) {
    if (e instanceof Problem) return reply(e.status, { detail: e.message });
    const err = e as Error & { code?: string };
    if (err.code === "23514") return reply(409, { detail: "O banco recusou a mudança (regra de aprovação ou de horário)." });
    return reply(502, { detail: err.message || String(e) });
  }
}
