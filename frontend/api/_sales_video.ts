// Video da chamada (24/11/2026, pedido do administrador): um video escolhido por
// ele (link de uma mensagem do Telegram, ex.: t.me/c/<canal>/<n>) passa a ir
// na boas-vindas de todo fa novo, com a frase da chamada e os botoes dos
// pacotes; e uma vez para todos que ja entraram (+18, sem bloqueio), aos poucos,
// pelo processador da fila. Cada fa recebe o envio "para todos" uma vez so.
//  - o bot de vendas puxa o video encaminhando a mensagem para o Telegram do
//    administrador (o bot precisa estar no canal/grupo de origem); guarda o
//    file_id (luna:video-chamada);
//  - envio para todos: luna:video-todos {startedAt, textPt, textEn, doneAt?};
//    quem ja recebeu: luna:video-todos:<fa> (60 dias); 25 por rodada.
import { dbSelect, dbUpdateWhere } from "./_supabase.js";
import { storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { telegram, telegramConfig, sendNotice } from "./_telegram.js";
import type { FanRow } from "./_sales_bot.js";

const VIDEO_KEY = "luna:video-chamada";
const BCAST_KEY = "luna:video-todos";
const TICK_KEY = "luna:video-todos-tick";
const SENT = (fanId: string) => `luna:video-todos:${fanId}`;
const SENT_TTL = 60 * 86400;
const PER_RUN = 25;

export interface CallVideo {
  file_id: string;
  kind: "video" | "photo" | "animation";
  link: string;
  at: string;
}

export interface Broadcast {
  startedAt: string;
  textPt: string;
  textEn: string;
  doneAt?: string;
  sent?: number;
  blocked?: number;
  failed?: number;
}

export const DEFAULT_BROADCAST = {
  pt: "Oxe, {nome}! Olha o que a coroa preparou pra tu 😏🔥\n\nIsso aqui é só um gostinho… o resto eu mostro no meu VIP. Escolhe o teu pacote aqui embaixo 👇",
  en: "Hey {nome}! Look what I made just for you 😏🔥\n\nThis is just a little taste… the rest is waiting in my VIP. Pick your pack below 👇",
};

export async function callVideo(): Promise<CallVideo | null> {
  try {
    const raw = await storeGet(VIDEO_KEY);
    const v = raw ? (JSON.parse(raw) as CallVideo) : null;
    return v && typeof v.file_id === "string" ? v : null;
  } catch {
    return null;
  }
}

export async function clearCallVideo(): Promise<void> {
  await storeDelete(VIDEO_KEY);
}

// "t.me/c/4359039759/12" (canal/grupo privado) ou "t.me/canal/12" (publico)
export function parseMessageLink(link: string): { chat: number | string; message: number } | null {
  const priv = /t\.me\/c\/(\d+)\/(?:\d+\/)?(\d+)/i.exec(link);
  if (priv) return { chat: Number(`-100${priv[1]}`), message: Number(priv[2]) };
  const pub = /t\.me\/([A-Za-z][A-Za-z0-9_]{3,})\/(\d+)/i.exec(link);
  if (pub) return { chat: `@${pub[1]}`, message: Number(pub[2]) };
  return null;
}

interface TgMessage {
  message_id: number;
  video?: { file_id: string };
  animation?: { file_id: string };
  document?: { file_id: string; mime_type?: string };
  photo?: { file_id: string; width: number }[];
}

// Encaminha a mensagem do link para o Telegram do administrador (pelo bot de
// vendas) e guarda o arquivo. O bot precisa estar no canal/grupo de origem.
export async function importCallVideo(token: string, link: string, adminChat: number): Promise<CallVideo> {
  const ref = parseMessageLink(link.trim());
  if (!ref) throw new Error("Link inválido. Use o link da mensagem do Telegram (ex.: https://t.me/c/1234567890/12).");
  let msg: TgMessage;
  try {
    // video grande: o Telegram pode demorar para encaminhar (24/11: passou de 20 s)
    msg = await telegram<TgMessage>(token, "forwardMessage", { chat_id: adminChat, from_chat_id: ref.chat, message_id: ref.message }, 50_000);
  } catch (e) {
    const why = e instanceof Error ? e.message : String(e);
    throw new Error(
      /chat not found|not enough rights|message to forward not found|CHAT_ADMIN_REQUIRED/i.test(why)
        ? `O bot de vendas não conseguiu ler essa mensagem (${why}). Ele precisa estar no canal/grupo do link (como administrador). Ou mande o vídeo direto para o bot e use o link do canal VIP.`
        : `O Telegram recusou: ${why}`
    );
  }
  let file: CallVideo | null = null;
  const at = new Date().toISOString();
  if (msg.video) file = { file_id: msg.video.file_id, kind: "video", link, at };
  else if (msg.animation) file = { file_id: msg.animation.file_id, kind: "animation", link, at };
  else if (msg.document?.mime_type?.startsWith("video/")) file = { file_id: msg.document.file_id, kind: "video", link, at };
  else if (msg.photo?.length) file = { file_id: [...msg.photo].sort((a, b) => b.width - a.width)[0].file_id, kind: "photo", link, at };
  if (!file) throw new Error("Essa mensagem não tem vídeo nem foto.");
  await storeSet(VIDEO_KEY, JSON.stringify(file));
  return file;
}

// 24/11: o administrador manda o video direto para o bot de vendas (sem link).
const PENDING_KEY = "luna:video-chamada-pendente";
export function fileFromMessage(msg: TgMessage, link: string): CallVideo | null {
  const at = new Date().toISOString();
  if (msg.video) return { file_id: msg.video.file_id, kind: "video", link, at };
  if (msg.animation) return { file_id: msg.animation.file_id, kind: "animation", link, at };
  if (msg.document?.mime_type?.startsWith("video/")) return { file_id: msg.document.file_id, kind: "video", link, at };
  return null;
}
export async function holdPendingVideo(file: CallVideo): Promise<void> {
  await storeSet(PENDING_KEY, JSON.stringify(file));
}
// "ok": o video pendente vira o video da chamada
export async function confirmPendingVideo(): Promise<CallVideo | null> {
  const raw = await storeGet(PENDING_KEY).catch(() => null);
  if (!raw) return null;
  await storeSet(VIDEO_KEY, raw);
  await storeDelete(PENDING_KEY).catch(() => undefined);
  return JSON.parse(raw) as CallVideo;
}

// Manda o video com o texto na legenda e os botoes dados (protegido: nao da para salvar/encaminhar).
export async function sendWithCallVideo(
  token: string,
  chatId: number,
  video: CallVideo,
  text: string,
  rows: { text: string; callback_data: string }[][]
): Promise<void> {
  const method = video.kind === "photo" ? "sendPhoto" : video.kind === "animation" ? "sendAnimation" : "sendVideo";
  await telegram(token, method, {
    chat_id: chatId,
    [video.kind === "photo" ? "photo" : video.kind === "animation" ? "animation" : "video"]: video.file_id,
    caption: text.slice(0, 1024),
    protect_content: true,
    ...(rows.length ? { reply_markup: { inline_keyboard: rows } } : {}),
  });
}

export async function broadcast(): Promise<Broadcast | null> {
  try {
    const raw = await storeGet(BCAST_KEY);
    return raw ? (JSON.parse(raw) as Broadcast) : null;
  } catch {
    return null;
  }
}

// Quantos ja receberam e quantos faltam (so para mostrar).
export async function broadcastStatus(): Promise<(Broadcast & { pending: number }) | null> {
  const b = await broadcast();
  if (!b) return null;
  const fans = await audience();
  let pending = 0;
  if (!b.doneAt) for (const f of fans) if (!(await storeGet(SENT(f.id)).catch(() => "?"))) pending++;
  return { ...b, pending };
}

export async function startBroadcast(textPt: string, textEn: string): Promise<Broadcast> {
  if (!(await callVideo())) throw new Error("Escolha o vídeo da chamada antes.");
  const b: Broadcast = { startedAt: new Date().toISOString(), textPt: textPt.trim().slice(0, 900), textEn: textEn.trim().slice(0, 900), sent: 0, blocked: 0, failed: 0 };
  if (!b.textPt || !b.textEn) throw new Error("Falta o texto em português ou em inglês.");
  await storeSet(BCAST_KEY, JSON.stringify(b));
  return b;
}

export async function stopBroadcast(): Promise<void> {
  const b = await broadcast();
  if (b && !b.doneAt) await storeSet(BCAST_KEY, JSON.stringify({ ...b, doneAt: new Date().toISOString() }));
}

// +18, sem bloqueio, sem ter pedido para sair
function audience(): Promise<FanRow[]> {
  return dbSelect<FanRow>("fans", "select=*&adult_confirmed_at=not.is.null&opted_out_at=is.null&blocked_at=is.null&order=started_at.asc&limit=1000");
}

const isBlockedError = (msg: string) => /blocked by the user|user is deactivated|chat not found|bot can't initiate/i.test(msg);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Uma rodada do envio para todos (processador da fila, a cada minuto).
export async function videoBroadcastTick(token: string): Promise<number> {
  const b = await broadcast();
  if (!b || b.doneAt) return 0;
  const video = await callVideo();
  if (!video) return 0;
  if (!(await storeSetIfAbsent(TICK_KEY, "1", 55).catch(() => false))) return 0;
  const { fanLang } = await import("./_sales_lang.js");
  const { fill, buyButtonsFor, promoLine } = await import("./_sales_cta.js");
  let sent = 0;
  let blocked = 0;
  let failed = 0;
  let remaining = 0;
  for (const fan of await audience()) {
    if (await storeGet(SENT(fan.id)).catch(() => "?")) continue;
    if (sent + blocked + failed >= PER_RUN) {
      remaining++;
      continue;
    }
    if (!(await storeSetIfAbsent(SENT(fan.id), "1", SENT_TTL).catch(() => false))) continue;
    const lang = await fanLang(fan);
    const base = lang === "en" ? b.textEn : lang === "pt" ? b.textPt : `${b.textPt}\n\n🇺🇸 ${b.textEn}`;
    const text = await promoLine(fill(base, fan, {}, lang === "en" ? "en" : "pt"), lang);
    try {
      await sendWithCallVideo(token, fan.telegram_id, video, text, await buyButtonsFor(lang));
      sent++;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (isBlockedError(msg)) {
        blocked++;
        await dbUpdateWhere("fans", `id=eq.${fan.id}`, { blocked_at: new Date().toISOString() }).catch(() => undefined);
      } else if (/Too Many Requests/i.test(msg)) {
        await storeDelete(SENT(fan.id)).catch(() => undefined);
        remaining++;
        break;
      } else {
        failed++;
      }
    }
    await sleep(350);
  }
  const next: Broadcast = { ...b, sent: (b.sent ?? 0) + sent, blocked: (b.blocked ?? 0) + blocked, failed: (b.failed ?? 0) + failed };
  if (!remaining) next.doneAt = new Date().toISOString();
  await storeSet(BCAST_KEY, JSON.stringify(next));
  if (next.doneAt) {
    await sendNotice(
      await telegramConfig(),
      `🎬 Vídeo da chamada enviado para todos: ${next.sent} receberam${next.blocked ? `, ${next.blocked} tinham bloqueado o bot` : ""}${next.failed ? `, ${next.failed} falharam` : ""}.`
    ).catch(() => undefined);
  }
  return sent;
}
