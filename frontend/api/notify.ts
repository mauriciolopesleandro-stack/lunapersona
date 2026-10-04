import { createHash, createHmac, timingSafeEqual } from "crypto";
import type { IncomingMessage, ServerResponse } from "http";
import { sendNotice, telegramConfig } from "./_telegram.js";

const MAX_AGE_SECONDS = 300;

// Mesma chave do backend (backend/app/notify.py): derivada da RUNPOD_API_KEY,
// que o pod e a Vercel ja tem - sem segredo novo para distribuir.
function notifyKey(): Buffer | null {
  const apiKey = process.env.RUNPOD_API_KEY;
  return apiKey ? createHash("sha256").update(`luna-notify:${apiKey}`).digest() : null;
}

function validSignature(raw: string, signature: string, key: Buffer): boolean {
  const expected = createHmac("sha256", key).update(raw).digest();
  let given: Buffer;
  try {
    given = Buffer.from(signature, "hex");
  } catch {
    return false;
  }
  return given.length === expected.length && timingSafeEqual(given, expected);
}

// POST /api/notify - o pod avisa que uma tarefa terminou; repassa para o
// Telegram. Sem login (quem chama e o pod), entao so aceita corpo assinado
// e recente: ninguem de fora consegue mandar mensagem pelo bot.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (req.method !== "POST") return reply(405, { detail: "Use POST." });

  const key = notifyKey();
  if (!key) return reply(501, { detail: "RUNPOD_API_KEY nao configurada." });
  let raw = "";
  for await (const chunk of req) raw += chunk;
  const signature = String(req.headers["x-luna-signature"] ?? "");
  if (!validSignature(raw, signature, key)) return reply(403, { detail: "Assinatura invalida." });

  let body: { ts?: unknown; text?: unknown; photo?: unknown; video?: unknown };
  try {
    body = JSON.parse(raw);
  } catch {
    return reply(400, { detail: "JSON invalido." });
  }
  const ts = typeof body.ts === "number" ? body.ts : 0;
  if (Math.abs(Date.now() / 1000 - ts) > MAX_AGE_SECONDS) return reply(403, { detail: "Aviso velho demais." });
  const text = typeof body.text === "string" ? body.text.slice(0, 900) : "";
  if (!text) return reply(400, { detail: "Sem texto." });
  const link = (value: unknown) => (typeof value === "string" && value.startsWith("https://") ? value : null);

  const config = await telegramConfig();
  if (!config.token || !config.chatId) return reply(200, { sent: false, reason: "telegram nao conectado" });
  try {
    await sendNotice(config, text, link(body.photo), link(body.video));
  } catch (e) {
    return reply(502, { detail: e instanceof Error ? e.message : String(e) });
  }
  return reply(200, { sent: true });
}
