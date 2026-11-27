import type { IncomingMessage, ServerResponse } from "http";
import { promoConfig, withPromo, type SaleProduct } from "./_sales_promo.js";
import { createHmac, timingSafeEqual } from "crypto";
import { storeConfigured, storeDelete, storeGet, storeSet } from "./_store.js";
import { dbInsertIgnoringDuplicates, dbSelect, dbUpdateWhere } from "./_supabase.js";
import { sendNotice, telegram, telegramChannel, telegramConfig } from "./_telegram.js";
import { activePacks, baseUrl, cancelSubscription, checkPaid, handleCustomerData, openPack, previewPack, startPayment } from "./_sales_pay.js";
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
//   /ver              so o administrador: escolhe um pacote e recebe igual ao fa
// O Telegram avisa quando o fa bloqueia o bot (my_chat_member): para de mandar.

import { personaPath, personaText } from "./_persona.js";
import { fanAbroad, fanLang, langOf, packLabels, rememberLang, setAbroad, titleEn, type FanLang } from "./_sales_lang.js";

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
    url: `${baseUrl}/api/vendas/telegram${personaPath()}`,
    secret_token: salesWebhookSecret(token),
    allowed_updates: ["message", "callback_query", "my_chat_member", "pre_checkout_query"],
  });
  await telegram(token, "setMyCommands", {
    commands: [
      { command: "start", description: "Ver os pacotes" },
      { command: "pacotes", description: "Abrir os pacotes que você comprou" },
      { command: "cancelar", description: "Cancelar o VIP mensal" },
      { command: "parar", description: "Parar de receber novidades" },
      { command: "voltar", description: "Voltar a receber novidades" },
      { command: "paysupport", description: "Ajuda com pagamento / Payment support" },
    ],
  }).catch(() => undefined);
}

export function price(cents: number): string {
  return `R$ ${(cents / 100).toFixed(2).replace(".", ",")}`;
}

export function productLabel(p: Pick<ProductRow, "kind" | "title" | "price_cents" | "period_days" | "billing"> & { regular_cents?: number }): string {
  const icon = p.kind === "vip" ? "💎" : p.kind === "album" ? "🔥" : "📸";
  const period = p.billing === "mensal" ? "/mês" : /\bdias?\b/i.test(p.title) ? "" : ` · ${p.period_days} dias`;
  // promocao: o preco de hoje e o normal ("era")
  const cost = p.regular_cents ? `${price(p.price_cents)} (era ${price(p.regular_cents)})` : price(p.price_cents);
  return `${icon} ${p.title} — ${cost}${period}`;
}

// Pacotes que aparecem para o fa: ativos e com conteudo (o VIP precisa do canal),
// ja com o preco da promocao quando ela vale hoje.
export async function productsForSale(): Promise<SaleProduct[]> {
  const products = await dbSelect<ProductRow>("sales_products", "select=*&active=eq.true&order=position.asc");
  const media = await dbSelect<{ product_id: string }>("sales_product_media", "select=product_id");
  const withMedia = new Set(media.map((m) => m.product_id));
  // o VIP e o acesso ao canal +18 (Meu perfil -> Canal +18)
  const vipReady = Boolean(await telegramChannel().catch(() => null));
  const promo = await promoConfig();
  return products.filter((p) => (p.kind === "vip" ? vipReady : withMedia.has(p.id))).map((p) => withPromo(p, promo));
}

const AGE_TEXT = "Oi! Aqui é a Luna 💛\n\nAqui tem conteúdo só para maiores de 18 anos. Se você tem 18 ou mais, vem comigo curtir os meus melhores momentos 😘";
// 23/11: a confirmacao da idade ja pergunta se o fa e do Brasil ou de fora (lingua e pagamento)
const AGE_TEXT_EN = "Hi! It's Luna 💛\n\nThis is 18+ content only. If you're 18 or older, come enjoy my best moments with me 😘";
const AGE_ASK = "Toque no botão: / Tap the button:";
const MENU_TEXT = "Que bom te ver por aqui 😘\n\nEscolha o seu pacote:";
const MENU_TEXT_NAMED = "Que bom te ver por aqui, {nome} 😘\n\nEscolha o seu pacote:";
const MENU_TEXT_EN = "So good to see you here 😘\n\nPick your package:";
const MENU_TEXT_NAMED_EN = "So good to see you here, {nome} 😘\n\nPick your package:";
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
  language_code?: string; // lingua do Telegram do fa (fora de "pt": estrelas primeiro)
}

interface Update {
  message?: {
    message_id: number;
    chat: { id: number; type: string };
    from?: From;
    text?: string;
    // 24/11: o administrador manda o video da chamada direto para o bot
    video?: { file_id: string };
    animation?: { file_id: string };
    document?: { file_id: string; mime_type?: string };
    successful_payment?: { currency: string; total_amount: number; invoice_payload: string; telegram_payment_charge_id: string };
  };
  pre_checkout_query?: { id: string; from: From; currency: string; total_amount: number; invoice_payload: string };
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
  // lingua do Telegram do fa, para as mensagens que o bot manda sozinho (_sales_lang.ts)
  if (fan && from.language_code) await rememberLang(fan.id, from.language_code);
  return fan;
}

// lang: lingua do menu (sem ela, a guardada do fa; "en" = nomes em ingles e preco em estrelas/dolar)
export async function sendMenu(token: string, chatId: number, fan?: FanRow, lang?: FanLang): Promise<void> {
  const products = await productsForSale();
  const mine = fan ? await activePacks(fan.id) : [];
  if (!products.length && !mine.length) {
    await telegram(token, "sendMessage", { chat_id: chatId, text: NOTHING_TEXT });
    return;
  }
  const shown: FanLang = (lang === undefined ? (fan ? await fanLang(fan) : "pt") : lang) === "en" ? "en" : "pt";
  const labels = await packLabels(products, shown);
  const rows = products.map((p, i) => [{ text: labels[i], callback_data: `buy:${p.id}` }]);
  if (mine.length) rows.push([{ text: shown === "en" ? "📂 My packages" : "📂 Meus pacotes", callback_data: "mine" }]);
  // com previas cadastradas: a previa da vez + a historia dela (ou o texto do menu) + os botoes
  const { previews, sendCta, fill } = await import("./_sales_cta.js");
  const list = products.length ? await previews() : [];
  if (list.length) {
    const turn = Math.floor(Date.now() / 60_000); // gira a cada minuto
    // a historia da previa e em portugues: o fa de fora recebe o texto do menu em ingles
    const own = shown === "en" ? undefined : list[turn % list.length].text;
    await sendCta(token, chatId, fill(own || (shown === "en" ? MENU_TEXT_NAMED_EN : MENU_TEXT_NAMED), fan ?? { first_name: null }, {}, shown), turn, rows, shown);
    return;
  }
  const { promoBanner } = await import("./_sales_promo.js");
  const banner = products.length ? await promoBanner(shown) : null;
  const text = products.length ? (shown === "en" ? MENU_TEXT_EN : MENU_TEXT) : shown === "en" ? "Your packages:" : "Seus pacotes:";
  await telegram(token, "sendMessage", { chat_id: chatId, text: banner ? `${banner}\n\n${text}` : text, reply_markup: { inline_keyboard: rows } });
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

// Idade + pais num toque so: "Brasil" recebe tudo em portugues e o Pix; "fora do
// Brasil" recebe em ingles, com o cartao (estrelas) e a cripto. O texto vem na
// lingua do Telegram (portugues primeiro para quem esta em portugues).
async function askAge(token: string, chatId: number, code?: string): Promise<void> {
  const foreign = langOf(code) === "en";
  const br = [{ text: "🇧🇷 Tenho 18+ e sou do Brasil", callback_data: "age:br" }];
  const abroad = [{ text: "🌎 I'm 18+ and outside Brazil", callback_data: "age:int" }];
  const text = foreign ? `${AGE_TEXT_EN}\n\n🇧🇷 ${AGE_TEXT}` : `${AGE_TEXT}\n\n🌎 ${AGE_TEXT_EN}`;
  await telegram(token, "sendMessage", {
    chat_id: chatId,
    text: personaText(`${text}\n\n${AGE_ASK}`),
    reply_markup: {
      inline_keyboard: [...(foreign ? [abroad, br] : [br, abroad]), [{ text: foreign ? "Leave / Sair" : "Sair / Leave", callback_data: "age:no" }]],
    },
  });
}

async function adminNotice(text: string): Promise<void> {
  await sendNotice(await telegramConfig(), text).catch(() => undefined);
}

function fanName(f: Pick<FanRow, "username" | "first_name" | "telegram_id">): string {
  return f.username ? `@${f.username}` : f.first_name || `fã ${f.telegram_id}`;
}

// how: "auto" (pela lingua do fa), "pix" (botao Pix) ou "estrelas" (botao das estrelas)
async function buy(token: string, fan: FanRow, productId: string, chatId: number, site: string, lang?: string, how: "auto" | "pix" | "estrelas" | "intl" = "auto"): Promise<void> {
  if (!/^[0-9a-f-]{36}$/i.test(productId)) return;
  const [sold] = (await productsForSale()).filter((p) => p.id === productId);
  if (!sold) {
    await telegram(token, "sendMessage", { chat_id: chatId, text: "Esse pacote não está mais disponível. Veja os outros:" });
    await sendMenu(token, chatId, fan);
    return;
  }
  const { isForeign, sendStarsInvoice } = await import("./_sales_stars.js");
  const { startIntl } = await import("./_sales_intl.js");
  const { abroadRows } = await import("./_sales_pay.js");
  // escolheu o pais na confirmacao da idade (23/11): vale mais que a lingua do Telegram
  const foreign = (await fanAbroad(fan.id)) ?? isForeign(lang);
  // fora do Brasil: o nome do pacote em ingles na fatura e na pagina da cripto
  const { ctaConfig } = await import("./_sales_cta.js");
  const product = foreign ? { ...sold, title: titleEn(sold, (await ctaConfig()).titlesEn) } : sold;
  const pixRow = [{ text: "🇧🇷 Pix (Brasil)", callback_data: `pix:${product.id}` }];
  // fora do Brasil (Telegram em outra lingua): primeiro o cartao (fatura em
  // estrelas: cartao, Apple Pay ou Google Pay no Telegram), com cripto e Pix de
  // alternativa; sem estrelas, a pagina em dolar (cripto)
  // 27/11: quem e de fora nao ve o Pix (nao mistura); antes do pagamento, foto com chamada
  void pixRow;
  const others = foreign ? await abroadRows(product, "estrelas") : undefined;
  const { sendStepPhoto } = await import("./_sales_card.js");
  await sendStepPhoto(token, chatId, fan.first_name, foreign).catch(() => undefined);
  if (how === "estrelas" || (how === "auto" && foreign)) {
    if (await sendStarsInvoice(token, fan, product, chatId, foreign, others).catch(() => false)) return;
  }
  if (how === "intl" || (how === "auto" && foreign)) {
    if (await startIntl(token, fan, product, chatId, site, await abroadRows(product, "intl")).catch(() => false)) return;
    // cripto fora do ar: o fa de fora que tocou nela recebe o cartao (estrelas)
    if (how === "intl" && foreign && (await sendStarsInvoice(token, fan, product, chatId, foreign, others).catch(() => false))) return;
  }
  if (await startPayment(token, fan, product, chatId, site)) return;
  // Pix desligado: ainda da para pagar de fora do Brasil
  if (how !== "intl" && (await startIntl(token, fan, product, chatId, site, await abroadRows(product, "intl")).catch(() => false))) return;
  if (how !== "estrelas" && (await sendStarsInvoice(token, fan, product, chatId, foreign).catch(() => false))) return;
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
    // estrelas: o Telegram pergunta antes de cobrar (responder em ate 10 s)
    if (update.pre_checkout_query) {
      const { answerPreCheckout } = await import("./_sales_stars.js");
      await answerPreCheckout(token, update.pre_checkout_query);
      return done();
    }
    // estrelas: cobrou -> marca pago e entrega
    if (update.message?.successful_payment && update.message.chat.type === "private") {
      const { starsPaid } = await import("./_sales_stars.js");
      await starsPaid(token, update.message.successful_payment);
      return done();
    }

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
      // /ver do administrador: o pacote chega como o fa recebe
      if (kind === "ver" && String(cb.from.id) === (await telegramConfig()).chatId) {
        await previewPack(arg ?? "", token, chatId).catch((e) =>
          telegram(token, "sendMessage", { chat_id: chatId, text: `⚠️ Não consegui mostrar: ${e instanceof Error ? e.message : String(e)}` })
        );
        return done();
      }
      // chamada para quem nao pagou: "Enviar" / "Cancelar" do administrador
      // video mandado pelo administrador: "usar como video da chamada"
      if (kind === "vidcall" && String(cb.from.id) === (await telegramConfig()).chatId) {
        const { confirmPendingVideo, sendWithCallVideo, DEFAULT_BROADCAST } = await import("./_sales_video.js");
        if (arg !== "ok") {
          await telegram(token, "sendMessage", { chat_id: chatId, text: "Tudo bem, não mudei nada." });
          return done();
        }
        const video = await confirmPendingVideo();
        if (!video) {
          await telegram(token, "sendMessage", { chat_id: chatId, text: "⚠️ Não achei o vídeo. Mande de novo." });
          return done();
        }
        const { buyButtonsFor, fill, promoLine } = await import("./_sales_cta.js");
        await telegram(token, "sendMessage", { chat_id: chatId, text: "🎬 Pronto! Esse vídeo agora vai na boas-vindas de todo fã novo. Assim o fã recebe a chamada para todos (o envio para todos você liga no estúdio, em Vendas → Vídeo da chamada):" });
        await sendWithCallVideo(token, chatId, video, await promoLine(fill(DEFAULT_BROADCAST.pt, fan, {}, "pt"), "pt"), await buyButtonsFor("pt"));
        return done();
      }
      if ((kind === "chamadaok" || kind === "chamadano" || kind === "chamadaprep") && String(cb.from.id) === (await telegramConfig()).chatId) {
        const { chamadaCallback } = await import("./_sales_chamada.js");
        await chamadaCallback(token, chatId, kind, arg ?? "");
        return done();
      }
      if (kind === "age") {
        if (arg === "yes" || arg === "br" || arg === "int") {
          await dbUpdateWhere("fans", `id=eq.${fan.id}`, { adult_confirmed_at: new Date().toISOString() });
          // br / int: o fa disse onde mora (lingua e pagamento); "yes" (botao antigo): lingua do Telegram
          if (arg !== "yes") await setAbroad(fan.id, arg === "int");
          const lang: FanLang = arg === "int" ? "en" : arg === "br" ? "pt" : langOf(cb.from.language_code) ?? "pt";
          // boas-vindas com o nome, a previa e os pacotes (sem pacote pronto: o menu avisa)
          if (!(await sendWelcome(token, fan, chatId, lang))) await sendMenu(token, chatId, fan, lang);
        } else {
          const en = langOf(cb.from.language_code) === "en";
          await telegram(token, "sendMessage", { chat_id: chatId, text: en ? "All right! This bot is for adults (18+) only. Bye 👋" : "Tudo bem! Este bot é só para maiores de 18 anos. Até mais 👋" });
        }
      } else if (!fan.adult_confirmed_at) {
        await askAge(token, chatId, cb.from.language_code);
      } else if (kind === "buy") {
        await buy(token, fan, arg ?? "", chatId, baseUrl(req), cb.from.language_code);
      } else if (kind === "stars" || kind === "pix" || kind === "intl") {
        await buy(token, fan, arg ?? "", chatId, baseUrl(req), cb.from.language_code, kind === "stars" ? "estrelas" : kind === "intl" ? "intl" : "pix");
      } else if (kind === "intlpaid") {
        const { checkIntlPaid } = await import("./_sales_intl.js");
        await checkIntlPaid(arg ?? "", fan, token, chatId);
      } else if (kind === "paid") {
        await checkPaid(arg ?? "", fan, token, chatId);
      } else if (kind === "menu") {
        await sendMenu(token, chatId, fan);
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
        const { forgetFanLang } = await import("./_sales_lang.js");
        await forgetFanLang(fan.id);
        await telegram(token, "sendMessage", { chat_id: msg.chat.id, text: "Pronto, você voltou a ser um fã novo 🔄 Mande /start para ver tudo do começo (suas compras de teste continuam registradas)." });
        return done();
      }
      // video mandado pelo administrador: pergunta se vira o video da chamada
      if ((msg.video || msg.animation || msg.document?.mime_type?.startsWith("video/")) && String(msg.from.id) === (await telegramConfig()).chatId) {
        const { fileFromMessage, holdPendingVideo } = await import("./_sales_video.js");
        const file = fileFromMessage(msg, "enviado ao bot");
        if (file) {
          await holdPendingVideo(file);
          await telegram(token, "sendMessage", {
            chat_id: msg.chat.id,
            text: "🎬 Quer usar esse vídeo na chamada? Ele vai na boas-vindas de todo fã novo (e no envio para todos, quando você ligar).",
            reply_markup: { inline_keyboard: [[{ text: "🎬 Usar na chamada", callback_data: "vidcall:ok" }], [{ text: "Não", callback_data: "vidcall:no" }]] },
          });
          return done();
        }
      }
      // /teste1: so o administrador; fatura de 1 estrela do 1o pacote (compra de verdade, para testar)
      if (text.startsWith("/teste1") && String(msg.from.id) === (await telegramConfig()).chatId) {
        const { sendStarsTest } = await import("./_sales_stars.js");
        await sendStarsTest(token, fan, msg.chat.id)
          .then((title) => telegram(token, "sendMessage", { chat_id: msg.chat.id, text: `🧪 Teste: “${title}” por ⭐ 1. Toque em Pagar acima; se precisar, o Telegram oferece comprar estrelas. O pacote deve chegar aqui na hora.` }))
          .catch((e) => telegram(token, "sendMessage", { chat_id: msg.chat.id, text: `⚠️ ${e instanceof Error ? e.message : String(e)}` }));
        return done();
      }
      // /ver: so o administrador; escolhe o pacote e recebe igual ao fa
      if (text.startsWith("/ver") && String(msg.from.id) === (await telegramConfig()).chatId) {
        const packs = (await dbSelect<ProductRow>("sales_products", "select=*&active=eq.true&order=position.asc")).filter((p) => p.kind !== "vip");
        await telegram(token, "sendMessage", {
          chat_id: msg.chat.id,
          text: packs.length ? "👀 Qual pacote você quer ver como o fã recebe?" : "Nenhum pacote ativo.",
          ...(packs.length ? { reply_markup: { inline_keyboard: packs.map((p) => [{ text: `👀 ${p.title}`, callback_data: `ver:${p.id}` }]) } } : {}),
        });
        return done();
      }
      // ajuda com pagamento (o Telegram pede /paysupport em bots que cobram em estrelas)
      if (text.startsWith("/paysupport") || text.startsWith("/terms")) {
        const said = (msg.text ?? "").replace(/^\/\w+(@\w+)?\s*/, "").slice(0, 300);
        // tentou pagar e nao conseguiu: guia de como pagar + botoes do pacote
        if (text.startsWith("/paysupport")) {
          const { attemptsSummary, payAttempts, sendPayHelp, shouldNotifyHelp } = await import("./_sales_help.js");
          const { tried, paid } = await payAttempts(fan);
          if (tried.length && !paid) {
            await sendPayHelp(token, msg.chat.id, tried);
            if (said || (await shouldNotifyHelp(fan))) {
              await adminNotice(`🆘 ${fanName(fan)} pediu ajuda com pagamento${said ? `: “${said}”` : ""}. Ele ${attemptsSummary(tried)} e não pagou; já mandei o guia (estrelas com cartão, cripto em USDT/Litecoin).`);
            }
            return done();
          }
        }
        await telegram(token, "sendMessage", {
          chat_id: msg.chat.id,
          text: text.startsWith("/terms")
            ? "Conteúdo digital só para maiores de 18 anos, entregue aqui no chat logo após o pagamento, com acesso pelo prazo do pacote. Pagamentos em estrelas seguem os termos do Telegram.\n\nDigital content for adults (18+) only, delivered in this chat right after payment, with access for the package period. Star payments follow Telegram's terms."
            : "Pagou e não recebeu? Toque em /pacotes para abrir o que você comprou. Se ainda tiver problema, mande /paysupport e o que aconteceu (ex.: /paysupport paguei e não recebi) que eu verifico 💛\n\nPaid and didn't get it? Tap /pacotes to open what you bought. Still a problem? Send /paysupport with what happened (e.g. /paysupport I paid but got nothing) and I will check it 💛",
        });
        if (text.startsWith("/paysupport") && (said || (await (await import("./_sales_help.js")).shouldNotifyHelp(fan)))) await adminNotice(`🆘 ${fanName(fan)} pediu ajuda com pagamento${said ? `: “${said}”` : ""}. Confira em Vendas (pedidos) ou em @BotFather → Balance (estrelas).`);
        return done();
      }
      if (!fan.adult_confirmed_at) {
        await askAge(token, msg.chat.id, msg.from.language_code);
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
