import { randomUUID } from "crypto";
import { DANCE_LINK, OUTFITS } from "./_outfits.js";
import { ADULT_DESTS } from "./_photo_blocks.js";
import { UPLOAD_BUCKET } from "./_posts.js";
import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { storageRemove } from "./_supabase.js";
import { telegram } from "./_telegram.js";

// Video pelo Telegram (01/11, parte 2 do pedido pelo celular): o administrador
// manda um video (ou o link de uma dancinha do TikTok/Instagram) no bot de
// aprovacao. O bot anota o video (o robo do PC baixa direto do Telegram na
// hora de fazer: baixar na Vercel estourava os 60 s - 01/11 22:13), pergunta o
// que fazer e mostra o resumo com
// o custo; "Gerar" poe na fila do robo do PC (queueDance / queueLongVideo, os
// mesmos do estudio) e o video pronto volta aqui para aprovar.
//
// Botoes: vd:<id>:<danca|longo> o que fazer, vs:<id>:<5|8|10> segundos da
// dancinha, vo:<id>:<n> roupa (-1 = a do video), vm:<id> som original/sem som,
// va:<id>:<canal|ensaio|album-vip> destinos do +18, vg:<id> gerar, vx:<id>
// cancelar. Responder o resumo com texto troca a legenda (a historinha no +18).

export const VIDEO_KINDS = new Set(["vd", "vs", "vo", "vm", "va", "vg", "vx"]);
export const VIDEO_TAG = "#video";
const LIST_KEY = "luna:tg-videos";
const KEEP = 10;
const TTL_MS = 24 * 3600_000;
const MAX_VIDEO = 20 * 1024 * 1024; // o bot so baixa arquivo do Telegram ate 20 MB
const LONG_SEGMENT = 5; // o mesmo de api/_plans.ts (trechos de 5 s, 6 creditos cada)
const LONG_MAX_SECONDS = 300;
const DANCE_SECONDS = [5, 8, 10];
const DANCE_TAGS = ["#dancinha", "#trend", "#reelsbrasil"];

type Kind = "danca" | "longo";

interface VideoOrder {
  id: string;
  path?: string; // video no armazenamento temporario (danca/<uuid>.mp4; pedidos antigos)
  tgFile?: string; // file_id do video no Telegram (o robo baixa de la)
  ext?: string;
  link?: string; // ou o link da dancinha
  duration?: number; // segundos (o Telegram manda no video; arquivo nao tem)
  caption: string;
  kind?: Kind;
  seconds: number;
  outfit: number; // indice em OUTFITS; -1 = a roupa do video
  sound: "original" | "sem";
  adult: string[];
  at: number;
}

type Say = (text: string, extra?: Record<string, unknown>) => Promise<unknown>;

export interface TgVideoMessage {
  message_id: number;
  caption?: string;
  text?: string;
  video?: { file_id: string; duration?: number; mime_type?: string; file_size?: number };
  document?: { file_id: string; mime_type?: string; file_size?: number; file_name?: string };
}

async function list(): Promise<VideoOrder[]> {
  try {
    const all = JSON.parse((await storeGet(LIST_KEY)) ?? "[]") as VideoOrder[];
    return all.filter((v) => Date.now() - v.at < TTL_MS);
  } catch {
    return [];
  }
}

async function load(id: string): Promise<VideoOrder | null> {
  return (await list()).find((v) => v.id === id) ?? null;
}

async function save(v: VideoOrder): Promise<void> {
  const rest = (await list()).filter((x) => x.id !== v.id);
  await storeSet(LIST_KEY, JSON.stringify([...rest, { ...v, at: Date.now() }].slice(-KEEP)));
}

async function drop(id: string): Promise<void> {
  await storeSet(LIST_KEY, JSON.stringify((await list()).filter((x) => x.id !== id)));
}

// dancinha: a maior duracao da Naiaflow (5, 8 ou 10 s) que cabe no video
const danceSecondsFor = (video?: number) => (video === undefined ? 5 : video >= 10 ? 10 : video >= 8 ? 8 : 5);
const segmentsOf = (v: VideoOrder) => Math.max(1, Math.ceil((v.duration ?? 0) / LONG_SEGMENT - 0.02));
// Copiar movimento: 1 credito por segundo + 1 do clone; video longo: 6 por trecho
const creditsOf = (v: VideoOrder) => (v.kind === "longo" ? segmentsOf(v) * (LONG_SEGMENT + 1) : v.seconds + 1);
const outfitText = (v: VideoOrder) => (v.outfit >= 0 ? OUTFITS[v.outfit] : "a do vídeo");
const plural = (n: number) => `${n} crédito${n > 1 ? "s" : ""}`;
const canLong = (v: VideoOrder) => !!(v.path || v.tgFile) && v.duration !== undefined && v.duration >= LONG_SEGMENT && v.duration <= LONG_MAX_SECONDS;

function kindButtons(v: VideoOrder) {
  const rows: { text: string; callback_data: string }[][] = [[{ text: "💃 Dancinha (Reels + TikTok)", callback_data: `vd:${v.id}:danca` }]];
  if (canLong(v)) rows.push([{ text: "🔥 Vídeo longo +18 (canal VIP e pacotes)", callback_data: `vd:${v.id}:longo` }]);
  rows.push([{ text: "✖ Cancelar", callback_data: `vx:${v.id}` }]);
  return { inline_keyboard: rows };
}

function summaryButtons(v: VideoOrder) {
  const rows: { text: string; callback_data: string }[][] = [];
  if (v.kind === "danca") {
    rows.push(
      DANCE_SECONDS.filter((s) => s === 5 || v.duration === undefined || v.duration >= s).map((s) => ({ text: `${v.seconds === s ? "✅" : "▫️"} ${s} s`, callback_data: `vs:${v.id}:${s}` }))
    );
    rows.push([{ text: v.sound === "original" ? "🔊 Som original (trocar para sem som)" : "🔇 Sem som (trocar para o original)", callback_data: `vm:${v.id}` }]);
  } else {
    rows.push(ADULT_DESTS.map((d) => ({ text: `${v.adult.includes(d.key) ? "✅" : "▫️"} ${d.label}`, callback_data: `va:${v.id}:${d.key}` })));
  }
  // roupas salvas na galeria da Naiaflow ("Minhas roupas"), duas por linha
  const outfits = [{ text: `${v.outfit < 0 ? "✅" : "▫️"} Roupa do vídeo`, callback_data: `vo:${v.id}:-1` }, ...OUTFITS.map((o, k) => ({ text: `${v.outfit === k ? "✅" : "▫️"} ${o}`, callback_data: `vo:${v.id}:${k}` }))];
  for (let k = 0; k < outfits.length; k += 2) rows.push(outfits.slice(k, k + 2));
  rows.push([{ text: `✅ Gerar (${plural(creditsOf(v))})`, callback_data: `vg:${v.id}` }]);
  rows.push([{ text: "✖ Cancelar", callback_data: `vx:${v.id}` }]);
  return { inline_keyboard: rows };
}

function summaryText(v: VideoOrder): string {
  const source = v.link ? `🔗 ${v.link}` : `🎞️ Vídeo de ${v.duration !== undefined ? `${Math.round(v.duration)} s` : "duração desconhecida"}`;
  const lines =
    v.kind === "longo"
      ? [
          `🔥 Vídeo longo +18 → ${ADULT_DESTS.filter((d) => v.adult.includes(d.key)).map((d) => d.label).join(" + ") || "nenhum marcado"}`,
          source,
          `✂️ ${segmentsOf(v)} trechos de ${LONG_SEGMENT} s, emendados com o som original`,
          "🕒 No próximo horário vazio do canal",
          `👗 Roupa: ${outfitText(v)}`,
          `💳 ${plural(creditsOf(v))}`,
          "",
          `💬 ${v.caption.slice(0, 1500) || "(sem historinha)"}`,
        ]
      : [
          "💃 Dancinha → Reels + TikTok",
          source,
          `⏱️ ${v.seconds} s · ${v.sound === "original" ? "som original" : "sem som"}`,
          "🕒 No próximo horário vazio dos Reels",
          `👗 Roupa: ${outfitText(v)}`,
          `💳 ${plural(creditsOf(v))}`,
          "",
          `💬 ${v.caption.slice(0, 1500) || "(sem legenda)"}`,
          `#️⃣ ${DANCE_TAGS.join(" ")}`,
        ];
  return [
    `🎬 Pedido de vídeo ${VIDEO_TAG} ${v.id}`,
    "",
    ...lines,
    "",
    `✏️ Para trocar ${v.kind === "longo" ? "a historinha" : "a legenda"}, responda esta mensagem com o texto novo.`,
  ].join("\n");
}

function newOrder(fields: Partial<VideoOrder>): VideoOrder {
  return { id: randomUUID().slice(0, 8), caption: "", seconds: 5, outfit: -1, sound: "original", adult: ADULT_DESTS.map((d) => d.key), at: Date.now(), ...fields };
}

const EXT: Record<string, string> = { "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm" };

// Video (ou arquivo de video) mandado no chat do bot: anota e pergunta o que fazer
export async function videoMessage(msg: TgVideoMessage, say: Say): Promise<void> {
  const replyTo = { reply_to_message_id: msg.message_id };
  if (!storeConfigured()) {
    await say("⚠️ Falta o Upstash Redis na Vercel para guardar o pedido.", replyTo);
    return;
  }
  const file = msg.video ?? msg.document;
  if (!file) return;
  const ext = EXT[file.mime_type ?? "video/mp4"];
  if (!ext) {
    await say("🎬 Mande o vídeo em MP4, MOV ou WEBM.", replyTo);
    return;
  }
  // o Telegram repete o aviso se a resposta demorar: o mesmo video e anotado uma vez so
  if (!(await storeSetIfAbsent(`luna:tg-video:${msg.message_id}`, "1", 3600))) return;
  if ((file.file_size ?? 0) > MAX_VIDEO) {
    await say("⚠️ Vídeo grande demais para o bot baixar (o Telegram só deixa até 20 MB). Corte ou comprima, mande o link do TikTok/Instagram, ou use o estúdio (até 50 MB).", replyTo);
    return;
  }
  const duration = msg.video?.duration;
  const v = newOrder({ tgFile: file.file_id, ext, ...(duration !== undefined ? { duration, seconds: danceSecondsFor(duration) } : {}), caption: (msg.caption ?? "").trim().slice(0, 2200) });
  await save(v);
  const tip =
    duration === undefined
      ? " (mandado como arquivo: para o vídeo longo +18, mande como vídeo)"
      : duration > LONG_MAX_SECONDS
        ? ` (${Math.round(duration)} s: o vídeo longo vai até ${LONG_MAX_SECONDS / 60} min)`
        : "";
  await say(`🎬 Recebi o vídeo${duration !== undefined ? ` (${Math.round(duration)} s)` : ""}${tip}. O que eu faço?`, { ...replyTo, reply_markup: kindButtons(v) });
}

// Link de dancinha (TikTok, Instagram...) mandado no chat: so a dancinha (o
// robo abre o link; o video longo precisa do arquivo)
export async function videoLink(text: string, messageId: number, say: Say): Promise<boolean> {
  const link = text.match(DANCE_LINK)?.[0];
  if (!link || !storeConfigured()) return false;
  if (!(await storeSetIfAbsent(`luna:tg-video:${messageId}`, "1", 3600))) return true;
  const caption = text.replace(link, "").trim().slice(0, 2200);
  const v = newOrder({ link, caption, kind: "danca" });
  await save(v);
  await say(summaryText(v), { reply_to_message_id: messageId, reply_markup: summaryButtons(v) });
  return true;
}

// Toque num botao do pedido de video
export async function videoCallback(token: string, chatId: string, kind: string, id: string, value: string, messageId: number | undefined, say: Say): Promise<void> {
  const replyTo = messageId ? { reply_to_message_id: messageId } : {};
  const clearButtons = () =>
    messageId ? telegram(token, "editMessageReplyMarkup", { chat_id: chatId, message_id: messageId, reply_markup: { inline_keyboard: [] } }).catch(() => undefined) : Promise.resolve();
  const v = await load(id);
  if (!v) {
    await clearButtons();
    await say("⏱️ Esse pedido expirou ou já foi feito. Mande o vídeo de novo.", replyTo);
    return;
  }
  if (kind === "vx") {
    await drop(id);
    // o video guardado nao vai ser usado
    if (v.path) await storageRemove(UPLOAD_BUCKET, [v.path]).catch(() => undefined);
    await clearButtons();
    await say("✖ Pedido de vídeo cancelado.", replyTo);
    return;
  }
  if (kind === "vd") {
    if (value !== "danca" && value !== "longo") return;
    if (value === "longo" && !canLong(v)) return;
    const next = { ...v, kind: value as Kind };
    await save(next);
    await clearButtons();
    await say(summaryText(next), { ...replyTo, reply_markup: summaryButtons(next) });
    return;
  }
  if (kind === "vs" || kind === "vo" || kind === "vm" || kind === "va") {
    if (!v.kind) return;
    let next: VideoOrder;
    if (kind === "vs") {
      const s = Number(value);
      if (v.kind !== "danca" || !DANCE_SECONDS.includes(s) || (s > 5 && v.duration !== undefined && v.duration < s)) return;
      next = { ...v, seconds: s };
    } else if (kind === "vo") {
      const k = Number(value);
      if (!Number.isInteger(k) || k < -1 || k >= OUTFITS.length) return;
      next = { ...v, outfit: k };
    } else if (kind === "vm") {
      if (v.kind !== "danca") return;
      next = { ...v, sound: v.sound === "original" ? "sem" : "original" };
    } else {
      if (v.kind !== "longo" || !ADULT_DESTS.some((d) => d.key === value)) return;
      next = { ...v, adult: v.adult.includes(value) ? v.adult.filter((k) => k !== value) : [...v.adult, value] };
    }
    await save(next);
    if (messageId) await telegram(token, "editMessageText", { chat_id: chatId, message_id: messageId, text: summaryText(next), reply_markup: summaryButtons(next) }).catch(() => undefined);
    return;
  }
  if (kind === "vg") {
    if (!v.kind) return;
    if (v.kind === "longo" && !v.adult.length) {
      await say("Marque pelo menos um destino do +18 (canal ou pacote).", replyTo);
      return;
    }
    // dois toques seguidos no Gerar nao poem o video duas vezes na fila
    if (!(await storeSetIfAbsent(`luna:tg-video-gerando:${id}`, "1", 60))) return;
    try {
      const { queueDance, queueLongVideo } = await import("./_plans.js");
      const outfit = v.outfit >= 0 ? OUTFITS[v.outfit] : "";
      const out =
        v.kind === "longo"
          ? await queueLongVideo({
              ...(v.tgFile ? { tg_file: v.tgFile, tg_ext: v.ext } : { path: v.path }),
              duration: v.duration,
              credits: creditsOf(v),
              outfit,
              caption: v.caption,
              scheduled_at: "auto",
              adultDest: { channel: v.adult.includes("canal"), packs: v.adult.filter((k) => k !== "canal") },
            })
          : await queueDance({
              ...(v.link ? { link: v.link } : v.tgFile ? { tg_file: v.tgFile, tg_ext: v.ext } : { path: v.path }),
              seconds: v.seconds,
              outfit,
              sound: v.sound,
              slots: ["reels", "tiktok"],
              caption: v.caption,
              hashtags: DANCE_TAGS,
              scheduled_at: "auto",
            });
      if (out.status !== 200) {
        await say(`⚠️ ${String(out.body.detail ?? "Não consegui pôr na fila.")}`, replyTo);
        return;
      }
      await drop(id);
      await clearButtons();
      const items = (out.body.items ?? []) as { format: string; scheduled_at: string }[];
      const when = items
        .map((i) => `${i.format === "Vídeo" ? "TikTok" : i.format}: ${new Date(i.scheduled_at).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo", weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}`)
        .join(" · ");
      await say(`✅ Na fila do robô (${v.kind === "longo" ? "vídeo longo +18" : "dancinha"}) · ${when}.\nO vídeo chega aqui para você aprovar — o robô do PC precisa estar aberto.`, replyTo);
    } finally {
      await storeDelete(`luna:tg-video-gerando:${id}`).catch(() => undefined);
    }
  }
}

// Resposta (texto) ao resumo do pedido de video: troca a legenda/historinha
export async function videoChange(id: string, text: string, messageId: number, say: Say): Promise<void> {
  const replyTo = { reply_to_message_id: messageId };
  const v = await load(id);
  if (!v || !v.kind) {
    await say("⏱️ Esse pedido expirou ou já foi feito. Mande o vídeo de novo.", replyTo);
    return;
  }
  const next = { ...v, caption: text.trim().slice(0, 2200) };
  await save(next);
  await say(summaryText(next), { ...replyTo, reply_markup: summaryButtons(next) });
}
