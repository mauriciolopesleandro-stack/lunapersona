import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { audit, dbSelect, dbUpdateWhere } from "./_supabase.js";
import { telegram, telegramConfig } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";
import { personaIdentity, personaText } from "./_persona.js";
import type { FanLang } from "./_sales_lang.js";

// Chamada para quem gerou o pagamento e nao pagou (pedido de 22/11/2026, Cleuza
// viralizando com muito fa de fora sem Pix). Uma mensagem por fa, uma vez so:
// previa borrada + texto em portugues e em ingles (a IA escreve 6 versoes no
// jeito da persona) + os botoes do mesmo pacote que ele escolheu: em dolar
// (pagina da NOWPayments) e Pix.
//
// Para quem: pedido pendente (Pix, dolar ou estrelas) dos ultimos 3 dias, fa
// +18 confirmado, sem /parar, sem bloqueio e sem nenhuma compra. O administrador
// nao entra (ele recebe o exemplo).
//
// Fluxo: preparar (estudio "Chamar quem nao pagou" ou nada no Telegram) -> o
// exemplo chega no Telegram do administrador com "Enviar para N fas" -> enviar
// (botao no Telegram ou no estudio) -> o processador de cada minuto manda aos
// poucos (chave por fa: nunca duas vezes) -> no fim, o resultado no Telegram.

const KEY = "luna:chamada";
// 23/11: 7 dias (a chamada do fim de semana alcanca quem tentou durante a semana)
const WINDOW_MS = 7 * 86400_000;
const PER_TICK = 15;
const TICK_BUDGET_MS = 12_000;
const CLAIM_TTL = 14 * 86400;

export interface ChamadaText {
  pt: string;
  en: string;
}

export interface Chamada {
  id: string;
  status: "pronta" | "enviando" | "feita" | "cancelada";
  created_at: string;
  started_at: string | null;
  done_at: string | null;
  texts: ChamadaText[];
  targets: { fan: string; product: string }[];
  next: number;
  sent: number;
  blocked: number;
  failed: number;
  error: string | null;
}

export async function chamada(): Promise<Chamada | null> {
  if (!storeConfigured()) return null;
  try {
    const saved = JSON.parse((await storeGet(KEY)) ?? "null");
    return saved && typeof saved === "object" && Array.isArray(saved.targets) ? (saved as Chamada) : null;
  } catch {
    return null;
  }
}

async function save(c: Chamada): Promise<void> {
  await storeSet(KEY, JSON.stringify(c));
}

// resumo para o estudio (sem os ids dos fas)
export async function chamadaStatus() {
  const c = await chamada();
  if (!c) return null;
  return {
    id: c.id,
    status: c.status,
    created_at: c.created_at,
    started_at: c.started_at,
    done_at: c.done_at,
    total: c.targets.length,
    done: Math.min(c.next, c.targets.length),
    sent: c.sent,
    blocked: c.blocked,
    failed: c.failed,
    error: c.error,
    example: c.texts[0] ?? null,
    texts: c.texts,
  };
}

// --- quem recebe --------------------------------------------------------------------------------

export async function chamadaAudience(adminChat: string | null): Promise<{ fan: FanRow; product: string }[]> {
  const since = new Date(Date.now() - WINDOW_MS).toISOString();
  const orders = await dbSelect<{ fan_id: string; product_id: string }>(
    "sales_orders",
    `select=fan_id,product_id&status=eq.pendente&gateway=in.(pushinpay,nowpayments,estrelas)&created_at=gt.${since}&order=created_at.desc&limit=1000`
  );
  // o pacote do pedido mais recente de cada fa
  const latest = new Map<string, string>();
  for (const o of orders) if (!latest.has(o.fan_id)) latest.set(o.fan_id, o.product_id);
  if (!latest.size) return [];
  const ids = [...latest.keys()];
  const buyers = new Set<string>();
  const fans: FanRow[] = [];
  for (let i = 0; i < ids.length; i += 80) {
    const part = ids.slice(i, i + 80).join(",");
    for (const o of await dbSelect<{ fan_id: string }>("sales_orders", `select=fan_id&status=in.(pago,entregue)&fan_id=in.(${part})`)) buyers.add(o.fan_id);
    fans.push(...(await dbSelect<FanRow>("fans", `select=*&id=in.(${part})&adult_confirmed_at=not.is.null&opted_out_at=is.null&blocked_at=is.null`)));
  }
  return fans
    .filter((f) => !buyers.has(f.id) && String(f.telegram_id) !== String(adminChat ?? ""))
    .map((f) => ({ fan: f, product: latest.get(f.id)! }));
}

// --- textos (IA) --------------------------------------------------------------------------------

const FALLBACK: ChamadaText[] = [
  {
    pt: "Ei {nome}… tu deixou a gente aqui esperando, foi? 😏 Agora dá pra pagar de qualquer lugar: cartão, Apple Pay ou Google Pay aqui mesmo no Telegram, ou cripto. Essa aí borrada é só o aperitivo… o resto tá sem censura te esperando 🔥 Toca aqui embaixo.",
    en: "Hey {nome}… you left me waiting 😏 Now you can pay from anywhere: card, Apple Pay or Google Pay right here in Telegram, or crypto. That blurry pic? Just a taste. The rest is waiting for you, uncensored 🔥 Tap below.",
  },
  {
    pt: "{nome}, vi que tu quase levou… 👀 Agora é só tocar no botão e pagar com cartão aqui no Telegram, sem complicação. Olha só o que tu tá perdendo 😈",
    en: "{nome}, you were so close… 👀 Now just tap the button and pay by card right here in Telegram, quick and easy. Look what you're missing 😈",
  },
];

function chamadaPrompt(promo: string | null): string {
  const who = personaIdentity() ?? "A Luna Vox é uma influenciadora virtual brasileira criada com IA, adulta, carinhosa, confiante e provocante.";
  return personaText(`Você escreve mensagens curtas para o bot de vendas no Telegram (conteúdo adulto, só para quem já disse ter 18 anos).
${who}
Escreva no jeito de falar dela, em primeira pessoa.
Situação: o fã escolheu um pacote, gerou o pagamento e não pagou. Muitos estão fora do Brasil e não tinham como pagar o Pix. Agora dá para pagar de qualquer país: cartão, Apple Pay ou Google Pay aqui mesmo no Telegram (sem sair do app), ou cripto; e o Pix continua para quem está no Brasil.
${promo ? `Hoje tem promoção ("${promo}"): preço especial só hoje. Diga isso em todas as mensagens, sem dizer o preço.\n` : ""}A mensagem vai junto de uma foto dela borrada (prévia) e dos botões de pagamento.
Escreva 6 mensagens diferentes. Cada uma com:
- "pt": português do Brasil, 2 ou 3 frases curtas, safada e provocante, com malícia e duplo sentido, 1 a 3 emojis;
- "en": a mesma ideia em inglês natural (não traduza ao pé da letra), 2 ou 3 frases, 1 a 3 emojis.
Regras:
- use exatamente {nome} onde entra o primeiro nome do fã (com as chaves), nas duas línguas;
- diga que agora dá para pagar de qualquer país, com cartão aqui no Telegram;
- provoque com a foto borrada (é só o começo, o resto está sem censura no pacote);
- termine chamando para tocar no botão;
- sem descrever ato sexual nem partes íntimas (o fã ainda não pagou), sem preço, sem link, sem prometer encontro;
- o fã é homem: fale com ele no masculino (nunca "gostosa", "linda");
- é um convite safado, não uma cobrança: nada de "paga o que deve", "resolve esse negócio", tom de dívida ou bronca;
- português correto e natural, só letras do português e do inglês (sem outros alfabetos), sem gíria inventada, sem palavra em CAIXA ALTA.
Exemplos do tom (não copie, varie):
{"pt": "Ei {nome}… tu deixou a coroa aqui esperando, foi? 😏 Agora dá pra pagar de qualquer lugar, com cartão aqui mesmo no Telegram. Essa foto borrada é só o aperitivo… o resto tá sem censura te esperando 🔥 Toca aqui embaixo.", "en": "Hey {nome}… you left me waiting 😏 Now you can pay from anywhere, by card right here in Telegram. That blurry pic? Just a taste. The rest is waiting for you, uncensored 🔥 Tap below."}
Responda só JSON: {"mensagens": [{"pt": "...", "en": "..."}]}`);
}

// texto que nao vai para o fa: outro alfabeto (a IA misturou arabe em 22/11),
// sem o {nome}, chamando o fa no feminino, tom de divida ou gritando
function badText(t: string): boolean {
  return (
    /[Ͱ-ϿЀ-ӿ֐-ࣿ฀-๿぀-ヿ一-鿿가-힯]/.test(t) ||
    !t.includes("{nome}") ||
    /\b(gostosa|linda|gatinha|princesa)\b/i.test(t) ||
    // preco inventado pela IA (o preco certo esta nos botoes)
    /(R\$|US\$|\$)\s?\d|\d+([.,]\d+)?\s?(reais|d[oó]lares|dollars|estrelas|stars)\b/i.test(t) ||
    /\b(o que (você|tu) deve|resolve (logo )?esse neg[oó]cio|pay what you owe|pay up)\b/i.test(t) ||
    /\b[A-ZÀ-Ú]{5,}\b.*\b[A-ZÀ-Ú]{5,}\b/.test(t)
  );
}

async function askTexts(mode: "normal" | "adulto", promo: string | null): Promise<ChamadaText[]> {
  const { askAi } = await import("./_assistant.js");
  const out = await askAi(
    mode,
    [
      { role: "system", content: chamadaPrompt(promo) },
      { role: "user", content: "Escreva as 6 mensagens." },
    ],
    { json: true, maxTokens: 1800, timeoutMs: 25_000, temperature: 0.85 }
  );
  const raw = out.text.replace(/^```(?:json)?\s*|\s*```$/g, "");
  const parsed = JSON.parse(raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1));
  return (Array.isArray(parsed.mensagens) ? parsed.mensagens : [])
    .map((m: unknown) => {
      const x = m as Record<string, unknown>;
      const pt = typeof x?.pt === "string" ? x.pt.trim().slice(0, 450) : "";
      const en = typeof x?.en === "string" ? x.en.trim().slice(0, 450) : "";
      return pt.length > 10 && en.length > 10 && !badText(pt) && !badText(en) ? { pt, en } : null;
    })
    .filter(Boolean) as ChamadaText[];
}

// 22/11: o modelo sem censura (dolphin) escreveu mal (arabe no meio, "gostosa",
// tom de cobranca); o texto aqui e so provocante, entao vai primeiro o modelo
// normal (melhor portugues) e o sem censura so se ele falhar. O que nao passa
// no filtro sai; sobrando menos de 3, entram os textos prontos.
// promo: nome da promocao de hoje (os textos dizem "so hoje"), ou null
export async function writeChamadaTexts(promo: string | null = null): Promise<ChamadaText[]> {
  for (const mode of ["normal", "adulto"] as const) {
    const list = await askTexts(mode, promo).catch(() => [] as ChamadaText[]);
    if (list.length >= 3) return list.slice(0, 6);
  }
  return FALLBACK;
}

// --- a mensagem ---------------------------------------------------------------------------------

function firstName(fan: Pick<FanRow, "first_name">): string {
  return (fan.first_name ?? "").trim().split(/\s+/)[0] || "";
}

// lang (23/11): "pt" so portugues, "en" so ingles, null (nao se sabe) as duas
export function chamadaCaption(t: ChamadaText, fan: Pick<FanRow, "first_name">, lang: FanLang = null, banner: string | null = null): string {
  const name = firstName(fan);
  const put = (s: string, fallback: string) => s.split("{nome}").join(name || fallback).replace(/^,\s*/, "");
  const body = lang === "en" ? put(t.en, "babe") : lang === "pt" ? put(t.pt, "amor") : `${put(t.pt, "amor")}\n\n🌎 ${put(t.en, "babe")}`;
  return (banner ? `${banner}\n\n${body}` : body).slice(0, 1024);
}

// Botoes do pacote que o fa escolheu, com o preco de hoje (promocao inclusa):
// brasileiro: Pix primeiro; fora do Brasil (ou nao se sabe): cartao (estrelas),
// cripto e o Pix por ultimo
// O pacote que o fa escolheu com o preco de hoje: os botoes e, no dia da
// promocao, a linha do topo da mensagem com o pacote e o preco dela (pedido de
// 23/11: "o pacote X esta na promocao por Y, so hoje"). Fa de fora: preco em
// estrelas; Brasil: em reais; sem saber, as duas linhas.
// fanId: o fa (23/11): se a fatura em estrelas que ele recebeu antes era mais cara
// que o preco de agora, a mensagem diz "agora so ⭐ X (era ⭐ Y)" - os precos em
// estrelas cairam em 23/11 (250/500/1000 -> 155/310/465)
export async function chamadaOffer(productId: string, lang: FanLang, fanId?: string): Promise<{ rows: { text: string; callback_data: string }[][]; line: string | null } | null> {
  const { productsForSale, price } = await import("./_sales_bot.js");
  const { abroadRows } = await import("./_sales_pay.js");
  const forSale = await productsForSale();
  const product = forSale.find((p) => p.id === productId) ?? forSale.find((p) => p.billing !== "mensal");
  if (!product) return null;
  const was = product.regular_cents ? ` (era ${price(product.regular_cents)})` : "";
  const pix = [{ text: `🇧🇷 Pix — ${price(product.price_cents)}${was}`, callback_data: `pix:${product.id}` }];
  const abroad = await abroadRows(product);
  const rows = lang === "pt" ? [pix, ...abroad] : [...abroad, pix];

  const { starsConfig, starsPrice } = await import("./_sales_stars.js");
  const { ctaConfig } = await import("./_sales_cta.js");
  const { titleEn } = await import("./_sales_lang.js");
  const sc = await starsConfig();
  const stars = starsPrice(product, sc);
  const { promoActive, promoConfig } = await import("./_sales_promo.js");
  const promo = await promoConfig();
  if (!promoActive(promo)) {
    // sem promocao: o fa de fora que viu a fatura mais cara fica sabendo que baixou
    if (lang === "pt" || !stars || !fanId) return { rows, line: null };
    const [seen] = await dbSelect<{ amount_cents: number }>(
      "sales_orders",
      `select=amount_cents&fan_id=eq.${fanId}&product_id=eq.${product.id}&gateway=eq.estrelas&order=amount_cents.desc&limit=1`
    ).catch(() => [] as { amount_cents: number }[]);
    if (!seen || seen.amount_cents <= stars) return { rows, line: null };
    return { rows, line: `⭐ New lower price: ${titleEn(product, (await ctaConfig()).titlesEn)} now only ⭐ ${stars} (was ⭐ ${seen.amount_cents}).` };
  }
  const starsWas = starsPrice({ ...product, promo_stars: undefined }, sc);
  const pt = product.regular_cents ? `🔥 ${promo.label}: ${product.title} por só ${price(product.price_cents)} (era ${price(product.regular_cents)}). Só hoje!` : null;
  const en =
    stars && starsWas && stars < starsWas
      ? `🔥 ${promo.labelEn}: ${titleEn(product, (await ctaConfig()).titlesEn)} for only ⭐ ${stars} (was ⭐ ${starsWas}). Today only!`
      : null;
  const line = lang === "en" ? en : lang === "pt" ? pt : [pt, en].filter(Boolean).join("\n") || null;
  return { rows, line };
}

// as 3 previas mais novas, girando por fa
async function previewFor(turn: number) {
  const { previews } = await import("./_sales_cta.js");
  const list = (await previews()).slice(-3);
  return list.length ? list[list.length - 1 - (Math.abs(turn) % list.length)] : null;
}

async function sendOne(token: string, chatId: number, caption: string, rows: { text: string; callback_data: string }[][], turn: number): Promise<void> {
  const preview = await previewFor(turn);
  const markup = { reply_markup: { inline_keyboard: rows } };
  if (!preview) {
    await telegram(token, "sendMessage", { chat_id: chatId, text: caption, ...markup });
    return;
  }
  await telegram(token, preview.kind === "video" ? "sendVideo" : "sendPhoto", {
    chat_id: chatId,
    [preview.kind === "video" ? "video" : "photo"]: preview.file_id,
    caption,
    protect_content: true,
    ...markup,
  });
}

// --- preparar, enviar, cancelar ----------------------------------------------------------------

async function adminChatId(): Promise<string | null> {
  return (await telegramConfig().catch(() => null))?.chatId ?? null;
}

// Monta a chamada (publico + textos) e manda o exemplo ao administrador pelo bot
// de vendas, com "Enviar para N fas". Nao manda nada para os fas.
export async function prepareChamada(token: string): Promise<Chamada> {
  const current = await chamada();
  if (current?.status === "enviando") throw new Error("Já tem uma chamada sendo enviada. Espere terminar.");
  const admin = await adminChatId();
  if (!admin) throw new Error("Conecte primeiro o seu bot de aprovações em Meu perfil.");
  const audience = await chamadaAudience(admin);
  // dia de promocao: os textos dizem "so hoje" e cada mensagem leva a faixa 🔥
  const { promoActive, promoConfig } = await import("./_sales_promo.js");
  const promo = await promoConfig();
  const texts = await writeChamadaTexts(promoActive(promo) ? promo.label : null);
  const c: Chamada = {
    id: Date.now().toString(36),
    status: "pronta",
    created_at: new Date().toISOString(),
    started_at: null,
    done_at: null,
    texts,
    targets: audience.map((a) => ({ fan: a.fan.id, product: a.product })),
    next: 0,
    sent: 0,
    blocked: 0,
    failed: 0,
    error: null,
  };
  await save(c);
  await audit("chamada_preparada", "sales", null, { fas: c.targets.length });
  // o exemplo, como o fa de fora recebe (com o nome do administrador; as duas linguas para ele conferir)
  const { promoBanner } = await import("./_sales_promo.js");
  const [me] = await dbSelect<FanRow>("fans", `select=*&telegram_id=eq.${admin}`);
  const offer = await chamadaOffer(c.targets[0]?.product ?? "", null, c.targets[0]?.fan);
  if (offer) await sendOne(token, Number(admin), chamadaCaption(texts[0], me ?? { first_name: null }, null, offer.line ?? (await promoBanner(null))), offer.rows, 0).catch(() => undefined);
  const others = texts
    .slice(1)
    .map((t, i) => `${i + 2}. ${t.pt}`)
    .join("\n\n");
  const { fanLang } = await import("./_sales_lang.js");
  const langs = await Promise.all(audience.map((a) => fanLang(a.fan)));
  const abroad = langs.filter((l) => l === "en").length;
  const br = langs.filter((l) => l === "pt").length;
  await telegram(token, "sendMessage", {
    chat_id: Number(admin),
    text:
      `📣 Chamada para quem gerou o pagamento e não pagou (últimos 7 dias, +18, sem bloqueio e sem compra): ${c.targets.length} fã(s)` +
      ` (${abroad} de fora: só em inglês · ${br} do Brasil: só em português · ${c.targets.length - abroad - br} sem saber: as duas línguas).\n` +
      `Cada um recebe uma vez, com uma das ${texts.length} mensagens (a 1ª está acima), uma das prévias borradas e os botões do pacote que escolheu (cartão, cripto e Pix${promoActive(promo) ? `, com o preço da ${promo.label}` : ""}).` +
      (others ? `\n\nAs outras:\n\n${others}` : ""),
    ...(c.targets.length
      ? { reply_markup: { inline_keyboard: [[{ text: `✅ Enviar para ${c.targets.length} fã(s)`, callback_data: `chamadaok:${c.id}` }], [{ text: "❌ Cancelar", callback_data: `chamadano:${c.id}` }]] } }
      : {}),
  }).catch(() => undefined);
  return c;
}

export async function startChamada(id: string): Promise<Chamada> {
  const c = await chamada();
  if (!c || c.id !== id) throw new Error("Essa chamada não existe mais. Prepare de novo.");
  if (c.status === "enviando" || c.status === "feita") return c;
  if (c.status === "cancelada") throw new Error("Essa chamada foi cancelada. Prepare de novo.");
  if (!c.targets.length) throw new Error("Ninguém para chamar agora.");
  c.status = "enviando";
  c.started_at = new Date().toISOString();
  await save(c);
  await audit("chamada_enviar", "sales", null, { fas: c.targets.length });
  return c;
}

export async function cancelChamada(id: string): Promise<Chamada | null> {
  const c = await chamada();
  if (!c || c.id !== id || c.status === "feita") return c;
  c.status = "cancelada";
  await save(c);
  await audit("chamada_cancelada", "sales", null, { enviadas: c.sent });
  return c;
}

// --- aviso do dia da promocao (23/11) -----------------------------------------------------------
// No 1o dia de cada promocao (ex.: sabado, num sabado+domingo), a partir das 11h,
// o administrador recebe no Telegram quantos fas tentaram pagar e nao pagaram,
// com "Preparar a chamada com a promoção". Nada vai para os fas sem ele tocar.
const REMINDER_HOUR = 11;

export async function promoReminderTick(token: string): Promise<void> {
  const { promoActive, promoConfig } = await import("./_sales_promo.js");
  const promo = await promoConfig();
  const now = new Date();
  if (!promoActive(promo, now)) return;
  const yesterday = new Date(now.getTime() - 86400_000);
  if (promoActive(promo, yesterday)) return; // so no 1o dia
  const hour = Number(now.toLocaleString("en-US", { timeZone: "America/Sao_Paulo", hour: "numeric", hour12: false })) % 24;
  if (hour < REMINDER_HOUR) return;
  const day = now.toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
  if (!(await storeSetIfAbsent(`luna:chamada-lembrete:${day}`, "1", 3 * 86400).catch(() => false))) return;
  const admin = await adminChatId();
  if (!admin) return;
  const current = await chamada();
  if (current?.status === "enviando") return;
  const audience = await chamadaAudience(admin);
  if (!audience.length) return;
  await telegram(token, "sendMessage", {
    chat_id: Number(admin),
    text:
      `🔥 Hoje começa a ${promo.label}. ${audience.length} fã(s) tentaram pagar nos últimos 7 dias e não pagaram.\n\n` +
      "Quer mandar para eles a chamada com o preço da promoção (cartão, cripto e Pix, na língua de cada um)? Eu preparo e te mostro o exemplo antes de enviar.",
    reply_markup: { inline_keyboard: [[{ text: "📣 Preparar a chamada com a promoção", callback_data: "chamadaprep:1" }]] },
  }).catch(() => undefined);
}

// botoes do administrador no Telegram (chamadaprep / chamadaok / chamadano)
export async function chamadaCallback(token: string, chatId: number, kind: string, id: string): Promise<void> {
  try {
    if (kind === "chamadaprep") {
      await telegram(token, "sendMessage", { chat_id: chatId, text: "✍️ Preparando (a IA está escrevendo as mensagens)…" }).catch(() => undefined);
      await prepareChamada(token);
      return;
    }
    if (kind === "chamadano") {
      const c = await cancelChamada(id);
      await telegram(token, "sendMessage", { chat_id: chatId, text: c?.status === "cancelada" ? `❌ Chamada cancelada${c.sent ? ` (${c.sent} já tinham recebido)` : ""}.` : "Essa chamada já terminou." });
      return;
    }
    const c = await startChamada(id);
    await telegram(token, "sendMessage", { chat_id: chatId, text: `🚀 Enviando para ${c.targets.length} fã(s), aos poucos (uns ${PER_TICK} por minuto). Te aviso aqui quando terminar.` });
  } catch (e) {
    await telegram(token, "sendMessage", { chat_id: chatId, text: `⚠️ ${e instanceof Error ? e.message : String(e)}` }).catch(() => undefined);
  }
}

// --- envio (processador de cada minuto) --------------------------------------------------------

export async function chamadaTick(token: string): Promise<number> {
  const c = await chamada();
  if (!c || c.status !== "enviando") return 0;
  const began = Date.now();
  let sent = 0;
  let handled = 0;
  while (c.next < c.targets.length && handled < PER_TICK && Date.now() - began < TICK_BUDGET_MS) {
    const target = c.targets[c.next];
    const claim = `luna:chamada:${c.id}:${target.fan}`;
    if (!(await storeSetIfAbsent(claim, "1", CLAIM_TTL).catch(() => false))) {
      c.next++; // outro minuto ja cuidou deste fa
      continue;
    }
    handled++;
    const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${target.fan}`);
    // bloqueou, pediu /parar ou comprou depois de preparada: pula
    const paid = fan ? await dbSelect<{ id: string }>("sales_orders", `select=id&fan_id=eq.${fan.id}&status=in.(pago,entregue)&limit=1`) : [];
    if (!fan || fan.blocked_at || fan.opted_out_at || paid.length) {
      c.next++;
      continue;
    }
    try {
      const { fanLang } = await import("./_sales_lang.js");
      const { promoBanner } = await import("./_sales_promo.js");
      const lang = await fanLang(fan);
      const offer = await chamadaOffer(target.product, lang, fan.id);
      if (!offer) throw new Error("nenhum pacote à venda");
      // promocao: "o pacote X por Y, so hoje" no topo (sem preco de promocao no pacote, a faixa geral)
      await sendOne(token, fan.telegram_id, chamadaCaption(c.texts[c.next % c.texts.length], fan, lang, offer.line ?? (await promoBanner(lang))), offer.rows, c.next);
      c.sent++;
      sent++;
      c.next++;
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (/too many requests|retry after/i.test(message)) {
        // o Telegram pediu calma: tenta este fa de novo no proximo minuto
        await storeDelete(claim).catch(() => undefined);
        break;
      }
      if (/blocked|deactivated|chat not found/i.test(message)) {
        await dbUpdateWhere("fans", `id=eq.${fan.id}`, { blocked_at: new Date().toISOString() }).catch(() => undefined);
        c.blocked++;
      } else {
        c.failed++;
        c.error = message.slice(0, 200);
      }
      c.next++;
    }
  }
  // cancelada no meio do envio: nao sobrescreve o status
  const latest = await chamada();
  if (latest && latest.id === c.id && latest.status === "cancelada") c.status = "cancelada";
  if (c.status === "enviando" && c.next >= c.targets.length) {
    c.status = "feita";
    c.done_at = new Date().toISOString();
    const admin = await adminChatId();
    if (admin) {
      await telegram(token, "sendMessage", {
        chat_id: Number(admin),
        text: `✅ Chamada enviada: ${c.sent} fã(s) receberam${c.blocked ? `, ${c.blocked} tinham bloqueado o bot` : ""}${c.failed ? `, ${c.failed} falharam (${c.error ?? "erro"})` : ""}. As vendas que vierem dela chegam aqui como sempre 💰`,
      }).catch(() => undefined);
    }
    await audit("chamada_feita", "sales", null, { enviadas: c.sent, bloqueados: c.blocked, falhas: c.failed });
  }
  await save(c);
  return sent;
}
