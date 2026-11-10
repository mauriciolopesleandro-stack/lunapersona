import { randomUUID } from "crypto";
import { AiError, askAi, referenceSystem } from "./_assistant.js";
import {
  ADULT_DESTS,
  ADULT_PHOTOS,
  REF_ASK,
  REF_FRESH,
  creditsFor,
  nudityMismatch,
  parsePhotoBlocks,
  readingGist,
  readingNudity,
  refBotHintText,
  splitRefBotNote,
  type PhotoBlock,
} from "./_photo_blocks.js";
import { UPLOAD_BUCKET } from "./_posts.js";
import { storeConfigured, storeDelete, storeGet, storeIncrWithTtl, storeSet, storeSetIfAbsent } from "./_store.js";
import { adultBlockText, checkedVisionPrompt, vipCaption } from "./_ref_writer.js";
import { storageReadUrl, storageUploadUrl } from "./_supabase.js";
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
// marca/desmarca o destino +18, pp:<id>:<3|6|9> fotos do post +18, pg:<id> gerar, pf:<id> gerar mesmo repetida,
// pn:<id> escrever de novo, px:<id> cancelar. Responder o resumo com texto
// muda o pedido.
//
// Album (01/11): 2 ou 3 fotos parecidas mandadas juntas viram um pedido so; a
// 1a e lida pela IA e cada Criar do robo usa uma delas como referencia (IMG1),
// na ordem (6 fotos = 2 Criar, 9 = 3), para parecer o mesmo ensaio.

export const PEDIDO_KINDS = new Set(["pd", "pa", "pp", "pg", "pf", "pn", "px"]);
export const PEDIDO_TAG = "#pedido";
const LIST_KEY = "luna:tg-pedidos";
const KEEP = 10;
const TTL_MS = 24 * 3600_000;
const MAX_PHOTO = 20 * 1024 * 1024; // o bot so baixa arquivo do Telegram ate 20 MB
const MAX_REFS = 3; // uma referencia por Criar (9 fotos = 3 Criar)

type Dest = "feed" | "stories" | "18";
const DEST_LABEL: Record<Dest, string> = { feed: "Feed + TikTok", stories: "Stories + TikTok", "18": "+18 Telegram" };

interface Pedido {
  id: string;
  ref: string; // foto no armazenamento temporario (vai como IMG1 no Criar)
  album?: string; // media_group_id do Telegram: as outras fotos do album
  refs?: string[]; // todas as referencias do album, na ordem (a 1a e `ref`)
  reading: string; // leitura da foto pela IA com visao
  note: string; // o que o administrador escreveu junto da foto
  dest?: Dest;
  adult: string[]; // destinos +18 marcados (canal e pacotes)
  photos?: number; // +18: fotos da mesma cena no post (3, 6 ou 9)
  reply?: string; // a ultima resposta da IA (para mudar sem perder o resto)
  block?: PhotoBlock;
  by?: "visao"; // +18: o prompt saiu da IA que ve a foto
  at: number;
}

type Say = (text: string, extra?: Record<string, unknown>) => Promise<unknown>;

export interface TgPhotoMessage {
  message_id: number;
  media_group_id?: string;
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

// fotos do post: Feed em carrossel de 3; Stories as 3 do Criar; +18 a escolha (3, 6 ou 9)
const photosOf = (p: Pedido) => (p.dest === "18" ? (p.photos ?? ADULT_PHOTOS[0]) : p.dest === "feed" ? 3 : 1);
const refsOf = (p: Pedido) => (p.refs?.length ? p.refs : [p.ref]);

// album: cada foto guardada fica numa chave propria (as fotos chegam juntas,
// em chamadas separadas); a ordem e a do envio
interface AlbumPhoto {
  mid: number;
  ref: string;
  note: string;
}
const albumCount = (album: string) => `luna:tg-album-n:${album}`;
const albumKey = (album: string, n: number) => `luna:tg-album:${album}:${n}`;

async function albumPhotos(album: string): Promise<AlbumPhoto[]> {
  const n = Math.min(10, Number((await storeGet(albumCount(album))) ?? 0));
  const all = await Promise.all(Array.from({ length: n }, (_, k) => storeGet(albumKey(album, k + 1)).catch(() => null)));
  return all
    .flatMap((raw) => {
      try {
        return raw ? [JSON.parse(raw) as AlbumPhoto] : [];
      } catch {
        return [];
      }
    })
    .sort((a, b) => a.mid - b.mid);
}

// com o destino escolhido as fotos do album ja chegaram: junta as referencias
// (e o recado, que o Telegram poe so numa das fotos)
async function withAlbum(p: Pedido): Promise<Pedido> {
  if (!p.album) return p;
  const photos = await albumPhotos(p.album);
  if (!photos.length) return p;
  const refs = photos.map((a) => a.ref).slice(0, MAX_REFS);
  if (!refs.includes(p.ref)) refs.unshift(p.ref);
  return { ...p, refs: refs.slice(0, MAX_REFS), note: p.note || photos.find((a) => a.note)?.note || "" };
}

function refsText(p: Pedido): string | null {
  const refs = refsOf(p).length;
  if (refs < 2) return null;
  const criar = Math.ceil(photosOf(p) / 3);
  const used = Math.min(refs, criar);
  return used < refs
    ? `🖼️ ${refs} fotos de referência: com ${photosOf(p)} fotos só ${used === 1 ? "a 1ª entra" : `as ${used} primeiras entram`} (uma por Criar)`
    : `🖼️ ${refs} fotos de referência: uma em cada Criar`;
}

function destText(p: Pedido): string {
  if (p.dest === "18") return `+18 → ${ADULT_DESTS.filter((d) => p.adult.includes(d.key)).map((d) => d.label).join(" + ") || "nenhum marcado"} · ${photosOf(p)} fotos`;
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
  if (p.dest === "18") {
    rows.push(ADULT_DESTS.map((d) => ({ text: `${p.adult.includes(d.key) ? "✅" : "▫️"} ${d.label}`, callback_data: `pa:${p.id}:${d.key}` })));
    rows.push(ADULT_PHOTOS.map((n) => ({ text: `${photosOf(p) === n ? "✅" : "▫️"} ${n} fotos`, callback_data: `pp:${p.id}:${n}` })));
  }
  const credits = creditsFor(photosOf(p));
  if (p.block) rows.push([{ text: `✅ Gerar (${credits} crédito${credits > 1 ? "s" : ""})`, callback_data: `pg:${p.id}` }]);
  rows.push([
    { text: "🔁 Escrever de novo", callback_data: `pn:${p.id}` },
    { text: "✖ Cancelar", callback_data: `px:${p.id}` },
  ]);
  return { inline_keyboard: rows };
}

function summaryText(p: Pedido): string {
  const b = p.block!;
  const tags = p.dest === "18" ? "" : b.hashtags ? `\n#️⃣ ${b.hashtags}` : "";
  // o que a leitura viu (para ver na hora se errou a leitura ou o prompt)
  const gist = readingGist(p.reading);
  const wrong = p.dest === "18" ? nudityMismatch(p.reading, b.prompt) : null;
  return [
    `📝 Pedido pronto ${PEDIDO_TAG} ${p.id}`,
    "",
    `🎯 ${destText(p)}`,
    "🕒 No próximo horário vazio da agenda",
    ...(refsText(p) ? [refsText(p)!] : []),
    `💳 ${creditsFor(photosOf(p))} crédito${creditsFor(photosOf(p)) > 1 ? "s" : ""}`,
    "",
    ...(gist ? [`👁️ Leitura: ${gist}`] : []),
    ...(p.by === "visao" ? ["✍️ Escrito pela IA que vê a foto (prompt)"] : []),
    ...(wrong ? [`⚠️ A foto está ${wrong.nudity}, mas o prompt ainda fala em "${wrong.word}". Responda dizendo o que tirar, ou toque em Escrever de novo.`] : []),
    ...(p.dest !== "18" && readingNudity(p.reading) ? ["⚠️ A foto de referência tem nudez: para o Instagram a Luna vai vestida (confira o prompt)."] : []),
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
  const note = (msg.caption ?? "").trim().slice(0, 1000);
  // album: so a 1a foto que chega abre o pedido (e e lida); as outras so ficam
  // guardadas como referencia dos proximos Criar
  const album = msg.media_group_id;
  const n = album ? await storeIncrWithTtl(albumCount(album), 24 * 3600) : 1;
  if (album && n > 1) {
    if (n > MAX_REFS) return;
    try {
      const { ref } = await fetchPhoto(token, photo.file_id);
      await storeSetIfAbsent(albumKey(album, n), JSON.stringify({ mid: msg.message_id, ref, note } satisfies AlbumPhoto), 24 * 3600);
    } catch (e) {
      await say(`⚠️ Não consegui guardar esta foto do álbum: ${e instanceof Error ? e.message : String(e)}. O pedido segue com as outras.`, replyTo);
    }
    return;
  }
  await say(album ? "🔎 Recebi as fotos. Estou lendo a primeira (as outras viram referência dos próximos Criar)…" : "🔎 Recebi a foto. Estou lendo a imagem…", replyTo);
  try {
    const { ref, bytes } = await fetchPhoto(token, photo.file_id);
    if (album) await storeSetIfAbsent(albumKey(album, 1), JSON.stringify({ mid: msg.message_id, ref, note } satisfies AlbumPhoto), 24 * 3600);
    const { analyzeImages } = await import("./_assistant_refs.js");
    const reading = await analyzeImages([`data:image/jpeg;base64,${bytes.toString("base64")}`], "photo");
    const p: Pedido = { id: randomUUID().slice(0, 8), ref, ...(album ? { album } : {}), reading, note, adult: ADULT_DESTS.map((d) => d.key), at: Date.now() };
    await save(p);
    await say(`📷 Li a foto${splitRefBotNote(p.note).hint ? " (a legenda do Reference Bot vai só como dica)" : p.note ? " e o seu recado" : ""}. Para onde vai?`, { ...replyTo, reply_markup: destButtons(p.id) });
  } catch (e) {
    if (album) await storeDelete(albumCount(album)).catch(() => undefined);
    await storeDelete(`luna:tg-foto:${msg.message_id}`).catch(() => undefined);
    await say(`⚠️ Não consegui ler a foto: ${e instanceof Error ? e.message : String(e)}. Mande de novo.`, replyTo);
  }
}

// baixa a foto do Telegram e guarda no armazenamento temporario: o robo baixa
// e anexa como IMG1 (o mesmo lugar da foto mandada no estudio)
export async function fetchPhoto(token: string, fileId: string): Promise<{ ref: string; bytes: Buffer }> {
  const file = await telegram<{ file_path?: string }>(token, "getFile", { file_id: fileId });
  if (!file.file_path) throw new Error("o Telegram não entregou a foto");
  const res = await fetch(`https://api.telegram.org/file/bot${token}/${file.file_path}`, { signal: AbortSignal.timeout(20_000) });
  if (!res.ok) throw new Error(`o Telegram não entregou a foto (HTTP ${res.status})`);
  const bytes = Buffer.from(await res.arrayBuffer());
  const ref = `refs/${randomUUID()}.jpg`;
  const form = new FormData();
  form.append("cacheControl", "3600");
  form.append("", new Blob([bytes], { type: "image/jpeg" }));
  const up = await fetch(await storageUploadUrl(UPLOAD_BUCKET, ref), { method: "PUT", headers: { "x-upsert": "true" }, body: form });
  if (!up.ok) throw new Error(`o armazenamento recusou a foto (HTTP ${up.status})`);
  return { ref, bytes };
}

// Escreve (ou reescreve com a mudanca pedida) o bloco PROMPT/LEGENDA com as
// regras do chat do estudio (sala normal ou +18)
// +18 (09/11): o prompt sai da IA que ve a foto; a IA sem censura so escreve a
// legenda. Sem a foto, com erro ou recusa: null (segue o jeito das duas IAs).
async function adultByVision(p: Pedido, change: string | null): Promise<{ text: string; block: PhotoBlock } | null> {
  try {
    const res = await fetch(await storageReadUrl(UPLOAD_BUCKET, p.ref, 300), { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return null;
    const image = `data:image/jpeg;base64,${Buffer.from(await res.arrayBuffer()).toString("base64")}`;
    const started = Date.now();
    // a roupa da leitura vai junto; nudez errada ou peca esquecida -> confere na foto uma vez
    const prompt = await checkedVisionPrompt(image, p.reading, change ? { change, previous: p.block?.prompt } : {});
    const caption = await vipCaption(prompt, change ?? undefined, Math.max(8_000, 50_000 - (Date.now() - started))).catch(() => p.block?.caption ?? "");
    const text = adultBlockText(prompt, caption);
    const block = parsePhotoBlocks(text)[0];
    return block ? { text, block } : null;
  } catch {
    return null;
  }
}

async function write(p: Pedido, change: string | null, say: Say, replyTo: Record<string, unknown>): Promise<void> {
  // um pedido escrevendo por vez (dois toques seguidos nao gastam a IA duas vezes)
  if (!(await storeSetIfAbsent(`luna:tg-pedido-escrevendo:${p.id}`, "1", 60))) return;
  try {
    await say(change ? "✍️ Mudando o pedido…" : "✍️ Escrevendo o pedido…", replyTo);
    const room = p.dest === "18" ? "18" : "chat";
    if (room === "18") {
      // o recado escrito junto da foto vale como pedido de mudanca
      const asked = change ?? (splitRefBotNote(p.note).ask || null);
      const vision = await adultByVision(p, asked);
      if (vision) {
        const next: Pedido = { ...p, reply: vision.text, block: vision.block, by: "visao" };
        await save(next);
        await say(summaryText(next), { ...replyTo, reply_markup: summaryButtons(next) });
        return;
      }
    }
    // legenda do Reference Bot: dica, nao pedido do administrador
    const { ask: typed, hint } = splitRefBotNote(p.note);
    const ask = typed || "Recria essa foto com a Luna.";
    const recreate =
      room === "18"
        ? `${REF_ASK} Diga também a nudez (o que aparece), a expressão e a textura da pele. Termine com o bloco PROMPT/LEGENDA (a historinha safada do canal, sem hashtag)/DESTINO: +18 Telegram.`
        : `${REF_ASK} Termine com o bloco PROMPT/LEGENDA/HASHTAGS/DESTINO: ${p.dest === "stories" ? "Stories" : "Feed"}.`;
    // mudanca sem resposta anterior (a IA tinha falhado): vai junto do primeiro pedido
    const extra = change && !p.reply ? `\n\nMudança pedida: ${change.slice(0, 1500)}` : "";
    const first = `${REF_FRESH}\n\n${ask}\n\n[Referência: foto]\n${p.reading}${refBotHintText(hint)}\n\n${recreate}${extra}`.slice(0, 7900);
    const messages: { role: "system" | "user" | "assistant"; content: string }[] = [
      { role: "system", content: referenceSystem(room) },
      { role: "user", content: first },
    ];
    if (change && p.reply) {
      messages.push({ role: "assistant", content: p.reply }, { role: "user", content: `${change.slice(0, 1500)}\n\nEscreva o bloco PROMPT/LEGENDA${room === "18" ? "" : "/HASHTAGS"}/DESTINO de novo com essa mudança.` });
    }
    const started = Date.now();
    const out = await askAi("adulto", messages, { timeoutMs: 45_000 });
    let text = out.text;
    let block = parsePhotoBlocks(text)[0];
    // trava da nudez (+18, 09/11): a foto esta nua e o prompt vestiu a Luna ->
    // reescreve uma vez (se ele pediu a peca numa mudanca, fica como ele pediu)
    const wrong = room === "18" && block && !change ? nudityMismatch(p.reading, block.prompt) : null;
    const left = 55_000 - (Date.now() - started);
    if (wrong && left > 8_000) {
      const fix = `A foto está ${wrong.nudity === "nua" ? "totalmente nua" : "topless"}: o PROMPT tem "${wrong.word}", que não está na foto. Tire essa peça e escreva o bloco de novo, com a roupa exatamente como na leitura.`;
      const retry = await askAi("adulto", [...messages, { role: "assistant", content: text }, { role: "user", content: fix }], { timeoutMs: left - 3_000 }).catch(() => null);
      const again = retry ? parsePhotoBlocks(retry.text)[0] : undefined;
      if (retry && again) {
        text = retry.text;
        block = again;
      }
    }
    const next: Pedido = { ...p, reply: text.slice(0, 6000), block, by: undefined };
    await save(next);
    if (!block) {
      // a IA respondeu sem o bloco (ex.: perguntou algo): mostra e deixa tentar de novo
      const gist = readingGist(p.reading);
      await say(`🤖 ${text.slice(0, 3500)}${gist ? `\n\n👁️ Leitura: ${gist}` : ""}\n\n${PEDIDO_TAG} ${p.id}\nResponda esta mensagem para continuar, ou toque em Escrever de novo.`, { ...replyTo, reply_markup: summaryButtons(next) });
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
    const withRefs = await withAlbum({ ...p, dest: value });
    // +18 com album: uma foto de referencia por Criar (2 fotos = 6, 3 = 9)
    const refs = refsOf(withRefs).length;
    await write(value === "18" && refs > 1 && !p.photos ? { ...withRefs, photos: ADULT_PHOTOS[Math.min(refs, ADULT_PHOTOS.length) - 1] } : withRefs, null, say, replyTo);
    return;
  }
  if (kind === "pn") {
    if (!p.dest) return;
    await clearButtons();
    await write(p, null, say, replyTo);
    return;
  }
  if (kind === "pa" || kind === "pp") {
    if (p.dest !== "18") return;
    if (kind === "pa" && !ADULT_DESTS.some((d) => d.key === value)) return;
    if (kind === "pp" && !ADULT_PHOTOS.includes(Number(value))) return;
    const next =
      kind === "pp"
        ? { ...p, photos: Number(value) }
        : { ...p, adult: p.adult.includes(value) ? p.adult.filter((k) => k !== value) : [...p.adult, value] };
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
      photos: p.dest === "stories" ? 1 : photosOf(p),
      prompt: b.prompt,
      caption: b.caption,
      hashtags: adult ? [] : b.hashtags.split(/[\s,]+/).filter(Boolean),
      scheduled_at: "auto",
      ref_path: p.ref,
      ...(refsOf(p).length > 1 ? { ref_paths: refsOf(p) } : {}),
      ...(adult ? { adultDest: { channel: p.adult.includes("canal"), packs: p.adult.filter((k) => k !== "canal") } } : {}),
      ...(kind === "pf" ? { force: true } : {}),
    });
    if (out.status === 409) {
      await say(`⚠️ ${String(out.body.detail ?? "Esse pedido já foi feito há pouco.")}\nGerar de novo mesmo assim?`, {
        ...replyTo,
        reply_markup: { inline_keyboard: [[{ text: `Gerar de novo (${creditsFor(photosOf(p))} crédito${creditsFor(photosOf(p)) > 1 ? "s" : ""})`, callback_data: `pf:${id}` }, { text: "✖ Cancelar", callback_data: `px:${id}` }]] },
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
