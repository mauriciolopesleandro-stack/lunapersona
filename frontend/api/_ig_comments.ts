// Respostas automaticas aos comentarios do Instagram (03/11/2026). A mesma IA
// das legendas (OPENROUTER_MODEL, DeepSeek) responde como a propria Luna, no
// tom da conta e olhando a legenda do post; sem aprovacao (pedido do
// administrador), mas ele recebe no Telegram o que foi respondido e desliga
// com /comentarios off. Roda pelo processador da fila (Supabase Cron, a cada
// minuto), no maximo a cada 4 min: nao depende do PC ligado.
//  - so comentarios de primeiro nivel dos ultimos 7 dias, de outras pessoas,
//    que a conta ainda nao respondeu (nem o administrador a mao);
//  - cada comentario e decidido uma vez so (Upstash luna:ig-c:<id>, 60 dias):
//    respondido ou ignorado (spam, ofensa, pedido explicito...);
//  - falta de permissao (conexao antiga, sem instagram_manage_comments): avisa
//    uma vez por dia para reconectar pelo Facebook.
// Convite no direct (11/11/2026): quem comenta o nome da persona (LUNA) recebe,
// alem da resposta no comentario, uma resposta privada (Private Reply) com o @
// da conta nova - so o @, nunca link, preco, Telegram, +18, VIP. Uma vez por
// pessoa (luna:ig-dm:<usuario>, 30 dias). Liga junto com /comentarios e tem o
// proprio /dmluna on|off|@conta. Sem a permissao de mensagens, avisa uma vez
// por dia e segue respondendo so no comentario.
import { askAi, assistantConfigured } from "./_assistant.js";
import { InstagramError, igComments, igConnection, igPrivateReply, igRecentMedia, igReplyComment, type IgComment, type IgConnection, type IgMedia } from "./_instagram.js";
import { DEFAULT_PERSONA, persona } from "./_persona.js";
import { storeConfigured, storeDelete, storeGet, storeSet, storeSetIfAbsent } from "./_store.js";
import { sendNotice, telegramConfig } from "./_telegram.js";

export const SWITCH_KEY = "luna:ig-comentarios";
const STATUS_KEY = "luna:ig-comentarios-status";
const TICK_KEY = "luna:ig-comentarios-tick";
const SEEN = (id: string) => `luna:ig-c:${id}`;
const WINDOW_MS = 7 * 86400_000;
const MAX_MEDIA = 8;
const MAX_PER_TICK = 8;
// convite no direct
export const DM_SWITCH_KEY = "luna:ig-dm";
const DM_ACCOUNT_KEY = "luna:ig-dm-conta";
const DM_SENT = (username: string) => `luna:ig-dm:${username.toLowerCase()}`;
const DM_ONCE_S = 30 * 86400;
// a Meta aceita a resposta privada ate 7 dias depois do comentario; margem de 1 h
const DM_WINDOW_MS = 7 * 86400_000 - 3600_000;

export const REPLY_SYSTEM = `Você é a Luna Vox (@lunavoxlopes), influenciadora virtual brasileira, respondendo os comentários do SEU próprio Instagram.
Quem é a Luna: garota praiana, alegre, carinhosa, confiante e um pouco provocante; posta um pedacinho da rotina — praia, piscina, casa, academia, rolê à noite, viagens, futebol. Fala como gente de verdade no Instagram: português do Brasil informal, leve, simpática, com charme.
Como responder:
- Em primeira pessoa, como a própria Luna, respondendo ao que a pessoa escreveu e ao post (veja a legenda do post).
- Curto: uma frase, no máximo 15 palavras, com 0 a 2 emojis do estilo da conta (❤️ 🥰 👀 😏 ☀️ 🌊 💃 🎶 🩷 😘 🙈).
- Elogio: agradece com charme (sem ser melosa demais). Pergunta: responde curtinho e simpático, sem inventar endereço, telefone, cidade exata ou compromisso. Só emoji: devolve algo curtinho e fofo.
- Cada resposta diferente das outras; nada de frase pronta repetida.
- Não escreva @ nem o nome de usuário (o Instagram já marca a pessoa) e não use aspas.
- Se perguntarem se ela é real ou IA: responde com leveza que é uma influenciadora virtual criada com IA.
Nunca:
- nada sexual ou explícito (sensual leve e brincalhão pode, como nas legendas), nada de prometer encontro, mandar contato, link, preço, Telegram, VIP, +18 ou "conteúdo exclusivo";
- política, religião, falar mal de alguém, discutir.
Comentário marcado CONVITE (a pessoa escreveu o seu nome para receber o convite): responda sempre (responder: true), mesmo que seja só a palavra, com uma frase curtinha e carinhosa; só não responda se for ofensa, assédio pesado ou pessoa que parece menor de idade.
Não responda (responder: false) quando for: spam, propaganda, link, "sigam meu perfil", golpe; ofensa, ódio ou assédio pesado; pedido sexual explícito ou nudes; pessoa que parece menor de idade; texto sem sentido.
Devolva só JSON: {"respostas":[{"id":"<id do comentário>","responder":true,"texto":"<resposta>"},{"id":"...","responder":false,"motivo":"<curto>"}]}`;

interface Pending {
  comment: IgComment;
  media: IgMedia;
}

interface Decision {
  id: string;
  responder?: boolean;
  texto?: string;
  motivo?: string;
}

export interface CommentsStatus {
  at: string;
  replied: number;
  ignored: number;
  dms?: number;
  error: string | null;
}

export async function commentsEnabled(): Promise<boolean> {
  return (await storeGet(SWITCH_KEY).catch(() => null)) !== "off";
}

export async function setCommentsEnabled(on: boolean): Promise<void> {
  await storeSet(SWITCH_KEY, on ? "on" : "off");
}

export async function commentsStatus(): Promise<CommentsStatus | null> {
  const raw = await storeGet(STATUS_KEY).catch(() => null);
  return raw ? (JSON.parse(raw) as CommentsStatus) : null;
}

const quote = (s: string, n = 80) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

// --- convite no direct ---

export async function dmEnabled(): Promise<boolean> {
  return (await storeGet(DM_SWITCH_KEY).catch(() => null)) !== "off";
}

export async function setDmEnabled(on: boolean): Promise<void> {
  await storeSet(DM_SWITCH_KEY, on ? "on" : "off");
}

// @ da conta nova (sem o @). Sem conta guardada, o convite nao sai.
export async function dmAccount(): Promise<string | null> {
  return (await storeGet(DM_ACCOUNT_KEY).catch(() => null)) || null;
}

export async function setDmAccount(handle: string): Promise<string> {
  const clean = handle.replace(/^@/, "").trim().toLowerCase();
  if (!/^[a-z0-9._]{1,30}$/.test(clean)) throw new Error("@ inválido: use só letras, números, ponto e sublinhado (ex.: /dmluna @minhaconta).");
  await storeSet(DM_ACCOUNT_KEY, clean);
  return clean;
}

const plain = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

// O comentario tem a palavra-chave (o nome da persona: LUNA), sozinha ou na frase,
// maiuscula ou minuscula, com ou sem emoji. "@lunavoxlopes" nao conta.
export function hasKeyword(text: string, word = persona().name): boolean {
  const w = plain(word).replace(/[^a-z0-9]/g, "");
  if (!w) return false;
  return new RegExp(`(^|[^a-z0-9@._])${w}(?![a-z0-9_]|\\.[a-z0-9_])`).test(plain(text));
}

// Texto fixo: so o @ da conta nova. Nunca link, preco, Telegram, +18, VIP.
export function dmText(handle: string): string {
  return `Oi! Que bom que você veio 🥰 Meu cantinho mais reservado é o @${handle}, me segue lá que eu posto todo dia nos stories 🌙`;
}

// Falta de permissao de mensagens: aviso uma vez por dia (separado dos comentarios).
async function warnDmPermission(message: string): Promise<void> {
  const day = new Date().toISOString().slice(0, 10);
  if (!(await storeSetIfAbsent(`luna:ig-dm-aviso:${day}`, "1", 2 * 86400).catch(() => false))) return;
  const name = persona().name;
  await sendNotice(
    await telegramConfig(),
    `⚠️ A ${name} ainda não consegue mandar o convite no direct para quem comenta ${name.toUpperCase()} (${quote(message, 140)}).\nFalta a permissão de mensagens (instagram_manage_messages) na configuração do login da Meta; depois reconecte o Instagram no estúdio (Configurações → Contas conectadas). Enquanto isso ela responde só no comentário.\nDesligar o convite: /dmluna off`
  ).catch(() => undefined);
}

const own = (conn: IgConnection, name?: string) => (name ?? "").toLowerCase() === conn.username.toLowerCase();

// Resposta limpa: sem link, sem @, sem aspas, curta.
export function cleanReply(text: string): string {
  return text
    .replace(/https?:\/\/\S+|www\.\S+/gi, "")
    .replace(/@[\w.]+/g, "")
    .replace(/["“”]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 220);
}

// Pede a decisao de todos os comentarios numa chamada so.
async function decide(list: Pending[], invite: boolean): Promise<Decision[]> {
  const posts = [...new Map(list.map((p) => [p.media.id, p.media])).values()];
  const prompt = [
    "Posts:",
    ...posts.map((m, i) => `P${i + 1} (${m.media_type ?? "post"}): ${(m.caption ?? "sem legenda").replace(/\s+/g, " ").slice(0, 300)}`),
    "",
    "Comentários para responder:",
    ...list.map(
      (p) =>
        `- id ${p.comment.id} | post P${posts.indexOf(p.media) + 1}${invite && hasKeyword(p.comment.text ?? "") ? " | CONVITE" : ""} | ${(p.comment.text ?? "").replace(/\s+/g, " ").slice(0, 300)}`
    ),
  ].join("\n");
  const out = await askAi(
    "normal",
    [
      { role: "system", content: REPLY_SYSTEM },
      { role: "user", content: prompt },
    ],
    { json: true, maxTokens: 900, timeoutMs: 25_000, temperature: 0.9 }
  );
  const json = out.text.replace(/^```(?:json)?\s*|\s*```$/g, "");
  const parsed = JSON.parse(json) as { respostas?: Decision[] };
  return Array.isArray(parsed.respostas) ? parsed.respostas : [];
}


async function saveStatus(status: CommentsStatus): Promise<void> {
  await storeSet(STATUS_KEY, JSON.stringify(status)).catch(() => undefined);
}

// Falta de permissao: aviso uma vez por dia.
async function warnPermission(message: string): Promise<void> {
  const day = new Date().toISOString().slice(0, 10);
  if (!(await storeSetIfAbsent(`luna:ig-comentarios-aviso:${day}`, "1", 2 * 86400).catch(() => false))) return;
  await sendNotice(
    await telegramConfig(),
    `⚠️ A Luna ainda não consegue responder os comentários do Instagram (${quote(message, 140)}).\nNo estúdio: Configurações → Contas conectadas → Conectar pelo Facebook, e aceite a permissão de comentários. Depois disso ela responde sozinha.`
  ).catch(() => undefined);
}

const isPermission = (e: unknown) => e instanceof InstagramError && (e.code === 10 || e.code === 200 || /permission/i.test(e.message));

// Uma rodada. Devolve o resumo para o resultado do processador.
export async function commentsTick(): Promise<string | null> {
  if (!storeConfigured() || !assistantConfigured()) return null;
  if (!(await commentsEnabled())) return null;
  // outras personas (11/11): as regras da resposta (REPLY_SYSTEM) ainda sao as da
  // Luna - ate ganharem o texto proprio, nao respondem nada (nem direct)
  if (persona().id !== DEFAULT_PERSONA) return null;
  if (!(await storeSetIfAbsent(TICK_KEY, "1", 240).catch(() => false))) return null;
  const conn = await igConnection().catch(() => null);
  if (!conn) return null;
  const dmHandle = (await dmEnabled()) ? await dmAccount() : null;
  let dmBlocked = false;

  const pending: Pending[] = [];
  try {
    const since = Date.now() - WINDOW_MS;
    const media = (await igRecentMedia(conn)).filter((m) => (m.comments_count ?? 0) > 0).slice(0, MAX_MEDIA);
    for (const m of media) {
      if (pending.length >= MAX_PER_TICK) break;
      for (const c of await igComments(conn, m.id)) {
        if (pending.length >= MAX_PER_TICK) break;
        if (!c.text?.trim() || own(conn, c.username)) continue;
        if (new Date(c.timestamp ?? 0).getTime() < since) continue;
        if ((c.replies?.data ?? []).some((r) => own(conn, r.username))) continue;
        if (await storeGet(SEEN(c.id)).catch(() => "erro")) continue;
        pending.push({ comment: c, media: m });
      }
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    await saveStatus({ at: new Date().toISOString(), replied: 0, ignored: 0, error: msg.slice(0, 300) });
    if (isPermission(e)) await warnPermission(msg);
    return `erro: ${msg.slice(0, 120)}`;
  }
  if (!pending.length) {
    await saveStatus({ at: new Date().toISOString(), replied: 0, ignored: 0, dms: 0, error: null });
    return "0";
  }

  let decisions: Decision[];
  try {
    decisions = await decide(pending, Boolean(dmHandle));
  } catch (e) {
    // IA fora do ar ou JSON torto: tenta de novo na proxima rodada
    const msg = e instanceof Error ? e.message : String(e);
    await saveStatus({ at: new Date().toISOString(), replied: 0, ignored: 0, error: `IA: ${msg.slice(0, 280)}` });
    return `erro: IA ${msg.slice(0, 100)}`;
  }

  const replied: string[] = [];
  const ignored: string[] = [];
  const dms: string[] = [];
  let error: string | null = null;
  let dmError: string | null = null;

  // Convite no direct: uma vez por pessoa, ate 7 dias depois do comentario.
  const invite = async (p: Pending) => {
    const user = p.comment.username;
    if (!dmHandle || dmBlocked || !user || !hasKeyword(p.comment.text ?? "")) return;
    if (Date.now() - new Date(p.comment.timestamp ?? 0).getTime() > DM_WINDOW_MS) return;
    if (!(await storeSetIfAbsent(DM_SENT(user), p.comment.id, DM_ONCE_S).catch(() => false))) return;
    try {
      await igPrivateReply(conn, p.comment.id, dmText(dmHandle));
      dms.push(`@${user}`);
    } catch (e) {
      dmError = (e instanceof Error ? e.message : String(e)).slice(0, 300);
      // falhou: libera a pessoa para receber numa proxima vez
      await storeDelete(DM_SENT(user)).catch(() => undefined);
      if (isPermission(e) || (e instanceof InstagramError && e.code === 3)) {
        dmBlocked = true;
        await warnDmPermission(dmError);
      }
    }
  };

  for (const p of pending) {
    const d = decisions.find((x) => String(x.id) === p.comment.id);
    if (!d) continue; // a IA pulou: fica para a proxima rodada
    const text = d.responder ? cleanReply(d.texto ?? "") : "";
    // trava: cada comentario e decidido uma vez so
    if (!(await storeSetIfAbsent(SEEN(p.comment.id), text ? "respondido" : "ignorado", 60 * 86400).catch(() => false))) continue;
    const who = `@${p.comment.username ?? "alguém"}`;
    if (!text) {
      ignored.push(`${who}: ${quote(p.comment.text ?? "")}${d.motivo ? ` (${quote(d.motivo, 40)})` : ""}`);
      continue;
    }
    try {
      await igReplyComment(conn, p.comment.id, `@${p.comment.username} ${text}`.trim());
      replied.push(`${who}: ${quote(p.comment.text ?? "")}\n↳ ${text}`);
      await invite(p);
    } catch (e) {
      error = (e instanceof Error ? e.message : String(e)).slice(0, 300);
      // falha passageira: libera para tentar na proxima rodada
      if (!(e instanceof InstagramError) || e.retryable || isPermission(e)) await storeDelete(SEEN(p.comment.id)).catch(() => undefined);
      if (isPermission(e)) {
        await warnPermission(error);
        break;
      }
    }
  }
  await saveStatus({
    at: new Date().toISOString(),
    replied: replied.length,
    ignored: ignored.length,
    dms: dms.length,
    error: error ?? (dmError ? `direct: ${dmError}` : null),
  });
  if (replied.length || ignored.length) {
    const lines = [
      ...(replied.length ? [`💬 A ${persona().name} respondeu ${replied.length} comentário(s) no Instagram:`, "", ...replied.map((r) => `${r}\n`)] : []),
      ...(ignored.length ? [`🙈 Sem resposta (${ignored.length}):`, ...ignored, ""] : []),
      ...(dms.length ? [`📩 Convite no direct enviado (${dms.length}): ${dms.join(", ")}`, ""] : []),
      `Desligar: /comentarios off${dmHandle ? " · só o direct: /dmluna off" : ""}`,
    ];
    await sendNotice(await telegramConfig(), lines.join("\n").slice(0, 4000)).catch(() => undefined);
  }
  return `${replied.length} respondido(s), ${ignored.length} ignorado(s), ${dms.length} direct(s)${error ? `, erro: ${error.slice(0, 80)}` : ""}${dmError ? `, erro direct: ${(dmError as string).slice(0, 80)}` : ""}`;
}

// Texto do /comentarios no bot de aprovacao.
export async function commentsText(): Promise<string> {
  const on = await commentsEnabled();
  const st = await commentsStatus();
  const when = st ? new Date(st.at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" }) : null;
  return [
    `💬 Respostas automáticas aos comentários do Instagram: ${on ? "✅ ligadas" : "⏸️ desligadas"}`,
    when
      ? `Última olhada: ${when} — ${st!.replied} respondido(s), ${st!.ignored} sem resposta, ${st!.dms ?? 0} direct(s)${st!.error ? `\n⚠️ ${quote(st!.error, 200)}` : ""}`
      : "Ainda não rodou.",
    "",
    on ? "Desligar: /comentarios off" : "Ligar: /comentarios on",
    "Convite no direct: /dmluna",
  ].join("\n");
}

// Texto do /dmluna no bot de aprovacao.
export async function dmStatusText(): Promise<string> {
  const [comments, on, handle] = await Promise.all([commentsEnabled(), dmEnabled(), dmAccount()]);
  const word = persona().name.toUpperCase();
  const state = !handle ? "⚙️ falta o @ da conta nova" : !on ? "⏸️ desligado" : !comments ? "⏸️ parado (as respostas aos comentários estão desligadas)" : "✅ ligado";
  return [
    `📩 Convite no direct para quem comenta ${word}: ${state}`,
    handle ? `Conta divulgada: @${handle}` : "Diga qual é a conta: /dmluna @conta_nova",
    ...(handle ? [`Mensagem: ${dmText(handle)}`] : []),
    "Uma vez por pessoa (30 dias). Só o @, sem link.",
    "",
    on ? "Desligar: /dmluna off" : "Ligar: /dmluna on",
    "Trocar a conta: /dmluna @conta_nova",
  ].join("\n");
}
