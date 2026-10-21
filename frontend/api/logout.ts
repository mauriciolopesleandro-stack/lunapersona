import type { IncomingMessage, ServerResponse } from "http";
import { buildClearCookie, isAuthenticated, revokeAllSessions } from "./_auth.js";
import { storeConfigured } from "./_store.js";

// POST /api/logout - limpa o cookie de sessao deste navegador.
// POST /api/logout { all: true } - tambem derruba as sessoes de todos os
// aparelhos (exige sessao valida e o armazenamento Upstash).
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (req.method !== "POST") return reply(405, { detail: "Use POST." });

  let body = "";
  for await (const chunk of req) body += chunk;
  let all = false;
  try {
    all = JSON.parse(body || "{}")?.all === true;
  } catch {
    // corpo invalido = logout simples
  }

  if (all) {
    if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessao expirada. Entre de novo." });
    if (!storeConfigured()) {
      return reply(501, { detail: "Falta conectar o banco Upstash Redis ao projeto na Vercel." });
    }
    try {
      await revokeAllSessions();
    } catch (e) {
      return reply(503, { detail: `Nao deu para encerrar as sessoes agora (${e instanceof Error ? e.message : e}).` });
    }
  }

  res.setHeader("Set-Cookie", buildClearCookie());
  return reply(200, { ok: true });
}
