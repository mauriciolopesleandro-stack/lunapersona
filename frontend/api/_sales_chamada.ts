import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { audit, dbSelect, dbUpdateWhere } from "./_supabase.js";
import { telegram, telegramConfig } from "./_telegram.js";
import type { FanRow, ProductRow } from "./_sales_bot.js";
import { personaIdentity, personaText } from "./_persona.js";

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
const WINDOW_MS = 3 * 86400_000;
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
    pt: "Ei {nome}… tu deixou a gente aqui esperando, foi? 😏 Agora dá pra pagar de fora do Brasil também, em dólar, no cartão ou cripto. Essa aí borrada é só o aperitivo… o resto tá sem censura te esperando 🔥 Toca aqui embaixo.",
    en: "Hey {nome}… you left me waiting 😏 Now you can pay from anywhere: card or crypto in US$. That blurry pic? Just a taste. The rest is waiting for you, uncensored 🔥 Tap below.",
  },
  {
    pt: "{nome}, vi que tu quase levou… 👀 Agora aceito pagamento de fora também, em dólar, sem complicação. Olha só o que tu tá perdendo 😈",
    en: "{nome}, you were so close… 👀 Now I take payments from abroad too, in US$, quick and easy. Look what you're missing 😈",
  },
];

function chamadaPrompt(): string {
  const who = personaIdentity() ?? "A Luna Vox é uma influenciadora virtual brasileira criada com IA, adulta, carinhosa, confiante e provocante.";
  return personaText(`Você escreve mensagens curtas para o bot de vendas no Telegram (conteúdo adulto, só para quem já disse ter 18 anos).
${who}
Escreva no jeito de falar dela, em primeira pessoa.
Situação: o fã escolheu um pacote, gerou o pagamento e não pagou. Muitos estão fora do Brasil e não tinham como pagar o Pix. Novidade: agora dá para pagar de qualquer país, em dólar, com cartão ou cripto (e o Pix continua para quem está no Brasil).
A mensagem vai junto de uma foto dela borrada (prévia) e dos botões de pagamento.
Escreva 6 mensagens diferentes. Cada uma com:
- "pt": português do Brasil, 2 ou 3 frases curtas, safada e provocante, com malícia e duplo sentido, 1 a 3 emojis;
- "en": a mesma ideia em inglês natural (não traduza ao pé da letra), 2 ou 3 frases, 1 a 3 emojis.
Regras:
- use exatamente {nome} onde entra o primeiro nome do fã (com as chaves), nas duas línguas;
- diga que agora aceita pagamento de fora do Brasil / em dólar;
- provoque com a foto borrada (é só o começo, o resto está sem censura no pacote);
- termine chamando para tocar no botão;
- sem descrever ato sexual nem partes íntimas (o fã ainda não pagou), sem preço, sem link, sem prometer encontro.
Responda só JSON: {"mensagens": [{"pt": "...", "en": "..."}]}`);
}

export async function writeChamadaTexts(): Promise<ChamadaText[]> {
  try {
    const { askAi } = await import("./_assistant.js");
    const out = await askAi(
      "adulto",
      [
        { role: "system", content: chamadaPrompt() },
        { role: "user", content: "Escreva as 6 mensagens." },
      ],
      { json: true, maxTokens: 1800, timeoutMs: 40_000, temperature: 0.9 }
    );
    const raw = out.text.replace(/^```(?:json)?\s*|\s*```$/g, "");
    const parsed = JSON.parse(raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1));
    const list = (Array.isArray(parsed.mensagens) ? parsed.mensagens : [])
      .map((m: unknown) => {
        const x = m as Record<string, unknown>;
        const pt = typeof x?.pt === "string" ? x.pt.trim().slice(0, 450) : "";
        const en = typeof x?.en === "string" ? x.en.trim().slice(0, 450) : "";
        return pt.length > 10 && en.length > 10 ? { pt, en } : null;
      })
      .filter(Boolean) as ChamadaText[];
    return list.length >= 2 ? list.slice(0, 6) : FALLBACK;
  } catch {
    return FALLBACK;
  }
}

// --- a mensagem ---------------------------------------------------------------------------------

function firstName(fan: Pick<FanRow, "first_name">): string {
  return (fan.first_name ?? "").trim().split(/\s+/)[0] || "";
}

export function chamadaCaption(t: ChamadaText, fan: Pick<FanRow, "first_name">): string {
  const name = firstName(fan);
  const put = (s: string, fallback: string) => s.split("{nome}").join(name || fallback).replace(/^,\s*/, "");
  return `${put(t.pt, "amor")}\n\n🌎 ${put(t.en, "babe")}`.slice(0, 1024);
}

async function chamadaButtons(productId: string): Promise<{ text: string; callback_data: string }[][] | null> {
  const { productsForSale, price } = await import("./_sales_bot.js");
  const { intlConfig, intlPrice, usd } = await import("./_sales_intl.js");
  const forSale = await productsForSale();
  const product = forSale.find((p) => p.id === productId) ?? forSale.find((p) => p.billing !== "mensal");
  if (!product) return null;
  const cents = intlPrice(product, await intlConfig());
  return [
    ...(cents ? [[{ text: `💳 Pay in US$ / Pagar em dólar — ${usd(cents)}`, callback_data: `intl:${product.id}` }]] : []),
    [{ text: `🇧🇷 Pix — ${price(product.price_cents)}`, callback_data: `pix:${product.id}` }],
  ];
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
  const texts = await writeChamadaTexts();
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
  // o exemplo, como o fa recebe (com o nome do administrador)
  const [me] = await dbSelect<FanRow>("fans", `select=*&telegram_id=eq.${admin}`);
  const rows = await chamadaButtons(c.targets[0]?.product ?? "");
  if (rows) await sendOne(token, Number(admin), chamadaCaption(texts[0], me ?? { first_name: null }), rows, 0).catch(() => undefined);
  const others = texts
    .slice(1)
    .map((t, i) => `${i + 2}. ${t.pt}`)
    .join("\n\n");
  await telegram(token, "sendMessage", {
    chat_id: Number(admin),
    text:
      `📣 Chamada para quem gerou o pagamento e não pagou (últimos 3 dias, +18, sem bloqueio e sem compra): ${c.targets.length} fã(s).\n` +
      `Cada um recebe uma vez, com uma das ${texts.length} mensagens (a 1ª está acima), uma das prévias borradas e os botões do pacote que escolheu (dólar e Pix).` +
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

// botoes do administrador no Telegram (chamadaok / chamadano)
export async function chamadaCallback(token: string, chatId: number, kind: string, id: string): Promise<void> {
  try {
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
      const rows = await chamadaButtons(target.product);
      if (!rows) throw new Error("nenhum pacote à venda");
      await sendOne(token, fan.telegram_id, chamadaCaption(c.texts[c.next % c.texts.length], fan), rows, c.next);
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
