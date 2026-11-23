import { storeConfigured, storeDelete, storeGet, storeIncr, storeSet, storeSetIfAbsent } from "./_store.js";
import { dbSelect } from "./_supabase.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";
import type { SaleProduct } from "./_sales_promo.js";

// Lingua do fa (pedido de 23/11/2026: a maioria de quem entra no bot da Cleuza e
// de fora do Brasil e recebia tudo em portugues nordestino, sem entender, e
// bloqueava). O Telegram manda o language_code em cada toque; ele fica guardado
// no Upstash (luna:fan-lang:<fa>, por persona) para as mensagens que o bot manda
// sozinho (boas-vindas, diaria, chamada):
//   "pt"  Telegram em portugues  -> so portugues
//   "en"  qualquer outra lingua  -> so ingles (pacotes com o preco em estrelas/dolar)
//   null  ainda nao se sabe      -> portugues + ingles na mesma mensagem
// Fa antigo sem lingua guardada: nome em outro alfabeto, ou o 1o pedido dele em
// estrelas/dolar (o bot so manda isso primeiro para quem esta em outra lingua),
// conta como "en".

export type FanLang = "pt" | "en" | null;

const KEY = (fanId: string) => `luna:fan-lang:${fanId}`;
// 23/11 (ideia dele): na confirmacao da idade o fa escolhe "sou do Brasil" ou
// "fora do Brasil". Isso vale mais que a lingua do Telegram: decide a lingua das
// mensagens e a forma de pagar (Brasil: Pix primeiro; fora: cartao/estrelas e cripto)
const COUNTRY = (fanId: string) => `luna:fan-pais:${fanId}`;

export async function setAbroad(fanId: string, abroad: boolean): Promise<void> {
  if (!storeConfigured()) return;
  await storeSet(COUNTRY(fanId), abroad ? "fora" : "br").catch(() => undefined);
  await storeSet(KEY(fanId), abroad ? "en" : "pt").catch(() => undefined);
  // estudo do publico: quantos escolheram Brasil / fora (cada fa conta uma vez)
  if (await storeSetIfAbsent(`luna:fan-pais-contado:${fanId}`, "1", STUDY_TTL).catch(() => false)) {
    await storeIncr(`luna:pais-count:${abroad ? "fora" : "br"}`).catch(() => undefined);
  }
}

// --- estudo do publico (23/11, pedido dele: "faz um estudo, nao deduz") ---------------------------
// A lingua do app do Telegram de cada fa (language_code: "fa" persa, "ar" arabe,
// "hi" hindi, "en" ingles...) e contada uma vez por fa. Junto com o "sou do
// Brasil / fora", mostra de onde vem quem entra no bot (Vendas -> Fas).
const STUDY_TTL = 400 * 86400;
const CODES_KEY = "luna:lang-codes";

async function countCode(fanId: string, code: string): Promise<void> {
  const c = code.toLowerCase().slice(0, 2);
  if (!/^[a-z]{2}$/.test(c)) return;
  if (!(await storeSetIfAbsent(`luna:fan-code:${fanId}`, c, STUDY_TTL).catch(() => false))) return;
  await storeIncr(`luna:lang-count:${c}`).catch(() => undefined);
  const codes = new Set<string>(JSON.parse((await storeGet(CODES_KEY).catch(() => null)) ?? "[]"));
  if (!codes.has(c)) {
    codes.add(c);
    await storeSet(CODES_KEY, JSON.stringify([...codes])).catch(() => undefined);
  }
}

export async function audienceStudy(): Promise<{ langs: { code: string; fans: number }[]; brasil: number; fora: number }> {
  if (!storeConfigured()) return { langs: [], brasil: 0, fora: 0 };
  const codes: string[] = JSON.parse((await storeGet(CODES_KEY).catch(() => null)) ?? "[]");
  const langs = await Promise.all(codes.map(async (code) => ({ code, fans: Number((await storeGet(`luna:lang-count:${code}`).catch(() => null)) ?? 0) })));
  const n = async (k: string) => Number((await storeGet(k).catch(() => null)) ?? 0);
  return { langs: langs.filter((l) => l.fans > 0).sort((a, b) => b.fans - a.fans), brasil: await n("luna:pais-count:br"), fora: await n("luna:pais-count:fora") };
}

// /zerar do administrador: volta a ser fa novo (escolhe o pais de novo)
export async function forgetFanLang(fanId: string): Promise<void> {
  if (!storeConfigured()) return;
  await storeDelete(COUNTRY(fanId)).catch(() => undefined);
  await storeDelete(KEY(fanId)).catch(() => undefined);
}

// true: fora do Brasil, false: Brasil, null: nao escolheu (vale a lingua do Telegram)
export async function fanAbroad(fanId: string): Promise<boolean | null> {
  if (!storeConfigured()) return null;
  const saved = await storeGet(COUNTRY(fanId)).catch(() => null);
  return saved === "fora" ? true : saved === "br" ? false : null;
}
const OTHER_SCRIPT = /[Ͱ-ϿЀ-ӿ֐-ࣿ฀-๿぀-ヿ一-鿿가-힯]/;

export function langOf(code?: string | null): FanLang {
  if (!code) return null;
  return /^pt\b/i.test(String(code)) ? "pt" : "en";
}

// lingua do Telegram, so enquanto o fa nao escolheu o pais
export async function rememberLang(fanId: string, code?: string | null): Promise<void> {
  const lang = langOf(code);
  if (!lang || !storeConfigured()) return;
  await countCode(fanId, String(code)).catch(() => undefined);
  if ((await fanAbroad(fanId)) !== null) return;
  await storeSet(KEY(fanId), lang).catch(() => undefined);
}

export async function fanLang(fan: Pick<FanRow, "id" | "first_name">): Promise<FanLang> {
  if (storeConfigured()) {
    const abroad = await fanAbroad(fan.id);
    if (abroad !== null) return abroad ? "en" : "pt";
    const saved = await storeGet(KEY(fan.id)).catch(() => null);
    if (saved === "pt" || saved === "en") return saved;
  }
  let lang: FanLang = OTHER_SCRIPT.test(fan.first_name ?? "") ? "en" : null;
  if (!lang) {
    const [first] = await dbSelect<{ gateway: string | null }>(
      "sales_orders",
      `select=gateway&fan_id=eq.${fan.id}&gateway=in.(pushinpay,estrelas,nowpayments)&order=created_at.asc&limit=1`
    ).catch(() => [] as { gateway: string | null }[]);
    // 1o pedido em Pix nao prova nada (antes de 21/11 todo mundo recebia o Pix primeiro)
    if (first && first.gateway !== "pushinpay") lang = "en";
  }
  if (lang && storeConfigured()) await storeSet(KEY(fan.id), lang).catch(() => undefined);
  return lang;
}

// --- nomes e botoes dos pacotes em ingles -----------------------------------------------------

const EN_TITLES: Record<ProductRow["kind"], string> = {
  ensaio: "Photo set",
  album: "Uncensored VIP album",
  vip: "Full VIP: videos + photos every day",
};

export function titleEn(product: Pick<ProductRow, "id" | "kind">, titles: Record<string, string> = {}): string {
  return (titles[product.id] ?? "").trim() || EN_TITLES[product.kind] || "Package";
}

const icon = (p: Pick<ProductRow, "kind">) => (p.kind === "vip" ? "💎" : p.kind === "album" ? "🔥" : "📸");

// Textos dos botoes dos pacotes na lingua do fa. Em ingles o preco vem em
// estrelas (o botao abre a fatura em estrelas) ou, sem estrelas, em dolar; na
// promocao, com o "was".
export async function packLabels(products: SaleProduct[], lang: FanLang): Promise<string[]> {
  const { productLabel, price } = await import("./_sales_bot.js");
  if (lang !== "en") return products.map((p) => productLabel(p));
  const { ctaConfig } = await import("./_sales_cta.js");
  const { starsConfig, starsPrice } = await import("./_sales_stars.js");
  const { intlConfig, intlPrice, usd } = await import("./_sales_intl.js");
  const [cta, stars, intl] = await Promise.all([ctaConfig(), starsConfig().catch(() => ({ enabled: false, prices: {} })), intlConfig().catch(() => null)]);
  return products.map((p) => {
    const title = titleEn(p, cta.titlesEn);
    const period = p.billing === "mensal" ? "/month" : /\bdays?\b/i.test(title) ? "" : ` · ${p.period_days} days`;
    const s = starsPrice(p, stars);
    if (s) {
      const was = starsPrice({ ...p, promo_stars: undefined }, stars);
      return `${icon(p)} ${title} — ⭐ ${s}${was && was > s ? ` (was ⭐ ${was})` : ""}${period}`;
    }
    const c = intl ? intlPrice(p, intl) : null;
    if (c) {
      const was = intl ? intlPrice({ ...p, promo_usd: undefined }, intl) : null;
      return `${icon(p)} ${title} — ${usd(c)}${was && was > c ? ` (was ${usd(was)})` : ""}${period}`;
    }
    return `${icon(p)} ${title} — ${price(p.price_cents)}${period}`;
  });
}
