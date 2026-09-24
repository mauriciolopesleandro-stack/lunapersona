import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";

// GET /api/session - a tela de login (e o resto do app) chama isso ao
// carregar pra saber se ja tem uma sessao valida (cookie) e pode pular a
// tela de login direto.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.statusCode = 200;
  res.end(JSON.stringify({ authenticated: isAuthenticated(req) }));
}
