import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { ADULT_NOTE, AiError, PERSONA, askAi, type Mode } from "./_assistant.js";
import { audit, dbDelete, dbInsert, dbInsertIgnoringDuplicates, dbSelect, dbUpdate, dbUpdateWhere, supabaseConfigured } from "./_supabase.js";

// /api/studio?r=plans - planejamento feito pelo chat (parte A do fluxo).
// O estudio monta a grade (dias x quantidades x horarios padrao); a IA so
// preenche o criativo de cada item (prompt simples para a Naiaflow, legenda,
// hashtags), 20 por vez, para caber no tempo da funcao. O administrador revisa,
// ve a estimativa de creditos e aprova: os itens vao para a fila da automacao
// local (parte B), que gera, cria a publicacao e manda para o bot do Telegram.
//
// GET -> { plans: [{ ...plan, items }] }
// POST { action }:
//   generate { start, end, perDay, theme?, tiktokReuse? } -> cria o plano (grade)
//   fill { plan_id }            -> a IA escreve os proximos itens vazios
//   update-item { id, prompt?, caption?, hashtags?, scheduled_at? }
//   remove-item { id }
//   approve { plan_id }         -> itens vao para a fila (na_fila)
//   cancel { plan_id }          -> itens que ainda nao foram gerados sao cancelados
//   quick { slots (ou slot), prompt, caption?, hashtags?, scheduled_at } -> foto avulsa
//                               da Conversa: ja na fila do robo; com varios destinos
//                               a foto sai uma vez so e os outros a reaproveitam

const TZ_OFFSET = "-03:00"; // horario de Brasilia (sem horario de verao desde 2019)
const MAX_DAYS = 92; // trimestre
const MAX_ITEMS = 400;
const FILL_BATCH = 20;
// creditos da Naiaflow (estimativa): foto 1; Reels 1 (a foto; o video com zoom
// e montado no PC, de graca)
const CREDITS = { image: 1, video: 1 };

type SlotKey = "feed" | "stories" | "reels" | "tiktok" | "telegram18";
const SLOTS: Record<SlotKey, { network: "instagram" | "tiktok" | "telegram"; format: string; kind: "image" | "video"; adult: boolean; times: string[]; label: string }> = {
  feed: { network: "instagram", format: "Feed", kind: "image", adult: false, times: ["12:00", "19:00", "15:30", "21:00", "10:00"], label: "Instagram Feed" },
  stories: { network: "instagram", format: "Stories", kind: "image", adult: false, times: ["09:00", "13:00", "17:00", "21:30", "11:00", "15:00", "19:30", "23:00"], label: "Instagram Stories" },
  reels: { network: "instagram", format: "Reels", kind: "video", adult: false, times: ["18:00", "20:30", "14:00"], label: "Instagram Reels" },
  tiktok: { network: "tiktok", format: "Foto", kind: "image", adult: false, times: ["20:00", "13:30", "17:30"], label: "TikTok" },
  telegram18: { network: "telegram", format: "Canal", kind: "image", adult: true, times: ["22:00", "23:30", "21:00"], label: "Telegram +18" },
};

class Problem extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

interface PlanRow {
  id: string;
  title: string;
  request: Record<string, unknown>;
  status: "rascunho" | "aprovado" | "cancelado";
  approved_at: string | null;
  created_at: string;
}

interface ItemRow {
  id: string;
  plan_id: string;
  position: number;
  scheduled_at: string;
  network: string;
  format: string;
  kind: "image" | "video";
  adult: boolean;
  prompt: string;
  caption: string;
  hashtags: string[];
  source_item_id: string | null;
  status: string;
  attempts: number;
  post_id: string | null;
  note: string | null;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function uuid(v: unknown): string {
  if (typeof v !== "string" || !UUID.test(v)) throw new Problem(400, "Identificador inválido.");
  return v;
}

function days(start: string, end: string): string[] {
  if (!DAY.test(start) || !DAY.test(end)) throw new Problem(400, "Escolha a data de início e de fim.");
  const out: string[] = [];
  for (let d = new Date(`${start}T12:00:00Z`); d <= new Date(`${end}T12:00:00Z`); d = new Date(d.getTime() + 86400_000)) {
    out.push(d.toISOString().slice(0, 10));
    if (out.length > MAX_DAYS) throw new Problem(400, `No máximo ${MAX_DAYS} dias por plano (um trimestre).`);
  }
  if (!out.length) throw new Problem(400, "A data de fim vem antes do início.");
  return out;
}

// Grade do plano: cada dia, cada destino, na quantidade pedida, nos horarios padrao.
// TikTok reaproveita a foto do Feed do mesmo dia (mesmo conteudo nas duas redes).
export function buildSlots(start: string, end: string, perDay: Partial<Record<SlotKey, number>>, tiktokReuse: boolean) {
  const list = days(start, end);
  const counts = Object.fromEntries(
    (Object.keys(SLOTS) as SlotKey[]).map((k) => {
      const n = Number(perDay[k] ?? 0);
      if (!Number.isInteger(n) || n < 0 || n > SLOTS[k].times.length) throw new Problem(400, `${SLOTS[k].label}: de 0 a ${SLOTS[k].times.length} por dia.`);
      return [k, n];
    })
  ) as Record<SlotKey, number>;
  const slots: { key: SlotKey; day: string; scheduled_at: string; reuseOf: number | null }[] = [];
  for (const day of list) {
    const feedIndex: number[] = [];
    for (const key of Object.keys(SLOTS) as SlotKey[]) {
      for (let i = 0; i < counts[key]; i++) {
        const time = [...SLOTS[key].times].slice(0, counts[key]).sort()[i];
        let reuseOf: number | null = null;
        if (key === "tiktok" && tiktokReuse && feedIndex[i] !== undefined) reuseOf = feedIndex[i];
        slots.push({ key, day, scheduled_at: `${day}T${time}:00${TZ_OFFSET}`, reuseOf });
        if (key === "feed") feedIndex.push(slots.length - 1);
      }
    }
  }
  if (!slots.length) throw new Problem(400, "Escolha pelo menos uma publicação por dia.");
  if (slots.length > MAX_ITEMS) throw new Problem(400, `O plano passou de ${MAX_ITEMS} publicações: diminua o período ou as quantidades.`);
  return slots;
}

function credits(items: Pick<ItemRow, "kind" | "source_item_id" | "status">[]) {
  const live = items.filter((i) => i.status !== "cancelado" && !i.source_item_id);
  return {
    images: live.filter((i) => i.kind === "image").length,
    videos: live.filter((i) => i.kind === "video").length,
    credits: live.reduce((s, i) => s + CREDITS[i.kind], 0),
  };
}

function weekday(iso: string): string {
  return new Date(iso).toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "2-digit", timeZone: "America/Sao_Paulo" });
}

function parseJson(text: string): { items?: { i?: number; prompt?: string; caption?: string; hashtags?: unknown }[] } {
  const clean = text.replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
  try {
    return JSON.parse(clean);
  } catch {
    const start = clean.indexOf("{");
    const end = clean.lastIndexOf("}");
    if (start >= 0 && end > start) return JSON.parse(clean.slice(start, end + 1));
    throw new Problem(502, "A IA não devolveu o plano no formato certo. Tente de novo.");
  }
}

function cleanTags(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return [...new Set(raw.map((t) => `#${String(t).replace(/^#+/, "").replace(/\s+/g, "")}`).filter((t) => t.length > 1 && !/papai/i.test(t)))].slice(0, 5);
}

// A IA escreve o criativo de um lote de itens do mesmo modo (redes ou +18).
async function writeBatch(mode: Mode, plan: PlanRow, batch: ItemRow[]): Promise<number> {
  const theme = typeof plan.request.theme === "string" && plan.request.theme.trim() ? plan.request.theme.trim() : "livre, variado, cenários reais do Brasil";
  const system = [
    PERSONA,
    mode === "adulto" ? ADULT_NOTE : "",
    'Responda SÓ com JSON no formato {"items":[{"i":0,"prompt":"...","caption":"...","hashtags":["#..."]}]}, um objeto por item pedido, com o mesmo "i".',
  ]
    .filter(Boolean)
    .join("\n\n");
  const user = [
    `Tema do período: ${theme}.`,
    "Para cada publicação abaixo, escreva: prompt (frase simples para a Naiaflow: Luna + roupa + lugar + o que faz + clima), caption (legenda curta) e hashtags (até 5).",
    "Varie roupas, lugares e momentos entre os itens; nada repetido.",
    ...batch.map((it, i) => `${i}) ${weekday(it.scheduled_at)} · ${SLOTS[(Object.keys(SLOTS) as SlotKey[]).find((k) => SLOTS[k].network === it.network && SLOTS[k].format === it.format)!].label}${it.kind === "video" ? " (foto que vira Reels com zoom leve)" : " (foto)"}`),
  ].join("\n");
  const out = await askAi(mode, [{ role: "system", content: system }, { role: "user", content: user }], { json: true, maxTokens: 3000, timeoutMs: 50_000 });
  const parsed = parseJson(out.text);
  let written = 0;
  for (const r of parsed.items ?? []) {
    const it = batch[Number(r.i)];
    if (!it || typeof r.prompt !== "string" || !r.prompt.trim()) continue;
    await dbUpdateWhere("content_plan_items", `id=eq.${it.id}`, {
      prompt: r.prompt.trim().slice(0, 2000),
      caption: typeof r.caption === "string" ? r.caption.trim().slice(0, 4000) : "",
      hashtags: cleanTags(r.hashtags),
      updated_at: new Date().toISOString(),
    });
    written++;
  }
  return written;
}

async function getPlan(id: string): Promise<PlanRow> {
  const [plan] = await dbSelect<PlanRow>("content_plans", `select=*&id=eq.${uuid(id)}`);
  if (!plan) throw new Problem(404, "Plano não encontrado.");
  return plan;
}

async function itemsOf(planId: string): Promise<ItemRow[]> {
  return dbSelect<ItemRow>("content_plan_items", `select=*&plan_id=eq.${planId}&order=position.asc`);
}

// itens que reaproveitam outro copiam o criativo dele (mesma foto, mesma legenda)
async function syncReused(items: ItemRow[]) {
  for (const it of items.filter((i) => i.source_item_id)) {
    const src = items.find((s) => s.id === it.source_item_id);
    if (src && src.prompt && (it.prompt !== src.prompt || it.caption !== src.caption)) {
      await dbUpdateWhere("content_plan_items", `id=eq.${it.id}`, { prompt: src.prompt, caption: src.caption, hashtags: src.hashtags, updated_at: new Date().toISOString() });
    }
  }
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
  if (!supabaseConfigured()) return reply(501, { detail: "Banco (Supabase) ainda não configurado na Vercel." });

  try {
    if (req.method === "GET") {
      const plans = await dbSelect<PlanRow>("content_plans", "select=*&order=created_at.desc&limit=20");
      const out = [];
      for (const p of plans) {
        const items = await itemsOf(p.id);
        out.push({ ...p, items, estimate: credits(items), pending: items.filter((i) => !i.prompt && !i.source_item_id && i.status !== "cancelado").length });
      }
      return reply(200, { plans: out });
    }
    if (req.method !== "POST") return reply(405, { detail: "Use GET ou POST." });
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw || "{}") as Record<string, unknown>;

    if (body.action === "generate") {
      const start = String(body.start ?? "");
      const end = String(body.end ?? "");
      const perDay = (body.perDay ?? {}) as Partial<Record<SlotKey, number>>;
      const tiktokReuse = body.tiktokReuse !== false;
      const slots = buildSlots(start, end, perDay, tiktokReuse);
      const theme = typeof body.theme === "string" ? body.theme.slice(0, 1000) : "";
      const title = `${start.split("-").reverse().join("/")} a ${end.split("-").reverse().join("/")}`;
      const plan = await dbInsert<PlanRow>("content_plans", { title, request: { start, end, perDay, theme, tiktokReuse } });
      const rows = slots.map((s, position) => ({
        plan_id: plan.id,
        position,
        scheduled_at: s.scheduled_at,
        network: SLOTS[s.key].network,
        format: SLOTS[s.key].format,
        kind: SLOTS[s.key].kind,
        adult: SLOTS[s.key].adult,
      }));
      const inserted = await dbInsertIgnoringDuplicates<ItemRow>("content_plan_items", rows, "plan_id,position");
      // TikTok aponta para o Feed do mesmo dia (depois de inserir, ja com ids)
      const byPos = new Map(inserted.map((r) => [r.position, r]));
      for (const [pos, s] of slots.entries()) {
        if (s.reuseOf !== null) await dbUpdateWhere("content_plan_items", `id=eq.${byPos.get(pos)!.id}`, { source_item_id: byPos.get(s.reuseOf)!.id });
      }
      await audit("planejar", "content_plans", plan.id, { itens: slots.length });
      const items = await itemsOf(plan.id);
      return reply(200, { plan: { ...plan, items, estimate: credits(items), pending: items.filter((i) => !i.source_item_id).length } });
    }

    // Foto avulsa pedida na Conversa do Assistente: um plano de 1 item, ja
    // aprovado, direto na fila do robo do PC (gera, cria a publicacao e manda
    // para a analise no Telegram, igual aos itens de um plano).
    if (body.action === "quick") {
      // um ou mais destinos; a foto e gerada uma vez so e os outros destinos
      // reaproveitam (mesma foto e legenda, uma publicacao para cada)
      const raw: unknown[] = Array.isArray(body.slots) ? body.slots : [body.slot];
      const keys = [...new Set(raw.map((k) => String(k ?? "")))] as SlotKey[];
      if (!keys.length || keys.length > 4 || keys.some((k) => !SLOTS[k] || SLOTS[k].kind !== "image")) throw new Problem(400, "Escolha o destino da foto.");
      if (keys.includes("telegram18") && keys.length > 1) throw new Problem(400, "Foto +18 vai só para o canal do Telegram: não dá para juntar com Instagram ou TikTok.");
      const prompt = typeof body.prompt === "string" ? body.prompt.trim().slice(0, 2000) : "";
      if (!prompt) throw new Problem(400, "Escreva o pedido da foto.");
      const when = typeof body.scheduled_at === "string" ? Date.parse(body.scheduled_at) : NaN;
      if (Number.isNaN(when)) throw new Problem(400, "Escolha o horário da publicação.");
      const stamp = new Date().toISOString();
      const plan = await dbInsert<PlanRow>("content_plans", {
        title: `Foto avulsa do chat (${keys.map((k) => SLOTS[k].label).join(" + ")})`,
        request: { source: "chat", slot: keys[0], slots: keys },
        status: "aprovado",
        approved_at: stamp,
      });
      const caption = typeof body.caption === "string" ? body.caption.slice(0, 4000) : "";
      const hashtags = cleanTags(body.hashtags);
      const items: ItemRow[] = [];
      for (const [position, key] of keys.entries()) {
        const slot = SLOTS[key];
        items.push(
          await dbInsert<ItemRow>("content_plan_items", {
            plan_id: plan.id,
            position,
            scheduled_at: new Date(when).toISOString(),
            network: slot.network,
            format: slot.format,
            kind: "image",
            adult: slot.adult,
            prompt,
            caption,
            hashtags,
            status: "na_fila",
            ...(items[0] ? { source_item_id: items[0].id } : {}),
          })
        );
      }
      await audit("foto_avulsa", "content_plan_items", items[0].id, { destino: keys.join(",") });
      return reply(200, { item: items[0], items, estimate: credits(items) });
    }

    // acoes de item chegam com o id do item; as de plano, com plan_id
    const planId =
      typeof body.plan_id === "string"
        ? body.plan_id
        : (await dbSelect<{ plan_id: string }>("content_plan_items", `select=plan_id&id=eq.${uuid(body.id)}`))[0]?.plan_id;
    if (!planId) throw new Problem(404, "Item não encontrado.");
    const plan = await getPlan(planId);

    if (body.action === "fill") {
      if (plan.status !== "rascunho") throw new Problem(409, "Este plano já foi aprovado ou cancelado.");
      const items = await itemsOf(plan.id);
      const empty = items.filter((i) => !i.prompt && !i.source_item_id && i.status !== "cancelado");
      // um modo por chamada: primeiro as redes, depois o +18 (cada um com a sua IA)
      const mode: Mode = empty.some((i) => !i.adult) ? "normal" : "adulto";
      const batch = empty.filter((i) => i.adult === (mode === "adulto")).slice(0, FILL_BATCH);
      const written = batch.length ? await writeBatch(mode, plan, batch) : 0;
      const fresh = await itemsOf(plan.id);
      await syncReused(fresh);
      const after = await itemsOf(plan.id);
      return reply(200, { written, pending: after.filter((i) => !i.prompt && !i.source_item_id && i.status !== "cancelado").length, plan: { ...plan, items: after, estimate: credits(after) } });
    }

    if (body.action === "update-item" || body.action === "remove-item") {
      if (plan.status !== "rascunho") throw new Problem(409, "Só dá para mexer nos itens antes de aprovar o plano.");
      const id = uuid(body.id);
      if (body.action === "remove-item") {
        await dbUpdateWhere("content_plan_items", `source_item_id=eq.${id}`, { source_item_id: null });
        await dbDelete("content_plan_items", `id=eq.${id}`);
      } else {
        const patch: Record<string, unknown> = { updated_at: new Date().toISOString() };
        if (typeof body.prompt === "string") patch.prompt = body.prompt.slice(0, 2000);
        if (typeof body.caption === "string") patch.caption = body.caption.slice(0, 4000);
        if (body.hashtags !== undefined) patch.hashtags = cleanTags(body.hashtags);
        if (typeof body.scheduled_at === "string" && !Number.isNaN(Date.parse(body.scheduled_at))) patch.scheduled_at = new Date(body.scheduled_at).toISOString();
        await dbUpdateWhere("content_plan_items", `id=eq.${id}`, patch);
      }
      const items = await itemsOf(plan.id);
      await syncReused(items);
      const after = await itemsOf(plan.id);
      return reply(200, { plan: { ...plan, items: after, estimate: credits(after) } });
    }

    if (body.action === "approve") {
      if (plan.status !== "rascunho") throw new Problem(409, "Este plano já foi aprovado ou cancelado.");
      const items = await itemsOf(plan.id);
      if (items.some((i) => !i.prompt && i.status !== "cancelado")) throw new Problem(400, "Ainda há itens sem prompt: espere a IA terminar de escrever.");
      const saved = await dbUpdate<PlanRow>("content_plans", `id=eq.${plan.id}&status=eq.rascunho`, { status: "aprovado", approved_at: new Date().toISOString(), updated_at: new Date().toISOString() });
      if (!saved) throw new Problem(409, "O plano mudou em outro lugar. Recarregue.");
      await dbUpdateWhere("content_plan_items", `plan_id=eq.${plan.id}&status=eq.planejado`, { status: "na_fila", updated_at: new Date().toISOString() });
      await audit("aprovar_plano", "content_plans", plan.id, credits(items));
      const after = await itemsOf(plan.id);
      return reply(200, { plan: { ...saved, items: after, estimate: credits(after) } });
    }

    if (body.action === "cancel") {
      await dbUpdateWhere("content_plans", `id=eq.${plan.id}`, { status: "cancelado", updated_at: new Date().toISOString() });
      for (const st of ["planejado", "na_fila"]) await dbUpdateWhere("content_plan_items", `plan_id=eq.${plan.id}&status=eq.${st}`, { status: "cancelado", updated_at: new Date().toISOString() });
      await audit("cancelar_plano", "content_plans", plan.id, {});
      const after = await itemsOf(plan.id);
      return reply(200, { plan: { ...plan, status: "cancelado", items: after, estimate: credits(after) } });
    }

    return reply(400, { detail: "Ação desconhecida." });
  } catch (e) {
    if (e instanceof Problem || e instanceof AiError) return reply(e.status, { detail: e.message });
    return reply(500, { detail: e instanceof Error ? e.message : String(e) });
  }
}
