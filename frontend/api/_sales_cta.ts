import { storeConfigured, storeGet, storeIncr, storeIncrWithTtl, storeSet, storeSetIfAbsent } from "./_store.js";
import { dbSelect, dbUpdate, dbUpdateWhere } from "./_supabase.js";
import { telegram } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";
import { personaIdentity, personaText } from "./_persona.js";
import { fanLang, type FanLang } from "./_sales_lang.js";

// Mensagens que chamam o fa para comprar (CTA) no bot de vendas, sempre com o
// nome dele ({nome}), uma previa (foto com as partes intimas cobertas, escolhida
// pelo administrador na aba Telegram -> Previas) e os botoes dos pacotes:
//   boas-vindas    logo depois de confirmar os 18 anos
//   Pix esquecido  20 min depois de gerar o Pix sem pagar (uma vez)
//   diaria         nos horarios escolhidos (padrao 12h e 19h), para quem nao e VIP
//                  (para com /parar, bloqueio ou VIP ativo; o administrador recebe
//                  mesmo com VIP, desde 21/11); cada fa recebe os
//                  modelos em sequencia (1, 2, ... o ultimo e volta ao 1), a
//                  previa pode repetir
//   renovacao      VIP perto de vencer (3 dias antes) e vencido (3 dias de folga
//                  no canal): uma por dia, com previa e "Renovar agora" (_sales_pay.ts)
// Os textos ficam no Upstash (luna:cta) e o administrador edita em Vendas. Nas
// mensagens gratis o texto e provocante, sem descricao explicita: quem recebe
// ainda nao passou por verificacao de idade alem do "tenho 18".

const CONFIG_KEY = "luna:cta";
const PREVIEWS_KEY = "luna:sales-previews";
const PIX_REMINDER_MS = 20 * 60_000;
const MIN_GAP_MS = 3 * 3600_000; // boas-vindas ou diaria ha menos de 3 h: espera o proximo horario
const BATCH = 25; // fas por minuto (o processador roda a cada minuto)
// 21/11 (Cleuza viralizando): o Pix pago nunca depende so do aviso da PushinPay.
// A cada minuto confere na PushinPay os Pix pendentes das ultimas horas (cada
// pedido no maximo a cada 3 min) e, se pagou, entrega na hora.
const PIX_SWEEP_WINDOW_MS = 6 * 3600_000;
const PIX_SWEEP_EVERY_S = 180;
const PIX_SWEEP_MAX = 8;
// a diaria para de mandar no minuto quando passa disto (a funcao tem 60 s para
// todas as personas e o resto do processador); quem ficou recebe no minuto seguinte
const CTA_BUDGET_MS = 20_000;

export interface CtaConfig {
  enabled: boolean; // mensagem diaria ligada
  hours: number[]; // horarios (Brasilia) da diaria, em ordem; cada um manda uma mensagem
  hour: number; // o primeiro horario (compatibilidade com telas antigas)
  welcome: string;
  pixReminder: string;
  daily: string[];
  renewal: string[]; // VIP vence em breve: {data} = vencimento, {dias} = quanto falta
  renewalGrace: string; // VIP venceu e ainda esta no canal: {data} = quando sai
  // 23/11: fa de fora (Telegram em outra lingua) recebe em ingles; quem ainda nao
  // tem lingua conhecida recebe as duas (_sales_lang.ts)
  welcomeEn: string;
  dailyEn: string[];
  titlesEn: Record<string, string>; // id do pacote -> nome em ingles (vazio: nome padrao pelo tipo)
  // 23/11 (140 de 223 bloquearam): no maximo 1 diaria por dia (os horarios viram
  // tentativas) e para de mandar para quem nao toca em nada ha stopAfterDays dias
  oncePerDay: boolean;
  stopAfterDays: number; // 0 = nunca para
}

export interface Preview {
  media_id: string;
  kind: "photo" | "video";
  file_id: string;
  text?: string; // chamada feita para esta previa (a IA olhou a foto); vai na diaria
}

export const DEFAULT_CTA: CtaConfig = {
  enabled: true,
  hours: [12, 19],
  hour: 12,
  welcome: "Oi {nome}! Que bom que você veio 😘\n\nAqui eu mostro o que não posso mostrar em nenhum outro lugar… Escolhe o seu pacote e vem me ver 🔥",
  pixReminder: "Oi {nome}, vi que você quase garantiu o {pacote} 👀\n\nSeu Pix ainda está aqui em cima esperando… paga rapidinho que eu te mando tudo na hora 💛",
  daily: [
    "Oi {nome}, não esqueceu de mim né? 😈 Hoje eu acordei com vontade de te mostrar tudo…",
    "Oi de novo {nome}… achei que você já tinha voltado pra mim 👀 Ainda dá tempo de ver tudo que você ficou curioso pra ver 🔥",
    "{nome}, separei umas fotos que eu nunca mostrei aqui fora 🤫 Só quem entra no meu VIP vê.",
    "Tô aqui pensando em você, {nome} 💭 Que tal a gente se ver hoje? Lá no VIP eu fico bem à vontade 😏",
    "{nome}, hoje tem foto nova no VIP 📸 e tá do jeitinho que você gosta… Vem ver antes que eu me arrependa 🙈",
    "Psiu, {nome}… 💋 Em menos de 2 minutinhos você pode estar vendo tudo, sem censura e sem mistério.",
    "{nome}, você só viu a prévia… imagina o resto 😈 Escolhe o seu pacote aqui embaixo.",
    "Saudade de você por aqui, {nome} 🥺 Liberei um ensaio novinho, vem conferir 🔥",
    "{nome}, meu VIP tá cada dia mais quente 🌶️ Conteúdo novo quase todo dia, só pra quem tá lá dentro.",
    "Ei {nome} 😘 Hoje eu tô só de lingerie e com vontade de ser vista… mas só no VIP 🔒",
    "{nome}, tô de biquíni novo e ninguém aqui fora viu ainda 👙 Quer ser o primeiro? Te espero no VIP 😏",
    "Acabei de sair do banho, {nome}… 🚿 e tirei umas fotos que só vão pro meu VIP 🤭",
    "{nome}, hoje é dia de te mimar 💛 Escolhe o seu pacote e vem ver o que eu preparei só pra você.",
    "Confesso, {nome}: gravei uma coisa ontem à noite que me deixou com vergonha 🙈 Tá lá no VIP…",
    "{nome}, quem entrou no VIP essa semana não se arrependeu 😈 Só falta você.",
  ],
  renewal: [
    "⏰ {nome}, seu VIP vence em {data}. Não vai perder o que vem por aí: tô preparando um ensaio novinho 📸🔥",
    "{nome}, falta {dias} pra você perder tudo do meu VIP 😢 Ontem eu postei uma surpresa só pra quem tá lá dentro…",
    "Amor, seu VIP vence em {data} 💔 Minhas próximas aventuras eu fiz pensando em você 😏 Renova e fica comigo.",
  ],
  renewalGrace: "{nome}, seu VIP venceu 😢 Você ainda tá no canal até {data}… renova agora pra não perder o ensaio novo que tá chegando 🔥",
  welcomeEn: "Hey {nome}! So glad you came 😘\n\nHere I show what I can't show anywhere else… Pick your package and come see me 🔥",
  dailyEn: [
    "Hey {nome}, you didn't forget about me, right? 😈 I woke up today wanting to show you everything…",
    "Hi again {nome}… I thought you'd be back by now 👀 There's still time to see everything you were curious about 🔥",
    "{nome}, I picked some pics I've never shown out here 🤫 Only my VIP gets to see them.",
    "I'm here thinking about you, {nome} 💭 How about we meet today? In my VIP I get really comfortable 😏",
    "{nome}, new pics in the VIP today 📸 just the way you like it… Come see before I change my mind 🙈",
    "Psst, {nome}… 💋 In less than 2 minutes you could be seeing everything, uncensored.",
    "{nome}, you only saw the preview… imagine the rest 😈 Pick your package below.",
    "Missing you here, {nome} 🥺 I just dropped a brand-new photo set, come check it out 🔥",
    "{nome}, my VIP gets hotter every day 🌶️ New content almost daily, only for those inside.",
    "Hey {nome} 😘 Today I'm just in lingerie and I feel like being seen… but only in the VIP 🔒",
  ],
  titlesEn: {},
  oncePerDay: true,
  stopAfterDays: 7,
};

const STOP_MAX_DAYS = 60;

function cleanList(raw: unknown, max: number): string[] {
  return Array.isArray(raw) ? raw.map((t) => clean(t, 900)).filter(Boolean).slice(0, max) : [];
}

function cleanTitles(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, string> = {};
  for (const [id, t] of Object.entries(raw as Record<string, unknown>)) {
    const title = clean(t, 60);
    if (/^[0-9a-f-]{36}$/i.test(id) && title) out[id] = title;
  }
  return out;
}

function cleanStop(raw: unknown): number {
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 && n <= STOP_MAX_DAYS ? n : DEFAULT_CTA.stopAfterDays;
}

// 1 a 4 horarios inteiros (0-23), sem repetir, em ordem
function cleanHours(raw: unknown, fallback: unknown): number[] {
  const list = (Array.isArray(raw) ? raw : [fallback]).map(Number).filter((h) => Number.isInteger(h) && h >= 0 && h <= 23);
  const hours = [...new Set(list)].sort((a, b) => a - b).slice(0, 4);
  return hours.length ? hours : DEFAULT_CTA.hours;
}

function clean(text: unknown, max: number): string {
  return typeof text === "string" ? text.trim().slice(0, max) : "";
}

export async function ctaConfig(): Promise<CtaConfig> {
  if (!storeConfigured()) return DEFAULT_CTA;
  try {
    const saved = JSON.parse((await storeGet(CONFIG_KEY)) ?? "null") as Partial<CtaConfig> | null;
    if (!saved) return DEFAULT_CTA;
    const daily = Array.isArray(saved.daily) ? saved.daily.map((t) => clean(t, 900)).filter(Boolean) : [];
    const hours = cleanHours(saved.hours, saved.hour);
    return {
      enabled: saved.enabled !== false,
      hours,
      hour: hours[0],
      welcome: clean(saved.welcome, 900) || DEFAULT_CTA.welcome,
      pixReminder: clean(saved.pixReminder, 900) || DEFAULT_CTA.pixReminder,
      daily: daily.length ? daily : DEFAULT_CTA.daily,
      renewal: Array.isArray(saved.renewal) && saved.renewal.some((t) => clean(t, 900)) ? saved.renewal.map((t) => clean(t, 900)).filter(Boolean) : DEFAULT_CTA.renewal,
      renewalGrace: clean(saved.renewalGrace, 900) || DEFAULT_CTA.renewalGrace,
      welcomeEn: clean(saved.welcomeEn, 900) || DEFAULT_CTA.welcomeEn,
      dailyEn: cleanList(saved.dailyEn, 60).length ? cleanList(saved.dailyEn, 60) : DEFAULT_CTA.dailyEn,
      titlesEn: cleanTitles(saved.titlesEn),
      oncePerDay: saved.oncePerDay !== false,
      stopAfterDays: saved.stopAfterDays === undefined ? DEFAULT_CTA.stopAfterDays : cleanStop(saved.stopAfterDays),
    };
  } catch {
    return DEFAULT_CTA;
  }
}

export async function saveCtaConfig(raw: Record<string, unknown>): Promise<CtaConfig> {
  const daily = Array.isArray(raw.daily) ? raw.daily.map((t) => clean(t, 900)).filter(Boolean).slice(0, 60) : [];
  if (!daily.length) throw new Error("Deixe pelo menos uma frase para a mensagem diária.");
  const hours = cleanHours(raw.hours, raw.hour);
  const renewal = Array.isArray(raw.renewal) ? raw.renewal.map((t) => clean(t, 900)).filter(Boolean).slice(0, 30) : [];
  const config: CtaConfig = {
    enabled: raw.enabled !== false,
    hours,
    hour: hours[0],
    welcome: clean(raw.welcome, 900) || DEFAULT_CTA.welcome,
    pixReminder: clean(raw.pixReminder, 900) || DEFAULT_CTA.pixReminder,
    daily,
    renewal: renewal.length ? renewal : DEFAULT_CTA.renewal,
    renewalGrace: clean(raw.renewalGrace, 900) || DEFAULT_CTA.renewalGrace,
    welcomeEn: clean(raw.welcomeEn, 900) || DEFAULT_CTA.welcomeEn,
    dailyEn: cleanList(raw.dailyEn, 60).length ? cleanList(raw.dailyEn, 60) : DEFAULT_CTA.dailyEn,
    titlesEn: cleanTitles(raw.titlesEn),
    oncePerDay: raw.oncePerDay !== false,
    stopAfterDays: raw.stopAfterDays === undefined ? DEFAULT_CTA.stopAfterDays : cleanStop(raw.stopAfterDays),
  };
  if (raw.stopAfterDays !== undefined && cleanStop(raw.stopAfterDays) !== Number(raw.stopAfterDays)) throw new Error(`“Parar depois de” vai de 0 (nunca) a ${STOP_MAX_DAYS} dias.`);
  await storeSet(CONFIG_KEY, JSON.stringify(config));
  return config;
}

// --- textos em ingles pela IA (botao do estudio: o administrador confere antes de salvar) ------

// texto que nao vai para o fa: outro alfabeto ou sem o {nome}
const BAD_EN = /[Ͱ-ϿЀ-ӿ֐-ࣿ฀-๿぀-ヿ一-鿿가-힯]/;

export async function translateCta(config: Pick<CtaConfig, "welcome" | "daily">, products: { id: string; title: string }[]): Promise<{ welcomeEn: string; dailyEn: string[]; titlesEn: Record<string, string> }> {
  const { askAi } = await import("./_assistant.js");
  const who = personaIdentity() ?? "A Luna Vox é uma influenciadora virtual brasileira criada com IA, adulta, carinhosa, confiante e provocante.";
  const system = personaText(`Você traduz as mensagens do bot de vendas no Telegram (conteúdo adulto, só para quem já disse ter 18 anos) para fãs de fora do Brasil.
${who}
Traduza para um inglês natural, no jeito dela (não ao pé da letra): mantenha a malícia, o charme e os emojis. Ela é brasileira: pode deixar uma palavra carinhosa em português de vez em quando (ex.: "meu bem"), no máximo uma por mensagem.
Regras:
- mantenha exatamente {nome} (com as chaves) onde entra o nome do fã;
- uma tradução para cada mensagem, na mesma ordem e na mesma quantidade;
- sem descrever ato sexual nem partes íntimas, sem preço, sem link;
- nomes dos pacotes: curtos (até 40 letras), claros para um gringo.
Responda só JSON: {"welcome": "...", "daily": ["...", ...], "titles": {"<id>": "..."}}`);
  const out = await askAi(
    "normal",
    [
      { role: "system", content: system },
      { role: "user", content: JSON.stringify({ welcome: config.welcome, daily: config.daily, titles: Object.fromEntries(products.map((p) => [p.id, p.title])) }) },
    ],
    { json: true, maxTokens: 3000, timeoutMs: 45_000, temperature: 0.6 }
  );
  const raw = out.text.replace(/^```(?:json)?\s*|\s*```$/g, "");
  const parsed = JSON.parse(raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1));
  const ok = (t: unknown) => typeof t === "string" && t.trim().length > 5 && t.includes("{nome}") && !BAD_EN.test(t);
  const welcomeEn = ok(parsed.welcome) ? clean(parsed.welcome, 900) : "";
  const dailyEn = (Array.isArray(parsed.daily) ? parsed.daily : []).filter(ok).map((t: string) => clean(t, 900));
  const titlesEn = cleanTitles(parsed.titles);
  for (const [id, t] of Object.entries(titlesEn)) if (BAD_EN.test(t) || !products.some((p) => p.id === id)) delete titlesEn[id];
  if (!welcomeEn && !dailyEn.length) throw new Error("A IA não devolveu a tradução. Tente de novo.");
  return { welcomeEn, dailyEn, titlesEn };
}

export async function previews(): Promise<Preview[]> {
  if (!storeConfigured()) return [];
  try {
    const list = JSON.parse((await storeGet(PREVIEWS_KEY)) ?? "[]");
    return Array.isArray(list) ? list.filter((p) => p && typeof p.file_id === "string") : [];
  } catch {
    return [];
  }
}

export async function savePreviews(list: Preview[]): Promise<void> {
  await storeSet(PREVIEWS_KEY, JSON.stringify(list.slice(0, 60)));
}

// --- chamada pela previa (IA com visao) -----------------------------------------------------------
const VISION_PROMPT = `Você escreve chamadas curtas (CTA) para o bot de vendas da Luna Vox no Telegram.
A Luna Vox é uma influenciadora virtual brasileira criada com IA, adulta, carinhosa, confiante e provocante.
Você recebe a foto de prévia que vai junto da mensagem (as partes íntimas já estão cobertas).
Escreva 3 mini-histórias diferentes, em português do Brasil natural, como se fosse a própria Luna contando ao fã o que está fazendo na cena:
- use exatamente {nome} onde entra o primeiro nome do fã (com as chaves);
- 2 ou 3 frases curtas, em primeira pessoa, com 1 a 3 emojis (ex.: se é um quarto de hotel: "Tô aqui no hotel de roupão, {nome}… vem curtir o dia todo comigo 😏");
- ligadas ao que aparece na foto (lugar, roupa, pose, clima, hora do dia);
- tom íntimo e provocante, criando vontade de ver o resto, e terminando com o convite para escolher um pacote ou entrar no VIP;
- sem descrever atos sexuais nem partes íntimas (quem recebe ainda não pagou);
- sem inventar nomes, preços ou promoções.
Responda só JSON: {"chamadas": ["...", "...", "..."]}`;

// na persona do pedido (Luna: o texto de sempre; outra: o nome e a ficha dela)
function ctaPrompt(): string {
  const who = personaIdentity();
  if (!who) return VISION_PROMPT;
  const lines = VISION_PROMPT.split("\n");
  return personaText([lines[0], `${who}\nEscreva no jeito de falar dela.`, ...lines.slice(2)].join("\n"));
}

// Sugere 3 chamadas para a previa (imagem JPEG em data URL, ja reduzida no navegador).
export async function suggestCaptions(image: string): Promise<string[]> {
  const { askAi, visionModel } = await import("./_assistant.js");
  const out = await askAi(
    "adulto",
    [
      { role: "system", content: ctaPrompt() },
      { role: "user", content: [{ type: "text", text: "Esta é a prévia. Escreva as 3 chamadas." }, { type: "image_url", image_url: { url: image } }] },
    ],
    { json: true, maxTokens: 700, timeoutMs: 45_000, model: visionModel() }
  );
  const raw = out.text.replace(/^```(?:json)?\s*|\s*```$/g, "");
  let list: unknown = [];
  try {
    const parsed = JSON.parse(raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1));
    list = parsed.chamadas ?? parsed.calls ?? [];
  } catch {
    // resposta fora do JSON: uma chamada por linha
    list = raw.split("\n").map((l) => l.replace(/^\s*(?:[-*\d.)]+\s*)?"?|"?,?\s*$/g, ""));
  }
  const clean = (Array.isArray(list) ? list : [])
    .map((t) => (typeof t === "string" ? t.trim().slice(0, 900) : ""))
    .filter((t) => t.length > 5)
    .map((t) => (t.includes("{nome}") ? t : `{nome}, ${t.charAt(0).toLowerCase()}${t.slice(1)}`));
  if (!clean.length) throw new Error("A IA não devolveu chamadas. Tente de novo.");
  return clean.slice(0, 3);
}

export function fill(text: string, fan: Pick<FanRow, "first_name">, extra: Record<string, string> = {}, lang: FanLang = "pt"): string {
  const name = (fan.first_name ?? "").trim().split(/\s+/)[0] || (lang === "en" ? "babe" : "amor");
  return Object.entries({ nome: name, ...extra }).reduce((t, [k, v]) => t.split(`{${k}}`).join(v), text);
}

// botoes dos pacotes na lingua do fa ("en": nome em ingles e preco em estrelas/dolar)
async function buyButtons(lang: FanLang = "pt"): Promise<{ text: string; callback_data: string }[][]> {
  const { productsForSale } = await import("./_sales_bot.js");
  const { packLabels } = await import("./_sales_lang.js");
  const products = await productsForSale();
  const labels = await packLabels(products, lang);
  return products.map((p: ProductRow, i) => [{ text: labels[i], callback_data: `buy:${p.id}` }]);
}

// para o video da chamada (_sales_video.ts): os mesmos botoes e a faixa da promocao
export const buyButtonsFor = (lang: FanLang = "pt") => buyButtons(lang);
export async function promoLine(text: string, lang: FanLang = "pt"): Promise<string> {
  const { promoBanner } = await import("./_sales_promo.js");
  const banner = await promoBanner(lang);
  return banner ? `${banner}\n\n${text}` : text;
}

// Manda o CTA: previa (se houver) com o texto na legenda e os botoes dos pacotes
// (ou os botoes dados). "turn" escolhe a previa (gira a cada mensagem). "lang":
// lingua da faixa da promocao e dos botoes (null: as duas faixas, botoes em reais).
export async function sendCta(
  token: string,
  chatId: number,
  text: string,
  turn: number,
  buttons?: { text: string; callback_data: string }[][],
  lang: FanLang = "pt"
): Promise<void> {
  const rows = buttons ?? (await buyButtons(lang));
  // dia de promocao: a faixa vai no topo de toda mensagem com os pacotes
  if (rows.length) {
    const { promoBanner } = await import("./_sales_promo.js");
    const banner = await promoBanner(lang);
    if (banner) text = `${banner}\n\n${text}`;
  }
  const list = await previews();
  const markup = rows.length ? { reply_markup: { inline_keyboard: rows } } : {};
  const preview = list.length ? list[Math.abs(turn) % list.length] : null;
  if (preview) {
    await telegram(token, preview.kind === "video" ? "sendVideo" : "sendPhoto", {
      chat_id: chatId,
      [preview.kind === "video" ? "video" : "photo"]: preview.file_id,
      caption: text.slice(0, 1024),
      protect_content: true,
      ...markup,
    });
  } else {
    await telegram(token, "sendMessage", { chat_id: chatId, text, ...markup });
  }
}

// texto na lingua do fa: "pt", "en" ou os dois (lingua ainda desconhecida)
export function inLang(pt: string, en: string, lang: FanLang): string {
  return lang === "en" ? en : lang === "pt" ? pt : `${pt}\n\n🌎 ${en}`;
}

// Boas-vindas logo depois do "tenho 18". Conta como a mensagem do dia.
export async function sendWelcome(token: string, fan: FanRow, chatId: number, lang: FanLang = "pt"): Promise<boolean> {
  const rows = await buyButtons(lang);
  if (!rows.length) return false;
  const config = await ctaConfig();
  // a previa gira a cada fa novo (antes ia sempre a primeira): usa todas as que estiverem na pasta
  const turn = storeConfigured() ? await storeIncr("luna:cta-boasvindas-giro").catch(() => Date.now()) : Date.now();
  const text = fill(inLang(config.welcome, config.welcomeEn, lang), fan, {}, lang);
  // 24/11: com o video da chamada escolhido, a boas-vindas vai com ele (no lugar da previa)
  const { callVideo, sendWithCallVideo } = await import("./_sales_video.js");
  const video = await callVideo();
  if (video) await sendWithCallVideo(token, chatId, video, await promoLine(text, lang), rows);
  else await sendCta(token, chatId, text, turn, rows, lang);
  await dbUpdateWhere("fans", `id=eq.${fan.id}`, { cta_sent_at: new Date().toISOString() });
  return true;
}

function hourInBrazil(): number {
  return Number(new Date().toLocaleString("en-US", { timeZone: "America/Sao_Paulo", hour: "numeric", hour12: false })) % 24;
}

function dayNumber(at = Date.now()): number {
  return Math.floor(at / 86400_000);
}

// --- registro da diaria por dia (calendario) -------------------------------------------------------
// luna:cta-dia:<AAAA-MM-DD>:rodou|ok|erro|amostra|motivo, guardados 90 dias. O
// calendario mostra em cada dia a mensagem das {hour}h com ✓ (saiu) ou ✗ (falhou).
const LOG_TTL = 90 * 86400;
const LOG_SINCE_KEY = "luna:cta-registro-desde";
const logKey = (day: string, hour: number, field: string) => `luna:cta-dia:${day}:${hour}h:${field}`;
const brDay = (at = Date.now()) => new Date(at).toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });

async function logDaily(hour: number, field: "ok" | "erro", sample?: { text: string; media_id: string | null }, reason?: string): Promise<void> {
  const day = brDay();
  await storeIncrWithTtl(logKey(day, hour, field), LOG_TTL).catch(() => undefined);
  if (sample) await storeSetIfAbsent(logKey(day, hour, "amostra"), JSON.stringify(sample), LOG_TTL).catch(() => undefined);
  if (reason) await storeSetIfAbsent(logKey(day, hour, "motivo"), reason.slice(0, 200), LOG_TTL).catch(() => undefined);
}

export type CtaDayStatus = "enviada" | "falhou" | "nao-rodou" | "sem-fas" | "desligada" | "programada" | "aguardando" | "sem-registro";
export interface CtaDay {
  day: string;
  hour: number;
  status: CtaDayStatus;
  sent: number;
  failed: number;
  reason: string | null;
  text: string; // a mensagem que saiu (ou um exemplo da que vai sair), com {nome}
  media_id: string | null; // previa que foi junto
  kind: "photo" | "video" | null;
}

// Os dias de start (AAAA-MM-DD) ate start + days - 1: o que saiu (passado) ou o
// exemplo do que vai sair (futuro: cada fa recebe a sua, girando frases e previas).
export async function ctaDays(start: string, days: number): Promise<CtaDay[]> {
  const config = await ctaConfig();
  const list = await previews();
  const since = storeConfigured() ? await storeGet(LOG_SINCE_KEY).catch(() => null) : null;
  const today = brDay();
  const nowHour = hourInBrazil();
  const out: CtaDay[] = [];
  for (let i = 0; i < days; i++) {
    const noon = Date.parse(`${start}T12:00:00-03:00`) + i * 86400_000;
    const day = brDay(noon);
    const n = dayNumber(noon);
    for (const [k, hour] of config.hours.entries()) {
      // exemplo: frases e previas girando por horario
      const turn = n * config.hours.length + k;
      const preview = list.length ? list[turn % list.length] : null;
      const example = { text: preview?.text || config.daily[turn % config.daily.length], media_id: preview?.media_id ?? null, kind: preview?.kind ?? null };
      const base = { day, hour, sent: 0, failed: 0, reason: null as string | null, ...example };
      const future = day > today || (day === today && nowHour < hour);
      if (future) {
        // sem previa a mensagem nao sai (so vai com foto ou video): o calendario ja avisa
        if (config.enabled && !list.length) out.push({ ...base, status: "falhou", reason: "não vai sair: não tem foto/vídeo nas prévias" });
        else out.push({ ...base, status: config.enabled ? "programada" : "desligada" });
        continue;
      }
      if (!storeConfigured() || !since || day < since) {
        out.push({ ...base, status: "sem-registro" });
        continue;
      }
      const [ran, ok, err, sample, reason] = await Promise.all(
        ["rodou", "ok", "erro", "amostra", "motivo"].map((f) => storeGet(logKey(day, hour, f)).catch(() => null))
      );
      const sent = Number(ok ?? 0);
      const failed = Number(err ?? 0);
      let used = example;
      try {
        const s = JSON.parse(sample ?? "null") as { text: string; media_id: string | null } | null;
        if (s) used = { text: s.text, media_id: s.media_id, kind: list.find((p) => p.media_id === s.media_id)?.kind ?? (s.media_id ? "photo" : null) };
      } catch {
        // amostra quebrada: fica o exemplo
      }
      const status: CtaDayStatus =
        sent > 0 ? "enviada" : failed > 0 ? "falhou" : ran ? "sem-fas" : !config.enabled ? "desligada" : day === today && nowHour === hour ? "aguardando" : "nao-rodou";
      out.push({ ...base, ...used, sent, failed, reason: reason ?? null, status });
    }
  }
  return out;
}

interface CtaFan extends FanRow {
  cta_count: number;
  cta_sent_at: string | null;
}

// Processador da fila (a cada minuto): lembrete do Pix esquecido e mensagem
// diaria. Nunca derruba o processador. Devolve quantas mensagens saíram.
export async function ctaTick(): Promise<number> {
  try {
    const { salesToken } = await import("./_sales_bot.js");
    const { activeVip, settle } = await import("./_sales_pay.js");
    const token = await salesToken();
    if (!token) return 0;
    const config = await ctaConfig();
    const now = Date.now();
    let sent = 0;

    // Pix pendente das ultimas horas: confere na PushinPay (aviso que nao chegou)
    const open = await dbSelect<{ id: string }>(
      "sales_orders",
      `select=id&status=eq.pendente&gateway=eq.pushinpay&gateway_charge_id=not.is.null&created_at=gt.${new Date(now - PIX_SWEEP_WINDOW_MS).toISOString()}&order=created_at.desc&limit=40`
    ).catch(() => [] as { id: string }[]);
    let checked = 0;
    for (const order of open) {
      if (checked >= PIX_SWEEP_MAX) break;
      if (!(await storeSetIfAbsent(`luna:pix-conferido:${order.id}`, "1", PIX_SWEEP_EVERY_S).catch(() => false))) continue;
      checked++;
      await settle(order.id, token).catch(() => undefined);
    }

    // pagamento internacional pendente: confere na NOWPayments (aviso que nao chegou)
    const { intlSweep } = await import("./_sales_intl.js");
    await intlSweep(token).catch(() => undefined);

    // chamada para quem gerou o pagamento e nao pagou (22/11): aos poucos, depois do "Enviar"
    const { chamadaTick, promoReminderTick } = await import("./_sales_chamada.js");
    sent += await chamadaTick(token).catch(() => 0);
    // 1o dia da promocao: pergunta ao administrador se manda a chamada com o preco dela
    await promoReminderTick(token).catch(() => undefined);

    // Pix gerado e nao pago ha 20 min (no maximo 1 dia): um lembrete. So Pix (ate
    // 23/11 o pedido em dolar tambem caia aqui e o fa de fora recebia "teu Pix
    // ta aqui"); fa de fora nao recebe (ele nao paga Pix)
    const pending = await dbSelect<{ id: string; fan_id: string; product_id: string }>(
      "sales_orders",
      `select=id,fan_id,product_id&status=eq.pendente&gateway=eq.pushinpay&reminded_at=is.null&gateway_charge_id=not.is.null&created_at=lt.${new Date(now - PIX_REMINDER_MS).toISOString()}&created_at=gt.${new Date(now - 86400_000).toISOString()}&limit=${BATCH}`
    );
    for (const order of pending) {
      // pagou e o aviso nao chegou: entrega em vez de cobrar de novo
      if ((await settle(order.id, token).catch(() => "pendente")) !== "pendente") continue;
      const marked = await dbUpdate("sales_orders", `id=eq.${order.id}&reminded_at=is.null`, { reminded_at: new Date().toISOString() });
      if (!marked) continue;
      const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${order.fan_id}`);
      const [product] = await dbSelect<ProductRow>("sales_products", `select=*&id=eq.${order.product_id}`);
      if (!fan || !product || fan.blocked_at) continue;
      if ((await fanLang(fan)) === "en") continue;
      await telegram(token, "sendMessage", {
        chat_id: fan.telegram_id,
        text: fill(config.pixReminder, fan, { pacote: product.title }),
        reply_markup: { inline_keyboard: [[{ text: "✅ Já paguei", callback_data: `paid:${order.id}` }], [{ text: "🔁 Gerar outro Pix", callback_data: `buy:${product.id}` }]] },
      }).catch(() => undefined);
      sent++;
    }

    // a partir de quando ha registro por dia (dias anteriores aparecem "sem registro")
    if (storeConfigured() && !(await storeGet(LOG_SINCE_KEY))) await storeSet(LOG_SINCE_KEY, brDay());

    // mensagem diaria: o horario da vez e o ultimo que ja chegou hoje (12h ou 19h, no padrao)
    const nowHour = hourInBrazil();
    const slot = [...config.hours].reverse().find((h) => h <= nowHour);
    if (!config.enabled || slot === undefined) return sent;
    // a mensagem do dia sempre vai com foto ou video (pedido de 31/10): sem previa nao sai so o texto
    if (!(await previews()).length) {
      const reason = "sem foto/vídeo de prévia (a mensagem não sai só com texto)";
      if (await storeSetIfAbsent(logKey(brDay(), slot, "motivo"), reason, LOG_TTL).catch(() => false)) {
        await storeIncrWithTtl(logKey(brDay(), slot, "erro"), LOG_TTL).catch(() => undefined);
        const { sendNotice, telegramConfig } = await import("./_telegram.js");
        await sendNotice(
          await telegramConfig(),
          `📣 A mensagem das ${slot}h do bot de vendas não saiu: não tem nenhuma foto ou vídeo nas prévias e ela não vai só com texto. Adicione em Monetização → Conteúdo do Telegram → Prévias.`
        ).catch(() => undefined);
      }
      return sent;
    }
    await storeSetIfAbsent(logKey(brDay(), slot, "rodou"), "1", LOG_TTL).catch(() => undefined);
    // uma por horario: quem recebeu depois do inicio do horario (ou ha menos de 3 h) espera o proximo.
    // 1 por dia (padrao desde 23/11): quem ja recebeu hoje (boas-vindas conta) espera amanha
    const slotStart = Date.parse(`${brDay()}T${String(slot).padStart(2, "0")}:00:00-03:00`);
    const dayStart = Date.parse(`${brDay()}T00:00:00-03:00`);
    const since = new Date(Math.min(config.oncePerDay ? dayStart : slotStart, now - MIN_GAP_MS)).toISOString();
    // quem nao toca em nada no bot ha stopAfterDays dias para de receber (volta quando tocar)
    const quiet = config.stopAfterDays > 0 ? `&last_seen_at=gt.${new Date(now - config.stopAfterDays * 86400_000).toISOString()}` : "";
    // o administrador recebe a chamada mesmo com VIP (cortesia para ver como o cliente ve):
    // assim ele confere as mensagens do dia em todas as personas
    const { telegramConfig } = await import("./_telegram.js");
    const adminChat = (await telegramConfig().catch(() => null))?.chatId ?? null;
    const fans = await dbSelect<CtaFan>(
      "fans",
      `select=*&adult_confirmed_at=not.is.null&opted_out_at=is.null&blocked_at=is.null${quiet}&or=(cta_sent_at.is.null,cta_sent_at.lt.${since})&order=cta_sent_at.asc&limit=${BATCH}`
    );
    for (const fan of fans) {
      if (Date.now() - now > CTA_BUDGET_MS) break;
      const claimed = await dbUpdate("fans", `id=eq.${fan.id}&or=(cta_sent_at.is.null,cta_sent_at.lt.${since})`, {
        cta_sent_at: new Date().toISOString(),
        cta_count: fan.cta_count + 1,
      });
      // VIP ativo (ou nos dias de renovacao) nao recebe a chamada para comprar
      if (!claimed) continue;
      if (String(fan.telegram_id) !== String(adminChat) && (await activeVip(fan.id, true))) continue;
      // a previa da vez tem chamada propria (feita olhando a foto)? vai ela; senao, a frase do dia
      const turn = fan.cta_count + 1;
      const list = await previews();
      const own = list.length ? list[turn % list.length].text : undefined;
      // modelos em sequencia por fa: a 1a diaria e o modelo 1; depois do ultimo, volta ao 1
      const pt = own || config.daily[fan.cta_count % config.daily.length];
      // fa de fora: a frase em ingles da vez (a chamada da previa e em portugues)
      const en = config.dailyEn[fan.cta_count % config.dailyEn.length];
      const lang = await fanLang(fan);
      const text = inLang(pt, en, lang);
      try {
        await sendCta(token, fan.telegram_id, fill(text, fan, {}, lang), turn, undefined, lang);
        sent++;
        await logDaily(slot, "ok", { text, media_id: list.length ? list[Math.abs(turn) % list.length].media_id : null });
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        // o fa bloqueou o bot: para de mandar (nao conta como falha da mensagem)
        if (/blocked|deactivated|chat not found/i.test(message)) {
          await dbUpdateWhere("fans", `id=eq.${fan.id}`, { blocked_at: new Date().toISOString() });
        } else {
          await logDaily(slot, "erro", undefined, message);
        }
      }
    }
    return sent;
  } catch {
    return 0;
  }
}
