import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { audit, dbInsert, dbInsertIgnoringDuplicates, dbSelect, dbUpdate, dbUpdateWhere } from "./_supabase.js";
import { sendNotice, telegram, telegramChannel, telegramConfig } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";

// Pix pela PushinPay (gateway escolhido pelo administrador em 27/10/2026: R$ 0,35
// por Pix, aceita conteudo adulto com a confirmacao de 18+ do comprador).
//
// Fluxo:
//  1. o fa toca no pacote -> pedido "pendente" + cobranca com webhook_url assinado
//     para aquele pedido (POST /api/pix/cashIn; VIP mensal: Pix Recorrente em
//     /api/pix/cashIn/subscription, que renova sozinho todo mes); o bot manda o
//     Pix copia-e-cola e o botao "Ja paguei";
//  2. a PushinPay avisa em /api/studio?r=sales-pix&o=<pedido>&s=<assinatura>. O aviso
//     nao basta: o estudio confere na propria PushinPay (GET /api/transactions/{id})
//     se esta "paid" e se o valor bate - aviso falso nao libera nada. Parcela nova
//     do VIP mensal chega no mesmo link (outro id, mesmo subscription_id) e vira
//     outro pedido;
//  3. pago -> entrega, com acesso por period_days (30) dias:
//     ensaio/album pelo file_id do Telegram (protegido contra encaminhar e
//     salvar), que ficam no chat; o fa abre de novo em "Meus pacotes" enquanto
//     o acesso vale; VIP com convite do canal para 1 pessoa.
//  4. vencido (processador da fila, a cada minuto): VIP sai do canal; pacote para
//     de abrir; o fa recebe o botao para comprar de novo.
// O token da PushinPay fica no Upstash (colado em Vendas) e nunca volta ao navegador.

const TOKEN_KEY = "luna:pix-token";
const ENV_KEY = "luna:pix-env";
const API = { producao: "https://api.pushinpay.com.br", sandbox: "https://api-sandbox.pushinpay.com.br" };
const PENDING_REUSE_MS = 30 * 60_000; // mesmo pacote pedido de novo em 30 min: reaproveita o Pix
const INVITE_HOURS = 24;
const REOPEN_WAIT_S = 10 * 60; // abrir o mesmo pacote de novo: no maximo a cada 10 min
const MONTHLY_GRACE_MS = 3 * 86400_000; // VIP mensal: espera a parcela atrasada antes de tirar do canal
const FREQUENCY_MONTHLY = 2;
const RETRY_3X_7_DAYS = 2;

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
  subscription_id: string | null;
  installment: number | null;
  note: string | null;
}

interface Charge {
  id: string;
  status: string;
  value: number;
  qr_code?: string;
  subscription_id?: string;
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

async function pushin<T>(method: "GET" | "POST" | "DELETE", path: string, body?: object): Promise<T> {
  const { token, env } = await pixConfig();
  if (!token) throw new Error("Pix não configurado.");
  const res = await fetch(`${API[env]}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json", "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(20_000),
  });
  const data = (await res.json().catch(() => ({}))) as T & { message?: string; errors?: Record<string, string[] | string> };
  if (!res.ok) {
    // 422 traz a lista de campos com problema: vai inteira para o aviso (sem os dados do fa)
    const details = data?.errors && typeof data.errors === "object" ? Object.entries(data.errors).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join("; ") : "";
    throw new Error(`PushinPay respondeu ${res.status}${data?.message ? `: ${data.message}` : ""}${details ? ` [${details}]` : ""}`);
  }
  return data;
}

// --- VIP mensal: dados de quem paga ---------------------------------------------------------------
// O Pix automatico (recorrente) exige o nome e o CPF do pagador. O bot pede numa
// mensagem so; os dados vao direto para a PushinPay e nao ficam guardados aqui.
const SUB_DATA_KEY = (telegramId: number) => `luna:sub-data:${telegramId}`;
const SUB_DATA_TTL_S = 15 * 60;

export interface Customer {
  name: string;
  cpf: string; // so numeros
}

export function validCpf(raw: string): boolean {
  const cpf = raw.replace(/\D/g, "");
  if (cpf.length !== 11 || /^(\d)\1{10}$/.test(cpf)) return false;
  const digit = (len: number) => {
    let sum = 0;
    for (let i = 0; i < len; i++) sum += Number(cpf[i]) * (len + 1 - i);
    const rest = (sum * 10) % 11;
    return rest === 10 ? 0 : rest;
  };
  return digit(9) === Number(cpf[9]) && digit(10) === Number(cpf[10]);
}

// "Maria da Silva 123.456.789-00" (em qualquer ordem, numa mensagem so)
export function parseCustomer(text: string): Customer | "cpf" | null {
  const cpfMatch = text.match(/\d{3}\.?\d{3}\.?\d{3}-?\d{2}/);
  const name = text.replace(cpfMatch?.[0] ?? "", " ").replace(/[^\p{L}\s'-]/gu, " ").replace(/\s+/g, " ").trim();
  if (!cpfMatch || name.split(" ").length < 2) return null;
  if (!validCpf(cpfMatch[0])) return "cpf";
  return { name: name.slice(0, 120), cpf: cpfMatch[0].replace(/\D/g, "") };
}

async function askCustomer(salesToken: string, fan: FanRow, product: ProductRow, chatId: number): Promise<void> {
  await storeSet(SUB_DATA_KEY(fan.telegram_id), product.id);
  await storeSetIfAbsent(`${SUB_DATA_KEY(fan.telegram_id)}:ttl`, "1", SUB_DATA_TTL_S).catch(() => undefined);
  await telegram(salesToken, "sendMessage", {
    chat_id: chatId,
    text:
      `💎 ${product.title}\n\n` +
      "O Pix automático (que renova sozinho todo mês) pede o nome completo e o CPF de quem vai pagar. Seus dados vão só para a processadora do pagamento, não ficam salvos aqui.\n\n" +
      "Me manda numa mensagem só, assim:\nMaria da Silva 123.456.789-00\n\n" +
      "Prefere não informar? Escolha o VIP de 30 dias, que não pede cadastro 💛",
  });
}

// Mensagem do fa enquanto o bot espera os dados do VIP mensal. Devolve true se
// a mensagem era para isso (consumida aqui).
export async function handleCustomerData(salesToken: string, fan: FanRow, text: string, chatId: number, site: string): Promise<boolean> {
  if (!storeConfigured()) return false;
  const productId = await storeGet(SUB_DATA_KEY(fan.telegram_id)).catch(() => null);
  if (!productId) return false;
  // pedido velho (mais de 15 min): esquece
  if (!(await storeGet(`${SUB_DATA_KEY(fan.telegram_id)}:ttl`).catch(() => null))) {
    await storeDelete(SUB_DATA_KEY(fan.telegram_id)).catch(() => undefined);
    return false;
  }
  const parsed = parseCustomer(text);
  if (parsed === null) {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Não consegui entender 🙈 Manda o nome completo e o CPF numa mensagem só, assim:\nMaria da Silva 123.456.789-00" });
    return true;
  }
  if (parsed === "cpf") {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Esse CPF não parece válido. Confere os números e manda de novo 💛" });
    return true;
  }
  await storeDelete(SUB_DATA_KEY(fan.telegram_id)).catch(() => undefined);
  const [product] = await dbSelect<ProductRow>("sales_products", `select=*&id=eq.${productId}`);
  if (product) await startPayment(salesToken, fan, product, chatId, site, parsed);
  return true;
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

function day(iso: string): string {
  return new Date(iso).toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
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
  const monthly = product.billing === "mensal";
  await telegram(salesToken, "sendMessage", {
    chat_id: chatId,
    text:
      `💳 Pix de ${brl(order.amount_cents)}${monthly ? " por mês" : ""} — ${product.title}\n\n` +
      "Copie o código abaixo e pague no app do seu banco (Pix copia e cola). Assim que o pagamento cair, eu te mando tudo aqui 💛" +
      (monthly ? "\n\nÉ uma assinatura: pagando, você autoriza o Pix automático todo mês. Cancele quando quiser com /cancelar." : ""),
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
export async function startPayment(salesToken: string, fan: FanRow, product: ProductRow, chatId: number, site: string, customer?: Customer): Promise<boolean> {
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
  if (product.billing === "mensal" && !customer) {
    await askCustomer(salesToken, fan, product, chatId);
    return true;
  }
  const order = await dbInsert<OrderRow>("sales_orders", {
    fan_id: fan.id,
    product_id: product.id,
    amount_cents: product.price_cents,
    status: "pendente",
    gateway: "pushinpay",
    ...(product.billing === "mensal" ? { installment: 1 } : {}),
  });
  try {
    const webhook_url = `${site}/api/studio?r=sales-pix&o=${order.id}&s=${orderSignature(order.id)}`;
    const charge =
      product.billing === "mensal"
        ? await pushin<Charge>("POST", "/api/pix/cashIn/subscription", {
            value: product.price_cents,
            frequency: FREQUENCY_MONTHLY,
            pix_recurring_retry_policy: RETRY_3X_7_DAYS,
            webhook_url,
            name: `Luna Vox - ${product.title}`.slice(0, 200),
            customer: { name: customer!.name, document: { type: "cpf", number: customer!.cpf } },
          })
        : await pushin<Charge>("POST", "/api/pix/cashIn", { value: product.price_cents, description: `Luna Vox - ${product.title}`.slice(0, 255), webhook_url });
    if (!charge.id || !charge.qr_code) throw new Error("a PushinPay não devolveu o Pix");
    if (product.billing === "mensal" && !charge.subscription_id) throw new Error("a PushinPay não devolveu a assinatura");
    const saved = await dbUpdate<OrderRow>("sales_orders", `id=eq.${order.id}`, {
      gateway_charge_id: String(charge.id).toLowerCase(),
      pix_code: charge.qr_code.slice(0, 1000),
      ...(charge.subscription_id ? { subscription_id: String(charge.subscription_id) } : {}),
    });
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

// Parcela nova do VIP mensal (mesmo subscription_id, outro id de cobranca):
// conferida na PushinPay, vira um pedido novo (a chave unica da cobranca
// impede entregar duas vezes) e soma mais um periodo.
async function renewal(first: OrderRow, chargeId: string, salesToken: string): Promise<void> {
  const charge = await pushin<Charge>("GET", `/api/transactions/${chargeId}`);
  if (String(charge.status).toLowerCase() !== "paid" || Number(charge.value) !== first.amount_cents) return;
  const previous = await dbSelect<{ installment: number | null }>("sales_orders", `select=installment&subscription_id=eq.${first.subscription_id}&order=installment.desc&limit=1`);
  const [created] = await dbInsertIgnoringDuplicates<OrderRow>(
    "sales_orders",
    [
      {
        fan_id: first.fan_id,
        product_id: first.product_id,
        amount_cents: first.amount_cents,
        status: "pago",
        gateway: "pushinpay",
        gateway_charge_id: chargeId,
        subscription_id: first.subscription_id,
        installment: (previous[0]?.installment ?? 1) + 1,
        paid_at: new Date().toISOString(),
      },
    ],
    "gateway_charge_id"
  );
  if (created) await deliver(created, salesToken);
}

// Manda as fotos/videos do pacote (albuns de ate 10), protegidos contra
// encaminhar e salvar.
async function sendPack(product: ProductRow, chatId: number, salesToken: string): Promise<void> {
  const media = await dbSelect<{ kind: "photo" | "video"; telegram_file_id: string }>(
    "sales_product_media",
    `select=kind,telegram_file_id&product_id=eq.${product.id}&order=position.asc`
  );
  if (!media.length) throw new Error("o pacote está sem fotos");
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

async function deliver(order: OrderRow, salesToken: string): Promise<void> {
  const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${order.fan_id}`);
  const [product] = await dbSelect<ProductRow>("sales_products", `select=*&id=eq.${order.product_id}`);
  const chatId = fan.telegram_id;
  try {
    let accessUntil: string;
    if (product.kind === "vip") {
      accessUntil = await deliverVip(order, fan, product, salesToken);
    } else {
      accessUntil = new Date(Date.now() + product.period_days * 86400_000).toISOString();
      await telegram(salesToken, "sendMessage", { chat_id: chatId, text: `Pagamento confirmado! 💛 Aqui está: ${product.title}` });
      await sendPack(product, chatId, salesToken);
      await telegram(salesToken, "sendMessage", {
        chat_id: chatId,
        text: `Aproveite 💛 Você pode abrir este pacote de novo até ${day(accessUntil)} em 📂 Meus pacotes.`,
        reply_markup: { inline_keyboard: [[{ text: "📂 Meus pacotes", callback_data: "mine" }]] },
      });
    }
    await dbUpdate("sales_orders", `id=eq.${order.id}`, { status: "entregue", delivered_at: new Date().toISOString(), access_until: accessUntil });
    await audit("venda", "sales_orders", order.id, { produto: product.slug, valor: order.amount_cents, parcela: order.installment });
    const what = order.installment && order.installment > 1 ? `renovou “${product.title}” (parcela ${order.installment})` : `comprou “${product.title}”`;
    await adminNotice(`💰 Venda! ${fanName(fan)} ${what} por ${brl(order.amount_cents)} (Pix).`);
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
  const current = await activeVip(fan.id);
  const from = current ? Date.parse(current) : Date.now();
  const until = new Date(from + product.period_days * 86400_000).toISOString();
  // quem ja esta no canal (renovacao) so ganha mais dias; o convite serve para quem saiu
  await telegram(admin.token, "unbanChatMember", { chat_id: channel.id, user_id: fan.telegram_id, only_if_banned: true }).catch(() => undefined);
  const invite = await telegram<{ invite_link: string }>(admin.token, "createChatInviteLink", {
    chat_id: channel.id,
    name: `VIP ${fan.telegram_id}`.slice(0, 32),
    member_limit: 1,
    expire_date: Math.floor(Date.now() / 1000) + INVITE_HOURS * 3600,
  });
  if (!invite.invite_link) throw new Error("o Telegram não criou o convite do canal (o bot precisa poder convidar por link)");
  const renewing = Boolean(order.installment && order.installment > 1) || Boolean(current);
  await telegram(salesToken, "sendMessage", {
    chat_id: fan.telegram_id,
    text:
      (renewing ? `VIP renovado! 💎 Seu acesso agora vale até ${day(until)}.` : `Pagamento confirmado! 💎 Bem-vindo ao VIP.\n\nSeu acesso vale até ${day(until)}.`) +
      ` Se ainda não estiver no canal, entre pelo botão (o convite é só seu e vale por ${INVITE_HOURS} h):` +
      (product.billing === "mensal" ? "\n\nO VIP mensal renova sozinho pelo Pix. Para cancelar: /cancelar" : ""),
    reply_markup: { inline_keyboard: [[{ text: "Entrar no canal VIP", url: invite.invite_link }]] },
    protect_content: true,
  });
  return until;
}

// fim do VIP mais longo ainda valendo (de qualquer VIP: avulso ou mensal)
export async function activeVip(fanId: string): Promise<string | null> {
  const vips = await dbSelect<{ id: string }>("sales_products", "select=id&kind=eq.vip");
  if (!vips.length) return null;
  const [row] = await dbSelect<{ access_until: string }>(
    "sales_orders",
    `select=access_until&fan_id=eq.${fanId}&status=eq.entregue&product_id=in.(${vips.map((v) => v.id).join(",")})&access_until=gt.${new Date().toISOString()}&order=access_until.desc&limit=1`
  );
  return row?.access_until ?? null;
}

// Pacotes (ensaio/album) pagos e ainda dentro do prazo, para "Meus pacotes".
export async function activePacks(fanId: string): Promise<{ id: string; title: string; access_until: string }[]> {
  const orders = await dbSelect<OrderRow>("sales_orders", `select=*&fan_id=eq.${fanId}&status=eq.entregue&access_until=gt.${new Date().toISOString()}&order=access_until.desc`);
  if (!orders.length) return [];
  const products = await dbSelect<ProductRow>("sales_products", `select=id,title,kind&id=in.(${[...new Set(orders.map((o) => o.product_id))].join(",")})`);
  const packs = new Map(products.filter((p) => p.kind !== "vip").map((p) => [p.id, p.title]));
  return orders.filter((o) => packs.has(o.product_id)).map((o) => ({ id: o.id, title: packs.get(o.product_id)!, access_until: o.access_until! }));
}

// "Meus pacotes" -> abrir: manda o pacote de novo enquanto o acesso vale.
export async function openPack(orderId: string, fan: FanRow, salesToken: string, chatId: number): Promise<void> {
  if (!/^[0-9a-f-]{36}$/i.test(orderId)) return;
  const [order] = await dbSelect<OrderRow>("sales_orders", `select=*&id=eq.${orderId}&fan_id=eq.${fan.id}`);
  const [product] = order ? await dbSelect<ProductRow>("sales_products", `select=*&id=eq.${order.product_id}`) : [];
  if (!order || !product || product.kind === "vip") return;
  if (order.status !== "entregue" || !order.access_until || Date.parse(order.access_until) < Date.now()) {
    await telegram(salesToken, "sendMessage", {
      chat_id: chatId,
      text: `Seu acesso a “${product.title}” terminou. Quer de novo?`,
      reply_markup: { inline_keyboard: [[{ text: `🔁 Comprar de novo — ${brl(product.price_cents)}`, callback_data: `buy:${product.id}` }]] },
    });
    return;
  }
  if (!(await storeSetIfAbsent(`luna:pack-open:${order.id}`, "1", REOPEN_WAIT_S).catch(() => true))) {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Acabei de te mandar esse pacote 💛 Role a conversa para cima." });
    return;
  }
  await sendPack(product, chatId, salesToken);
  await telegram(salesToken, "sendMessage", { chat_id: chatId, text: `Aproveite 💛 Abra de novo quando quiser até ${day(order.access_until)}.` });
}

// /cancelar: cancela o VIP mensal na PushinPay. O acesso vai ate o fim do mes pago.
export async function cancelSubscription(fan: FanRow, salesToken: string, chatId: number): Promise<void> {
  const [order] = await dbSelect<OrderRow>(
    "sales_orders",
    `select=*&fan_id=eq.${fan.id}&subscription_id=not.is.null&status=in.(entregue,pago,expirado)&order=created_at.desc&limit=1`
  );
  if (!order?.subscription_id || (order.note ?? "").startsWith("Assinatura cancelada")) {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Você não tem VIP mensal ativo para cancelar." });
    return;
  }
  try {
    await pushin("DELETE", `/api/pix/cashIn/subscription/${encodeURIComponent(order.subscription_id)}/cancel`);
  } catch (e) {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Não consegui cancelar agora 😕 Tente de novo daqui a pouco." });
    await adminNotice(`⚠️ ${fanName(fan)} pediu para cancelar o VIP mensal e deu erro: ${(e instanceof Error ? e.message : String(e)).slice(0, 200)}`);
    return;
  }
  await dbUpdateWhere("sales_orders", `subscription_id=eq.${encodeURIComponent(order.subscription_id)}`, { note: "Assinatura cancelada pelo fã" });
  const until = await activeVip(fan.id);
  await telegram(salesToken, "sendMessage", {
    chat_id: chatId,
    text: `Pronto, o VIP mensal foi cancelado e não vai cobrar mais.${until ? ` Seu acesso continua até ${day(until)} 💛` : ""}`,
  });
  await adminNotice(`ℹ️ ${fanName(fan)} cancelou o VIP mensal.`);
}

// "Ja paguei": no maximo uma consulta por minuto por pedido (regra da PushinPay).
export async function checkPaid(orderId: string, fan: FanRow, salesToken: string, chatId: number): Promise<void> {
  if (!/^[0-9a-f-]{36}$/i.test(orderId)) return;
  const [order] = await dbSelect<OrderRow>("sales_orders", `select=*&id=eq.${orderId}&fan_id=eq.${fan.id}`);
  if (!order) return;
  if (order.status === "entregue") {
    await telegram(salesToken, "sendMessage", { chat_id: chatId, text: "Esse pedido já foi pago e entregue 💛 Veja as mensagens acima ou 📂 Meus pacotes." });
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

// POST da PushinPay em /api/studio?r=sales-pix&o=<pedido>&s=<assinatura>. Sempre
// 200 (a PushinPay repete ate 3 vezes em erro); a verdade vem da consulta na API.
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
  let body: Record<string, unknown>;
  try {
    body = raw.trim().startsWith("{") ? (JSON.parse(raw) as Record<string, unknown>) : Object.fromEntries(new URLSearchParams(raw));
  } catch {
    return done();
  }
  const chargeId = String(body.id ?? "").toLowerCase();
  const [order] = await dbSelect<OrderRow>("sales_orders", `select=*&id=eq.${orderId}`);
  if (!order || !chargeId) return done();
  const { salesToken } = await import("./_sales_bot.js");
  const token = await salesToken();
  if (!token) return done();
  if (order.gateway_charge_id === chargeId) {
    await settle(orderId, token).catch(() => undefined);
  } else if (order.subscription_id && String(body.subscription_id ?? "") === order.subscription_id && /^[0-9a-z-]{8,80}$/.test(chargeId)) {
    await renewal(order, chargeId, token).catch(() => undefined);
  }
  return done();
}

// Processador da fila (a cada minuto). Nunca derruba o processador.
// Acesso vencido: VIP sai do canal (o mensal espera 3 dias pela parcela),
// pacote para de abrir; o fa recebe o botao para comprar de novo.
export async function salesTick(): Promise<number> {
  try {
    const { salesToken } = await import("./_sales_bot.js");
    const token = await salesToken();
    const now = new Date().toISOString();
    const expired = await dbSelect<OrderRow>("sales_orders", `select=*&status=eq.entregue&access_until=lt.${now}&order=access_until.asc&limit=50`);
    if (!expired.length) return 0;
    const products = new Map((await dbSelect<ProductRow>("sales_products", "select=*")).map((p) => [p.id, p]));
    const channel = await telegramChannel();
    const admin = await telegramConfig();
    let done = 0;
    for (const order of expired) {
      const product = products.get(order.product_id);
      if (!product) continue;
      const monthlyActive = product.billing === "mensal" && !(order.note ?? "").startsWith("Assinatura cancelada");
      if (monthlyActive && Date.parse(order.access_until!) + MONTHLY_GRACE_MS > Date.now()) continue;
      const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${order.fan_id}`);
      if (product.kind === "vip") {
        // renovou (outro VIP ainda vale): so encerra este pedido
        if (!(await activeVip(order.fan_id)) && fan && channel && admin.token) {
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
      } else if (token && fan && !fan.blocked_at) {
        const again = await dbSelect<{ id: string }>("sales_orders", `select=id&fan_id=eq.${order.fan_id}&product_id=eq.${order.product_id}&status=eq.entregue&access_until=gt.${now}&limit=1`);
        if (!again.length) {
          await telegram(token, "sendMessage", {
            chat_id: fan.telegram_id,
            text: `Seu acesso a “${product.title}” terminou (${product.period_days} dias). Quer de novo?`,
            reply_markup: { inline_keyboard: [[{ text: `🔁 Comprar de novo — ${brl(product.price_cents)}`, callback_data: `buy:${product.id}` }]] },
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
