import { storeConfigured, storeGet, storeSet } from "./_store.js";
import type { ProductRow } from "./_sales_bot.js";

// Promocao do bot de vendas (ex.: "Promocao de domingo"): nos dias marcados
// (horario de Brasilia) cada pacote pode ter um preco menor. Vale para o
// botao do pacote, para o Pix e para a faixa no topo das mensagens com botoes.
// Desde 23/11 tambem em estrelas e em dolar (fa de fora), com o nome em ingles.
// Fica no Upstash (luna:promo), como as outras configuracoes do bot.

export interface PromoConfig {
  enabled: boolean;
  days: number[]; // 0 = domingo ... 6 = sabado
  label: string;
  labelEn: string; // nome para o fa de fora
  prices: Record<string, number>; // id do pacote -> preco da promocao em centavos
  stars: Record<string, number>; // id do pacote -> preco da promocao em estrelas
  usd: Record<string, number>; // id do pacote -> preco da promocao em centavos de dolar
}

const KEY = "luna:promo";
export const DEFAULT_PROMO: PromoConfig = { enabled: false, days: [0], label: "Promoção de domingo", labelEn: "Weekend deal", prices: {}, stars: {}, usd: {} };
// a NOWPayments recusa abaixo de US$ 10 (mesmo minimo de _sales_intl.ts)
export const PROMO_USD_MIN = 1000;

const map = (raw: unknown): Record<string, number> => (raw && typeof raw === "object" ? (raw as Record<string, number>) : {});

export async function promoConfig(): Promise<PromoConfig> {
  if (!storeConfigured()) return DEFAULT_PROMO;
  try {
    const saved = JSON.parse((await storeGet(KEY)) ?? "null");
    if (!saved || typeof saved !== "object") return DEFAULT_PROMO;
    return {
      enabled: Boolean(saved.enabled),
      days: Array.isArray(saved.days) ? saved.days.filter((d: unknown) => Number.isInteger(d) && Number(d) >= 0 && Number(d) <= 6) : DEFAULT_PROMO.days,
      label: typeof saved.label === "string" && saved.label.trim() ? saved.label.trim().slice(0, 60) : DEFAULT_PROMO.label,
      labelEn: typeof saved.labelEn === "string" && saved.labelEn.trim() ? saved.labelEn.trim().slice(0, 60) : DEFAULT_PROMO.labelEn,
      prices: map(saved.prices),
      stars: map(saved.stars),
      usd: map(saved.usd),
    };
  } catch {
    return DEFAULT_PROMO;
  }
}

export async function savePromo(config: PromoConfig): Promise<void> {
  await storeSet(KEY, JSON.stringify(config));
}

export function weekdayInBrazil(at = new Date()): number {
  const name = at.toLocaleDateString("en-US", { timeZone: "America/Sao_Paulo", weekday: "short" });
  return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(name);
}

export function promoActive(config: PromoConfig, at = new Date()): boolean {
  const any = Object.keys(config.prices).length + Object.keys(config.stars ?? {}).length + Object.keys(config.usd ?? {}).length > 0;
  return config.enabled && config.days.includes(weekdayInBrazil(at)) && any;
}

// promo_stars / promo_usd: preco da promocao em estrelas e em dolar (starsPrice e
// intlPrice usam quando for menor que o preco normal)
export type SaleProduct = ProductRow & { regular_cents?: number; promo_stars?: number; promo_usd?: number };

// preco de hoje: com a promocao ativa e um preco menor cadastrado, o pacote sai
// por ele (regular_cents guarda o preco normal para mostrar "era R$ ...")
export function withPromo<T extends ProductRow>(product: T, config: PromoConfig, at = new Date()): T & { regular_cents?: number; promo_stars?: number; promo_usd?: number } {
  if (!promoActive(config, at)) return product;
  let out: T & { regular_cents?: number; promo_stars?: number; promo_usd?: number } = product;
  const cents = Number(config.prices[product.id]);
  const regular = (product as { regular_cents?: number }).regular_cents ?? product.price_cents;
  if (Number.isInteger(cents) && cents >= 100 && cents < regular) out = { ...out, price_cents: cents, regular_cents: regular };
  const stars = Number(config.stars?.[product.id]);
  if (Number.isInteger(stars) && stars >= 1) out = { ...out, promo_stars: stars };
  const usd = Number(config.usd?.[product.id]);
  if (Number.isInteger(usd) && usd >= PROMO_USD_MIN) out = { ...out, promo_usd: usd };
  return out;
}

// lang: "pt" so a faixa em portugues, "en" so em ingles, null as duas
export async function promoBanner(lang: "pt" | "en" | null = "pt"): Promise<string | null> {
  const config = await promoConfig();
  if (!promoActive(config)) return null;
  const pt = `🔥 ${config.label}: só hoje, preço especial nos pacotes!`;
  const en = `🔥 ${config.labelEn}: today only, special prices!`;
  return lang === "en" ? en : lang === "pt" ? pt : `${pt}\n${en}`;
}
