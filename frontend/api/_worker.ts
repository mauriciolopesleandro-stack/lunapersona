import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { isAuthenticated } from "./_auth.js";
import { Problem, cancelDraftPost, createDraftPost, reviewOnTelegram, submitPost, uploadUrlsFor } from "./_posts_api.js";
import { storeConfigured, storeGet, storeIncr, storeSet } from "./_store.js";
import { audit, dbInsertIgnoringDuplicates, dbSelect, dbUpdate, dbUpdateWhere, supabaseConfigured } from "./_supabase.js";
import { sendNotice, telegramConfig } from "./_telegram.js";
import type { IgAudioFlag } from "./_flags.js";
import { igAudioSearch, igConnection } from "./_instagram.js";

// /api/studio?r=worker - a automacao local (parte B do fluxo): um programa no
// PC do administrador (Python + Playwright, pasta worker/) pega os itens dos
// planos aprovados, gera a midia na Naiaflow, salva na pasta do estudio e
// devolve aqui; o estudio cria a publicacao de cada destino e manda para a
// analise no bot do Telegram. O PC nunca guarda a chave do banco: fala so com
// esta rota, com a chave do PC (Bearer), derivada da TOKEN_ENCRYPTION_KEY e de
// uma "geracao" que o administrador troca para revogar.
//
// Com login (estudio):
//   GET                          -> { paired, lastSeen, queue }
//   POST { action: "pair" }      -> nova chave do PC + arquivo de configuracao (a anterior para de valer)
// Com a chave do PC:
//   POST { action: "ping" }      -> { queue }
//   POST { action: "next" }      -> proximo item da fila (vira "gerando") ou { item: null };
//                                   correcao pedida no Telegram vem com edit { source, instruction }:
//                                   o PC edita a foto que ja tem (opcao Editar da Naiaflow)
//                                   Reels (kind "video"): o PC gera a foto e monta no proprio PC o
//                                   video com zoom leve (gratis, sem credito de video)
//                                   carrossel (Feed/TikTok): vem prompts [] com uma variacao por foto
//   POST { action: "done", id, local_path, size_bytes, video_path?, video_size?, more_paths? }
//                                -> cadastra a midia, cria as publicacoes (o item e os que
//                                   reaproveitam a foto) e devolve os destinos; Reels sai com
//                                   uma musica em alta da biblioteca do Instagram
//   POST { action: "upload-url", post_id, version, size_bytes, mime_type? } -> link para subir a copia daquele destino
//   POST { action: "upload-url", post_id, version, items: [{ position, size_bytes, mime_type }] }
//                                -> { urls } (carrossel: todas as fotos da publicacao de uma vez)
//   POST { action: "uploaded", id } -> manda cada publicacao do item para a analise no bot
//   POST { action: "fail", id, error } -> volta para a fila ou, sem tentativas, falhou (aviso no Telegram)

const GEN_KEY = "luna:worker-gen";
const SEEN_KEY = "luna:worker-seen";

async function generation(): Promise<string> {
  if (!storeConfigured()) return "1";
  return (await storeGet(GEN_KEY).catch(() => null)) ?? "1";
}

function workerToken(gen: string): string {
  const key = process.env.TOKEN_ENCRYPTION_KEY;
  if (!key || key.length < 32) throw new Problem(501, "Falta TOKEN_ENCRYPTION_KEY na Vercel.");
  return createHmac("sha256", key).update(`luna-worker-v1|${gen}`).digest("base64url");
}

async function authorizedWorker(req: IncomingMessage): Promise<boolean> {
  const given = String(req.headers.authorization ?? "").replace(/^Bearer\s+/i, "");
  if (!given) return false;
  const expected = workerToken(await generation());
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

interface ItemRow {
  id: string;
  plan_id: string;
  scheduled_at: string;
  network: "instagram" | "tiktok" | "telegram";
  format: string;
  kind: "image" | "video";
  adult: boolean;
  prompt: string;
  caption: string;
  hashtags: string[];
  source_item_id: string | null;
  status: string;
  attempts: number;
  max_attempts: number;
  post_id: string | null;
  media_path: string | null;
  note: string | null;
}

function folderFor(item: ItemRow): string {
  const month = new Date(item.scheduled_at).toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" }).slice(0, 7);
  return item.adult ? `conteudos/${month}/+18` : `conteudos/${month}`;
}

function fileNameFor(item: ItemRow): string {
  const when = new Date(item.scheduled_at).toLocaleString("sv-SE", { timeZone: "America/Sao_Paulo" }).slice(0, 16).replace(/[-: ]/g, "");
  const dest = item.adult ? "telegram18" : `${item.network}-${item.format}`.toLowerCase();
  // a correcao nao sobrescreve a foto anterior
  const suffix = isCorrection(item) ? `-correcao-${Date.now().toString(36)}` : "";
  return `plano-${when}-${dest}-${item.id.slice(0, 8)}${suffix}.jpg`;
}

// Correcao pedida no Telegram: o item ja tem foto (media_path) e o pedido fica
// na nota ate o robo terminar.
const CORRECTION_PREFIX = "Correção: ";
function isCorrection(item: ItemRow): boolean {
  return Boolean(item.media_path) && (item.note ?? "").startsWith(CORRECTION_PREFIX);
}

// fotos e Reels novos (sem reaproveitar outro item) e correcoes, que tem foto
// propria (no Reels, media_path e a foto de onde o video sai)
const QUEUE_FILTER = "status=eq.na_fila&or=(source_item_id.is.null,media_path.not.is.null)";

// Carrossel: o plano da foto do chat guarda quantas fotos (request.photos). O
// robo gera todas no mesmo item (mesmo ensaio, pose e angulo diferentes); Feed e
// TikTok levam todas, Stories so a primeira.
async function photosOf(item: Pick<ItemRow, "plan_id">): Promise<number> {
  const [plan] = await dbSelect<{ request: { photos?: unknown } | null }>("content_plans", `select=request&id=eq.${item.plan_id}`);
  const n = Number(plan?.request?.photos ?? 1);
  return Number.isInteger(n) && n > 1 && n <= 10 ? n : 1;
}

function carouselPrompts(prompt: string, n: number): string[] {
  return Array.from({ length: n }, (_, i) =>
    i === 0 ? prompt : `${prompt}\n\nFoto ${i + 1} de ${n} do mesmo ensaio: mesma pessoa, mesma roupa e mesmo lugar; mude a pose e o ângulo da câmera.`
  );
}

async function queueSize(): Promise<number> {
  const rows = await dbSelect<{ id: string }>("content_plan_items", `select=id&${QUEUE_FILTER}`);
  return rows.length;
}

// Telegram: "Pedir correcao" de uma publicacao que veio do plano. A publicacao
// ja foi reprovada (decidePost); o item volta para a fila do robo, que edita a
// foto com o pedido. Sem item do plano (publicacao feita a mao) devolve false.
export async function queueCorrection(postId: string, text: string): Promise<boolean> {
  const [item] = await dbSelect<ItemRow>("content_plan_items", `select=*&post_id=eq.${postId}&media_path=not.is.null`);
  if (!item) return false;
  // carrossel: a correcao de uma foto so nao cabe aqui; a publicacao volta para rascunho
  if ((await photosOf(item)) > 1) return false;
  await dbUpdateWhere("content_plan_items", `id=eq.${item.id}`, {
    status: "na_fila",
    attempts: 0,
    note: (CORRECTION_PREFIX + text.trim()).slice(0, 1000),
    updated_at: new Date().toISOString(),
  });
  await audit("pedir_correcao", "content_plan_items", item.id, { post_id: postId });
  return true;
}

async function getItem(id: unknown): Promise<ItemRow> {
  if (typeof id !== "string" || !/^[0-9a-f-]{36}$/i.test(id)) throw new Problem(400, "Item inválido.");
  const [item] = await dbSelect<ItemRow>("content_plan_items", `select=*&id=eq.${id}`);
  if (!item) throw new Problem(404, "Item não encontrado.");
  return item;
}

// o item e os que reaproveitam a mesma foto (TikTok com a foto do Feed); a
// correcao vale so para a publicacao corrigida
async function familyOf(item: ItemRow): Promise<ItemRow[]> {
  if (isCorrection(item)) return [item];
  const reusers = await dbSelect<ItemRow>("content_plan_items", `select=*&source_item_id=eq.${item.id}`);
  return [item, ...reusers.filter((r) => r.status !== "cancelado")];
}

async function notify(text: string) {
  await sendNotice(await telegramConfig(), text).catch(() => undefined);
}

// Reels do plano: uma das musicas em alta da biblioteca do Instagram (so pela
// conexao do Facebook). Sem conexao ou sem resposta, o Reels sai sem musica;
// o administrador ve a escolhida na mensagem de aprovacao do Telegram.
async function reelsAudio(): Promise<IgAudioFlag | undefined> {
  try {
    const conn = await igConnection();
    if (!conn || conn.provider !== "facebook_login") return undefined;
    const top = (await igAudioSearch(conn, "")).slice(0, 5);
    const pick = top[Math.floor(Math.random() * top.length)];
    return pick ? { id: pick.id, title: pick.title, artist: pick.artist } : undefined;
  } catch {
    return undefined;
  }
}

function safeLocalPath(raw: unknown, ext: string): string {
  const path = String(raw ?? "");
  if (!path.startsWith("conteudos/") || path.includes("..") || !path.toLowerCase().endsWith(ext)) throw new Problem(400, "Caminho do arquivo inválido.");
  return path;
}

function safeSize(raw: unknown): number {
  const size = Number(raw);
  return Number.isSafeInteger(size) && size > 0 ? size : 0;
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!supabaseConfigured()) return reply(501, { detail: "Banco (Supabase) ainda não configurado na Vercel." });

  try {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = req.method === "POST" ? (JSON.parse(raw || "{}") as Record<string, unknown>) : {};

    // --- estudio (login) -----------------------------------------------------------------------
    if (req.method === "GET" || body.action === "pair") {
      if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
      if (req.method === "GET") {
        const lastSeen = storeConfigured() ? await storeGet(SEEN_KEY).catch(() => null) : null;
        return reply(200, { paired: storeConfigured() ? Boolean(await storeGet(GEN_KEY).catch(() => null)) : true, lastSeen, queue: await queueSize() });
      }
      if (!storeConfigured()) throw new Problem(501, "Falta conectar o banco Upstash Redis ao projeto na Vercel (aba Storage).");
      const gen = String(await storeIncr(GEN_KEY));
      const token = workerToken(gen);
      const host = String(req.headers["x-forwarded-host"] ?? req.headers.host ?? "");
      const proto = String(req.headers["x-forwarded-proto"] ?? "https").split(",")[0];
      await audit("parear_pc", "worker", null, { geracao: gen });
      const config = [
        "# Luna Studio - automacao local (gerado em Configuracoes). Nao compartilhe este arquivo.",
        `STUDIO_URL=${proto}://${host}`,
        `WORKER_TOKEN=${token}`,
        "",
      ].join("\n");
      return reply(200, { config });
    }

    // --- automacao local (chave do PC) ---------------------------------------------------------------
    if (req.method !== "POST") return reply(405, { detail: "Use POST." });
    if (!(await authorizedWorker(req))) return reply(401, { detail: "Chave do PC inválida. Gere outra em Configurações → Automação do PC." });
    if (storeConfigured()) await storeSet(SEEN_KEY, new Date().toISOString()).catch(() => undefined);
    const now = () => new Date().toISOString();

    if (body.action === "ping") return reply(200, { ok: true, queue: await queueSize() });

    if (body.action === "next") {
      // o PC pode ter caido no meio: item preso em "gerando" ha mais de 30 min volta para a fila
      const stale = new Date(Date.now() - 30 * 60_000).toISOString();
      await dbUpdateWhere("content_plan_items", `status=eq.gerando&updated_at=lt.${stale}`, { status: "na_fila", updated_at: now() });
      const [item] = await dbSelect<ItemRow>(
        "content_plan_items",
        `select=*&${QUEUE_FILTER}&order=scheduled_at.asc&limit=1`
      );
      if (!item) return reply(200, { item: null });
      const claimed = await dbUpdate<ItemRow>("content_plan_items", `id=eq.${item.id}&status=eq.na_fila`, { status: "gerando", attempts: item.attempts + 1, updated_at: now() });
      if (!claimed) return reply(200, { item: null });
      const photos = isCorrection(item) ? 1 : await photosOf(item);
      return reply(200, {
        item: {
          id: item.id,
          prompt: item.prompt,
          adult: item.adult,
          kind: item.kind,
          attempt: claimed.attempts,
          folder: folderFor(item),
          file: fileNameFor(item),
          // destinos que vao usar esta foto (o PC prepara uma copia para cada um)
          targets: (await familyOf(item)).map((i) => ({ network: i.network, format: i.format })),
          ...(isCorrection(item) ? { edit: { source: item.media_path, instruction: (item.note ?? "").slice(CORRECTION_PREFIX.length) } } : {}),
          ...(photos > 1 ? { prompts: carouselPrompts(item.prompt, photos) } : {}),
        },
      });
    }

    const item = await getItem(body.id ?? body.item_id);

    if (body.action === "done") {
      if (item.status !== "gerando") throw new Problem(409, `O item está "${item.status}", não "gerando".`);
      // a foto fica no item (a correcao edita ela); no Reels a midia publicada e o video
      const localPath = safeLocalPath(body.local_path, ".jpg");
      const video = item.kind === "video";
      const assetPath = video ? safeLocalPath(body.video_path, ".mp4") : localPath;
      const photos = isCorrection(item) || video ? 1 : await photosOf(item);
      const more = photos > 1 ? (Array.isArray(body.more_paths) ? body.more_paths : []).map((p) => safeLocalPath(p, ".jpg")) : [];
      if (more.length !== photos - 1) throw new Problem(400, `O carrossel tem ${photos} fotos: mande as outras ${photos - 1}.`);
      const sizes = Array.isArray(body.more_sizes) ? body.more_sizes : [];
      const paths = [assetPath, ...more];
      await dbInsertIgnoringDuplicates(
        "media_assets",
        paths.map((p, i) => ({
          local_path: p,
          kind: video ? "video" : "image",
          mime_type: video ? "video/mp4" : "image/jpeg",
          size_bytes: safeSize(i === 0 ? (video ? body.video_size : body.size_bytes) : sizes[i - 1]),
          title: (item.prompt.slice(0, 110) || "Gerada pela automação") + (photos > 1 ? ` (${i + 1}/${photos})` : ""),
          status: "rascunho",
          content_rating: item.adult ? "adulto" : "livre",
          created_at: now(),
        })),
        "local_path"
      );
      const mediaIds: string[] = [];
      for (const p of paths) {
        const [m] = await dbSelect<{ id: string }>("media_assets", `select=id&local_path=eq.${encodeURIComponent(p)}`);
        if (!m) throw new Problem(500, "Não consegui cadastrar a mídia na biblioteca.");
        mediaIds.push(m.id);
      }
      const media = { id: mediaIds[0] };
      const family = await familyOf(item);
      const correction = isCorrection(item);
      // a publicacao reprovada sai do caminho; a foto corrigida vira outra
      if (correction && item.post_id) await cancelDraftPost(item.post_id);
      const groupId = family.length > 1 ? crypto.randomUUID() : null;
      const targets = [];
      for (const it of family) {
        const igAudio = it.network === "instagram" && it.format === "Reels" ? await reelsAudio() : undefined;
        // carrossel: Feed e TikTok com todas as fotos; Stories so com a primeira
        const ids = it.format === "Stories" ? [media.id] : mediaIds;
        const post = await createDraftPost({
          network: it.network,
          formats: [it.format],
          media_ids: ids,
          caption: it.caption,
          hashtags: it.hashtags,
          scheduled_at: it.scheduled_at,
          // +18 vai para o canal do Telegram (postagem manual, com lembrete); o resto e automatico
          publish_mode: it.network === "telegram" ? "manual" : "api",
          flags: { aiGenerated: true, ...(igAudio ? { igAudio } : {}) },
          ...(groupId ? { group_id: groupId } : {}),
        });
        const submitted = await submitPost(post.id);
        await dbUpdateWhere("content_plan_items", `id=eq.${it.id}`, { post_id: post.id, media_path: localPath, ...(correction ? { note: null } : {}), updated_at: now() });
        targets.push({ item_id: it.id, post_id: post.id, version: submitted.version, network: it.network, format: it.format, photos: ids.length });
      }
      await audit(correction ? "corrigir_item" : "gerar_item", "content_plan_items", item.id, { publicacoes: targets.length });
      return reply(200, { media_id: media.id, targets });
    }

    if (body.action === "upload-url" && Array.isArray(body.items)) {
      const urls = await uploadUrlsFor(String(body.post_id ?? ""), body.version, body.items);
      return reply(200, { urls });
    }

    if (body.action === "upload-url") {
      const size = Number(body.size_bytes);
      const mime = body.mime_type === "video/mp4" ? "video/mp4" : "image/jpeg";
      const [url] = await uploadUrlsFor(String(body.post_id ?? ""), body.version, [{ position: 0, mime_type: mime, size_bytes: size }]);
      return reply(200, { url: url.url });
    }

    if (body.action === "uploaded") {
      const family = (await familyOf(item)).filter((i) => i.post_id);
      for (const it of family) {
        const [post] = await dbSelect<{ id: string; version: number }>("posts", `select=id,version&id=eq.${it.post_id}`);
        await reviewOnTelegram(post.id, post.version);
        await dbUpdateWhere("content_plan_items", `id=eq.${it.id}`, { status: "em_analise", updated_at: now() });
      }
      return reply(200, { sent: family.length });
    }

    if (body.action === "fail") {
      const error = String(body.error ?? "erro desconhecido").slice(0, 900);
      const final = item.attempts >= item.max_attempts;
      const correction = isCorrection(item);
      // a correcao guarda o pedido na nota ate a ultima tentativa
      const note = correction && !final ? item.note : `Automação: ${error}`;
      await dbUpdateWhere("content_plan_items", `id=eq.${item.id}`, { status: final ? "falhou" : "na_fila", note, updated_at: now() });
      if (final) {
        const what = correction
          ? `corrigir a foto (pedido: “${(item.note ?? "").slice(CORRECTION_PREFIX.length, CORRECTION_PREFIX.length + 80)}”)`
          : `gerar a foto do plano: “${item.prompt.slice(0, 80)}”`;
        await notify(`⚠️ Não consegui ${what} depois de ${item.attempts} tentativa(s).\nMotivo: ${error.slice(0, 200)}`);
      }
      return reply(200, { status: final ? "falhou" : "na_fila" });
    }

    return reply(400, { detail: "Ação desconhecida." });
  } catch (e) {
    if (e instanceof Problem) return reply(e.status, { detail: e.message });
    return reply(500, { detail: e instanceof Error ? e.message : String(e) });
  }
}
