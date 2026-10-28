import { storeConfigured, storeGet, storeSet } from "./_store.js";
import type { ProductRow } from "./_sales_bot.js";

// Promocao do bot de vendas (ex.: "Promocao de domingo"): nos dias marcados
// (horario de Brasilia) cada pacote pode ter um preco menor. Vale para o
// botao do pacote, para o Pix e para a faixa no topo das mensagens com botoes.
// Fica no Upstash (luna:promo), como as outras configuracoes do bot.

export interface PromoConfig {
  enabled: boolean;
  days: number[]; // 0 = domingo ... 6 = sabado
  label: string;
  prices: Record<string, number>; // id do pacote -> preco da promocao em centavos
}

const KEY = "luna:promo";
export const DEFAULT_PROMO: PromoConfig = { enabled: false, days: [0], label: "Promoção de domingo", prices: {} };

export async function promoConfig(): Promise<PromoConfig> {
  if (!storeConfigured()) return DEFAULT_PROMO;
  try {
    const saved = JSON.parse((await storeGet(KEY)) ?? "null");
    if (!saved || typeof saved !== "object") return DEFAULT_PROMO;
    return {
      enabled: Boolean(saved.enabled),
      days: Array.isArray(saved.days) ? saved.days.filter((d: unknown) => Number.isInteger(d) && Number(d) >= 0 && Number(d) <= 6) : DEFAULT_PROMO.days,
      label: typeof saved.label === "string" && saved.label.trim() ? saved.label.trim().slice(0, 60) : DEFAULT_PROMO.label,
      prices: saved.prices && typeof saved.prices === "object" ? saved.prices : {},
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
  return config.enabled && config.days.includes(weekdayInBrazil(at)) && Object.keys(config.prices).length > 0;
}

export type SaleProduct = ProductRow & { regular_cents?: number };

// preco de hoje: com a promocao ativa e um preco menor cadastrado, o pacote sai
// por ele (regular_cents guarda o preco normal para mostrar "era R$ ...")
export function withPromo<T extends ProductRow>(product: T, config: PromoConfig, at = new Date()): T & { regular_cents?: number } {
  const cents = Number(config.prices[product.id]);
  const regular = (product as { regular_cents?: number }).regular_cents ?? product.price_cents;
  if (!promoActive(config, at) || !Number.isInteger(cents) || cents < 100 || cents >= regular) return product;
  return { ...product, price_cents: cents, regular_cents: regular };
}

export async function promoBanner(): Promise<string | null> {
  const config = await promoConfig();
  return promoActive(config) ? `🔥 ${config.label}: só hoje, preço especial nos pacotes!` : null;
}
