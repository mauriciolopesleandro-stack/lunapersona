import type { IncomingMessage, ServerResponse } from "http";
import { buildSetCookie, currentSession, revokeAllSessions } from "./_auth.js";
import { checkPassword, MIN_PASSWORD_LENGTH, setPassword } from "./_password.js";
import { blockedFor, clearFailures, recordFailure, tooManyAttemptsMessage } from "./_ratelimit.js";
import { storeConfigured } from "./_store.js";

// POST /api/change-password { currentPassword, newPassword }
// Exige sessao valida E a senha atual - so o cookie nao basta, para que um
// navegador esquecido logado nao permita trocar a senha.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };

  if (req.method !== "POST") return reply(405, { detail: "Use POST." });
  const session = await currentSession(req);
  if (!session) return reply(401, { detail: "Sessao expirada. Entre de novo." });
  if (!storeConfigured()) {
    return reply(501, {
      detail: "Troca de senha ainda nao ativada: falta conectar o banco Upstash Redis ao projeto na Vercel.",
    });
  }

  let body = "";
  for await (const chunk of req) body += chunk;
  let parsed: { currentPassword?: unknown; newPassword?: unknown };
  try {
    parsed = JSON.parse(body || "{}");
  } catch {
    return reply(400, { detail: "JSON invalido." });
  }

  const currentPassword = typeof parsed.currentPassword === "string" ? parsed.currentPassword : "";
  const newPassword = typeof parsed.newPassword === "string" ? parsed.newPassword : "";

  if (newPassword.length < MIN_PASSWORD_LENGTH) {
    return reply(400, { detail: `A nova senha precisa ter pelo menos ${MIN_PASSWORD_LENGTH} caracteres.` });
  }
  if (newPassword === currentPassword) {
    return reply(400, { detail: "A nova senha precisa ser diferente da atual." });
  }

  try {
    // Mesmo limite do login: um cookie roubado nao serve para adivinhar a senha.
    const wait = await blockedFor(req);
    if (wait > 0) {
      res.setHeader("Retry-After", String(wait));
      return reply(429, { detail: tooManyAttemptsMessage(wait) });
    }
    if (!(await checkPassword(currentPassword))) {
      await recordFailure(req);
      return reply(403, { detail: "Senha atual incorreta." });
    }
    await clearFailures(req);
    await setPassword(newPassword);
  } catch (e) {
    return reply(503, { detail: `Nao foi possivel salvar a senha agora (${e instanceof Error ? e.message : e}).` });
  }

  // Senha nova derruba as sessoes de todos os aparelhos; este navegador
  // ganha um cookie novo com a mesma validade e continua entrando.
  try {
    await revokeAllSessions();
    res.setHeader("Set-Cookie", await buildSetCookie(session.persistent, session.expiresAt));
  } catch (e) {
    return reply(503, {
      detail: `A senha foi trocada, mas nao deu para encerrar as outras sessoes agora (${e instanceof Error ? e.message : e}). Use "Sair de todos os aparelhos".`,
    });
  }

  return reply(200, { ok: true });
}
