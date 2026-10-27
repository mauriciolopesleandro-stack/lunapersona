import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { audit, dbInsert, dbSelect, dbUpdate } from "./_supabase.js";
import { sendNotice, telegram, telegramChannel, telegramConfig } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";

// Pix pela PushinPay (gateway escolhido pelo administrador em 27/10/2026: R$ 0,35
// por Pix, aceita conteudo adulto com a confirmacao de 18+ do comprador).
//
// Fluxo:
//  1. o fa toca no pacote -> pedido "pendente" + cobranca (POST /api/pix/cashIn,
//     valor em centavos) com webhook_url assinado para aquele pedido; o bot
//     manda o Pix copia-e-cola e o botao "Ja paguei";
//  2. a PushinPay avisa em /api/studio?r=sales-pix&o=<pedido>&s=<assinatura>. O aviso
//     nao basta: o estudio confere na propria PushinPay (GET /api/transactions/{id})
//     se esta "paid" e se o valor bate - aviso falso nao libera nada;
//  3. pago -> entrega: ensaio/album pelo file_id do Telegram (protegido contra
//     encaminhar e salvar); VIP com convite do canal para 1 pessoa, ate a data.
//     Vencido o VIP, o processador da fila tira o fa do canal e oferece renovar.
// O token da PushinPay fica no Upstash (colado em Vendas) e nunca volta ao navegador.

const TOKEN_KEY = "luna:pix-token";
const ENV_KEY = "luna:pix-env";
const API = { producao: "https://api.pushinpay.com.br", sandbox: "https://api-sandbox.pushinpay.com.br" };
const PENDING_REUSE_MS = 30 * 60_000; // mesmo pacote pedido de novo em 30 min: reaproveita o Pix
const INVITE_HOURS = 24;

export type PixEnv = keyof typeof API;

export interface OrderRow {
  id: string;
  fan_id: string;
  product_id: string;
  amount_cents: number;
  status: "pendente" | "pago" | "entregue" | "expirado" | "cancelado";
  gateway: string | null;
  gateway_charge_id: string | null;
  pix_code: string | null;
  created_at: string;
  paid_at: string | null;
  delivered_at: string | null;
  access_until: string | null;
  note: string | null;
}

interface Charge {
  id: string;
  status: string;
  value: number;
  qr_code?: string;
}

export async function pixConfig(): Promise<{ token: string | null; env: PixEnv }> {
  if (!storeConfigured()) return { token: null, env: "producao" };
  const token = (await storeGet(TOKEN_KEY).catch(() => null)) ?? null;
  const env = (await storeGet(ENV_KEY).catch(() => null)) === "sandbox" ? "sandbox" : "producao";
  return { token, env };
}

export async function savePixConfig(token: string, env: PixEnv): Promise<void> {
  await storeSet(TOKEN_KEY, token);
  await storeSet(ENV_KEY, env);
}

export async function forgetPix(): Promise<void> {
  await storeDelete(TOKEN_KEY);
  await storeDelete(ENV_KEY);
}

async function pushin<T>(method: "GET" | "POST", path: string, body?: object): Promise<T> {
  const { token, env } = await pixConfig();
  if (!token) throw new Error("Pix não configurado.");
  const res = await fetch(`${API[env]}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json", "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(20_000),
  });
  const data = (await res.json().catch(() => ({}))) as T & { message?: string };
  if (!res.ok) throw new Error(`PushinPay respondeu ${res.status}${data?.message ? `: ${data.message}` : ""}`);
  return data;
}

function orderSignature(orderId: string): string {
  const key = process.env.TOKEN_ENCRYPTION_KEY;
  if (!key) throw new Error("Falta TOKEN_ENCRYPTION_KEY na Vercel.");
  return createHmac("sha256", key).update(`pix-order-v1|${orderId}`).digest("hex").slice(0, 40);
}

export function baseUrl(req: IncomingMessage): string {
  if (process.env.PUBLIC_APP_URL) return process.env.PUBLIC_APP_URL.replace(/\/+$/, "");
  const host = String(req.headers["x-forwarded-host"] ?? req.headers.host ?? "");
  const proto = String(req.headers["x-forwarded-proto"] ?? "https").split(",")[0];
  return `${proto}://${host}`;
}

function brl(cents: number): string {
  return `R$ ${(cents / 100).toFixed(2).replace(".", ",")}`;
}

function fanName(f: Pick<FanRow, "username" | "first_name" | "telegram_id">): string {
  return f.username ? `@${f.username}` : f.first_name || `fã ${f.telegram_id}`;
}

async function adminNotice(text: string): Promise<void> {
  await sendNotice(await telegramConfig(), text).catch(() => undefined);
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function sendPix(salesToken: string, chatId: number, product: ProductRow, order: OrderRow): Promise<void> {
  await telegram(salesToken, "sendMessage", {
    chat_id: chatId,
    text:
      `💳 Pix de ${brl(order.amount_cents)} — ${product.title}\n\n` +
      "Copie o código abaixo e pague no app do seu banco (Pix copia e cola). Assim que o pagamento cair, eu te mando tudo aqui 💛",
  });
  await telegram(salesToken, "sendMessage", {
    chat_id: chatId,
    text: `<code>${escapeHtml(order.pix_code ?? "")}</code>`,
    parse_mode: "HTML",
    reply_markup: { inline_keyboard: [[{ text: "✅ Já paguei", callback_data: `paid:${order.id}` }]] },
  });
}

// Botao do pacote, com o Pix ligado: cria (ou reaproveita) o pedido e manda o Pix.
// Devolve false quando o Pix nao esta configurado.
export async function startPayment(salesToken: string, fan: FanRow, product: ProductRow, chatId: number, site: string): Promise<boolean> {
  const { token } = await pixConfig();
  if (!token) return false;
  const since = new Date(Date.now() - PENDING_REUSE_MS).toISOString();
  const [open] = await dbSelect<OrderRow>(
    "sales_orders",
    `select=*&fan_id=eq.${fan.id}&product_id=eq.${product.id}&status=eq.pendente&created_at=gt.${since}&order=created_at.desc&limit=1`
  );
  if (open?.pix_code && open.amount_cents === product.price_cents) {
    await sendPix(salesToken, chatId, product, open);
    return true;
  }
  const order = await dbInsert<OrderRow>("sales_orders", {
    fan_id: fan.id,
    product_id: product.id,
    amount_cents: product.price_cents,
    status: "pendente",
    gateway: "pushinpay",
  });
  try {
    const charge = await pushin<Charge>("POST", "/api/pix/cashIn", {
      value: product.price_cents,
      description: `Luna Vox - ${product.title}`.slice(0, 255),
      webhook_url: `${site}/api/studio?r=sales-pix&o=${order.id}&s=${orderSignature(order.id)}`,
    });
    if (!charge.id || !charge.qr_code) throw new Error("a PushinPay não devolveu o Pix");
    const saved = await dbUpdate<OrderRow>("sales_orders", `id=eq.${order.id}`, { gateway_charge_id: String(charge.id).toLowerCase(), pix_code: charge.qr_code.slice(0, 1000) });
    await sendPix(salesToken, chatId, product, saved ?? { ...order, pix_code: charge.qr_code });
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { status: "cancelado", note: `Pix: ${error}`.slice(0, 500) });
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Não consegui gerar o Pix agora 😕 Tente de novo daqui a pouquinho." });
    await adminNotice(`⚠️ Não consegui gerar o Pix de “${product.title}” para ${fanName(fan)}: ${error.slice(0, 200)}`);
  }
  return true;
}

// Confere na PushinPay e, se pago, marca e entrega. Seguro para chamar de novo
// (aviso repetido, "Ja paguei" depois do aviso): so o primeiro passa.
export async function settle(orderId: string, salesToken: string): Promise<"entregue" | "pendente" | "ignorado"> {
  const [order] = await dbSelect<OrderRow>("sales_orders", `select=*&id=eq.${orderId}`);
  if (!order || !order.gateway_charge_id) return "ignorado";
  if (order.status !== "pendente") return order.status === "entregue" ? "entregue" : "ignorado";
  const charge = await pushin<Charge>("GET", `/api/transactions/${order.gateway_charge_id}`);
  if (String(charge.status).toLowerCase() !== "paid") return "pendente";
  if (Number(charge.value) !== order.amount_cents) {
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { note: `Valor pago (${charge.value}) diferente do pedido` });
    await adminNotice(`⚠️ Pix com valor diferente no pedido ${order.id.slice(0, 8)}: pago ${brl(Number(charge.value))}, pedido ${brl(order.amount_cents)}. Confira no painel da PushinPay.`);
    return "ignorado";
  }
  const paid = await dbUpdate<OrderRow>("sales_orders", `id=eq.${order.id}&status=eq.pendente`, { status: "pago", paid_at: new Date().toISOString() });
  if (!paid) return "ignorado"; // outra chamada ja pegou este pedido
  await deliver(paid, salesToken);
  return "entregue";
}

async function deliver(order: OrderRow, salesToken: string): Promise<void> {
  const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${order.fan_id}`);
  const [product] = await dbSelect<ProductRow>("sales_products", `select=*&id=eq.${order.product_id}`);
  const chatId = fan.telegram_id;
  try {
    let accessUntil: string | null = null;
    if (product.kind === "vip") {
      accessUntil = await deliverVip(order, fan, product, salesToken);
    } else {
      const media = await dbSelect<{ kind: "photo" | "video"; telegram_file_id: string }>(
        "sales_product_media",
        `select=kind,telegram_file_id&product_id=eq.${product.id}&order=position.asc`
      );
      await telegram(salesToken, "sendMessage", { chat_id: chatId, text: `Pagamento confirmado! 💛 Aqui está: ${product.title}` });
      for (let i = 0; i < media.length; i += 10) {
        const chunk = media.slice(i, i + 10);
        if (chunk.length === 1) {
          const m = chunk[0];
          await telegram(salesToken, m.kind === "video" ? "sendVideo" : "sendPhoto", { chat_id: chatId, [m.kind === "video" ? "video" : "photo"]: m.telegram_file_id, protect_content: true });
        } else {
          await telegram(salesToken, "sendMediaGroup", {
            chat_id: chatId,
            media: chunk.map((m) => ({ type: m.kind, media: m.telegram_file_id })),
            protect_content: true,
          });
        }
      }
    }
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { status: "entregue", delivered_at: new Date().toISOString(), ...(accessUntil ? { access_until: accessUntil } : {}) });
    await audit("venda", "sales_orders", order.id, { produto: product.slug, valor: order.amount_cents });
    await adminNotice(`💰 Venda! ${fanName(fan)} comprou “${product.title}” por ${brl(order.amount_cents)} (Pix).`);
  } catch (e) {
    const error = (e instanceof Error ? e.message : String(e)).slice(0, 300);
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { note: `Entrega: ${error}` });
    await adminNotice(`⚠️ Pix pago, mas não consegui entregar “${product.title}” para ${fanName(fan)}: ${error}\nEntregue à mão e confira em Vendas.`);
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Pagamento confirmado! 💛 Tive um probleminha para te mandar agora, mas já estou resolvendo e te mando aqui." }).catch(() => undefined);
  }
}

// VIP: convite do canal para 1 pessoa (vale 24 h para entrar). O acesso soma ao
// que ainda resta de um VIP anterior (renovacao antes de vencer nao perde dias).
async function deliverVip(order: OrderRow, fan: FanRow, product: ProductRow, salesToken: string): Promise<string> {
  const channel = await telegramChannel();
  const admin = await telegramConfig();
  if (!channel || !admin.token) throw new Error("o canal VIP não está conectado (Meu perfil → Canal +18)");
  const current = await dbSelect<{ access_until: string | null }>(
    "sales_orders",
    `select=access_until&fan_id=eq.${fan.id}&status=eq.entregue&access_until=gt.${new Date().toISOString()}&order=access_until.desc&limit=1`
  );
  const from = current[0]?.access_until ? Date.parse(current[0].access_until) : Date.now();
  const until = new Date(from + (product.period_days ?? 30) * 86400_000).toISOString();
  // quem ja esta no canal (renovacao) so ganha mais dias; o convite serve para quem saiu
  await telegram(admin.token, "unbanChatMember", { chat_id: channel.id, user_id: fan.telegram_id, only_if_banned: true }).catch(() => undefined);
  const invite = await telegram<{ invite_link: string }>(admin.token, "createChatInviteLink", {
    chat_id: channel.id,
    name: `VIP ${fan.telegram_id}`.slice(0, 32),
    member_limit: 1,
    expire_date: Math.floor(Date.now() / 1000) + INVITE_HOURS * 3600,
  });
  if (!invite.invite_link) throw new Error("o Telegram não criou o convite do canal (o bot precisa poder convidar por link)");
  const date = new Date(until).toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
  await telegram(salesToken, "sendMessage", {
    chat_id: fan.telegram_id,
    text: `Pagamento confirmado! 💎 Bem-vindo ao VIP.\n\nSeu acesso vale até ${date}. Entre no canal pelo botão (o convite é só seu e vale por ${INVITE_HOURS} h):`,
    reply_markup: { inline_keyboard: [[{ text: "Entrar no canal VIP", url: invite.invite_link }]] },
    protect_content: true,
  });
  return until;
}

// "Ja paguei": no maximo uma consulta por minuto por pedido (regra da PushinPay).
export async function checkPaid(orderId: string, fan: FanRow, salesToken: string, chatId: number): Promise<void> {
  if (!/^[0-9a-f-]{36}$/i.test(orderId)) return;
  const [order] = await dbSelect<OrderRow>("sales_orders", `select=*&id=eq.${orderId}&fan_id=eq.${fan.id}`);
  if (!order) return;
  if (order.status === "entregue") {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Esse pedido já foi pago e entregue 💛 Veja as mensagens acima." });
    return;
  }
  if (order.status !== "pendente") return;
  if (!(await storeSetIfAbsent(`luna:pix-check:${order.id}`, "1", 60).catch(() => true))) {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Estou conferindo... tente de novo em 1 minutinho 💛" });
    return;
  }
  const result = await settle(order.id, salesToken);
  if (result === "pendente") {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Ainda não caiu por aqui. Às vezes leva alguns segundos: assim que cair, eu te mando tudo 💛" });
  }
}

// POST da PushinPay em /api/studio?r=sales-pix&o=<pedido>&s=<assinatura>. Sempre 200
// (a PushinPay repete ate 3 vezes em erro); a verdade vem da consulta na API.
export default async function pixHook(req: IncomingMessage, res: ServerResponse) {
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
  const expected = orderSignature(orderId);
  if (given.length !== expected.length || !timingSafeEqual(Buffer.from(given), Buffer.from(expected))) return done(401);
  let raw = "";
  for await (const chunk of req) raw += chunk;
  let chargeId = "";
  try {
    const body = raw.trim().startsWith("{") ? (JSON.parse(raw) as Record<string, unknown>) : Object.fromEntries(new URLSearchParams(raw));
    chargeId = String(body.id ?? "").toLowerCase();
  } catch {
    return done();
  }
  const [order] = await dbSelect<OrderRow>("sales_orders", `select=id,gateway_charge_id&id=eq.${orderId}`);
  if (!order || order.gateway_charge_id !== chargeId) return done();
  const { salesToken } = await import("./_sales_bot.js");
  const token = await salesToken();
  if (token) await settle(orderId, token).catch(() => undefined);
  return done();
}

// Processador da fila (a cada minuto): VIP vencido sai do canal e recebe o
// convite para renovar. Nunca derruba o processador.
export async function salesTick(): Promise<number> {
  try {
    const now = new Date().toISOString();
    const due = await dbSelect<OrderRow>("sales_orders", `select=*&status=eq.entregue&access_until=lt.${now}&limit=20`);
    if (!due.length) return 0;
    const { salesToken } = await import("./_sales_bot.js");
    const token = await salesToken();
    const channel = await telegramChannel();
    const admin = await telegramConfig();
    let done = 0;
    for (const order of due) {
      // renovou: um pedido mais novo ainda vale -> so encerra este
      const newer = await dbSelect<{ id: string }>("sales_orders", `select=id&fan_id=eq.${order.fan_id}&status=eq.entregue&access_until=gt.${now}&limit=1`);
      const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${order.fan_id}`);
      if (!newer.length && fan && channel && admin.token) {
        await telegram(admin.token, "banChatMember", { chat_id: channel.id, user_id: fan.telegram_id }).catch(() => undefined);
        await telegram(admin.token, "unbanChatMember", { chat_id: channel.id, user_id: fan.telegram_id, only_if_banned: true }).catch(() => undefined);
        if (token && !fan.blocked_at) {
          await telegram(token, "sendMessage", {
            chat_id: fan.telegram_id,
            text: "Seu VIP terminou 💔 Quer continuar recebendo tudo? Renove por aqui:",
            reply_markup: { inline_keyboard: [[{ text: "💎 Renovar o VIP", callback_data: `buy:${order.product_id}` }]] },
          }).catch(() => undefined);
        }
      }
      await dbUpdate("sales_orders", `id=eq.${order.id}&status=eq.entregue`, { status: "expirado" });
      done++;
    }
    return done;
  } catch {
    return 0;
  }
}
