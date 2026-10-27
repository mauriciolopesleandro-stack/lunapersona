import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { isAuthenticated } from "./_auth.js";
import { Problem, cancelDraftPost, createDraftPost, reviewOnTelegram, submitPost, uploadUrlsFor } from "./_posts_api.js";
import { storeConfigured, storeGet, storeIncr, storeSet } from "./_store.js";
import { audit, dbInsertIgnoringDuplicates, dbSelect, dbUpdate, dbUpdateWhere, supabaseConfigured } from "./_supabase.js";
import { sendNotice, telegramConfig } from "./_telegram.js";

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
//   POST { action: "next" }      -> proximo item de foto na fila (vira "gerando") ou { item: null };
//                                   correcao pedida no Telegram vem com edit { source, instruction }:
//                                   o PC edita a foto que ja tem (opcao Editar da Naiaflow)
//   POST { action: "done", id, local_path, size_bytes }  -> cadastra a midia, cria as publicacoes
//                                   (o item e os que reaproveitam a foto) e devolve os destinos
//   POST { action: "upload-url", post_id, version, size_bytes } -> link para subir a copia daquele destino
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

// fotos novas (sem reaproveitar outra) e correcoes, que tem foto propria
const QUEUE_FILTER = "status=eq.na_fila&kind=eq.image&or=(source_item_id.is.null,media_path.not.is.null)";

async function queueSize(): Promise<number> {
  const rows = await dbSelect<{ id: string }>("content_plan_items", `select=id&${QUEUE_FILTER}`);
  return rows.length;
}

// Telegram: "Pedir correcao" de uma publicacao que veio do plano. A publicacao
// ja foi reprovada (decidePost); o item volta para a fila do robo, que edita a
// foto com o pedido. Sem item do plano (publicacao feita a mao) devolve false.
export async function queueCorrection(postId: string, text: string): Promise<boolean> {
  const [item] = await dbSelect<ItemRow>("content_plan_items", `select=*&post_id=eq.${postId}&kind=eq.image&media_path=not.is.null`);
  if (!item) return false;
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
        },
      });
    }

    const item = await getItem(body.id ?? body.item_id);

    if (body.action === "done") {
      if (item.status !== "gerando") throw new Problem(409, `O item está "${item.status}", não "gerando".`);
      const localPath = String(body.local_path ?? "");
      if (!localPath.startsWith("conteudos/") || localPath.includes("..")) throw new Problem(400, "Caminho da foto inválido.");
      const size = Number(body.size_bytes);
      await dbInsertIgnoringDuplicates(
        "media_assets",
        [
          {
            local_path: localPath,
            kind: "image",
            mime_type: "image/jpeg",
            size_bytes: Number.isSafeInteger(size) && size > 0 ? size : 0,
            title: item.prompt.slice(0, 120) || "Gerada pela automação",
            status: "rascunho",
            content_rating: item.adult ? "adulto" : "livre",
            created_at: now(),
          },
        ],
        "local_path"
      );
      const [media] = await dbSelect<{ id: string }>("media_assets", `select=id&local_path=eq.${encodeURIComponent(localPath)}`);
      if (!media) throw new Problem(500, "Não consegui cadastrar a foto na biblioteca.");
      const family = await familyOf(item);
      const correction = isCorrection(item);
      // a publicacao reprovada sai do caminho; a foto corrigida vira outra
      if (correction && item.post_id) await cancelDraftPost(item.post_id);
      const groupId = family.length > 1 ? crypto.randomUUID() : null;
      const targets = [];
      for (const it of family) {
        const post = await createDraftPost({
          network: it.network,
          formats: [it.format],
          media_ids: [media.id],
          caption: it.caption,
          hashtags: it.hashtags,
          scheduled_at: it.scheduled_at,
          // +18 vai para o canal do Telegram (postagem manual, com lembrete); o resto e automatico
          publish_mode: it.network === "telegram" ? "manual" : "api",
          flags: { aiGenerated: true },
          ...(groupId ? { group_id: groupId } : {}),
        });
        const submitted = await submitPost(post.id);
        await dbUpdateWhere("content_plan_items", `id=eq.${it.id}`, { post_id: post.id, media_path: localPath, ...(correction ? { note: null } : {}), updated_at: now() });
        targets.push({ item_id: it.id, post_id: post.id, version: submitted.version, network: it.network, format: it.format });
      }
      await audit(correction ? "corrigir_item" : "gerar_item", "content_plan_items", item.id, { publicacoes: targets.length });
      return reply(200, { media_id: media.id, targets });
    }

    if (body.action === "upload-url") {
      const size = Number(body.size_bytes);
      const [url] = await uploadUrlsFor(String(body.post_id ?? ""), body.version, [{ position: 0, mime_type: "image/jpeg", size_bytes: size }]);
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
