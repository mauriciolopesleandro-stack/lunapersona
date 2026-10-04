// Aviso no celular pelo Telegram. O token do bot fica so aqui no servidor
// do site (Upstash Redis, colado pelo usuario em "Meu perfil"; ou as
// variaveis TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID na Vercel) - nunca vai
// para o navegador nem para o pod, que nao tem login.
import { storeConfigured, storeDelete, storeGet, storeSet } from "./_store.js";

const TOKEN_KEY = "luna:telegram-token";
const CHAT_KEY = "luna:telegram-chat";
const MEDIA_KEY = "luna:telegram-media";
const TOKEN_PATTERN = /^\d{5,15}:[A-Za-z0-9_-]{30,60}$/;

export interface TelegramConfig {
  token: string | null;
  chatId: string | null;
  media: boolean;
}

async function safeGet(key: string): Promise<string | null> {
  if (!storeConfigured()) return null;
  try {
    return await storeGet(key);
  } catch {
    return null;
  }
}

export async function telegramConfig(): Promise<TelegramConfig> {
  const token = (await safeGet(TOKEN_KEY)) ?? process.env.TELEGRAM_BOT_TOKEN ?? null;
  const chatId = (await safeGet(CHAT_KEY)) ?? process.env.TELEGRAM_CHAT_ID ?? null;
  const media = (await safeGet(MEDIA_KEY)) !== "0";
  return { token, chatId, media };
}

export function looksLikeToken(token: string): boolean {
  return TOKEN_PATTERN.test(token);
}

export async function saveToken(token: string): Promise<void> {
  await storeSet(TOKEN_KEY, token);
  await storeDelete(CHAT_KEY); // bot novo: precisa conectar de novo
}

export async function saveChat(chatId: string): Promise<void> {
  await storeSet(CHAT_KEY, chatId);
}

export async function saveMedia(media: boolean): Promise<void> {
  await storeSet(MEDIA_KEY, media ? "1" : "0");
}

export async function forgetTelegram(): Promise<void> {
  await storeDelete(TOKEN_KEY);
  await storeDelete(CHAT_KEY);
}

interface TelegramReply<T> {
  ok: boolean;
  result?: T;
  description?: string;
}

export async function telegram<T>(token: string, method: string, payload: Record<string, unknown> = {}): Promise<T> {
  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(20_000),
  });
  const body = (await res.json().catch(() => ({}))) as TelegramReply<T>;
  if (!body.ok) throw new Error(body.description || `Telegram respondeu ${res.status}`);
  return body.result as T;
}

// Manda o aviso: foto/video por link (o Telegram baixa do pod) com o texto
// de legenda; se o arquivo falhar (pod desligou, grande demais), so o texto.
export async function sendNotice(
  config: TelegramConfig,
  text: string,
  photo?: string | null,
  video?: string | null
): Promise<void> {
  if (!config.token || !config.chatId) throw new Error("Telegram ainda nao conectado.");
  const base = { chat_id: config.chatId };
  if (config.media && photo) {
    try {
      await telegram(config.token, "sendPhoto", { ...base, photo, caption: text });
      return;
    } catch {
      // cai para so o texto
    }
  }
  if (config.media && video) {
    try {
      await telegram(config.token, "sendVideo", { ...base, video, caption: text, supports_streaming: true });
      return;
    } catch {
      // cai para so o texto
    }
  }
  await telegram(config.token, "sendMessage", { ...base, text });
}
