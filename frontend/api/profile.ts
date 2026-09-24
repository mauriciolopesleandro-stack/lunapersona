import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { MIN_PASSWORD_LENGTH } from "./_password.js";
import { storeConfigured } from "./_store.js";

// GET /api/profile - dados da conta unica para a pagina "Meu perfil".
// canChangePassword=false enquanto o armazenamento (Upstash Redis) nao
// estiver conectado ao projeto na Vercel.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  if (!isAuthenticated(req)) {
    res.statusCode = 401;
    res.end(JSON.stringify({ detail: "Sessao expirada. Entre de novo." }));
    return;
  }
  res.statusCode = 200;
  res.end(
    JSON.stringify({
      email: process.env.APP_LOGIN_EMAIL ?? "",
      canChangePassword: storeConfigured(),
      minPasswordLength: MIN_PASSWORD_LENGTH,
    })
  );
}
