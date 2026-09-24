import type { IncomingMessage, ServerResponse } from "http";
import { buildSetCookie, safeEqual } from "./_auth.js";
import { checkPassword } from "./_password.js";

// POST /api/login { username, password, remember }
// Conta unica (sem multiplos usuarios): e-mail em APP_LOGIN_EMAIL e senha
// em APP_LOGIN_PASSWORD, as duas na Vercel - ou, depois de trocada pela
// pagina "Meu perfil", o hash guardado (ver _password.ts). Sucesso -> cookie de sessao
// assinado (ver _auth.ts). Roda so aqui (Vercel), nunca depende do pod
// estar ligado.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end(JSON.stringify({ detail: "Use POST." }));
    return;
  }

  res.setHeader("Content-Type", "application/json");

  const expectedEmail = process.env.APP_LOGIN_EMAIL;
  const expectedPassword = process.env.APP_LOGIN_PASSWORD;
  if (!expectedEmail || !expectedPassword) {
    res.statusCode = 501;
    res.end(JSON.stringify({ detail: "APP_LOGIN_EMAIL / APP_LOGIN_PASSWORD nao configuradas na Vercel." }));
    return;
  }

  let body = "";
  for await (const chunk of req) body += chunk;

  let parsed: { username?: string; password?: string; remember?: boolean };
  try {
    parsed = JSON.parse(body || "{}");
  } catch {
    res.statusCode = 400;
    res.end(JSON.stringify({ detail: "JSON invalido." }));
    return;
  }

  const username = typeof parsed.username === "string" ? parsed.username.trim().toLowerCase() : "";
  const password = typeof parsed.password === "string" ? parsed.password : "";
  const emailOk = username.length > 0 && safeEqual(username, expectedEmail.trim().toLowerCase());
  let passwordOk: boolean;
  try {
    passwordOk = await checkPassword(password);
  } catch (e) {
    res.statusCode = 503;
    res.end(JSON.stringify({ detail: `Nao foi possivel conferir a senha agora (${e instanceof Error ? e.message : e}).` }));
    return;
  }

  if (!emailOk || !passwordOk) {
    res.statusCode = 401;
    res.end(JSON.stringify({ detail: "E-mail ou senha incorretos." }));
    return;
  }

  res.setHeader("Set-Cookie", buildSetCookie(Boolean(parsed.remember)));
  res.statusCode = 200;
  res.end(JSON.stringify({ ok: true }));
}
