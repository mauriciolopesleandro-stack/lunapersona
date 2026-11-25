import { storeConfigured, storeGet, storeSet } from "./_store.js";
import { audit, dbInsert, dbInsertIgnoringDuplicates, dbSelect, dbUpdate } from "./_supabase.js";
import { sendNotice, telegram, telegramConfig } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";

// Estrelas do Telegram (pedido de 21/11/2026): quem esta fora do Brasil nao tem
// Pix, entao o bot de vendas cobra tambem em estrelas (moeda XTR do proprio
// Telegram; o fa compra as estrelas no app com cartao, Apple Pay ou Google Pay).
// Nao precisa de conta nem de token de pagamento: o bot que ja existe cobra.
//
// Fluxo:
//  1. fa com o Telegram em outra lingua (language_code fora de "pt") toca no
//     pacote -> pedido "pendente" (gateway "estrelas", amount_cents = estrelas)
//     + fatura (sendInvoice, payload "o:<pedido>") e o botao "Pix (Brasil)";
//     fa brasileiro recebe o Pix como sempre, com o botao "Pagar com estrelas";
//  2. o Telegram pergunta antes de cobrar (pre_checkout_query): so confirma se o
//     pedido existe e o valor bate (resposta em ate 10 s);
//  3. cobrou (message.successful_payment): marca pago com o
//     telegram_payment_charge_id (chave unica: o mesmo pagamento nunca entrega
//     duas vezes) e entrega igual ao Pix (deliver em _sales_pay.ts).
// O saldo fica no bot (@BotFather -> o bot -> Balance) e sai pelo Fragment.
// Os precos ficam no Upstash (luna:stars), por persona, como a promocao.

export interface StarsConfig {
  enabled: boolean;
  prices: Record<string, number>; // id do pacote -> preco em estrelas
}

const KEY = "luna:stars";
export const STARS_GATEWAY = "estrelas";
export const DEFAULT_STARS: StarsConfig = { enabled: false, prices: {} };
// preco sugerido quando o pacote nao tem um preco proprio em estrelas
const SUGGESTED: Record<ProductRow["kind"], number> = { ensaio: 250, album: 500, vip: 1000 };
export const STARS_MIN = 1;
export const STARS_MAX = 10000;

export async function starsConfig(): Promise<StarsConfig> {
  if (!storeConfigured()) return DEFAULT_STARS;
  try {
    const saved = JSON.parse((await storeGet(KEY)) ?? "null");
    if (!saved || typeof saved !== "object") return DEFAULT_STARS;
    return { enabled: Boolean(saved.enabled), prices: saved.prices && typeof saved.prices === "object" ? saved.prices : {} };
  } catch {
    return DEFAULT_STARS;
  }
}

export async function saveStars(config: StarsConfig): Promise<void> {
  await storeSet(KEY, JSON.stringify(config));
}

export function suggestedStars(product: Pick<ProductRow, "kind">): number {
  return SUGGESTED[product.kind] ?? 250;
}

// preco em estrelas do pacote, ou null quando nao vende em estrelas
// (desligado, ou VIP mensal: a assinatura e so pelo Pix Recorrente)
// promo_stars: preco da promocao do dia (withPromo em _sales_promo.ts), so se for menor
export function starsPrice(product: Pick<ProductRow, "id" | "kind" | "billing"> & { promo_stars?: number }, config: StarsConfig): number | null {
  if (!config.enabled || product.billing === "mensal") return null;
  const own = Number(config.prices[product.id]);
  const stars = Math.min(STARS_MAX, Number.isInteger(own) && own >= STARS_MIN ? own : suggestedStars(product));
  const promo = Number(product.promo_stars);
  return Number.isInteger(promo) && promo >= STARS_MIN && promo < stars ? promo : stars;
}

// o Telegram do fa esta em outra lingua (sem Pix): a fatura em estrelas vem primeiro
export function isForeign(lang?: string | null): boolean {
  return Boolean(lang) && !/^pt\b/i.test(String(lang));
}

function fanName(f: Pick<FanRow, "username" | "first_name" | "telegram_id">): string {
  return f.username ? `@${f.username}` : f.first_name || `fã ${f.telegram_id}`;
}

async function adminNotice(text: string): Promise<void> {
  await sendNotice(await telegramConfig(), text).catch(() => undefined);
}

interface StarsOrder {
  id: string;
  fan_id: string;
  product_id: string;
  amount_cents: number;
  status: string;
  gateway: string | null;
}

// Fatura em estrelas do pacote. Devolve false quando o pacote nao vende em
// estrelas ou o Telegram recusou a fatura (quem chamou segue com o Pix).
// otherRows: botoes de outras formas de pagar abaixo do "Pay" (padrao: Pix para o fa de fora)
export async function sendStarsInvoice(salesToken: string, fan: FanRow, product: ProductRow, chatId: number, foreign: boolean, otherRows?: object[][]): Promise<boolean> {
  const stars = starsPrice(product, await starsConfig());
  if (!stars) return false;
  const order = await dbInsert<StarsOrder>("sales_orders", {
    fan_id: fan.id,
    product_id: product.id,
    amount_cents: stars, // em estrelas (gateway "estrelas"), nao em centavos
    status: "pendente",
    gateway: STARS_GATEWAY,
    note: `${stars} estrelas`,
  });
  if (!order) return false;
  const days = product.kind === "vip" ? `${product.period_days} days in the VIP channel` : `${product.period_days} days of access`;
  try {
    await telegram(salesToken, "sendInvoice", {
      chat_id: chatId,
      title: product.title.slice(0, 32),
      description: (foreign
        ? `${product.title} — ${days}. Pay with Telegram Stars ⭐ (card, UPI, Apple Pay or Google Pay right here in Telegram). Delivered instantly in this chat.`
        : `${product.title} — pague com estrelas do Telegram ⭐ (cartão, Apple Pay ou Google Pay aqui mesmo). Chega na hora aqui no chat.`
      ).slice(0, 255),
      payload: `o:${order.id}`,
      currency: "XTR",
      prices: [{ label: product.title.slice(0, 32), amount: stars }],
      reply_markup: {
        inline_keyboard: [
          [{ text: foreign ? `💳 Pay ⭐ ${stars}` : `💳 Pagar ⭐ ${stars}`, pay: true }],
          ...(otherRows ?? (foreign ? [[{ text: "🇧🇷 Pix (Brasil)", callback_data: `pix:${product.id}` }]] : [])),
        ],
      },
    });
    // 25/11: quem e de fora trava na 1a compra de estrelas; um passo a passo curto
    // (India: Google Play com UPI), uma vez por dia por fa
    if (foreign) await sendStarsGuide(salesToken, fan, chatId, stars).catch(() => undefined);
    return true;
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { status: "cancelado", note: `Estrelas: ${error}`.slice(0, 500) });
    await adminNotice(`⚠️ Não consegui mandar a fatura em estrelas de “${product.title}” para ${fanName(fan)}: ${error.slice(0, 200)}`);
    return false;
  }
}

export function starsGuideText(stars: number): string {
  return [
    "How to pay (1 minute) 👇",
    `1️⃣ Tap “💳 Pay ⭐ ${stars}” above.`,
    "2️⃣ No Stars yet? Tap “Buy Stars” — Telegram sells them right there.",
    "🇮🇳 India: on Android pick Google Play and pay with UPI (PhonePe, Google Pay or Paytm).",
    "🌎 Other countries: card, Apple Pay or Google Pay.",
    "3️⃣ Done! Your pack arrives here instantly 🔥",
  ].join("\n");
}

async function sendStarsGuide(salesToken: string, fan: FanRow, chatId: number, stars: number): Promise<void> {
  const { storeSetIfAbsent } = await import("./_store.js");
  if (!(await storeSetIfAbsent(`luna:guia-estrelas:${fan.id}`, "1", 86400).catch(() => false))) return;
  await telegram(salesToken, "sendMessage", { chat_id: chatId, text: starsGuideText(stars) });
}

// Teste do administrador (23/11, pedido dele): a fatura do 1o pacote a venda por
// 1 estrela, na conversa dele com o bot de vendas. E uma compra de verdade (1 ⭐):
// passa pela pergunta do Telegram, pelo pagamento e pela entrega, igual ao fa.
export async function sendStarsTest(salesToken: string, fan: FanRow, chatId: number): Promise<string> {
  const { productsForSale } = await import("./_sales_bot.js");
  const config = await starsConfig();
  if (!config.enabled) throw new Error("As estrelas estão desligadas nesta persona (Planos e preços → Estrelas).");
  const product = (await productsForSale()).find((p) => p.kind !== "vip" && p.billing !== "mensal");
  if (!product) throw new Error("Nenhum pacote à venda para testar.");
  const test = { ...product, promo_stars: STARS_MIN };
  if (!(await sendStarsInvoice(salesToken, fan, test, chatId, false, []))) throw new Error("O Telegram recusou a fatura de teste (veja o aviso no seu Telegram).");
  return product.title;
}

interface PreCheckout {
  id: string;
  currency: string;
  total_amount: number;
  invoice_payload: string;
}

function orderIdOf(payload: string): string | null {
  const m = /^o:([0-9a-f-]{36})$/i.exec(payload ?? "");
  return m ? m[1] : null;
}

// O Telegram pergunta antes de cobrar: so libera se o pedido e o valor batem.
export async function answerPreCheckout(salesToken: string, query: PreCheckout): Promise<void> {
  let ok = false;
  try {
    const id = orderIdOf(query.invoice_payload);
    const [order] = id ? await dbSelect<StarsOrder>("sales_orders", `select=*&id=eq.${id}`) : [];
    const [product] = order ? await dbSelect<ProductRow>("sales_products", `select=id,active&id=eq.${order.product_id}`) : [];
    ok =
      Boolean(order && product?.active) &&
      order.gateway === STARS_GATEWAY &&
      query.currency === "XTR" &&
      Number(query.total_amount) === order.amount_cents &&
      (order.status === "pendente" || order.status === "entregue" || order.status === "expirado");
  } catch {
    ok = false;
  }
  await telegram(salesToken, "answerPreCheckoutQuery", {
    pre_checkout_query_id: query.id,
    ok,
    ...(ok ? {} : { error_message: "This package is no longer available. Send /start to see the packages again." }),
  }).catch(() => undefined);
}

interface Payment {
  currency: string;
  total_amount: number;
  invoice_payload: string;
  telegram_payment_charge_id: string;
}

// Cobrou: marca pago e entrega. A chave unica do telegram_payment_charge_id
// impede entregar duas vezes; fatura antiga paga de novo vira outro pedido.
export async function starsPaid(salesToken: string, payment: Payment): Promise<void> {
  const charge = String(payment.telegram_payment_charge_id ?? "");
  const id = orderIdOf(payment.invoice_payload);
  if (!charge || !id) return;
  const [order] = await dbSelect<StarsOrder>("sales_orders", `select=*&id=eq.${id}`);
  if (!order) {
    await adminNotice(`⚠️ Pagamento em estrelas (${payment.total_amount} ⭐) sem pedido no banco. Confira em @BotFather → Balance e reembolse se preciso.`);
    return;
  }
  if (payment.currency !== "XTR" || Number(payment.total_amount) !== order.amount_cents) {
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { note: `Estrelas pagas (${payment.total_amount}) diferentes do pedido` });
    await adminNotice(`⚠️ Pagamento em estrelas com valor diferente no pedido ${order.id.slice(0, 8)}: pago ${payment.total_amount} ⭐, pedido ${order.amount_cents} ⭐.`);
    return;
  }
  const { deliver } = await import("./_sales_pay.js");
  const now = new Date().toISOString();
  const paid = await dbUpdate<StarsOrder>("sales_orders", `id=eq.${order.id}&status=eq.pendente`, {
    status: "pago",
    paid_at: now,
    gateway_charge_id: charge,
  });
  if (paid) {
    await deliver(paid as never, salesToken);
    return;
  }
  // a mesma fatura paga outra vez (compra de novo): pedido novo, se a cobranca for nova
  const [created] = await dbInsertIgnoringDuplicates<StarsOrder>(
    "sales_orders",
    [{ fan_id: order.fan_id, product_id: order.product_id, amount_cents: order.amount_cents, status: "pago", gateway: STARS_GATEWAY, gateway_charge_id: charge, paid_at: now, note: `${order.amount_cents} estrelas` }],
    "gateway_charge_id"
  );
  if (created) await deliver(created as never, salesToken);
  else await audit("estrelas_repetido", "sales_orders", order.id, { cobranca: charge.slice(0, 40) });
}
