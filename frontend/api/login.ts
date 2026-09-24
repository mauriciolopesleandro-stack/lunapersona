import type { IncomingMessage, ServerResponse } from "http";
import { buildSetCookie, safeEqual } from "./_auth.js";

// POST /api/login { password, remember }
// Senha unica (conta pessoal, sem multiplos usuarios) guardada em
// APP_LOGIN_PASSWORD na Vercel. Sucesso -> cookie de sessao assinado
// (ver _auth.ts). Roda so aqui (Vercel), nunca depende do pod estar ligado.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end(JSON.stringify({ detail: "Use POST." }));
    return;
  }

  res.setHeader("Content-Type", "application/json");

  const expected = process.env.APP_LOGIN_PASSWORD;
  if (!expected) {
    res.statusCode = 501;
    res.end(JSON.stringify({ detail: "APP_LOGIN_PASSWORD nao configurada na Vercel." }));
    return;
  }

  let body = "";
  for await (const chunk of req) body += chunk;

  let parsed: { password?: string; remember?: boolean };
  try {
    parsed = JSON.parse(body || "{}");
  } catch {
    res.statusCode = 400;
    res.end(JSON.stringify({ detail: "JSON invalido." }));
    return;
  }

  const password = typeof parsed.password === "string" ? parsed.password : "";
  if (!password || !safeEqual(password, expected)) {
    res.statusCode = 401;
    res.end(JSON.stringify({ detail: "Senha incorreta." }));
    return;
  }

  res.setHeader("Set-Cookie", buildSetCookie(Boolean(parsed.remember)));
  res.statusCode = 200;
  res.end(JSON.stringify({ ok: true }));
}
