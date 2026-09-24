import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { checkPassword, MIN_PASSWORD_LENGTH, setPassword } from "./_password.js";
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
  if (!isAuthenticated(req)) return reply(401, { detail: "Sessao expirada. Entre de novo." });
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
    if (!(await checkPassword(currentPassword))) {
      return reply(403, { detail: "Senha atual incorreta." });
    }
    await setPassword(newPassword);
  } catch (e) {
    return reply(503, { detail: `Nao foi possivel salvar a senha agora (${e instanceof Error ? e.message : e}).` });
  }

  return reply(200, { ok: true });
}
