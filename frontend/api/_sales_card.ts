// 27/11/2026: cartao para fas de fora do Brasil pelo Tribute (@tribute no Telegram).
// O Tribute cobra (cartao de qualquer pais, Apple Pay, Google Pay, cripto), coloca
// o fa no canal VIP e renova todo mes sozinho; o bot so mostra o link da assinatura.
// O link fica guardado por persona (luna:cartao-link -> cleuza:... via personaKey).
import { storeConfigured, storeDelete, storeGet, storeIncr, storeSet } from "./_store.js";
import { telegram } from "./_telegram.js";

const KEY = "luna:cartao-link";
const PRICE_KEY = "luna:cartao-preco";
const DEFAULT_PRICE = "$6.99/month";

export type SalesButton = { text: string; callback_data?: string; url?: string };

export async function cardLink(): Promise<string | null> {
  const raw = await storeGet(KEY).catch(() => null);
  return raw && /^https:\/\/t\.me\/[A-Za-z0-9_/?=&.-]+$/.test(raw) ? raw : null;
}

export async function cardPrice(): Promise<string> {
  return ((await storeGet(PRICE_KEY).catch(() => null)) ?? "").trim() || DEFAULT_PRICE;
}

export async function setCardLink(link: string | null, price?: string | null): Promise<void> {
  const clean = (link ?? "").trim();
  if (typeof price === "string") {
    const p = price.trim().slice(0, 24);
    if (p) await storeSet(PRICE_KEY, p);
    else await storeDelete(PRICE_KEY);
  }
  if (!clean) return storeDelete(KEY);
  if (!/^https:\/\/t\.me\/[A-Za-z0-9_/?=&.-]+$/.test(clean) || clean.length > 200) {
    throw new Error("Cole o link do Tribute no formato https://t.me/tribute/app?startapp=...");
  }
  await storeSet(KEY, clean);
}

// botao do cartao (so para quem e de fora do Brasil)
export async function cardRows(): Promise<SalesButton[][]> {
  const link = await cardLink();
  return link ? [[{ text: `💳 Pay with card — VIP ${await cardPrice()} 🔥`, url: link }]] : [];
}

// 27/11: cada passo da compra vai com uma foto da previa e uma chamada curta
// (gira entre as previas; sem previa nao manda nada)
const STEP_TEXT = {
  pt: "{nome}, ótima escolha 😏 Olha só um pedacinho do que te espera… paga aqui embaixo que eu te mando tudinho 🔥",
  en: "{nome}, great choice 😏 Here's a little taste of what's waiting for you… pay below and it's all yours 🔥",
};
export async function sendStepPhoto(token: string, chatId: number, firstName: string | null, foreign: boolean): Promise<void> {
  const { previews } = await import("./_sales_cta.js");
  const list = await previews();
  if (!list.length) return;
  const turn = storeConfigured() ? await storeIncr("luna:cta-passo-giro").catch(() => Date.now()) : Date.now();
  const p = list[Math.abs(turn) % list.length];
  const name = (firstName ?? "").trim().split(/\s+/)[0] || (foreign ? "babe" : "amor");
  const caption = (foreign ? STEP_TEXT.en : STEP_TEXT.pt).split("{nome}").join(name);
  await telegram(token, p.kind === "video" ? "sendVideo" : "sendPhoto", {
    chat_id: chatId,
    [p.kind === "video" ? "video" : "photo"]: p.file_id,
    caption,
    protect_content: true,
  }).catch(() => undefined);
}
