// Direct do Instagram respondido pela persona (24/11/2026, pedido do
// administrador: "muita gente chama no chat... manda a pessoa ir na Bio").
// A mesma IA das legendas e dos comentarios responde como a Cleuza, na lingua
// de quem escreveu, e leva a conversa para o link da bio (o bot de vendas no
// Telegram, que ja fala a lingua do fa). Nada explicito, sem preco, sem link.
//  - so a conta conectada no estudio (Cleuza: @joobcoroa), so personas com
//    regras proprias (DM_RULES); comeca DESLIGADO: /direct on liga;
//  - so conversa com mensagem nova do fa nas ultimas 23 h (a Meta so deixa
//    responder ate 24 h depois);
//  - cada mensagem do fa e respondida uma vez (luna:ig-dr:<id>);
//  - se alguem responder a mao pela conta, o robo para naquela conversa por
//    24 h (o que o robo manda fica marcado: luna:ig-db:<id> e pelo texto);
//  - no maximo 8 respostas por pessoa por dia; 3 conversas por rodada; rodada
//    no maximo a cada 2 min (processador da fila, Supabase Cron a cada minuto);
//  - o administrador recebe no Telegram o que foi respondido.
import { createHash } from "crypto";
import { askAi, assistantConfigured } from "./_assistant.js";
import { InstagramError, igBio, igConnection, igConversations, igSendDm, type IgConnection, type IgDmMessage, type IgDmThread } from "./_instagram.js";
import { persona } from "./_persona.js";
import { storeConfigured, storeGet, storeIncrWithTtl, storeSet, storeSetIfAbsent } from "./_store.js";
import { sendNotice, telegramConfig } from "./_telegram.js";

const SWITCH_KEY = "luna:ig-direct";
const STATUS_KEY = "luna:ig-direct-status";
const TICK_KEY = "luna:ig-direct-tick";
const DONE = (id: string) => `luna:ig-dr:${id}`;
const BOT_ID = (id: string) => `luna:ig-db:${id}`;
const BOT_TEXT = (thread: string, text: string) => `luna:ig-dbt:${thread}:${createHash("sha1").update(text.trim()).digest("hex").slice(0, 16)}`;
const PAUSE = (thread: string) => `luna:ig-dp:${thread}`;
const COUNT = (user: string, day: string) => `luna:ig-dc:${user}:${day}`;
const REPLY_WINDOW_MS = 23 * 3600_000;
const MAX_THREADS = 3;
const MAX_PER_DAY = 8;
const KEEP_S = 30 * 86400;

export const CLEUZA_DM_SYSTEM = `Você é a Cleuza (@joobcoroa), influenciadora virtual brasileira, respondendo o DIRECT (mensagens privadas) do seu próprio Instagram.
Quem é a Cleuza: uma coroa nordestina, madura e orgulhosa disso, caseira, de churrasco e cerveja, piadista e bem safada na malícia — adora provocar os novinhos que babam nas fotos dela.
Objetivo da conversa (24/11, pedido do dono): interpretar a conversa e interagir de verdade com a pessoa, e SEMPRE levar para o link da bio, onde tem mais conteúdo dela, explicando o caminho certo.
{CAMINHO}
Como responder:
- Língua: responda NA LÍNGUA da última mensagem da pessoa. Português: com jeitinho nordestino (oxe, visse, meu fi, vixe). Espanhol: em espanhol. Qualquer outra língua (inglês, hindi, persa, árabe, indonésio, turco...) ou só emoji: em inglês simples e natural. Nunca escreva em outro alfabeto.
- Curto: 2 ou 3 frases, no máximo 45 palavras, 0 a 2 emojis (😏 🔥 😜 😈 💋 👀 🤭 😘).
- Responda ao que a pessoa escreveu, olhando a conversa; cada resposta diferente, nada de frase pronta repetida.
- Primeiro reaja ao que a pessoa escreveu (elogio, pergunta, emoji, "hi"), com malícia e no jeito dela; depois, em TODA resposta, chame para o link da bio com charme.
- Explique o caminho: na primeira resposta da conversa (ou quando a pessoa perguntar como, disser que não achou ou pedir fotos, vídeos, contato, WhatsApp, Telegram, nudes, preço), explique o caminho completo, passo a passo e curtinho; nas outras, só lembre com variação ("já foi lá no link da minha bio? 😏", "did you check my bio link yet? 🔥").
- Pode dizer que lá tem mais fotos e vídeos dela e que lá ela conversa com a pessoa na língua dela; nada além disso.
- Se perguntarem se ela é real ou IA: responde na brincadeira que é uma influenciadora virtual criada com IA.
Nunca:
- nada sexual explícito: sem palavrão sexual, sem descrever sexo ou partes do corpo, sem prometer ou mandar nudes, fotos ou vídeos pelo direct;
- nada de link escrito, @ de outra conta (só o perfil reservado do caminho, se houver), preço, número de telefone, WhatsApp, e-mail, encontro ou cidade exata;
- política, religião, falar mal de alguém, discutir, prometer o que não existe.
Não responda (responder: false) quando for: spam, propaganda, golpe, pedido de dinheiro; ofensa, ódio ou assédio pesado; pessoa que parece menor de idade; mensagem automática ou sem sentido.
Devolva só JSON: {"responder":true,"texto":"<resposta>"} ou {"responder":false,"motivo":"<curto>"}`;

const DM_RULES: Record<string, string> = { cleuza: CLEUZA_DM_SYSTEM };
export function dmSystem(): string | null {
  return DM_RULES[persona().id] ?? null;
}

export interface DirectStatus {
  at: string;
  replied: number;
  ignored: number;
  error: string | null;
}

export async function directEnabled(): Promise<boolean> {
  return (await storeGet(SWITCH_KEY).catch(() => null)) === "on";
}

export async function setDirectEnabled(on: boolean): Promise<void> {
  await storeSet(SWITCH_KEY, on ? "on" : "off");
}

export async function directStatus(): Promise<DirectStatus | null> {
  const raw = await storeGet(STATUS_KEY).catch(() => null);
  return raw ? (JSON.parse(raw) as DirectStatus) : null;
}

const quote = (s: string, n = 80) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const isOwn = (conn: IgConnection, m: IgDmMessage) =>
  (m.from?.username ?? "").toLowerCase() === conn.username.toLowerCase() || (Boolean(m.from?.id) && m.from?.id === conn.userId);
const isPermission = (e: unknown) => e instanceof InstagramError && (e.code === 10 || e.code === 200 || e.code === 3 || /permission/i.test(e.message));

// Resposta limpa: sem link, sem @, sem telefone, sem aspas, curta.
// "Telegram" pode (24/11: ela explica o caminho bio -> perfil reservado -> Telegram).
const BLOCK = /\b(nudes?|pelad[ao]s?|buceta|xoxota|pau|pica|rola|sexo|transar|foder|fuck|pussy|dick|cock|boobs|tits|whats ?app|zap|pix|r\$|us\$|\$\s?\d)\b|\d{7,}/i;
export function cleanDm(text: string, allowHandle: string | null = null): string {
  return text
    .replace(/https?:\/\/\S+|www\.\S+|t\.me\/\S+/gi, "")
    .replace(/@[\w.]+/g, (h) => (allowHandle && h.slice(1).replace(/\.$/, "").toLowerCase() === allowHandle ? h : ""))
    .replace(/["“”]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 300);
}

// O caminho certo ate o conteudo, a partir do link da bio da conta (24/11: o
// @joobcoroa leva ao perfil reservado @cleuzaprivado, cuja bio leva ao Telegram).
export function pathFromBio(website: string | undefined | null): { text: string; handle: string | null } {
  const ig = /instagram\.com\/([a-z0-9._]{1,30})/i.exec(website ?? "");
  if (ig) {
    const handle = ig[1].toLowerCase();
    return {
      handle,
      text: `Caminho certo até o conteúdo: (1) tocar no link da bio do seu perfil; (2) ele abre o seu perfil reservado @${handle}; (3) lá, tocar no link da bio do @${handle}, que abre o seu cantinho no Telegram, com mais fotos e vídeos seus, onde você conversa com a pessoa na língua dela. Pode citar o @${handle}; nenhum outro @.`,
    };
  }
  if (/t\.me\//i.test(website ?? "")) {
    return { handle: null, text: "Caminho certo até o conteúdo: tocar no link da bio do seu perfil; ele abre o seu cantinho no Telegram, com mais fotos e vídeos seus, onde você conversa com a pessoa na língua dela." };
  }
  return { handle: null, text: "Caminho certo até o conteúdo: tocar no link da bio do seu perfil, onde tem mais fotos e vídeos seus." };
}

async function warnPermission(message: string): Promise<void> {
  const day = new Date().toISOString().slice(0, 10);
  if (!(await storeSetIfAbsent(`luna:ig-direct-aviso:${day}`, "1", 2 * 86400).catch(() => false))) return;
  await sendNotice(
    await telegramConfig(),
    `⚠️ A ${persona().name} não conseguiu ler ou responder o direct do Instagram (${quote(message, 160)}).\nConfira a permissão de mensagens na Meta e reconecte o Instagram no estúdio. Desligar: /direct off`
  ).catch(() => undefined);
}

interface Pending {
  thread: IgDmThread;
  fan: IgDmMessage;
  history: IgDmMessage[];
}

// Uma conversa: o que fazer agora (null = nada).
async function pendingOf(conn: IgConnection, thread: IgDmThread, now: number): Promise<Pending | null> {
  const msgs = thread.messages?.data ?? [];
  const latest = msgs[0];
  if (!latest) return null;
  // alguem respondeu a mao pela conta (mensagem da conta que o robo nao mandou): para 24 h
  for (const m of msgs) {
    if (!isOwn(conn, m) || now - new Date(m.created_time).getTime() > 24 * 3600_000) continue;
    const mine = (await storeGet(BOT_ID(m.id)).catch(() => "?")) || (m.message ? await storeGet(BOT_TEXT(thread.id, m.message)).catch(() => "?") : null);
    if (!mine) {
      await storeSetIfAbsent(PAUSE(thread.id), m.id, 24 * 3600).catch(() => false);
      return null;
    }
  }
  if (isOwn(conn, latest)) return null;
  if (!latest.message?.trim()) return null; // figurinha, audio, foto: nao responde
  if (now - new Date(latest.created_time).getTime() > REPLY_WINDOW_MS) return null;
  if (await storeGet(PAUSE(thread.id)).catch(() => "erro")) return null;
  if (await storeGet(DONE(latest.id)).catch(() => "erro")) return null;
  return { thread, fan: latest, history: [...msgs].reverse() };
}

async function decide(conn: IgConnection, p: Pending, system: string): Promise<{ responder?: boolean; texto?: string; motivo?: string }> {
  const name = persona().name;
  const transcript = p.history
    .map((m) => `${isOwn(conn, m) ? name : "Pessoa"}: ${(m.message ?? "[mídia]").replace(/\s+/g, " ").slice(0, 300)}`)
    .join("\n");
  const out = await askAi(
    "normal",
    [
      { role: "system", content: system },
      { role: "user", content: `Conversa no direct (mais antiga primeiro):\n${transcript}\n\nResponda a última mensagem da Pessoa.` },
    ],
    { json: true, maxTokens: 300, timeoutMs: 20_000, temperature: 0.9 }
  );
  return JSON.parse(out.text.replace(/^```(?:json)?\s*|\s*```$/g, ""));
}

// Uma rodada. Devolve o resumo para o resultado do processador.
export async function directTick(): Promise<string | null> {
  if (!storeConfigured() || !assistantConfigured()) return null;
  const system = dmSystem();
  if (!system || !(await directEnabled())) return null;
  if (!(await storeSetIfAbsent(TICK_KEY, "1", 110).catch(() => false))) return null;
  const conn = await igConnection().catch(() => null);
  if (!conn) return null;

  const now = Date.now();
  const path = pathFromBio((await igBio(conn).catch(() => null))?.website);
  const rules = system.replace("{CAMINHO}", path.text);
  const pending: Pending[] = [];
  try {
    for (const t of await igConversations(conn)) {
      if (pending.length >= MAX_THREADS) break;
      const p = await pendingOf(conn, t, now);
      if (p) pending.push(p);
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    await storeSet(STATUS_KEY, JSON.stringify({ at: new Date().toISOString(), replied: 0, ignored: 0, error: msg.slice(0, 300) })).catch(() => undefined);
    if (isPermission(e)) await warnPermission(msg);
    return `erro: ${msg.slice(0, 120)}`;
  }

  const replied: string[] = [];
  const ignored: string[] = [];
  let error: string | null = null;
  const day = new Date(now - 3 * 3600_000).toISOString().slice(0, 10);
  for (const p of pending) {
    const who = p.fan.from?.username ? `@${p.fan.from.username}` : "alguém";
    const fanId = p.fan.from?.id;
    if (!fanId) continue;
    const count = Number((await storeGet(COUNT(fanId, day)).catch(() => "0")) || 0);
    if (count >= MAX_PER_DAY) {
      await storeSetIfAbsent(DONE(p.fan.id), "limite", KEEP_S).catch(() => false);
      continue;
    }
    let d: { responder?: boolean; texto?: string; motivo?: string };
    try {
      d = await decide(conn, p, rules);
    } catch (e) {
      // IA fora do ar ou JSON torto: tenta de novo na proxima rodada
      error = `IA: ${(e instanceof Error ? e.message : String(e)).slice(0, 200)}`;
      continue;
    }
    const text = d.responder ? cleanDm(d.texto ?? "", path.handle) : "";
    const blocked = Boolean(text) && BLOCK.test(text);
    if (!(await storeSetIfAbsent(DONE(p.fan.id), text && !blocked ? "respondido" : "ignorado", KEEP_S).catch(() => false))) continue;
    if (!text || blocked) {
      ignored.push(`${who}: ${quote(p.fan.message ?? "")}${blocked ? " (resposta barrada pelo filtro)" : d.motivo ? ` (${quote(d.motivo, 40)})` : ""}`);
      continue;
    }
    try {
      // marca antes de mandar: a mensagem do robo nunca e confundida com resposta a mao
      await storeSetIfAbsent(BOT_TEXT(p.thread.id, text), "1", KEEP_S).catch(() => undefined);
      const mid = await igSendDm(conn, fanId, text);
      if (mid) await storeSetIfAbsent(BOT_ID(mid), "1", KEEP_S).catch(() => undefined);
      await storeIncrWithTtl(COUNT(fanId, day), 2 * 86400).catch(() => undefined);
      replied.push(`${who}: ${quote(p.fan.message ?? "")}\n↳ ${text}`);
    } catch (e) {
      error = (e instanceof Error ? e.message : String(e)).slice(0, 300);
      if (isPermission(e)) {
        await warnPermission(error);
        break;
      }
    }
  }
  await storeSet(STATUS_KEY, JSON.stringify({ at: new Date().toISOString(), replied: replied.length, ignored: ignored.length, error })).catch(() => undefined);
  if (replied.length || ignored.length) {
    const lines = [
      ...(replied.length ? [`📩 A ${persona().name} respondeu ${replied.length} mensagem(ns) no direct do Instagram:`, "", ...replied.map((r) => `${r}\n`)] : []),
      ...(ignored.length ? [`🙈 Sem resposta no direct (${ignored.length}):`, ...ignored, ""] : []),
      "Desligar: /direct off",
    ];
    await sendNotice(await telegramConfig(), lines.join("\n").slice(0, 4000)).catch(() => undefined);
  }
  return `${replied.length} respondida(s), ${ignored.length} ignorada(s)${error ? `, erro: ${error.slice(0, 80)}` : ""}`;
}

// Conferencia so de leitura (estudio): conversas recentes e a bio. Nao responde nada.
export async function directCheck(): Promise<{
  conversations: number;
  recent: { user: string | null; last: string; fromAccount: boolean; at: string }[];
  bio: { biography?: string; website?: string } | null;
  error: string | null;
}> {
  const conn = await igConnection();
  if (!conn) return { conversations: 0, recent: [], bio: null, error: "Instagram não conectado." };
  const bio = await igBio(conn).catch(() => null);
  try {
    const threads = await igConversations(conn, 15, 3);
    return {
      conversations: threads.length,
      recent: threads.slice(0, 8).map((t) => {
        const m = t.messages?.data?.[0];
        const other = (t.messages?.data ?? []).find((x) => !isOwn(conn, x));
        return { user: other?.from?.username ?? null, last: quote(m?.message ?? "[mídia]", 60), fromAccount: m ? isOwn(conn, m) : false, at: m?.created_time ?? t.updated_time ?? "" };
      }),
      bio,
      error: null,
    };
  } catch (e) {
    return { conversations: 0, recent: [], bio, error: e instanceof Error ? e.message : String(e) };
  }
}

// Texto do /direct no bot de aprovacao.
export async function directText(): Promise<string> {
  if (!dmSystem()) return `📩 O direct automático ainda não tem as regras da ${persona().name}.`;
  const on = await directEnabled();
  const st = await directStatus();
  const when = st ? new Date(st.at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" }) : null;
  return [
    `📩 Direct do Instagram respondido pela ${persona().name}: ${on ? "✅ ligado" : "⏸️ desligado"}`,
    "Responde na língua da pessoa e chama para o link da bio. Se você responder a mão numa conversa, ela para nela por 24 h.",
    when ? `Última olhada: ${when} — ${st!.replied} respondida(s), ${st!.ignored} sem resposta${st!.error ? `\n⚠️ ${quote(st!.error, 200)}` : ""}` : "Ainda não rodou.",
    "",
    on ? "Desligar: /direct off" : "Ligar: /direct on",
  ].join("\n");
}
