import type { IncomingMessage, ServerResponse } from "http";
import { createHmac, timingSafeEqual } from "crypto";
import { telegram, telegramConfig } from "./_telegram.js";
import { pedidoPhoto, type TgPhotoMessage } from "./_tg_pedido.js";
import { videoMessage, type TgVideoMessage } from "./_tg_video.js";

// Referencia aprovada no Reference Bot (bot "Aprova Luna", 05/11): o Reference Bot
// manda a foto/video no chat do administrador com o bot Luna studii (sendPhoto /
// sendVideo com o token do Luna studii) e chama esta rota com a mensagem que o
// Telegram devolveu. Aqui ela segue o mesmo caminho de uma foto/video mandado a
// mao no bot (pedido pelo Telegram): le, pergunta o destino e o resto e decidido
// no chat. Um bot nao recebe as proprias mensagens no webhook, por isso a chamada.
//
// Assinatura: X-Ref-Signature = HMAC-SHA256(chave = token do Luna studii, corpo).
// O Reference Bot ja tem esse token (entrega para o gerador); nada novo a guardar.

type Sent = TgPhotoMessage & TgVideoMessage & { chat?: { id: number | string } };

function signatureOk(raw: string, given: string, token: string): boolean {
  const want = createHmac("sha256", token).update(raw).digest("hex");
  const a = Buffer.from(want);
  const b = Buffer.from(given.trim().toLowerCase());
  return a.length === b.length && timingSafeEqual(a, b);
}

export default async function refForward(req: IncomingMessage, res: ServerResponse) {
  const reply = (status: number, body: Record<string, unknown>) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(body));
  };
  if (req.method !== "POST") return reply(405, { detail: "Use POST." });
  const config = await telegramConfig();
  if (!config.token || !config.chatId) return reply(503, { detail: "Bot do Telegram não configurado no estúdio." });
  let raw = "";
  for await (const chunk of req) raw += chunk;
  if (!signatureOk(raw, String(req.headers["x-ref-signature"] ?? ""), config.token)) return reply(401, { detail: "Assinatura inválida." });
  let body: { message?: Sent; source_url?: string };
  try {
    body = JSON.parse(raw || "{}");
  } catch {
    return reply(400, { detail: "JSON inválido." });
  }
  const msg = body.message;
  if (!msg?.message_id || String(msg.chat?.id ?? config.chatId) !== String(config.chatId)) {
    return reply(400, { detail: "Mensagem do Telegram ausente ou de outro chat." });
  }
  const token = config.token;
  const chatId = config.chatId;
  const say = (text: string, extra: Record<string, unknown> = {}) =>
    telegram(token, "sendMessage", { chat_id: chatId, text, ...extra }).catch(() => undefined);
  if (msg.photo?.length) {
    await pedidoPhoto(token, msg, say);
    return reply(200, { ok: true, kind: "photo" });
  }
  if (msg.video || msg.document?.mime_type?.startsWith("video/")) {
    await videoMessage(msg, say);
    return reply(200, { ok: true, kind: "video" });
  }
  return reply(400, { detail: "A mensagem não tem foto nem vídeo." });
}
