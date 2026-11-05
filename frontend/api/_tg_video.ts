import { randomUUID } from "crypto";
import { CUT_SECONDS, DANCE_LINK, longCut, longPieces } from "./_outfits.js";
import { outfitList } from "./_outfit_list.js";
import { ADULT_DESTS } from "./_photo_blocks.js";
import { UPLOAD_BUCKET } from "./_posts.js";
import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { storageRemove } from "./_supabase.js";
import { MIN_SCORE, ratingText, type RefRating } from "./_ref_rating.js";
import { sendNotice, telegram, telegramConfig } from "./_telegram.js";

// Video pelo Telegram (01/11, parte 2 do pedido pelo celular): o administrador
// manda um video (ou o link de uma dancinha do TikTok/Instagram) no bot de
// aprovacao. O bot anota o video (o robo do PC baixa direto do Telegram na
// hora de fazer: baixar na Vercel estourava os 60 s - 01/11 22:13), pergunta o
// que fazer e mostra o resumo com
// o custo; "Gerar" poe na fila do robo do PC (queueDance / queueLongVideo, os
// mesmos do estudio) e o video pronto volta aqui para aprovar.
//
// Botoes: vd:<id>:<danca|longo> o que fazer, vs:<id>:<5|8|10> segundos da
// dancinha, vo:<id>:<v|g|f|n> roupa (05/11: v = igual ao video, g = abre "Minhas
// roupas", n = a roupa n da lista, f = pede a foto da roupa), vm:<id> som original/sem som,
// va:<id>:<canal|ensaio|album-vip> destinos do +18, vt:<id>:<0|5|10|15> quanto
// do video longo (0 = completo), vg:<id> gerar, vx:<id> cancelar. Responder o
// resumo com texto troca a legenda (a historinha no +18); no video longo,
// "do 10 ao 20" escolhe o pedaco.

export const VIDEO_KINDS = new Set(["vd", "vs", "vo", "vm", "va", "vt", "vg", "vx"]);
export const VIDEO_TAG = "#video";
// pedido da foto da roupa: a resposta com a foto chega em videoOutfitPhoto
export const OUTFIT_TAG = "#roupa";
const LIST_KEY = "luna:tg-videos";
const KEEP = 10;
const TTL_MS = 24 * 3600_000;
const MAX_VIDEO = 20 * 1024 * 1024; // o bot so baixa arquivo do Telegram ate 20 MB
const LONG_SEGMENT = 5; // o mesmo de api/_plans.ts (trechos de 5 s, 6 creditos cada)
const LONG_MAX_SECONDS = 300;
const DANCE_SECONDS = [5, 8, 10];
// resposta "do 10 ao 20" (segundos do video) ao resumo do video longo
const CUT_REPLY = /^\s*(?:d[oe]s?\s*)?(\d{1,3})\s*(?:s|seg\w*)?\s*(?:ao?|at[ée]|-)\s*(\d{1,3})\s*(?:s|seg\w*)?\s*$/i;
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
  cut?: { from: number; seconds: number }; // video longo: so um pedaco (sem = completo)
  // roupa (05/11): nada = igual ao video; outfitName = de "Minhas roupas";
  // outfitRef = foto da roupa mandada (so para este pedido, gratis)
  outfitName?: string;
  outfitRef?: string;
  outfitOpen?: boolean; // lista "Minhas roupas" aberta nos botoes
  sound: "original" | "sem";
  adult: string[];
  at: number;
  // 03/11: nota do video (0 a 100) dada pelo robo do PC antes do Gerar
  rating?: { status: "pendente" | "pronto" | "erro"; score?: number; note?: string };
  // 05/11: os botoes so aparecem junto com a nota; ate la, "processando a nota"
  waiting?: boolean; // esperando a nota para mostrar os botoes
  waitMsg?: number; // mensagem "processando" (resposta ao video), para responder a nota nela
  optionsShown?: boolean; // os botoes ja foram mostrados (robo desligado: na hora)
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
const fullSegments = (v: VideoOrder) => longPieces(v.duration ?? 0);
const segmentsOf = (v: VideoOrder) => (v.cut ? v.cut.seconds / LONG_SEGMENT : fullSegments(v));
const trechos = (n: number) => `${n} trecho${n > 1 ? "s" : ""}`;
const cutOf = (v: VideoOrder, from: number, seconds: number) => longCut(v.duration ?? 0, from, seconds);
// Copiar movimento: 1 credito por segundo + 1 do clone; video longo: 6 por trecho
const creditsOf = (v: VideoOrder) => (v.kind === "longo" ? segmentsOf(v) * (LONG_SEGMENT + 1) : v.seconds + 1);
const outfitText = (v: VideoOrder) => (v.outfitRef ? "a da foto que você mandou" : v.outfitName ?? "igual ao vídeo");
const plural = (n: number) => `${n} crédito${n > 1 ? "s" : ""}`;
const canLong = (v: VideoOrder) => !!(v.path || v.tgFile) && v.duration !== undefined && v.duration >= LONG_SEGMENT && v.duration <= LONG_MAX_SECONDS;

function kindButtons(v: VideoOrder) {
  const rows: { text: string; callback_data: string }[][] = [[{ text: "💃 Dancinha (Reels + TikTok)", callback_data: `vd:${v.id}:danca` }]];
  if (canLong(v)) rows.push([{ text: "🔥 Vídeo longo +18 (canal VIP e pacotes)", callback_data: `vd:${v.id}:longo` }]);
  rows.push([{ text: "✖ Cancelar", callback_data: `vx:${v.id}` }]);
  return { inline_keyboard: rows };
}

function summaryButtons(v: VideoOrder, names: string[]) {
  const rows: { text: string; callback_data: string }[][] = [];
  if (v.kind === "danca") {
    rows.push(
      DANCE_SECONDS.filter((s) => s === 5 || v.duration === undefined || v.duration >= s).map((s) => ({ text: `${v.seconds === s ? "✅" : "▫️"} ${s} s`, callback_data: `vs:${v.id}:${s}` }))
    );
    rows.push([{ text: v.sound === "original" ? "🔊 Som original (trocar para sem som)" : "🔇 Sem som (trocar para o original)", callback_data: `vm:${v.id}` }]);
  } else {
    rows.push(ADULT_DESTS.map((d) => ({ text: `${v.adult.includes(d.key) ? "✅" : "▫️"} ${d.label}`, callback_data: `va:${v.id}:${d.key}` })));
    // quanto do video: um pedaco (5/10/15 s) ou o completo
    const cuts = CUT_SECONDS.filter((s) => s / LONG_SEGMENT < fullSegments(v));
    if (cuts.length) {
      rows.push(cuts.map((s) => ({ text: `${v.cut?.seconds === s ? "✅" : "▫️"} ${s} s · ${(s / LONG_SEGMENT) * (LONG_SEGMENT + 1)} créd`, callback_data: `vt:${v.id}:${s}` })));
      rows.push([{ text: `${v.cut ? "▫️" : "✅"} Completo (${Math.round(v.duration ?? 0)} s · ${fullSegments(v) * (LONG_SEGMENT + 1)} créd)`, callback_data: `vt:${v.id}:0` }]);
    }
  }
  // roupa (05/11): igual ao video, uma de "Minhas roupas" (a lista abre ao tocar)
  // ou a foto de uma roupa mandada aqui
  const mode = v.outfitRef ? "f" : v.outfitName ? "g" : "v";
  const mark = (m: string) => (mode === m ? "✅" : "▫️");
  rows.push([{ text: `${mark("v")} 🎥 Roupa igual ao vídeo`, callback_data: `vo:${v.id}:v` }]);
  rows.push([{ text: `${mark("g")} 👙 Minhas roupas${v.outfitName ? `: ${v.outfitName}` : ` (${names.length})`} ${v.outfitOpen ? "▴" : "▾"}`, callback_data: `vo:${v.id}:g` }]);
  if (v.outfitOpen) {
    const list = names.map((o, k) => ({ text: `${v.outfitName === o ? "✅" : "▫️"} ${o}`, callback_data: `vo:${v.id}:${k}` }));
    for (let k = 0; k < list.length; k += 2) rows.push(list.slice(k, k + 2));
  }
  rows.push([{ text: mode === "f" ? "✅ 📸 Roupa da foto (tocar para mandar outra)" : "▫️ 📸 Mandar foto da roupa", callback_data: `vo:${v.id}:f` }]);
  rows.push([{ text: `✅ Gerar (${plural(creditsOf(v))})`, callback_data: `vg:${v.id}` }]);
  rows.push([{ text: "✖ Cancelar", callback_data: `vx:${v.id}` }]);
  return { inline_keyboard: rows };
}

// botoes do resumo com a lista de roupas atual (a do robo, ou a fixa)
async function buttonsFor(v: VideoOrder) {
  return summaryButtons(v, await outfitList());
}

// linha da nota no resumo do pedido
function ratingLine(v: VideoOrder): string | null {
  const r = v.rating;
  if (!r) return null;
  if (r.status === "pendente") return "🔎 Nota do vídeo: o robô do PC está analisando (chega aqui em 1 a 2 min)…";
  if (r.status === "erro") return `🔎 Nota do vídeo: ${r.note ?? "não deu para analisar"}`;
  return `🔎 Nota prevista: ${r.score}/100 ${(r.score ?? 0) >= MIN_SCORE ? "✅ pode gerar" : "⚠️ deve sair com defeito"}`;
}

function summaryText(v: VideoOrder): string {
  const source = v.link ? `🔗 ${v.link}` : `🎞️ Vídeo de ${v.duration !== undefined ? `${Math.round(v.duration)} s` : "duração desconhecida"}`;
  const rating = ratingLine(v);
  const lines =
    v.kind === "longo"
      ? [
          `🔥 Vídeo longo +18 → ${ADULT_DESTS.filter((d) => v.adult.includes(d.key)).map((d) => d.label).join(" + ") || "nenhum marcado"}`,
          source,
          ...(rating ? [rating] : []),
          `✂️ ${trechos(segmentsOf(v))} de ${LONG_SEGMENT} s${v.cut ? ` (do ${v.cut.from} ao ${v.cut.from + v.cut.seconds} s do vídeo)` : " (o vídeo completo)"}, ${segmentsOf(v) > 1 ? "emendados " : ""}com o som original`,
          "🕒 No próximo horário vazio do canal",
          `👗 Roupa: ${outfitText(v)}`,
          `💳 ${plural(creditsOf(v))}`,
          "",
          `💬 ${v.caption.slice(0, 1500) || "(sem historinha)"}`,
        ]
      : [
          "💃 Dancinha → Reels + TikTok",
          source,
          ...(rating ? [rating] : []),
          `⏱️ ${v.seconds} s · ${v.sound === "original" ? "som original" : "sem som"}`,
          "🕒 No próximo horário vazio dos Reels",
          `👗 Roupa: ${outfitText(v)}`,
          `💳 ${plural(creditsOf(v))}`,
          "",
          `💬 ${v.caption.slice(0, 1500) || "(sem legenda: entra uma automática do dia, tipo \"Sextou pedindo dancinha 💃 Quem dança comigo?\")"}`,
          `#️⃣ ${DANCE_TAGS.join(" ")}`,
        ];
  return [
    `🎬 Pedido de vídeo ${VIDEO_TAG} ${v.id}`,
    "",
    ...lines,
    "",
    `✏️ Para trocar ${v.kind === "longo" ? "a historinha" : "a legenda"}, responda esta mensagem com o texto novo.`,
    ...(v.kind === "longo" && fullSegments(v) > 1 ? ["✂️ Para usar outro pedaço do vídeo, responda com \"do 10 ao 20\" (segundos)."] : []),
  ].join("\n");
}

// robo do PC ligado (falou com o estudio nos ultimos minutos): so ele da a nota
const WORKER_SEEN_KEY = "luna:worker-seen"; // o mesmo de api/_worker.ts
const WORKER_FRESH_MS = 5 * 60_000;
async function workerOnline(): Promise<boolean> {
  const seen = await storeGet(WORKER_SEEN_KEY).catch(() => null);
  return !!seen && Date.now() - Date.parse(seen) < WORKER_FRESH_MS;
}

const cancelButton = (v: VideoOrder) => ({ inline_keyboard: [[{ text: "✖ Cancelar", callback_data: `vx:${v.id}` }]] });
const WAITING = "⏳ Processando a nota do vídeo (de 0 a 100) no robô do PC… Quando ela chegar, aparecem aqui as opções.";
const OFFLINE = "🔎 O robô do PC está desligado: a nota chega aqui quando ele ligar (dá para escolher já).";

// Mostra o pedido: com o robo ligado, so "processando" (os botoes vem com a nota);
// desligado, os botoes na hora (senao o pedido ficaria parado sem opcao)
async function askOrder(v: VideoOrder, head: string, replyTo: Record<string, unknown>, say: Say): Promise<void> {
  if (await workerOnline()) {
    await save({ ...v, waiting: true });
    const sent = (await say(`${head}\n${WAITING}`, { ...replyTo, reply_markup: cancelButton(v) })) as { message_id?: number } | undefined;
    // relê: a nota pode ter chegado enquanto a mensagem ia (nao apaga a nota)
    const cur = await load(v.id);
    if (cur && !cur.optionsShown && sent?.message_id) await save({ ...cur, waitMsg: sent.message_id });
    return;
  }
  await save({ ...v, optionsShown: true });
  if (v.kind) await say(summaryText(v), { ...replyTo, reply_markup: await buttonsFor(v) });
  else await say(`${head} O que eu faço?\n${OFFLINE}`, { ...replyTo, reply_markup: kindButtons(v) });
}

function newOrder(fields: Partial<VideoOrder>): VideoOrder {
  return { id: randomUUID().slice(0, 8), caption: "", seconds: 5, sound: "original", adult: ADULT_DESTS.map((d) => d.key), at: Date.now(), ...fields };
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
  const v = newOrder({ tgFile: file.file_id, ext, ...(duration !== undefined ? { duration, seconds: danceSecondsFor(duration) } : {}), caption: (msg.caption ?? "").trim().slice(0, 2200), rating: { status: "pendente" } });
  await askOrder(v, `🎬 Recebi o vídeo${receivedTip(v)}.`, replyTo, say);
}

function receivedTip(v: VideoOrder): string {
  if (v.link) return "";
  const d = v.duration;
  const tip =
    d === undefined
      ? " (mandado como arquivo: para o vídeo longo +18, mande como vídeo)"
      : d > LONG_MAX_SECONDS
        ? ` (${Math.round(d)} s: o vídeo longo vai até ${LONG_MAX_SECONDS / 60} min)`
        : "";
  return `${d !== undefined ? ` (${Math.round(d)} s)` : ""}${tip}`;
}

// Link de dancinha (TikTok, Instagram...) mandado no chat: so a dancinha (o
// robo abre o link; o video longo precisa do arquivo)
export async function videoLink(text: string, messageId: number, say: Say): Promise<boolean> {
  const link = text.match(DANCE_LINK)?.[0];
  if (!link || !storeConfigured()) return false;
  if (!(await storeSetIfAbsent(`luna:tg-video:${messageId}`, "1", 3600))) return true;
  const caption = text.replace(link, "").trim().slice(0, 2200);
  const v = newOrder({ link, caption, kind: "danca", rating: { status: "pendente" } });
  await askOrder(v, "🔗 Recebi o link da dancinha.", { reply_to_message_id: messageId }, say);
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
    await say(summaryText(next), { ...replyTo, reply_markup: await buttonsFor(next) });
    return;
  }
  if (kind === "vs" || kind === "vo" || kind === "vm" || kind === "va" || kind === "vt") {
    if (!v.kind) return;
    let next: VideoOrder;
    if (kind === "vs") {
      const s = Number(value);
      if (v.kind !== "danca" || !DANCE_SECONDS.includes(s) || (s > 5 && v.duration !== undefined && v.duration < s)) return;
      next = { ...v, seconds: s };
    } else if (kind === "vo") {
      if (value === "f") {
        // pede a foto: a resposta (com a foto) chega em videoOutfitPhoto
        await say(`📸 Mande a foto da roupa respondendo esta mensagem (pode ser a peça sozinha ou alguém vestindo). A Luna usa essa roupa só neste vídeo, sem custo a mais.
${OUTFIT_TAG} ${v.id}`, {
          ...replyTo,
          reply_markup: { force_reply: true, input_field_placeholder: "Mande a foto da roupa" },
        });
        return;
      }
      // trocou a foto por outra opcao: a foto nao vai ser usada
      if ((value === "v" || /^\d+$/.test(value)) && v.outfitRef) await storageRemove(UPLOAD_BUCKET, [v.outfitRef]).catch(() => undefined);
      if (value === "v") next = { ...v, outfitName: undefined, outfitRef: undefined, outfitOpen: false };
      else if (value === "g") next = { ...v, outfitOpen: !v.outfitOpen };
      else {
        const names = await outfitList();
        const k = Number(value);
        if (!Number.isInteger(k) || k < 0 || k >= names.length) return;
        next = { ...v, outfitName: names[k], outfitRef: undefined, outfitOpen: false };
      }
    } else if (kind === "vt") {
      const s = Number(value);
      if (v.kind !== "longo" || (s !== 0 && !CUT_SECONDS.includes(s))) return;
      next = { ...v, cut: s ? cutOf(v, v.cut?.from ?? 0, s) : undefined };
    } else if (kind === "vm") {
      if (v.kind !== "danca") return;
      next = { ...v, sound: v.sound === "original" ? "sem" : "original" };
    } else {
      if (v.kind !== "longo" || !ADULT_DESTS.some((d) => d.key === value)) return;
      next = { ...v, adult: v.adult.includes(value) ? v.adult.filter((k) => k !== value) : [...v.adult, value] };
    }
    await save(next);
    if (messageId) await telegram(token, "editMessageText", { chat_id: chatId, message_id: messageId, text: summaryText(next), reply_markup: await buttonsFor(next) }).catch(() => undefined);
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
      const outfit = v.outfitName ?? "";
      const outfitRef = v.outfitRef ? { outfit_ref: v.outfitRef } : {};
      const out =
        v.kind === "longo"
          ? await queueLongVideo({
              ...(v.tgFile ? { tg_file: v.tgFile, tg_ext: v.ext } : { path: v.path }),
              duration: v.duration,
              ...(v.cut ? { cut_from: v.cut.from, cut_seconds: v.cut.seconds } : {}),
              credits: creditsOf(v),
              outfit,
              ...outfitRef,
              caption: v.caption,
              scheduled_at: "auto",
              adultDest: { channel: v.adult.includes("canal"), packs: v.adult.filter((k) => k !== "canal") },
            })
          : await queueDance({
              ...(v.link ? { link: v.link } : v.tgFile ? { tg_file: v.tgFile, tg_ext: v.ext } : { path: v.path }),
              seconds: v.seconds,
              outfit,
              ...outfitRef,
              sound: v.sound,
              // a nota ja foi dada (e ele gerou assim mesmo): o robo nao avalia de novo
              ...(v.rating?.status === "pronto" ? { rating: v.rating.score } : {}),
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
// (no video longo, "do 10 ao 20" escolhe o pedaco do video)
export async function videoChange(id: string, text: string, messageId: number, say: Say): Promise<void> {
  const replyTo = { reply_to_message_id: messageId };
  const v = await load(id);
  if (!v || !v.kind) {
    await say("⏱️ Esse pedido expirou ou já foi feito. Mande o vídeo de novo.", replyTo);
    return;
  }
  const range = v.kind === "longo" ? text.match(CUT_REPLY) : null;
  if (range) {
    const [from, to] = [Number(range[1]), Number(range[2])];
    if (to <= from || from >= (v.duration ?? 0)) {
      await say(`Não entendi o pedaço: o vídeo tem ${Math.round(v.duration ?? 0)} s. Exemplo: "do 10 ao 20".`, replyTo);
      return;
    }
    const next = { ...v, cut: cutOf(v, from, to - from) };
    await save(next);
    await say(summaryText(next), { ...replyTo, reply_markup: await buttonsFor(next) });
    return;
  }
  const next = { ...v, caption: text.trim().slice(0, 2200) };
  await save(next);
  await say(summaryText(next), { ...replyTo, reply_markup: await buttonsFor(next) });
}

// --- nota do video antes do Gerar (03/11) ----------------------------------------------------
// O robo do PC pergunta a cada volta da fila (ref-pending), baixa o video, tira os
// quadros e devolve (ref-rated); a nota chega no bot antes de ele tocar em Gerar.

export async function pendingRating(): Promise<{ id: string; file_url?: string; link?: string } | null> {
  if (!storeConfigured()) return null;
  const v = (await list()).filter((x) => x.rating?.status === "pendente").sort((a, b) => a.at - b.at)[0];
  if (!v) return null;
  if (v.link) return { id: v.id, link: v.link };
  if (!v.tgFile) return { id: v.id };
  const config = await telegramConfig();
  if (!config.token) return null;
  const file = await telegram<{ file_path?: string }>(config.token, "getFile", { file_id: v.tgFile });
  return file.file_path ? { id: v.id, file_url: `https://api.telegram.org/file/bot${config.token}/${file.file_path}` } : { id: v.id };
}

export async function saveRating(id: string, result: RefRating | { error: string }): Promise<void> {
  const v = await load(id);
  if (!v) return;
  const done = "score" in result;
  // pedido antigo (de antes de 05/11, sem "waiting"): ja tinha os botoes
  const showOptions = !!v.waiting && !v.optionsShown;
  const next: VideoOrder = { ...v, rating: done ? { status: "pronto", score: result.score } : { status: "erro", note: result.error.slice(0, 120) }, optionsShown: true };
  await save(next);
  const head = `🔎 Nota do vídeo ${VIDEO_TAG} ${id}`;
  const verdict = done
    ? result.score >= MIN_SCORE
      ? "✅ Deve ficar bom: pode gerar."
      : `⚠️ Abaixo de ${MIN_SCORE}: deve sair com defeito. Melhor mandar outro vídeo; se quiser, gere assim mesmo.`
    : "Você decide se gera mesmo assim.";
  const text = done ? [`${head}: ${result.score}/100`, ratingText(result), "", verdict].join("\n") : `${head}: ${result.error.slice(0, 200)}. ${verdict}`;
  const config = await telegramConfig();
  if (!showOptions || !config.token || !config.chatId) {
    await sendNotice(config, text).catch(() => undefined);
    return;
  }
  // tira o Cancelar do "processando" e responde nele com a nota + as opcoes
  const chat = { chat_id: config.chatId };
  if (v.waitMsg) await telegram(config.token, "editMessageReplyMarkup", { ...chat, message_id: v.waitMsg, reply_markup: { inline_keyboard: [] } }).catch(() => undefined);
  const replyTo = v.waitMsg ? { reply_to_message_id: v.waitMsg, allow_sending_without_reply: true } : {};
  if (next.kind) {
    // link de dancinha: a nota e depois o resumo com os botoes (a nota tambem vai no resumo)
    await telegram(config.token, "sendMessage", { ...chat, ...replyTo, text }).catch(() => undefined);
    await telegram(config.token, "sendMessage", { ...chat, text: summaryText(next), reply_markup: await buttonsFor(next) }).catch(() => undefined);
  } else {
    await telegram(config.token, "sendMessage", { ...chat, ...replyTo, text: `${text}\n\n🎬 O que eu faço?`, reply_markup: kindButtons(next) }).catch(() => undefined);
  }
}

// --- foto da roupa (05/11) ---------------------------------------------------------------------
// "📸 Mandar foto da roupa": a resposta com a foto guarda a roupa no pedido (o robo
// poe na Roupa da dancinha pelo "Do dispositivo" da Naiaflow, sem custo) e manda o
// resumo de novo com a roupa marcada.
export async function videoOutfitPhoto(token: string, id: string, photos: { file_id: string; file_size?: number; width: number; height: number }[], messageId: number, say: Say): Promise<void> {
  const replyTo = { reply_to_message_id: messageId };
  const v = await load(id);
  if (!v || !v.kind) {
    await say("⏱️ Esse pedido expirou ou já foi feito. Mande o vídeo de novo.", replyTo);
    return;
  }
  const photo = [...photos].sort((a, b) => b.width * b.height - a.width * a.height)[0];
  if (!photo) return;
  if ((photo.file_size ?? 0) > 20 * 1024 * 1024) {
    await say("⚠️ Foto grande demais para o bot baixar (mais de 20 MB).", replyTo);
    return;
  }
  try {
    const { fetchPhoto } = await import("./_tg_pedido.js");
    const { ref } = await fetchPhoto(token, photo.file_id);
    // a foto de antes (se ele trocou) nao vai ser usada
    if (v.outfitRef) await storageRemove(UPLOAD_BUCKET, [v.outfitRef]).catch(() => undefined);
    const next: VideoOrder = { ...v, outfitRef: ref, outfitName: undefined, outfitOpen: false };
    await save(next);
    await say(`✅ Roupa da foto escolhida (sem custo a mais).\n\n${summaryText(next)}`, { ...replyTo, reply_markup: await buttonsFor(next) });
  } catch (e) {
    await say(`⚠️ Não consegui guardar a foto da roupa: ${e instanceof Error ? e.message : String(e)}. Mande de novo.`, replyTo);
  }
}
