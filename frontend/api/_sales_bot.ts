import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { storeConfigured, storeDelete, storeGet, storeSet } from "./_store.js";
import { dbInsertIgnoringDuplicates, dbSelect, dbUpdateWhere } from "./_supabase.js";
import { sendNotice, telegram, telegramChannel, telegramConfig } from "./_telegram.js";
import { activePacks, baseUrl, cancelSubscription, checkPaid, handleCustomerData, openPack, startPayment } from "./_sales_pay.js";
import { sendWelcome } from "./_sales_cta.js";

// Bot de vendas (+18): o bot que os fas abrem. E outro bot, separado do bot de
// aprovacoes do administrador (que continua so dele). O token fica no Upstash
// (colado em Vendas no estudio) e nunca volta para o navegador.
//
// Webhook /api/vendas/telegram -> /api/studio?r=sales-hook, assinado com um
// segredo derivado do token. So conversas privadas sao atendidas:
//   /start            registra o fa; pede a confirmacao de 18+ antes de tudo
//   18+ confirmado    mostra os pacotes (botoes)
//   botao do pacote   Pix copia-e-cola (PushinPay, _sales_pay.ts) e "Ja paguei";
//                     sem o Pix configurado, avisa o fa e conta o interesse
//                     para o administrador
//   Meus pacotes      pacotes pagos ainda dentro dos 30 dias: abre de novo
//   (/pacotes)
//   /cancelar         cancela o VIP mensal (o acesso vai ate o fim do mes pago)
//   /parar /voltar    desliga e religa as novidades diarias
// O Telegram avisa quando o fa bloqueia o bot (my_chat_member): para de mandar.

const TOKEN_KEY = "luna:sales-token";
const TOKEN_PATTERN = /^\d{5,15}:[A-Za-z0-9_-]{30,60}$/;

export interface ProductRow {
  id: string;
  slug: string;
  kind: "ensaio" | "album" | "vip";
  title: string;
  description: string;
  price_cents: number;
  period_days: number;
  billing: "avulso" | "mensal";
  active: boolean;
  position: number;
}

export interface FanRow {
  id: string;
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  adult_confirmed_at: string | null;
  opted_out_at: string | null;
  blocked_at: string | null;
}

export async function salesToken(): Promise<string | null> {
  if (!storeConfigured()) return null;
  return (await storeGet(TOKEN_KEY).catch(() => null)) ?? null;
}

export function looksLikeSalesToken(token: string): boolean {
  return TOKEN_PATTERN.test(token);
}

export async function saveSalesToken(token: string): Promise<void> {
  await storeSet(TOKEN_KEY, token);
}

export async function forgetSalesToken(): Promise<void> {
  await storeDelete(TOKEN_KEY);
}

export function salesWebhookSecret(token: string): string {
  const key = process.env.TOKEN_ENCRYPTION_KEY || token;
  return createHmac("sha256", key).update(`sales-webhook-v1|${token}`).digest("hex");
}

export async function registerSalesWebhook(token: string, baseUrl: string): Promise<void> {
  await telegram(token, "setWebhook", {
    url: `${baseUrl}/api/vendas/telegram`,
    secret_token: salesWebhookSecret(token),
    allowed_updates: ["message", "callback_query", "my_chat_member"],
  });
  await telegram(token, "setMyCommands", {
    commands: [
      { command: "start", description: "Ver os pacotes" },
      { command: "pacotes", description: "Abrir os pacotes que você comprou" },
      { command: "cancelar", description: "Cancelar o VIP mensal" },
      { command: "parar", description: "Parar de receber novidades" },
      { command: "voltar", description: "Voltar a receber novidades" },
    ],
  }).catch(() => undefined);
}

export function price(cents: number): string {
  return `R$ ${(cents / 100).toFixed(2).replace(".", ",")}`;
}

export function productLabel(p: Pick<ProductRow, "kind" | "title" | "price_cents" | "period_days" | "billing">): string {
  const icon = p.kind === "vip" ? "💎" : p.kind === "album" ? "🔥" : "📸";
  const period = p.billing === "mensal" ? "/mês" : /\bdias?\b/i.test(p.title) ? "" : ` · ${p.period_days} dias`;
  return `${icon} ${p.title} — ${price(p.price_cents)}${period}`;
}

// Pacotes que aparecem para o fa: ativos e com conteudo (o VIP precisa do canal).
export async function productsForSale(): Promise<ProductRow[]> {
  const products = await dbSelect<ProductRow>("sales_products", "select=*&active=eq.true&order=position.asc");
  const media = await dbSelect<{ product_id: string }>("sales_product_media", "select=product_id");
  const withMedia = new Set(media.map((m) => m.product_id));
  // o VIP e o acesso ao canal +18 (Meu perfil -> Canal +18)
  const vipReady = Boolean(await telegramChannel().catch(() => null));
  return products.filter((p) => (p.kind === "vip" ? vipReady : withMedia.has(p.id)));
}

const AGE_TEXT = "Oi! Aqui é a Luna 💛\n\nAqui tem conteúdo só para maiores de 18 anos. Se você tem 18 ou mais, vem comigo curtir os meus melhores momentos 😘";
const MENU_TEXT = "Que bom te ver por aqui 😘\n\nEscolha o seu pacote:";
const MENU_TEXT_NAMED = "Que bom te ver por aqui, {nome} 😘\n\nEscolha o seu pacote:";
const NOTHING_TEXT = "Os pacotes estão sendo preparados. Volte daqui a pouquinho 💛";

function safeSame(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

interface From {
  id: number;
  username?: string;
  first_name?: string;
}

interface Update {
  message?: { message_id: number; chat: { id: number; type: string }; from?: From; text?: string };
  callback_query?: { id: string; from: From; data?: string; message?: { message_id: number; chat: { id: number; type: string } } };
  my_chat_member?: { chat: { id: number; type: string }; from: From; new_chat_member: { status: string } };
}

async function upsertFan(from: From): Promise<FanRow> {
  const now = new Date().toISOString();
  await dbInsertIgnoringDuplicates("fans", [{ telegram_id: from.id, username: from.username ?? null, first_name: from.first_name ?? null }], "telegram_id");
  await dbUpdateWhere("fans", `telegram_id=eq.${from.id}`, {
    username: from.username?.slice(0, 64) ?? null,
    first_name: from.first_name?.slice(0, 128) ?? null,
    last_seen_at: now,
    blocked_at: null,
  });
  const [fan] = await dbSelect<FanRow>("fans", `select=*&telegram_id=eq.${from.id}`);
  return fan;
}

export async function sendMenu(token: string, chatId: number, fan?: FanRow): Promise<void> {
  const products = await productsForSale();
  const mine = fan ? await activePacks(fan.id) : [];
  if (!products.length && !mine.length) {
    await telegram(token, "sendMessage", { chat_id: chatId, text: NOTHING_TEXT });
    return;
  }
  const rows = products.map((p) => [{ text: productLabel(p), callback_data: `buy:${p.id}` }]);
  if (mine.length) rows.push([{ text: "📂 Meus pacotes", callback_data: "mine" }]);
  // com previas cadastradas: a previa da vez + a historia dela (ou o texto do menu) + os botoes
  const { previews, sendCta, fill } = await import("./_sales_cta.js");
  const list = products.length ? await previews() : [];
  if (list.length) {
    const turn = Math.floor(Date.now() / 60_000); // gira a cada minuto
    const own = list[turn % list.length].text;
    await sendCta(token, chatId, fill(own || MENU_TEXT_NAMED, fan ?? { first_name: null }), turn, rows);
    return;
  }
  await telegram(token, "sendMessage", { chat_id: chatId, text: products.length ? MENU_TEXT : "Seus pacotes:", reply_markup: { inline_keyboard: rows } });
}

async function listMine(token: string, fan: FanRow, chatId: number): Promise<void> {
  const mine = await activePacks(fan.id);
  if (!mine.length) {
    await telegram(token, "sendMessage", { chat_id: chatId, text: "Você não tem pacotes ativos agora. Veja os pacotes com /start 💛" });
    return;
  }
  const date = (iso: string) => new Date(iso).toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
  await telegram(token, "sendMessage", {
    chat_id: chatId,
    text: "Seus pacotes (abra de novo quando quiser até a data):",
    reply_markup: { inline_keyboard: mine.map((o) => [{ text: `📂 ${o.title} — até ${date(o.access_until)}`, callback_data: `open:${o.id}` }]) },
  });
}

async function askAge(token: string, chatId: number): Promise<void> {
  await telegram(token, "sendMessage", {
    chat_id: chatId,
    text: AGE_TEXT,
    reply_markup: {
      inline_keyboard: [[{ text: "✅ Tenho 18 anos ou mais", callback_data: "age:yes" }], [{ text: "Sair", callback_data: "age:no" }]],
    },
  });
}

async function adminNotice(text: string): Promise<void> {
  await sendNotice(await telegramConfig(), text).catch(() => undefined);
}

function fanName(f: Pick<FanRow, "username" | "first_name" | "telegram_id">): string {
  return f.username ? `@${f.username}` : f.first_name || `fã ${f.telegram_id}`;
}

async function buy(token: string, fan: FanRow, productId: string, chatId: number, site: string): Promise<void> {
  if (!/^[0-9a-f-]{36}$/i.test(productId)) return;
  const [product] = (await productsForSale()).filter((p) => p.id === productId);
  if (!product) {
    await telegram(token, "sendMessage", { chat_id: chatId, text: "Esse pacote não está mais disponível. Veja os outros:" });
    await sendMenu(token, chatId);
    return;
  }
  if (await startPayment(token, fan, product, chatId, site)) return;
  // Pix ainda nao configurado: avisa e conta o interesse
  await telegram(token, "sendMessage", {
    chat_id: chatId,
    text: `Você escolheu: ${productLabel(product)}\n\nO pagamento por Pix está quase pronto! Assim que liberar, eu te aviso aqui 💛`,
  });
  await adminNotice(`🛒 ${fanName(fan)} quis comprar “${product.title}” (${price(product.price_cents)}), mas o Pix ainda não está ligado.`);
}

// POST do Telegram (webhook do bot de vendas). Sempre 200 para nao repetir.
export default async function salesHook(req: IncomingMessage, res: ServerResponse) {
  const done = (status = 200) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json");
    res.end("{}");
  };
  if (req.method !== "POST") return done(405);
  const token = await salesToken();
  const given = String(req.headers["x-telegram-bot-api-secret-token"] ?? "");
  if (!token || !safeSame(given, salesWebhookSecret(token))) return done(401);
  let update: Update;
  try {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    update = JSON.parse(raw || "{}");
  } catch {
    return done();
  }

  try {
    const member = update.my_chat_member;
    if (member && member.chat.type === "private") {
      const blocked = member.new_chat_member.status === "kicked";
      await dbUpdateWhere("fans", `telegram_id=eq.${member.from.id}`, { blocked_at: blocked ? new Date().toISOString() : null });
      return done();
    }

    const cb = update.callback_query;
    if (cb && cb.message?.chat.type === "private") {
      await telegram(token, "answerCallbackQuery", { callback_query_id: cb.id }).catch(() => undefined);
      const chatId = cb.message.chat.id;
      const fan = await upsertFan(cb.from);
      const [kind, arg] = (cb.data ?? "").split(":");
      if (kind === "age") {
        if (arg === "yes") {
          await dbUpdateWhere("fans", `id=eq.${fan.id}`, { adult_confirmed_at: new Date().toISOString() });
          // boas-vindas com o nome, a previa e os pacotes (sem pacote pronto: o menu avisa)
          if (!(await sendWelcome(token, fan, chatId))) await sendMenu(token, chatId, fan);
        } else {
          await telegram(token, "sendMessage", { chat_id: chatId, text: "Tudo bem! Este bot é só para maiores de 18 anos. Até mais 👋" });
        }
      } else if (!fan.adult_confirmed_at) {
        await askAge(token, chatId);
      } else if (kind === "buy") {
        await buy(token, fan, arg ?? "", chatId, baseUrl(req));
      } else if (kind === "paid") {
        await checkPaid(arg ?? "", fan, token, chatId);
      } else if (kind === "mine") {
        await listMine(token, fan, chatId);
      } else if (kind === "open") {
        await openPack(arg ?? "", fan, token, chatId);
      }
      return done();
    }

    const msg = update.message;
    if (msg && msg.chat.type === "private" && msg.from) {
      const fan = await upsertFan(msg.from);
      const text = (msg.text ?? "").trim().toLowerCase();
      // /zerar: so o administrador; volta a ser um fa novo para testar do inicio
      if (text.startsWith("/zerar") && String(msg.from.id) === (await telegramConfig()).chatId) {
        await dbUpdateWhere("fans", `id=eq.${fan.id}`, { adult_confirmed_at: null, cta_sent_at: null, cta_count: 0, opted_out_at: null });
        await telegram(token, "sendMessage", { chat_id: msg.chat.id, text: "Pronto, você voltou a ser um fã novo 🔄 Mande /start para ver tudo do começo (suas compras de teste continuam registradas)." });
        return done();
      }
      if (!fan.adult_confirmed_at) {
        await askAge(token, msg.chat.id);
      } else if (text.startsWith("/parar")) {
        await dbUpdateWhere("fans", `id=eq.${fan.id}`, { opted_out_at: new Date().toISOString() });
        await telegram(token, "sendMessage", { chat_id: msg.chat.id, text: "Pronto, não mando mais novidades. Suas compras continuam valendo. Para voltar, mande /voltar." });
      } else if (!text.startsWith("/") && (await handleCustomerData(token, fan, msg.text ?? "", msg.chat.id, baseUrl(req)))) {
        // eram o nome e o CPF do VIP mensal
      } else if (text.startsWith("/pacotes")) {
        await listMine(token, fan, msg.chat.id);
      } else if (text.startsWith("/cancelar")) {
        await cancelSubscription(fan, token, msg.chat.id);
      } else if (text.startsWith("/voltar")) {
        await dbUpdateWhere("fans", `id=eq.${fan.id}`, { opted_out_at: null });
        await telegram(token, "sendMessage", { chat_id: msg.chat.id, text: "Que bom que voltou 💛 Você vai receber as novidades de novo." });
      } else {
        await sendMenu(token, msg.chat.id, fan);
      }
    }
  } catch {
    // erro de rede ou do banco: o Telegram nao precisa repetir (o fa toca de novo)
  }
  return done();
}
