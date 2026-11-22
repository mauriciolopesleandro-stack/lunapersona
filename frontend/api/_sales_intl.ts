import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { audit, dbInsert, dbSelect, dbUpdate } from "./_supabase.js";
import { sendNotice, telegram, telegramConfig } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";
import { persona, personaParam } from "./_persona.js";

// Pagamento internacional pela NOWPayments (pedido de 21/11/2026): quem esta fora
// do Brasil nao tem Pix. Funciona como a PushinPay:
//  1. o fa toca no pacote -> pedido "pendente" (gateway "nowpayments",
//     amount_cents = centavos de dolar) + fatura (POST /v1/invoice) com o aviso
//     assinado para aquele pedido; o bot manda o botao com a pagina de pagamento;
//  2. a NOWPayments avisa em /api/studio?r=sales-intl&o=<pedido>&s=<assinatura>
//     a cada mudanca (waiting, confirming, finished...). O aviso nao basta: o
//     estudio confere na propria NOWPayments (GET /v1/payment/<id>) o status, o
//     pedido e o valor - aviso falso nao libera nada;
//  3. pago (confirmed/sending/finished) -> entrega igual ao Pix (deliver).
// A chave da API e a chave do IPN ficam no Upstash (coladas em Planos no
// estudio) e nunca voltam ao navegador. Precos em dolar por pacote.

const API = "https://api.nowpayments.io/v1";
const KEY = "luna:intl";
export const INTL_GATEWAY = "nowpayments";
const PAID = new Set(["confirmed", "sending", "finished"]);
const PENDING_REUSE_MS = 30 * 60_000;
// preco sugerido em centavos de dolar quando o pacote nao tem um proprio
const SUGGESTED: Record<ProductRow["kind"], number> = { ensaio: 1199, album: 1799, vip: 2499 };
// a NOWPayments recusa abaixo de US$ 10 (minimo da conta, 22/11): o estudio nao
// deixa salvar menos. Precos antigos salvos abaixo disso continuam valendo ate
// serem trocados (INTL_FLOOR_CENTS), para nao mudar preco sem o dono pedir.
export const INTL_MIN_CENTS = 1000;
export const INTL_FLOOR_CENTS = 200;
export const INTL_MAX_CENTS = 50000;

export interface IntlConfig {
  enabled: boolean;
  apiKey: string | null;
  ipnSecret: string | null;
  prices: Record<string, number>; // id do pacote -> centavos de dolar
}

const EMPTY: IntlConfig = { enabled: false, apiKey: null, ipnSecret: null, prices: {} };

export async function intlConfig(): Promise<IntlConfig> {
  if (!storeConfigured()) return EMPTY;
  try {
    const saved = JSON.parse((await storeGet(KEY)) ?? "null");
    if (!saved || typeof saved !== "object") return EMPTY;
    return {
      enabled: Boolean(saved.enabled),
      apiKey: typeof saved.apiKey === "string" && saved.apiKey ? saved.apiKey : null,
      ipnSecret: typeof saved.ipnSecret === "string" && saved.ipnSecret ? saved.ipnSecret : null,
      prices: saved.prices && typeof saved.prices === "object" ? saved.prices : {},
    };
  } catch {
    return EMPTY;
  }
}

export async function saveIntl(config: IntlConfig): Promise<void> {
  await storeSet(KEY, JSON.stringify(config));
}

export async function forgetIntl(): Promise<void> {
  await storeDelete(KEY);
}

export function suggestedUsd(product: Pick<ProductRow, "kind">): number {
  return SUGGESTED[product.kind] ?? 1199;
}

// preco em centavos de dolar, ou null quando nao vende fora do Brasil
// (desligado, sem as chaves, ou VIP mensal: a assinatura e so pelo Pix)
export function intlPrice(product: Pick<ProductRow, "id" | "kind" | "billing">, config: IntlConfig): number | null {
  if (!config.enabled || !config.apiKey || !config.ipnSecret || product.billing === "mensal") return null;
  const own = Number(config.prices[product.id]);
  return Number.isInteger(own) && own >= INTL_FLOOR_CENTS && own <= INTL_MAX_CENTS ? own : suggestedUsd(product);
}

export function usd(cents: number): string {
  return `US$ ${(cents / 100).toFixed(2)}`;
}

async function nowpay<T>(apiKey: string, method: "GET" | "POST", path: string, body?: object): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "x-api-key": apiKey, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15_000),
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const msg = (data as { message?: string } | null)?.message ?? (typeof data === "string" ? data.slice(0, 200) : "");
    throw new Error(`NOWPayments respondeu ${res.status}${msg ? `: ${msg}` : ""}`);
  }
  return data as T;
}

// confere a chave colada no estudio (GET /v1/status nao precisa de chave; a
// lista de moedas precisa)
export async function checkIntlKey(apiKey: string): Promise<void> {
  await nowpay(apiKey, "GET", "/merchant/coins");
}

export function intlSignature(orderId: string): string {
  const key = process.env.TOKEN_ENCRYPTION_KEY;
  if (!key) throw new Error("Falta TOKEN_ENCRYPTION_KEY na Vercel.");
  return createHmac("sha256", key).update(`intl-order-v1|${orderId}`).digest("hex").slice(0, 40);
}

function fanName(f: Pick<FanRow, "username" | "first_name" | "telegram_id">): string {
  return f.username ? `@${f.username}` : f.first_name || `fã ${f.telegram_id}`;
}

async function adminNotice(text: string): Promise<void> {
  await sendNotice(await telegramConfig(), text).catch(() => undefined);
}

interface IntlOrder {
  id: string;
  fan_id: string;
  product_id: string;
  amount_cents: number;
  status: string;
  gateway: string | null;
  gateway_charge_id: string | null;
  pix_code: string | null; // aqui: o link da pagina de pagamento
  created_at: string;
}

async function sendIntl(salesToken: string, chatId: number, product: ProductRow, order: IntlOrder, extraRows: object[][]): Promise<void> {
  await telegram(salesToken, "sendMessage", {
    chat_id: chatId,
    text:
      `🌎 ${product.title} — ${usd(order.amount_cents)}\n\n` +
      "Tap the button to pay (international payment, in USD). As soon as it is confirmed, everything arrives here automatically 💛\n\n" +
      "Toque no botão para pagar (pagamento internacional, em dólar). Assim que confirmar, chega tudo aqui sozinho 💛",
    reply_markup: {
      inline_keyboard: [
        [{ text: `💳 Pay ${usd(order.amount_cents)}`, url: order.pix_code }],
        [{ text: "✅ I paid / Já paguei", callback_data: `intlpaid:${order.id}` }],
        ...extraRows,
      ],
    },
  });
}

// Botao do pacote para quem esta fora do Brasil: cria (ou reaproveita) o pedido e
// manda a pagina de pagamento. Devolve false quando nao vende fora do Brasil ou
// a NOWPayments falhou (quem chamou segue com as estrelas ou o Pix).
export async function startIntl(salesToken: string, fan: FanRow, product: ProductRow, chatId: number, site: string, extraRows: object[][] = []): Promise<boolean> {
  const config = await intlConfig();
  const cents = intlPrice(product, config);
  if (!cents || !config.apiKey) return false;
  const since = new Date(Date.now() - PENDING_REUSE_MS).toISOString();
  const [open] = await dbSelect<IntlOrder>(
    "sales_orders",
    `select=*&fan_id=eq.${fan.id}&product_id=eq.${product.id}&status=eq.pendente&gateway=eq.${INTL_GATEWAY}&created_at=gt.${since}&order=created_at.desc&limit=1`
  );
  if (open?.pix_code && open.amount_cents === cents) {
    await sendIntl(salesToken, chatId, product, open, extraRows);
    return true;
  }
  const order = await dbInsert<IntlOrder>("sales_orders", {
    fan_id: fan.id,
    product_id: product.id,
    amount_cents: cents,
    status: "pendente",
    gateway: INTL_GATEWAY,
    note: usd(cents),
  });
  if (!order) return false;
  try {
    const bot = await telegram<{ username?: string }>(salesToken, "getMe").catch(() => ({}) as { username?: string });
    const back = bot.username ? `https://t.me/${bot.username}` : undefined;
    const invoice = await nowpay<{ id: string | number; invoice_url: string }>(config.apiKey, "POST", "/invoice", {
      price_amount: Number((cents / 100).toFixed(2)),
      price_currency: "usd",
      order_id: order.id,
      order_description: `${persona().fullName} - ${product.title}`.slice(0, 200),
      ipn_callback_url: `${site}/api/studio?r=sales-intl&o=${order.id}&s=${intlSignature(order.id)}${personaParam("&")}`,
      ...(back ? { success_url: back, cancel_url: back } : {}),
    });
    if (!invoice?.id || !invoice.invoice_url) throw new Error("a NOWPayments não devolveu a fatura");
    const saved = await dbUpdate<IntlOrder>("sales_orders", `id=eq.${order.id}`, {
      gateway_charge_id: `inv:${invoice.id}`.slice(0, 120),
      pix_code: String(invoice.invoice_url).slice(0, 1000),
    });
    await sendIntl(salesToken, chatId, product, saved ?? { ...order, pix_code: invoice.invoice_url }, extraRows);
    return true;
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { status: "cancelado", note: `Internacional: ${error}`.slice(0, 500) });
    await adminNotice(`⚠️ Não consegui gerar o pagamento internacional de “${product.title}” para ${fanName(fan)}: ${error.slice(0, 200)}`);
    return false;
  }
}

interface Payment {
  payment_id: number | string;
  invoice_id?: number | string | null;
  payment_status: string;
  price_amount: number | string;
  price_currency: string;
  order_id?: string | null;
  actually_paid?: number | string;
  pay_currency?: string;
}

const PAYMENT_KEY = (orderId: string) => `luna:intl-pagamento:${orderId}`;

// Confere na NOWPayments e, se pago, marca e entrega. Seguro para chamar de novo
// (avisos repetidos, "Ja paguei", conferencia do processador): so o primeiro passa.
export async function settleIntl(orderId: string, salesToken: string, paymentId?: string): Promise<"entregue" | "pendente" | "ignorado"> {
  const [order] = await dbSelect<IntlOrder>("sales_orders", `select=*&id=eq.${orderId}`);
  if (!order || order.gateway !== INTL_GATEWAY) return "ignorado";
  if (order.status !== "pendente") return order.status === "entregue" ? "entregue" : "ignorado";
  const config = await intlConfig();
  if (!config.apiKey) return "pendente";
  const id = paymentId ?? (await storeGet(PAYMENT_KEY(order.id)).catch(() => null));
  if (!id || !/^\d{1,20}$/.test(String(id))) return "pendente";
  const payment = await nowpay<Payment>(config.apiKey, "GET", `/payment/${id}`);
  // o pagamento tem que ser deste pedido e desta fatura
  if (String(payment.order_id ?? "") !== order.id) return "ignorado";
  if (order.gateway_charge_id?.startsWith("inv:") && payment.invoice_id != null && `inv:${payment.invoice_id}` !== order.gateway_charge_id) return "ignorado";
  const status = String(payment.payment_status).toLowerCase();
  if (status === "partially_paid") {
    if (await storeSetIfAbsent(`luna:intl-parcial:${order.id}`, "1", 86400).catch(() => false)) {
      await adminNotice(`⚠️ Pagamento internacional pago em parte no pedido ${order.id.slice(0, 8)} (${payment.actually_paid ?? "?"} ${payment.pay_currency ?? ""}). Confira no painel da NOWPayments.`);
    }
    return "pendente";
  }
  if (!PAID.has(status)) return "pendente";
  if (String(payment.price_currency).toLowerCase() !== "usd" || Math.round(Number(payment.price_amount) * 100) !== order.amount_cents) {
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { note: `Valor internacional (${payment.price_amount} ${payment.price_currency}) diferente do pedido` });
    await adminNotice(`⚠️ Pagamento internacional com valor diferente no pedido ${order.id.slice(0, 8)}: ${payment.price_amount} ${payment.price_currency}, pedido ${usd(order.amount_cents)}.`);
    return "ignorado";
  }
  const { deliver } = await import("./_sales_pay.js");
  const paid = await dbUpdate<IntlOrder>("sales_orders", `id=eq.${order.id}&status=eq.pendente`, { status: "pago", paid_at: new Date().toISOString() });
  if (!paid) return "ignorado";
  await deliver(paid as never, salesToken);
  return "entregue";
}

// "I paid / Ja paguei" do pagamento internacional
export async function checkIntlPaid(orderId: string, fan: FanRow, salesToken: string, chatId: number): Promise<void> {
  if (!/^[0-9a-f-]{36}$/i.test(orderId)) return;
  const [order] = await dbSelect<IntlOrder>("sales_orders", `select=*&id=eq.${orderId}&fan_id=eq.${fan.id}`);
  if (!order) return;
  if (order.status === "entregue") {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Already paid and delivered 💛 Check the messages above or /pacotes.\n\nJá foi pago e entregue 💛 Veja as mensagens acima ou /pacotes." });
    return;
  }
  if (order.status !== "pendente") return;
  if (!(await storeSetIfAbsent(`luna:intl-check:${order.id}`, "1", 60).catch(() => true))) {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Checking... try again in a minute 💛 / Conferindo... tente de novo em 1 minutinho 💛" });
    return;
  }
  const result = await settleIntl(order.id, salesToken).catch(() => "pendente" as const);
  if (result === "pendente") {
    await telegram(salesToken, "sendMessage", {
      chat_id: chatId,
      text: "Not confirmed yet. Crypto payments can take a few minutes to confirm — as soon as it does, I send everything here 💛\n\nAinda não confirmou. Pode levar alguns minutos: assim que confirmar, eu te mando tudo aqui 💛",
    });
  }
}

// assinatura da NOWPayments: HMAC-SHA512 do JSON com as chaves em ordem
function sortDeep(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === "object") return Object.fromEntries(Object.keys(v as object).sort().map((k) => [k, sortDeep((v as Record<string, unknown>)[k])]));
  return v;
}
export function ipnSignature(body: unknown, secret: string): string {
  return createHmac("sha512", secret).update(JSON.stringify(sortDeep(body))).digest("hex");
}

// Pagamentos internacionais pendentes das ultimas horas (o aviso pode nao
// chegar): o processador confere a cada poucos minutos.
export async function intlSweep(salesToken: string): Promise<void> {
  const config = await intlConfig();
  if (!config.apiKey) return;
  const open = await dbSelect<{ id: string }>(
    "sales_orders",
    `select=id&status=eq.pendente&gateway=eq.${INTL_GATEWAY}&created_at=gt.${new Date(Date.now() - 6 * 3600_000).toISOString()}&order=created_at.desc&limit=20`
  ).catch(() => [] as { id: string }[]);
  let checked = 0;
  for (const order of open) {
    if (checked >= 5) break;
    if (!(await storeGet(PAYMENT_KEY(order.id)).catch(() => null))) continue; // ainda nao escolheu como pagar
    if (!(await storeSetIfAbsent(`luna:intl-conferido:${order.id}`, "1", 180).catch(() => false))) continue;
    checked++;
    await settleIntl(order.id, salesToken).catch(() => undefined);
  }
}

// POST da NOWPayments em /api/studio?r=sales-intl&o=<pedido>&s=<assinatura>.
// Sempre 200; a verdade vem da consulta na API.
export default async function intlHook(req: IncomingMessage, res: ServerResponse) {
  const done = (status = 200) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json");
    res.end("{}");
  };
  if (req.method !== "POST") return done(405);
  const url = new URL(req.url ?? "/", "http://local");
  const orderId = url.searchParams.get("o") ?? "";
  const given = url.searchParams.get("s") ?? "";
  if (!/^[0-9a-f-]{36}$/i.test(orderId)) return done(400);
  const expected = intlSignature(orderId);
  if (given.length !== expected.length || !timingSafeEqual(Buffer.from(given), Buffer.from(expected))) return done(401);
  let raw = "";
  for await (const chunk of req) raw += chunk;
  let body: Payment;
  try {
    body = JSON.parse(raw) as Payment;
  } catch {
    return done();
  }
  const paymentId = String(body.payment_id ?? "");
  if (!/^\d{1,20}$/.test(paymentId)) return done();
  const config = await intlConfig();
  // a assinatura da NOWPayments e conferida; se nao bater, so registra (a
  // consulta na API com a nossa chave e que decide)
  if (config.ipnSecret) {
    const sig = String(req.headers["x-nowpayments-sig"] ?? "");
    if (!sig || sig !== ipnSignature(body, config.ipnSecret)) await audit("intl_assinatura", "sales_orders", orderId, { recebida: Boolean(sig) }).catch(() => undefined);
  }
  await storeSet(PAYMENT_KEY(orderId), paymentId).catch(() => undefined);
  const { salesToken } = await import("./_sales_bot.js");
  const token = await salesToken();
  if (!token) return done();
  await settleIntl(orderId, token, paymentId).catch(() => undefined);
  return done();
}
