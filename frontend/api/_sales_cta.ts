import { storeConfigured, storeGet, storeSet } from "./_store.js";
import { dbSelect, dbUpdate, dbUpdateWhere } from "./_supabase.js";
import { telegram } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";

// Mensagens que chamam o fa para comprar (CTA) no bot de vendas, sempre com o
// nome dele ({nome}), uma previa (foto com as partes intimas cobertas, escolhida
// pelo administrador na aba Telegram -> Previas) e os botoes dos pacotes:
//   boas-vindas    logo depois de confirmar os 18 anos
//   Pix esquecido  20 min depois de gerar o Pix sem pagar (uma vez)
//   diaria         uma por dia, no horario escolhido, para quem nao e VIP
//                  (para com /parar, bloqueio ou VIP ativo); o texto muda todo
//                  dia, a previa pode repetir
//   renovacao      VIP perto de vencer (3 dias antes) e vencido (3 dias de folga
//                  no canal): uma por dia, com previa e "Renovar agora" (_sales_pay.ts)
// Os textos ficam no Upstash (luna:cta) e o administrador edita em Vendas. Nas
// mensagens gratis o texto e provocante, sem descricao explicita: quem recebe
// ainda nao passou por verificacao de idade alem do "tenho 18".

const CONFIG_KEY = "luna:cta";
const PREVIEWS_KEY = "luna:sales-previews";
const PIX_REMINDER_MS = 20 * 60_000;
const DAILY_GAP_MS = 20 * 3600_000; // no maximo uma diaria a cada 20 h
const BATCH = 25; // fas por minuto (o processador roda a cada minuto)

export interface CtaConfig {
  enabled: boolean; // mensagem diaria ligada
  hour: number; // a partir de que hora (Brasilia) sai a diaria
  welcome: string;
  pixReminder: string;
  daily: string[];
  renewal: string[]; // VIP vence em breve: {data} = vencimento, {dias} = quanto falta
  renewalGrace: string; // VIP venceu e ainda esta no canal: {data} = quando sai
}

export interface Preview {
  media_id: string;
  kind: "photo" | "video";
  file_id: string;
}

export const DEFAULT_CTA: CtaConfig = {
  enabled: true,
  hour: 19,
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
  ],
  renewal: [
    "⏰ {nome}, seu VIP vence em {data}. Não vai perder o que vem por aí: tô preparando um ensaio novinho 📸🔥",
    "{nome}, falta {dias} pra você perder tudo do meu VIP 😢 Ontem eu postei uma surpresa só pra quem tá lá dentro…",
    "Amor, seu VIP vence em {data} 💔 Minhas próximas aventuras eu fiz pensando em você 😏 Renova e fica comigo.",
  ],
  renewalGrace: "{nome}, seu VIP venceu 😢 Você ainda tá no canal até {data}… renova agora pra não perder o ensaio novo que tá chegando 🔥",
};

function clean(text: unknown, max: number): string {
  return typeof text === "string" ? text.trim().slice(0, max) : "";
}

export async function ctaConfig(): Promise<CtaConfig> {
  if (!storeConfigured()) return DEFAULT_CTA;
  try {
    const saved = JSON.parse((await storeGet(CONFIG_KEY)) ?? "null") as Partial<CtaConfig> | null;
    if (!saved) return DEFAULT_CTA;
    const daily = Array.isArray(saved.daily) ? saved.daily.map((t) => clean(t, 900)).filter(Boolean) : [];
    return {
      enabled: saved.enabled !== false,
      hour: Number.isInteger(saved.hour) && saved.hour! >= 0 && saved.hour! <= 23 ? saved.hour! : DEFAULT_CTA.hour,
      welcome: clean(saved.welcome, 900) || DEFAULT_CTA.welcome,
      pixReminder: clean(saved.pixReminder, 900) || DEFAULT_CTA.pixReminder,
      daily: daily.length ? daily : DEFAULT_CTA.daily,
      renewal: Array.isArray(saved.renewal) && saved.renewal.some((t) => clean(t, 900)) ? saved.renewal.map((t) => clean(t, 900)).filter(Boolean) : DEFAULT_CTA.renewal,
      renewalGrace: clean(saved.renewalGrace, 900) || DEFAULT_CTA.renewalGrace,
    };
  } catch {
    return DEFAULT_CTA;
  }
}

export async function saveCtaConfig(raw: Record<string, unknown>): Promise<CtaConfig> {
  const daily = Array.isArray(raw.daily) ? raw.daily.map((t) => clean(t, 900)).filter(Boolean).slice(0, 60) : [];
  if (!daily.length) throw new Error("Deixe pelo menos uma frase para a mensagem diária.");
  const hour = Number(raw.hour);
  const renewal = Array.isArray(raw.renewal) ? raw.renewal.map((t) => clean(t, 900)).filter(Boolean).slice(0, 30) : [];
  const config: CtaConfig = {
    enabled: raw.enabled !== false,
    hour: Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : DEFAULT_CTA.hour,
    welcome: clean(raw.welcome, 900) || DEFAULT_CTA.welcome,
    pixReminder: clean(raw.pixReminder, 900) || DEFAULT_CTA.pixReminder,
    daily,
    renewal: renewal.length ? renewal : DEFAULT_CTA.renewal,
    renewalGrace: clean(raw.renewalGrace, 900) || DEFAULT_CTA.renewalGrace,
  };
  await storeSet(CONFIG_KEY, JSON.stringify(config));
  return config;
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

export function fill(text: string, fan: Pick<FanRow, "first_name">, extra: Record<string, string> = {}): string {
  const name = (fan.first_name ?? "").trim().split(/\s+/)[0] || "amor";
  return Object.entries({ nome: name, ...extra }).reduce((t, [k, v]) => t.split(`{${k}}`).join(v), text);
}

async function buyButtons(): Promise<{ text: string; callback_data: string }[][]> {
  const { productsForSale, productLabel } = await import("./_sales_bot.js");
  return (await productsForSale()).map((p: ProductRow) => [{ text: productLabel(p), callback_data: `buy:${p.id}` }]);
}

// Manda o CTA: previa (se houver) com o texto na legenda e os botoes dos pacotes
// (ou os botoes dados). "turn" escolhe a previa (gira a cada mensagem).
export async function sendCta(token: string, chatId: number, text: string, turn: number, buttons?: { text: string; callback_data: string }[][]): Promise<void> {
  const rows = buttons ?? (await buyButtons());
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

// Boas-vindas logo depois do "tenho 18". Conta como a mensagem do dia.
export async function sendWelcome(token: string, fan: FanRow, chatId: number): Promise<boolean> {
  if (!(await buyButtons()).length) return false;
  const config = await ctaConfig();
  await sendCta(token, chatId, fill(config.welcome, fan), 0);
  await dbUpdateWhere("fans", `id=eq.${fan.id}`, { cta_sent_at: new Date().toISOString() });
  return true;
}

function hourInBrazil(): number {
  return Number(new Date().toLocaleString("en-US", { timeZone: "America/Sao_Paulo", hour: "numeric", hour12: false })) % 24;
}

function dayNumber(): number {
  return Math.floor(Date.now() / 86400_000);
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
    const { activeVip } = await import("./_sales_pay.js");
    const token = await salesToken();
    if (!token) return 0;
    const config = await ctaConfig();
    const now = Date.now();
    let sent = 0;

    // Pix gerado e nao pago ha 20 min (no maximo 1 dia): um lembrete
    const pending = await dbSelect<{ id: string; fan_id: string; product_id: string }>(
      "sales_orders",
      `select=id,fan_id,product_id&status=eq.pendente&reminded_at=is.null&gateway_charge_id=not.is.null&created_at=lt.${new Date(now - PIX_REMINDER_MS).toISOString()}&created_at=gt.${new Date(now - 86400_000).toISOString()}&limit=${BATCH}`
    );
    for (const order of pending) {
      const marked = await dbUpdate("sales_orders", `id=eq.${order.id}&reminded_at=is.null`, { reminded_at: new Date().toISOString() });
      if (!marked) continue;
      const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${order.fan_id}`);
      const [product] = await dbSelect<ProductRow>("sales_products", `select=*&id=eq.${order.product_id}`);
      if (!fan || !product || fan.blocked_at) continue;
      await telegram(token, "sendMessage", {
        chat_id: fan.telegram_id,
        text: fill(config.pixReminder, fan, { pacote: product.title }),
        reply_markup: { inline_keyboard: [[{ text: "✅ Já paguei", callback_data: `paid:${order.id}` }], [{ text: "🔁 Gerar outro Pix", callback_data: `buy:${product.id}` }]] },
      }).catch(() => undefined);
      sent++;
    }

    // mensagem diaria
    if (!config.enabled || hourInBrazil() < config.hour) return sent;
    const since = new Date(now - DAILY_GAP_MS).toISOString();
    const fans = await dbSelect<CtaFan>(
      "fans",
      `select=*&adult_confirmed_at=not.is.null&opted_out_at=is.null&blocked_at=is.null&or=(cta_sent_at.is.null,cta_sent_at.lt.${since})&order=cta_sent_at.asc&limit=${BATCH}`
    );
    for (const fan of fans) {
      const claimed = await dbUpdate("fans", `id=eq.${fan.id}&or=(cta_sent_at.is.null,cta_sent_at.lt.${since})`, {
        cta_sent_at: new Date().toISOString(),
        cta_count: fan.cta_count + 1,
      });
      // VIP ativo (ou nos dias de renovacao) nao recebe a chamada para comprar
      if (!claimed || (await activeVip(fan.id, true))) continue;
      const text = config.daily[(dayNumber() + fan.cta_count) % config.daily.length];
      try {
        await sendCta(token, fan.telegram_id, fill(text, fan), fan.cta_count + 1);
        sent++;
      } catch (e) {
        // o fa bloqueou o bot: para de mandar
        if (/blocked|deactivated|chat not found/i.test(e instanceof Error ? e.message : String(e))) {
          await dbUpdateWhere("fans", `id=eq.${fan.id}`, { blocked_at: new Date().toISOString() });
        }
      }
    }
    return sent;
  } catch {
    return 0;
  }
}
