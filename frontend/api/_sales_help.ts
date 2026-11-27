// Ajuda com pagamento: o fa tentou pagar e nao conseguiu (geralmente de fora do
// Brasil: Pix nao serve, cripto abaixo de US$ 10 da erro de minimo, nao sabe comprar estrelas).
// Manda um guia curto (ingles, portugues e russo) com os botoes do ultimo pacote
// que ele tentou. Usado pelo /paysupport e pelo botao "Ajudar" do estudio.
import { storeSetIfAbsent } from "./_store.js";
import { dbSelect } from "./_supabase.js";
import { telegram } from "./_telegram.js";
import type { FanRow } from "./_sales_bot.js";

interface TriedOrder {
  product_id: string;
  gateway: string | null;
  status: string;
  created_at: string;
}

const DAY_MS = 24 * 3600_000;

export const PAY_HELP_TEXT = [
  "Having trouble paying? Here is the easy way 💛",
  "",
  "💳 Card, UPI, Apple Pay or Google Pay: tap the 💳 button and pay right here in Telegram (it uses Telegram Stars ⭐; if asked, tap “Buy stars” and pay with your card, or with UPI on Android in India).",
  "🪙 Crypto: tap the 🪙 button and choose Litecoin (LTC) or USDT (TRC20) on the page. Send the exact amount shown.",
  "🇧🇷 Pix only works with a Brazilian bank.",
  "",
  "Still not working? Send /paysupport and what the error says.",
  "",
  "— Português: toque no 💳 e pague com cartão, Apple Pay ou Google Pay dentro do Telegram (usa estrelas ⭐), ou no 🪙 para cripto (Litecoin ou USDT TRC20; mande o valor exato da página). Pix só com banco brasileiro.",
  "— Русский: нажмите 💳 и оплатите картой, Apple Pay или Google Pay прямо в Telegram (через Stars ⭐), или 🪙 для криптовалюты (Litecoin или USDT TRC20; отправьте точную сумму со страницы). Pix только для банков Бразилии.",
].join("\n");

// tentativas das ultimas 24 h (pedidos pendentes ou cancelados) e se ja pagou
export async function payAttempts(fan: Pick<FanRow, "id">): Promise<{ tried: TriedOrder[]; paid: boolean }> {
  const since = new Date(Date.now() - DAY_MS).toISOString();
  const rows = await dbSelect<TriedOrder>(
    "sales_orders",
    `select=product_id,gateway,status,created_at&fan_id=eq.${fan.id}&created_at=gt.${since}&order=created_at.desc&limit=30`
  ).catch(() => [] as TriedOrder[]);
  return { tried: rows.filter((o) => o.status === "pendente" || o.status === "cancelado"), paid: rows.some((o) => o.status === "pago" || o.status === "entregue") };
}

export function attemptsSummary(tried: TriedOrder[]): string {
  if (!tried.length) return "";
  const count = (g: string) => tried.filter((o) => (o.gateway ?? "pushinpay") === g).length;
  const parts = [
    [count("pushinpay"), "Pix"],
    [count("estrelas"), "estrelas"],
    [count("nowpayments"), "cripto"],
  ]
    .filter(([n]) => Number(n) > 0)
    .map(([n, label]) => `${label} ${n}x`);
  return `tentou ${tried.length}x nas últimas 24 h (${parts.join(", ")})`;
}

// guia + botoes do ultimo pacote tentado (cartao/estrelas, cripto e Pix por ultimo)
export async function sendPayHelp(token: string, chatId: number, tried: TriedOrder[]): Promise<void> {
  const { productsForSale } = await import("./_sales_bot.js");
  const { abroadRows } = await import("./_sales_pay.js");
  const products = await productsForSale();
  const product = tried.map((o) => products.find((p) => p.id === o.product_id)).find(Boolean);
  const rows: { text: string; callback_data?: string; url?: string }[][] = [];
  if (product) {
    const abroad = await abroadRows(product);
    // estrelas primeiro: e o que funciona com cartao sem sair do Telegram
    // 27/11: o cartao do Tribute (botao com link) vem antes de tudo
    const isStars = (r: (typeof abroad)[number]) => Boolean(r[0]?.callback_data?.startsWith("stars:"));
    const isCard = (r: (typeof abroad)[number]) => Boolean(r[0]?.url);
    rows.push(...abroad.filter(isCard), ...abroad.filter(isStars), ...abroad.filter((r) => !isCard(r) && !isStars(r)));
    rows.push([{ text: "🇧🇷 Pix (Brasil)", callback_data: `pix:${product.id}` }]);
  }
  rows.push([{ text: "📦 Packages / Pacotes", callback_data: "menu:" }]);
  await telegram(token, "sendMessage", {
    chat_id: chatId,
    text: product ? `${PAY_HELP_TEXT}\n\n👇 ${product.title}` : PAY_HELP_TEXT,
    reply_markup: { inline_keyboard: rows },
  });
}

// aviso ao administrador no maximo 1 vez a cada 15 min por fa
export async function shouldNotifyHelp(fan: Pick<FanRow, "id">): Promise<boolean> {
  return storeSetIfAbsent(`luna:ajuda-aviso:${fan.id}`, "1", 900).catch(() => true);
}
