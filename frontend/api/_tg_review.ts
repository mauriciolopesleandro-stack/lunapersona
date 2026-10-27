import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { NETWORK_RULES, UPLOAD_BUCKET, type PostRow } from "./_posts.js";
import { Problem, decidePost } from "./_posts_api.js";
import { storeConfigured, storeDelete, storeGet, storeSet } from "./_store.js";
import { dbSelect, storageReadUrl } from "./_supabase.js";
import { forgetChannel, saveChannel, telegram, telegramChannel, telegramConfig, type TelegramChannel } from "./_telegram.js";
import { queueCorrection } from "./_worker.js";

// Aprovacao pelo Telegram: a versao em revisao chega no celular do
// administrador (foto/video + legenda + horario) com os botoes Aprovar,
// Reprovar e Pedir correcao. O clique volta pelo webhook
// (/api/telegram/webhook -> /api/studio?r=telegram-hook) e decide com as
// mesmas regras do estudio (decidePost). So a conversa do administrador
// (chat salvo em Meu perfil) e atendida; o Telegram assina cada chamada com
// o segredo do webhook.
//
// Pedir correcao: o texto seguinte do administrador (respondendo a pergunta
// ou escrito direto no chat, ate 30 min depois do botao) e o pedido. Se a
// publicacao veio do plano, o robo do PC edita a foto na Naiaflow com esse
// pedido e a versao corrigida volta aqui para aprovar.
//
// Canal +18: o administrador cria um canal privado e poe o bot como
// administrador (com "Publicar mensagens"). O Telegram avisa por aqui
// (my_chat_member) e o canal fica salvo - so vale quando quem adicionou e o
// proprio administrador, para ninguem ligar o bot num canal de outra pessoa.
// No horario, a publicacao +18 aprovada sai sozinha no canal (_jobs.ts).

const CORRECTION_TAG = "#correcao";
const PENDING_KEY = "luna:tg-correcao";
const PENDING_TTL_MS = 30 * 60_000;
const LINK_TTL = 3 * 24 * 3600; // as fotos do Telegram ficam salvas no app; o link so precisa servir no envio

export function webhookSecret(token: string): string {
  const key = process.env.TOKEN_ENCRYPTION_KEY || token;
  return createHmac("sha256", key).update(`telegram-webhook-v1|${token}`).digest("hex");
}

export async function registerWebhook(token: string, baseUrl: string): Promise<void> {
  await telegram(token, "setWebhook", {
    url: `${baseUrl}/api/telegram/webhook`,
    secret_token: webhookSecret(token),
    allowed_updates: ["callback_query", "message", "my_chat_member"],
  });
}

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" }) : "sem horário (escolha no estúdio)";
}

function buttons(post: PostRow) {
  const ref = `${post.id}:${post.version}`;
  return {
    inline_keyboard: [
      [
        { text: "✅ Aprovar", callback_data: `ap:${ref}` },
        { text: "❌ Reprovar", callback_data: `rp:${ref}` },
      ],
      [{ text: "✏️ Pedir correção", callback_data: `cr:${ref}` }],
    ],
  };
}

export async function sendForReview(post: PostRow, formats: string[]): Promise<void> {
  const config = await telegramConfig();
  if (!config.token || !config.chatId) throw new Problem(400, "Conecte o bot do Telegram em Meu perfil antes de mandar para aprovar.");
  const uploads = await dbSelect<{ storage_path: string; mime_type: string }>(
    "post_uploads",
    `select=storage_path,mime_type&post_id=eq.${post.id}&post_version=eq.${post.version}&deleted_at=is.null&order=position.asc`
  );
  const media = await Promise.all(
    uploads.map(async (u) => ({ type: u.mime_type.startsWith("video/") ? "video" : "photo", url: await storageReadUrl(UPLOAD_BUCKET, u.storage_path, LINK_TTL) }))
  );
  const label = NETWORK_RULES[post.network as keyof typeof NETWORK_RULES]?.label ?? post.network;
  const text = [
    `📝 Para aprovar — ${label}${post.network === "telegram" ? " +18" : ""} ${formats.join(", ")} (versão ${post.version})`,
    `🕒 ${fmt(post.scheduled_at)}`,
    ...(post.flags?.igAudio ? [`🎵 ${post.flags.igAudio.title} — ${post.flags.igAudio.artist}`] : []),
    "",
    [post.caption, post.hashtags.join(" ")].filter(Boolean).join("\n\n") || "(sem legenda)",
  ]
    .join("\n")
    .slice(0, 1000);
  const base = { chat_id: config.chatId };
  if (media.length === 1) {
    const [m] = media;
    await telegram(config.token, m.type === "video" ? "sendVideo" : "sendPhoto", { ...base, [m.type]: m.url, caption: text, reply_markup: buttons(post) });
    return;
  }
  // carrossel: o album vai junto, os botoes ficam na mensagem logo depois
  if (media.length > 1) {
    await telegram(config.token, "sendMediaGroup", { ...base, media: media.slice(0, 10).map((m) => ({ type: m.type, media: m.url })) });
  }
  await telegram(config.token, "sendMessage", { ...base, text, reply_markup: buttons(post) });
}

// Aviso com as midias (ex.: rascunho do TikTok que chegou no app): as copias
// temporarias vao junto para o administrador ver o que publicar. Respeita a
// opcao "mandar foto/video junto" de Meu perfil; sem ela, so o texto.
export async function sendMediaNotice(copies: { storage_path: string; mime_type: string }[], text: string): Promise<void> {
  const config = await telegramConfig();
  if (!config.token || !config.chatId) return;
  const base = { chat_id: config.chatId };
  if (!config.media || !copies.length) {
    await telegram(config.token, "sendMessage", { ...base, text });
    return;
  }
  const media = await Promise.all(
    copies.slice(0, 10).map(async (c) => ({ type: c.mime_type.startsWith("video/") ? "video" : "photo", url: await storageReadUrl(UPLOAD_BUCKET, c.storage_path, LINK_TTL) }))
  );
  if (media.length === 1) {
    await telegram(config.token, media[0].type === "video" ? "sendVideo" : "sendPhoto", { ...base, [media[0].type]: media[0].url, caption: text.slice(0, 1000) });
    return;
  }
  await telegram(config.token, "sendMediaGroup", { ...base, media: media.map((m, i) => ({ type: m.type, media: m.url, ...(i === 0 ? { caption: text.slice(0, 1000) } : {}) })) });
}

// Posta a versao aprovada no canal +18: foto/video com a legenda, ou album
// (a legenda vai na primeira midia). Devolve o link da mensagem no canal.
export async function sendToChannel(post: PostRow, channel: TelegramChannel): Promise<{ messageId: number; url: string }> {
  const config = await telegramConfig();
  if (!config.token) throw new Problem(400, "O bot do Telegram não está conectado.");
  const uploads = await dbSelect<{ storage_path: string; mime_type: string }>(
    "post_uploads",
    `select=storage_path,mime_type&post_id=eq.${post.id}&post_version=eq.${post.version}&deleted_at=is.null&order=position.asc`
  );
  if (!uploads.length) throw new Problem(409, "As mídias desta versão não estão no armazenamento temporário.");
  const media = await Promise.all(
    uploads.slice(0, 10).map(async (u) => ({ type: u.mime_type.startsWith("video/") ? "video" : "photo", url: await storageReadUrl(UPLOAD_BUCKET, u.storage_path, LINK_TTL) }))
  );
  const caption = [post.caption, post.hashtags.join(" ")].filter(Boolean).join("\n\n").slice(0, 1024);
  const base = { chat_id: channel.id };
  let messageId: number;
  if (media.length === 1) {
    const [m] = media;
    const sent = await telegram<{ message_id: number }>(config.token, m.type === "video" ? "sendVideo" : "sendPhoto", { ...base, [m.type]: m.url, ...(caption ? { caption } : {}) });
    messageId = sent.message_id;
  } else {
    const sent = await telegram<{ message_id: number }[]>(config.token, "sendMediaGroup", {
      ...base,
      media: media.map((m, i) => ({ type: m.type, media: m.url, ...(i === 0 && caption ? { caption } : {}) })),
    });
    messageId = sent[0].message_id;
  }
  // canal privado: link t.me/c/<id sem o -100>/<mensagem> (abre para quem e membro)
  return { messageId, url: `https://t.me/c/${channel.id.replace(/^-100/, "")}/${messageId}` };
}

interface ChatMemberUpdate {
  chat: { id: number; type: string; title?: string };
  from: { id: number };
  new_chat_member: { status: string; can_post_messages?: boolean; can_invite_users?: boolean; can_restrict_members?: boolean };
}

// Bot posto (ou tirado) como administrador de um canal.
async function channelMembership(update: ChatMemberUpdate, adminChat: string, say: (text: string) => Promise<unknown>) {
  if (update.chat.type !== "channel" || String(update.from.id) !== adminChat) return;
  const id = String(update.chat.id);
  const title = update.chat.title ?? "canal";
  const current = await telegramChannel();
  const member = update.new_chat_member;
  if (member.status === "administrator") {
    if (member.can_post_messages === false) {
      await say(`⚠️ O bot entrou no canal “${title}”, mas sem a permissão “Publicar mensagens”. Edite o administrador e ligue essa permissão.`);
      return;
    }
    await saveChannel({ id, title });
    // o VIP do bot de vendas convida por link e tira do canal quem venceu
    const vipMissing = [member.can_invite_users === false && "“Convidar usuários via link”", member.can_restrict_members === false && "“Banir usuários”"].filter(Boolean);
    await say(
      `📢 Canal “${title}” conectado. As publicações +18 que você aprovar vão sair lá sozinhas, no horário marcado.` +
        (vipMissing.length ? `\n\n⚠️ Para o VIP das vendas funcionar, ligue também ${vipMissing.join(" e ")} nas permissões do bot no canal.` : "")
    );
  } else if (current?.id === id) {
    await forgetChannel();
    await say(`📢 O bot saiu do canal “${title}”. As publicações +18 voltam a chegar aqui para você postar à mão.`);
  }
}

interface Update {
  callback_query?: {
    id: string;
    data?: string;
    message?: { message_id: number; chat: { id: number } };
  };
  my_chat_member?: ChatMemberUpdate;
  message?: {
    message_id: number;
    chat: { id: number };
    text?: string;
    reply_to_message?: { text?: string };
  };
}

function safeSame(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

function decided(post: PostRow): string {
  if (post.status === "agendado") return `✅ Aprovada (versão ${post.version}). Sai em ${fmt(post.scheduled_at)}.`;
  if (post.status === "aprovado") return `✅ Aprovada (versão ${post.version}). Escolha o horário no estúdio.`;
  return "❌ Reprovada: voltou para rascunho no estúdio.";
}

async function isDraft(id: string): Promise<boolean> {
  if (!/^[0-9a-f-]{36}$/i.test(id)) return false;
  const [post] = await dbSelect<{ status: string }>("posts", `select=status&id=eq.${id}`);
  return post?.status === "rascunho";
}

// correcao pedida pelo botao ha pouco, esperando o texto
async function pendingCorrection(): Promise<string> {
  if (!storeConfigured()) return "";
  const raw = await storeGet(PENDING_KEY).catch(() => null);
  try {
    const pending = JSON.parse(raw ?? "null") as { ref: string; at: number } | null;
    return pending && Date.now() - pending.at < PENDING_TTL_MS ? pending.ref : "";
  } catch {
    return "";
  }
}

// POST do Telegram (webhook). Sempre responde 200 para o Telegram nao repetir.
export default async function telegramHook(req: IncomingMessage, res: ServerResponse) {
  const done = (status = 200) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json");
    res.end("{}");
  };
  if (req.method !== "POST") return done(405);
  const config = await telegramConfig();
  const given = String(req.headers["x-telegram-bot-api-secret-token"] ?? "");
  if (!config.token || !config.chatId || !safeSame(given, webhookSecret(config.token))) return done(401);
  let update: Update;
  try {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    update = JSON.parse(raw || "{}");
  } catch {
    return done();
  }
  const token = config.token;
  const chatId = config.chatId;
  const say = (text: string, extra: Record<string, unknown> = {}) => telegram(token, "sendMessage", { chat_id: chatId, text, ...extra }).catch(() => undefined);

  if (update.my_chat_member) {
    await channelMembership(update.my_chat_member, chatId, (text) => say(text)).catch(() => undefined);
    return done();
  }

  const cb = update.callback_query;
  if (cb) {
    if (String(cb.message?.chat.id ?? "") !== chatId) return done();
    const [kind, id, v] = (cb.data ?? "").split(":");
    const version = Number(v);
    await telegram(token, "answerCallbackQuery", { callback_query_id: cb.id }).catch(() => undefined);
    const replyTo = cb.message ? { reply_to_message_id: cb.message.message_id } : {};
    try {
      if (kind === "ap" || kind === "rp") {
        const post = await decidePost(id, version, kind === "ap" ? "approve" : "reject", kind === "rp" ? "Reprovada pelo Telegram" : null);
        if (cb.message) await telegram(token, "editMessageReplyMarkup", { chat_id: chatId, message_id: cb.message.message_id, reply_markup: { inline_keyboard: [] } }).catch(() => undefined);
        await say(decided(post), replyTo);
      } else if (kind === "cr") {
        if (storeConfigured()) await storeSet(PENDING_KEY, JSON.stringify({ ref: `${id}:${version}`, at: Date.now() })).catch(() => undefined);
        await say(`✏️ O que precisa mudar? Responda esta mensagem com a correção.\n${CORRECTION_TAG} ${id}:${version}`, { ...replyTo, reply_markup: { force_reply: true } });
      }
    } catch (e) {
      await say(`⚠️ ${e instanceof Error ? e.message : String(e)}`, replyTo);
    }
    return done();
  }

  const msg = update.message;
  if (msg && String(msg.chat.id) === chatId && msg.text && !msg.text.startsWith("/")) {
    const ref = msg.reply_to_message?.text?.includes(CORRECTION_TAG)
      ? msg.reply_to_message.text.split(CORRECTION_TAG)[1]?.trim() ?? ""
      : await pendingCorrection();
    if (!ref) return done();
    const [id, v] = ref.split(":");
    const replyTo = { reply_to_message_id: msg.message_id };
    try {
      // ja reprovada antes (volta para rascunho): a correcao ainda vale para o robo
      if (!(await isDraft(id))) await decidePost(id, Number(v), "reject", `Correção pedida pelo Telegram: ${msg.text}`);
      if (storeConfigured()) await storeDelete(PENDING_KEY).catch(() => undefined);
      if (await queueCorrection(id, msg.text)) {
        await say("✏️ Correção anotada: o robô do PC vai editar a foto na Naiaflow com o seu pedido e a nova versão chega aqui para aprovar.", replyTo);
      } else {
        await say("✏️ Correção anotada: a publicação voltou para rascunho no estúdio com o seu pedido.", replyTo);
      }
    } catch (e) {
      await say(`⚠️ ${e instanceof Error ? e.message : String(e)}`, replyTo);
    }
  }
  return done();
}
