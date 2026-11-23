import { storeConfigured, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { dbSelect } from "./_supabase.js";
import { telegram } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";

// VIP tem tudo (pedido de 23/11/2026): quem tem o VIP valendo recebe no chat do
// bot todas as fotos e videos de todos os pacotes (ensaio, album...), sem
// repetir, alem do conteudo de todo dia no canal.
//   - comprou o VIP: vai tudo logo depois do convite do canal;
//   - entrou foto/video novo num pacote: chega sozinho para os VIPs (o
//     processador confere a cada 10 min);
//   - "Meus pacotes" do VIP mostra todos os pacotes para abrir de novo.
// O que cada VIP ja recebeu fica no Upstash (luna:vip-recebeu:<fa>, lista de
// media_id): o mesmo arquivo nunca vai duas vezes, nem se estiver em 2 pacotes.

const KEY = (fanId: string) => `luna:vip-recebeu:${fanId}`;
const PER_RUN = 60; // arquivos por vez; o resto vai na proxima conferencia
const SYNC_EVERY_S = 600;
const GAP_MS = 1200; // entre um album e outro (o Telegram limita mensagens por conversa)

interface PackMedia {
  media_id: string;
  product_id: string;
  position: number;
  kind: "photo" | "video";
  telegram_file_id: string;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// todos os arquivos dos pacotes a venda (sem o VIP), na ordem dos pacotes, sem repetir
export async function allPackMedia(): Promise<PackMedia[]> {
  const packs = await dbSelect<ProductRow>("sales_products", "select=id,kind,position&active=eq.true&kind=neq.vip&order=position.asc");
  if (!packs.length) return [];
  const order = new Map(packs.map((p, i) => [p.id, i]));
  const media = await dbSelect<PackMedia>(
    "sales_product_media",
    `select=media_id,product_id,position,kind,telegram_file_id&product_id=in.(${packs.map((p) => p.id).join(",")})`
  );
  media.sort((a, b) => order.get(a.product_id)! - order.get(b.product_id)! || a.position - b.position);
  const seen = new Set<string>();
  return media.filter((m) => {
    if (seen.has(m.media_id) || seen.has(m.telegram_file_id)) return false;
    seen.add(m.media_id);
    seen.add(m.telegram_file_id);
    return true;
  });
}

async function received(fanId: string): Promise<Set<string>> {
  if (!storeConfigured()) return new Set();
  try {
    const list = JSON.parse((await storeGet(KEY(fanId))) ?? "[]");
    return new Set(Array.isArray(list) ? list.map(String) : []);
  } catch {
    return new Set();
  }
}

// Manda ao VIP o que ele ainda nao recebeu dos pacotes. Devolve quantos arquivos
// foram. "lang" so muda o texto do aviso ("en": ingles; null: as duas linguas).
export async function sendVipPacks(salesToken: string, fan: Pick<FanRow, "id" | "telegram_id">, lang: "pt" | "en" | null = "pt"): Promise<number> {
  if (!storeConfigured()) return 0;
  const got = await received(fan.id);
  const missing = (await allPackMedia()).filter((m) => !got.has(m.media_id));
  if (!missing.length) return 0;
  const first = got.size === 0;
  const total = missing.length;
  const pt = first
    ? `💎 Como VIP você tem tudo: aqui estão todos os meus pacotes (${total} fotos e vídeos). O conteúdo novo chega todo dia no canal 🔥`
    : `💎 Conteúdo novo nos pacotes, só para o VIP (${total}) 🔥`;
  const en = first
    ? `💎 As a VIP you get everything: here are all my packages (${total} photos and videos). New content arrives every day in the channel 🔥`
    : `💎 New content in the packages, just for VIP (${total}) 🔥`;
  await telegram(salesToken, "sendMessage", { chat_id: fan.telegram_id, text: lang === "en" ? en : lang === "pt" ? pt : `${pt}\n\n🌎 ${en}`, protect_content: true });
  let sent = 0;
  const batch = missing.slice(0, PER_RUN);
  for (let i = 0; i < batch.length; i += 10) {
    const chunk = batch.slice(i, i + 10);
    try {
      if (chunk.length === 1) {
        const m = chunk[0];
        await telegram(salesToken, m.kind === "video" ? "sendVideo" : "sendPhoto", { chat_id: fan.telegram_id, [m.kind === "video" ? "video" : "photo"]: m.telegram_file_id, protect_content: true });
      } else {
        await telegram(salesToken, "sendMediaGroup", { chat_id: fan.telegram_id, media: chunk.map((m) => ({ type: m.kind, media: m.telegram_file_id })), protect_content: true });
      }
    } catch (e) {
      // o Telegram pediu calma ou o fa bloqueou: o resto vai na proxima conferencia
      if (/too many requests|retry after|blocked|deactivated|chat not found/i.test(e instanceof Error ? e.message : String(e))) break;
      throw e;
    }
    for (const m of chunk) got.add(m.media_id);
    sent += chunk.length;
    await storeSet(KEY(fan.id), JSON.stringify([...got]));
    if (i + 10 < batch.length) await sleep(GAP_MS);
  }
  return sent;
}

// Processador: a cada 10 min, cada VIP valendo recebe o que falta dos pacotes
// (VIP novo que nao recebeu tudo de uma vez, ou arquivo novo num pacote).
export async function vipPacksTick(salesToken: string, budgetMs = 20_000): Promise<number> {
  if (!storeConfigured()) return 0;
  if (!(await storeSetIfAbsent("luna:vip-pacotes-conferido", "1", SYNC_EVERY_S).catch(() => false))) return 0;
  const vips = await dbSelect<{ id: string }>("sales_products", "select=id&kind=eq.vip");
  if (!vips.length) return 0;
  const orders = await dbSelect<{ fan_id: string }>(
    "sales_orders",
    `select=fan_id&status=eq.entregue&product_id=in.(${vips.map((v) => v.id).join(",")})&access_until=gt.${new Date().toISOString()}`
  );
  const began = Date.now();
  const { fanLang } = await import("./_sales_lang.js");
  let sent = 0;
  for (const fanId of [...new Set(orders.map((o) => o.fan_id))]) {
    if (Date.now() - began > budgetMs) break;
    const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${fanId}`);
    if (!fan || fan.blocked_at) continue;
    sent += await sendVipPacks(salesToken, fan, await fanLang(fan)).catch(() => 0);
  }
  return sent;
}
