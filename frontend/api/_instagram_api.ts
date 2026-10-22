import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { encryptionConfigured } from "./_crypto.js";
import { InstagramError, igConnectionRow, igDisconnect, igProfile, igSaveConnection } from "./_instagram.js";
import { audit, supabaseConfigured } from "./_supabase.js";

// /api/studio?r=instagram - conexao da conta profissional do Instagram.
// GET  -> estado (nunca devolve o token)
// POST { action: "connect", token }   confere o token na API oficial e guarda cifrado
// POST { action: "disconnect" }
//
// O token e gerado pelo proprio usuario no painel da Meta (app dele, Standard
// Access, conta dele) e colado no estudio; o app nao pede senha de rede social.

// O token gerado no painel da Meta e de longa duracao (60 dias); a data exata
// so vem na primeira renovacao, feita pelo processador da fila.
const LONG_LIVED_SECONDS = 60 * 24 * 3600;

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
  if (!supabaseConfigured()) return reply(501, { configured: false, detail: "Banco (Supabase) ainda não configurado na Vercel." });

  const status = async () => {
    const row = await igConnectionRow();
    return {
      encryption: encryptionConfigured(),
      connected: row?.status === "conectado",
      status: row?.status ?? "desconectado",
      username: row?.handle ?? null,
      expiresAt: row?.token_expires_at ?? null,
      refreshedAt: row?.token_refreshed_at ?? null,
      lastError: row?.last_error ?? null,
    };
  };

  try {
    if (req.method === "GET") return reply(200, await status());
    if (req.method !== "POST") return reply(405, { detail: "Use GET ou POST." });
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw || "{}") as { action?: string; token?: unknown };

    if (body.action === "connect") {
      if (!encryptionConfigured()) return reply(501, { detail: "Falta cadastrar TOKEN_ENCRYPTION_KEY na Vercel (só servidor) para guardar o token cifrado." });
      const token = typeof body.token === "string" ? body.token.trim() : "";
      if (token.length < 20 || /\s/.test(token)) return reply(400, { detail: "Isso não parece um token de acesso do Instagram." });
      let profile;
      try {
        profile = await igProfile(token);
      } catch (e) {
        return reply(400, { detail: `O Instagram não aceitou o token: ${e instanceof Error ? e.message : e}` });
      }
      if (profile.account_type && !["BUSINESS", "MEDIA_CREATOR"].includes(profile.account_type)) {
        return reply(400, { detail: "A conta precisa ser profissional (Empresa ou Criador de conteúdo)." });
      }
      await igSaveConnection(profile, token, LONG_LIVED_SECONDS);
      await audit("conectar", "instagram", profile.user_id, { username: profile.username });
      return reply(200, await status());
    }
    if (body.action === "disconnect") {
      await igDisconnect();
      await audit("desconectar", "instagram", null, {});
      return reply(200, await status());
    }
    return reply(400, { detail: "Ação desconhecida." });
  } catch (e) {
    const detail = e instanceof InstagramError || e instanceof Error ? e.message : String(e);
    return reply(502, { detail });
  }
}
