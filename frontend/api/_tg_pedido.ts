import { randomUUID } from "crypto";
import { AiError, askAi, chatSystem } from "./_assistant.js";
import { ADULT_DESTS, REF_ASK, parsePhotoBlocks, type PhotoBlock } from "./_photo_blocks.js";
import { UPLOAD_BUCKET } from "./_posts.js";
import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { storageUploadUrl } from "./_supabase.js";
import { telegram } from "./_telegram.js";

// Pedido pelo Telegram (01/11): o administrador manda uma foto de referencia no
// bot de aprovacao, do celular. O bot le a foto (IA com visao) e pergunta para
// onde vai; com o destino, escreve o pedido com as mesmas regras do chat do
// estudio e mostra o resumo com o custo. "Gerar" poe na fila do robo do PC
// (queueQuick, o mesmo da foto avulsa do estudio) e a foto volta aqui para
// aprovar, como sempre. Cada passo (ler a foto, escrever o pedido) e uma
// chamada da IA so, para caber nos 60 s da Vercel.
//
// Botoes: pd:<id>:<feed|stories|18> destino, pa:<id>:<canal|ensaio|album-vip>
// marca/desmarca o destino +18, pg:<id> gerar, pf:<id> gerar mesmo repetida,
// pn:<id> escrever de novo, px:<id> cancelar. Responder o resumo com texto
// muda o pedido.

export const PEDIDO_KINDS = new Set(["pd", "pa", "pg", "pf", "pn", "px"]);
export const PEDIDO_TAG = "#pedido";
const LIST_KEY = "luna:tg-pedidos";
const KEEP = 10;
const TTL_MS = 24 * 3600_000;
const MAX_PHOTO = 20 * 1024 * 1024; // o bot so baixa arquivo do Telegram ate 20 MB

type Dest = "feed" | "stories" | "18";
const DEST_LABEL: Record<Dest, string> = { feed: "Feed + TikTok", stories: "Stories + TikTok", "18": "+18 Telegram" };

interface Pedido {
  id: string;
  ref: string; // foto no armazenamento temporario (vai como IMG1 no Criar)
  reading: string; // leitura da foto pela IA com visao
  note: string; // o que o administrador escreveu junto da foto
  dest?: Dest;
  adult: string[]; // destinos +18 marcados (canal e pacotes)
  reply?: string; // a ultima resposta da IA (para mudar sem perder o resto)
  block?: PhotoBlock;
  at: number;
}

type Say = (text: string, extra?: Record<string, unknown>) => Promise<unknown>;

export interface TgPhotoMessage {
  message_id: number;
  caption?: string;
  photo?: { file_id: string; file_size?: number; width: number; height: number }[];
}

async function list(): Promise<Pedido[]> {
  try {
    const all = JSON.parse((await storeGet(LIST_KEY)) ?? "[]") as Pedido[];
    return all.filter((p) => Date.now() - p.at < TTL_MS);
  } catch {
    return [];
  }
}

async function load(id: string): Promise<Pedido | null> {
  return (await list()).find((p) => p.id === id) ?? null;
}

async function save(p: Pedido): Promise<void> {
  const rest = (await list()).filter((x) => x.id !== p.id);
  await storeSet(LIST_KEY, JSON.stringify([...rest, { ...p, at: Date.now() }].slice(-KEEP)));
}

async function drop(id: string): Promise<void> {
  await storeSet(LIST_KEY, JSON.stringify((await list()).filter((x) => x.id !== id)));
}

function destText(p: Pedido): string {
  if (p.dest === "18") return `+18 → ${ADULT_DESTS.filter((d) => p.adult.includes(d.key)).map((d) => d.label).join(" + ") || "nenhum marcado"}`;
  if (p.dest === "feed") return "Feed (carrossel de 3 fotos) + TikTok";
  return "Stories (as 3 fotos para você escolher) + TikTok";
}

function destButtons(id: string) {
  return {
    inline_keyboard: [
      [
        { text: "📷 Feed + TikTok", callback_data: `pd:${id}:feed` },
        { text: "📱 Stories + TikTok", callback_data: `pd:${id}:stories` },
      ],
      [{ text: "🔥 +18 (canal VIP e pacotes)", callback_data: `pd:${id}:18` }],
      [{ text: "✖ Cancelar", callback_data: `px:${id}` }],
    ],
  };
}

function summaryButtons(p: Pedido) {
  const rows: { text: string; callback_data: string }[][] = [];
  if (p.dest === "18") rows.push(ADULT_DESTS.map((d) => ({ text: `${p.adult.includes(d.key) ? "✅" : "▫️"} ${d.label}`, callback_data: `pa:${p.id}:${d.key}` })));
  if (p.block) rows.push([{ text: "✅ Gerar (1 crédito)", callback_data: `pg:${p.id}` }]);
  rows.push([
    { text: "🔁 Escrever de novo", callback_data: `pn:${p.id}` },
    { text: "✖ Cancelar", callback_data: `px:${p.id}` },
  ]);
  return { inline_keyboard: rows };
}

function summaryText(p: Pedido): string {
  const b = p.block!;
  const tags = p.dest === "18" ? "" : b.hashtags ? `\n#️⃣ ${b.hashtags}` : "";
  return [
    `📝 Pedido pronto ${PEDIDO_TAG} ${p.id}`,
    "",
    `🎯 ${destText(p)}`,
    "🕒 No próximo horário vazio da agenda",
    "💳 1 crédito",
    "",
    `📸 ${b.prompt.slice(0, 1800)}`,
    "",
    `💬 ${b.caption.slice(0, 1200) || "(sem legenda)"}${tags}`,
    "",
    "✏️ Para mudar algo, responda esta mensagem dizendo o que mudar.",
  ].join("\n");
}

// "Feed: sab 01/11, 15:30" de cada publicacao criada
function placed(items: { format: string; scheduled_at: string }[]): string {
  return items
    .map(
      (i) =>
        `${i.format === "Foto" ? "TikTok" : i.format}: ${new Date(i.scheduled_at).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo", weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}`
    )
    .join(" · ");
}

// Foto mandada no chat do bot: guarda (IMG1), le e pergunta o destino.
export async function pedidoPhoto(token: string, msg: TgPhotoMessage, say: Say): Promise<void> {
  const replyTo = { reply_to_message_id: msg.message_id };
  if (!storeConfigured()) {
    await say("⚠️ Falta o Upstash Redis na Vercel para guardar o pedido.", replyTo);
    return;
  }
  // o Telegram repete o aviso se a resposta demorar: a mesma foto e lida uma vez so
  if (!(await storeSetIfAbsent(`luna:tg-foto:${msg.message_id}`, "1", 3600))) return;
  const photo = [...(msg.photo ?? [])].sort((a, b) => b.width * b.height - a.width * a.height)[0];
  if (!photo) return;
  if ((photo.file_size ?? 0) > MAX_PHOTO) {
    await say("⚠️ Foto grande demais para o bot baixar (mais de 20 MB).", replyTo);
    return;
  }
  await say("🔎 Recebi a foto. Estou lendo a imagem…", replyTo);
  try {
    const file = await telegram<{ file_path?: string }>(token, "getFile", { file_id: photo.file_id });
    if (!file.file_path) throw new Error("o Telegram não entregou a foto");
    const res = await fetch(`https://api.telegram.org/file/bot${token}/${file.file_path}`, { signal: AbortSignal.timeout(20_000) });
    if (!res.ok) throw new Error(`o Telegram não entregou a foto (HTTP ${res.status})`);
    const bytes = Buffer.from(await res.arrayBuffer());
    // referencia: o robo baixa e anexa como IMG1 (o mesmo lugar da foto mandada no estudio)
    const ref = `refs/${randomUUID()}.jpg`;
    const form = new FormData();
    form.append("cacheControl", "3600");
    form.append("", new Blob([bytes], { type: "image/jpeg" }));
    const up = await fetch(await storageUploadUrl(UPLOAD_BUCKET, ref), { method: "PUT", headers: { "x-upsert": "true" }, body: form });
    if (!up.ok) throw new Error(`o armazenamento recusou a foto (HTTP ${up.status})`);
    const { analyzeImages } = await import("./_assistant_refs.js");
    const reading = await analyzeImages([`data:image/jpeg;base64,${bytes.toString("base64")}`], "photo");
    const p: Pedido = { id: randomUUID().slice(0, 8), ref, reading, note: (msg.caption ?? "").trim().slice(0, 1000), adult: ADULT_DESTS.map((d) => d.key), at: Date.now() };
    await save(p);
    await say(`📷 Li a foto${p.note ? " e o seu recado" : ""}. Para onde vai?`, { ...replyTo, reply_markup: destButtons(p.id) });
  } catch (e) {
    await storeDelete(`luna:tg-foto:${msg.message_id}`).catch(() => undefined);
    await say(`⚠️ Não consegui ler a foto: ${e instanceof Error ? e.message : String(e)}. Mande de novo.`, replyTo);
  }
}

// Escreve (ou reescreve com a mudanca pedida) o bloco PROMPT/LEGENDA com as
// regras do chat do estudio (sala normal ou +18)
async function write(p: Pedido, change: string | null, say: Say, replyTo: Record<string, unknown>): Promise<void> {
  // um pedido escrevendo por vez (dois toques seguidos nao gastam a IA duas vezes)
  if (!(await storeSetIfAbsent(`luna:tg-pedido-escrevendo:${p.id}`, "1", 60))) return;
  try {
    await say(change ? "✍️ Mudando o pedido…" : "✍️ Escrevendo o pedido…", replyTo);
    const room = p.dest === "18" ? "18" : "chat";
    const ask = p.note || "Recria essa foto com a Luna.";
    const recreate =
      room === "18"
        ? `${REF_ASK} Diga também a nudez (o que aparece), a expressão e a textura da pele. Termine com o bloco PROMPT/LEGENDA (a historinha safada do canal, sem hashtag)/DESTINO: +18 Telegram.`
        : `${REF_ASK} Termine com o bloco PROMPT/LEGENDA/HASHTAGS/DESTINO: ${p.dest === "stories" ? "Stories" : "Feed"}.`;
    // mudanca sem resposta anterior (a IA tinha falhado): vai junto do primeiro pedido
    const extra = change && !p.reply ? `\n\nMudança pedida: ${change.slice(0, 1500)}` : "";
    const first = `${ask}\n\n[Referência: foto]\n${p.reading}\n\n${recreate}${extra}`.slice(0, 7900);
    const messages: { role: "system" | "user" | "assistant"; content: string }[] = [
      { role: "system", content: await chatSystem(room) },
      { role: "user", content: first },
    ];
    if (change && p.reply) {
      messages.push({ role: "assistant", content: p.reply }, { role: "user", content: `${change.slice(0, 1500)}\n\nEscreva o bloco PROMPT/LEGENDA${room === "18" ? "" : "/HASHTAGS"}/DESTINO de novo com essa mudança.` });
    }
    const out = await askAi("adulto", messages, { timeoutMs: 45_000 });
    const block = parsePhotoBlocks(out.text)[0];
    const next: Pedido = { ...p, reply: out.text.slice(0, 6000), block };
    await save(next);
    if (!block) {
      // a IA respondeu sem o bloco (ex.: perguntou algo): mostra e deixa tentar de novo
      await say(`🤖 ${out.text.slice(0, 3500)}\n\n${PEDIDO_TAG} ${p.id}\nResponda esta mensagem para continuar, ou toque em Escrever de novo.`, { ...replyTo, reply_markup: summaryButtons(next) });
      return;
    }
    await say(summaryText(next), { ...replyTo, reply_markup: summaryButtons(next) });
  } catch (e) {
    await say(`⚠️ ${e instanceof AiError ? e.message : e instanceof Error ? e.message : String(e)}`, { ...replyTo, reply_markup: summaryButtons(p) });
  } finally {
    await storeDelete(`luna:tg-pedido-escrevendo:${p.id}`).catch(() => undefined);
  }
}

// Toque num botao do pedido
export async function pedidoCallback(token: string, chatId: string, kind: string, id: string, value: string, messageId: number | undefined, say: Say): Promise<void> {
  const replyTo = messageId ? { reply_to_message_id: messageId } : {};
  const clearButtons = () =>
    messageId ? telegram(token, "editMessageReplyMarkup", { chat_id: chatId, message_id: messageId, reply_markup: { inline_keyboard: [] } }).catch(() => undefined) : Promise.resolve();
  const p = await load(id);
  if (!p) {
    await clearButtons();
    await say("⏱️ Esse pedido expirou ou já foi feito. Mande a foto de novo.", replyTo);
    return;
  }
  if (kind === "px") {
    await drop(id);
    await clearButtons();
    await say("✖ Pedido cancelado.", replyTo);
    return;
  }
  if (kind === "pd") {
    if (value !== "feed" && value !== "stories" && value !== "18") return;
    await clearButtons();
    await write({ ...p, dest: value }, null, say, replyTo);
    return;
  }
  if (kind === "pn") {
    if (!p.dest) return;
    await clearButtons();
    await write(p, null, say, replyTo);
    return;
  }
  if (kind === "pa") {
    if (p.dest !== "18" || !ADULT_DESTS.some((d) => d.key === value)) return;
    const next = { ...p, adult: p.adult.includes(value) ? p.adult.filter((k) => k !== value) : [...p.adult, value] };
    await save(next);
    if (messageId && next.block) {
      await telegram(token, "editMessageText", { chat_id: chatId, message_id: messageId, text: summaryText(next), reply_markup: summaryButtons(next) }).catch(() => undefined);
    }
    return;
  }
  if (kind === "pg" || kind === "pf") {
    const b = p.block;
    if (!b || !p.dest) return;
    if (p.dest === "18" && !p.adult.length) {
      await say("Marque pelo menos um destino do +18 (canal ou pacote).", replyTo);
      return;
    }
    const { queueQuick } = await import("./_plans.js");
    const adult = p.dest === "18";
    const out = await queueQuick({
      slots: p.dest === "feed" ? ["feed", "tiktok"] : p.dest === "stories" ? ["stories", "tiktok"] : ["telegram18"],
      photos: p.dest === "feed" ? 3 : 1,
      prompt: b.prompt,
      caption: b.caption,
      hashtags: adult ? [] : b.hashtags.split(/[\s,]+/).filter(Boolean),
      scheduled_at: "auto",
      ref_path: p.ref,
      ...(adult ? { adultDest: { channel: p.adult.includes("canal"), packs: p.adult.filter((k) => k !== "canal") } } : {}),
      ...(kind === "pf" ? { force: true } : {}),
    });
    if (out.status === 409) {
      await say(`⚠️ ${String(out.body.detail ?? "Esse pedido já foi feito há pouco.")}\nGerar de novo mesmo assim?`, {
        ...replyTo,
        reply_markup: { inline_keyboard: [[{ text: "Gerar de novo (1 crédito)", callback_data: `pf:${id}` }, { text: "✖ Cancelar", callback_data: `px:${id}` }]] },
      });
      return;
    }
    await drop(id);
    await clearButtons();
    const items = (out.body.items ?? []) as { format: string; scheduled_at: string }[];
    await say(`✅ Na fila do robô (${DEST_LABEL[p.dest]}) · ${placed(items)}.\nA foto chega aqui para você aprovar — o robô do PC precisa estar aberto.`, replyTo);
  }
}

// Resposta (texto) ao resumo do pedido: muda o pedido com o que foi pedido
export async function pedidoChange(id: string, text: string, messageId: number, say: Say): Promise<void> {
  const replyTo = { reply_to_message_id: messageId };
  const p = await load(id);
  if (!p || !p.dest) {
    await say("⏱️ Esse pedido expirou ou já foi feito. Mande a foto de novo.", replyTo);
    return;
  }
  await write(p, text, say, replyTo);
}
