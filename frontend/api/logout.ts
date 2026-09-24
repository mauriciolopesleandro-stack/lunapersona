import type { IncomingMessage, ServerResponse } from "http";
import { buildClearCookie } from "./_auth.js";

// POST /api/logout - so limpa o cookie de sessao.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end(JSON.stringify({ detail: "Use POST." }));
    return;
  }
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Set-Cookie", buildClearCookie());
  res.statusCode = 200;
  res.end(JSON.stringify({ ok: true }));
}
