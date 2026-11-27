// 27/11/2026: cartao para fas de fora do Brasil pelo Tribute (@tribute no Telegram).
// O Tribute cobra (cartao de qualquer pais, Apple Pay, Google Pay, cripto), coloca
// o fa no canal VIP e renova todo mes sozinho; o bot so mostra o link da assinatura.
// O link fica guardado por persona (luna:cartao-link -> cleuza:... via personaKey).
import { storeDelete, storeGet, storeSet } from "./_store.js";

const KEY = "luna:cartao-link";

export type SalesButton = { text: string; callback_data?: string; url?: string };

export async function cardLink(): Promise<string | null> {
  const raw = await storeGet(KEY).catch(() => null);
  return raw && /^https:\/\/t\.me\/[A-Za-z0-9_/?=&.-]+$/.test(raw) ? raw : null;
}

export async function setCardLink(link: string | null): Promise<void> {
  const clean = (link ?? "").trim();
  if (!clean) return storeDelete(KEY);
  if (!/^https:\/\/t\.me\/[A-Za-z0-9_/?=&.-]+$/.test(clean) || clean.length > 200) {
    throw new Error("Cole o link do Tribute no formato https://t.me/tribute/app?startapp=...");
  }
  await storeSet(KEY, clean);
}

// botao do cartao (so para quem e de fora do Brasil)
export async function cardRows(): Promise<SalesButton[][]> {
  const link = await cardLink();
  return link ? [[{ text: "💳 Card / Apple Pay / Google Pay — VIP channel 🔥", url: link }]] : [];
}
