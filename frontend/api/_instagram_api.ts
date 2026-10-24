import type { IncomingMessage, ServerResponse } from "http";
import { randomBytes } from "crypto";
import { isAuthenticated, parseCookies, safeEqual } from "./_auth.js";
import { encryptionConfigured } from "./_crypto.js";
import {
  InstagramError,
  fbAuthorizeUrl,
  fbConfigured,
  fbExchangeCode,
  fbFindInstagram,
  igAudioSearch,
  igConnection,
  igConnectionRow,
  igDisconnect,
  igProfile,
  igRestoreInstagramLogin,
  igSaveConnection,
  igSaveFacebookConnection,
} from "./_instagram.js";
import { audit, supabaseConfigured } from "./_supabase.js";

// /api/studio?r=instagram - conexao da conta profissional do Instagram.
// GET                          estado (nunca devolve o token)
// GET  &action=fb-connect      vai para o Login do Facebook para Empresas
// GET  &action=audio&q=...     musicas da biblioteca do Instagram (so pela conexao do Facebook)
// POST { action: "connect", token }   confere o token (Instagram Login) e guarda cifrado
// POST { action: "restore" }          volta para a conexao anterior (Instagram Login)
// POST { action: "disconnect" }
// /api/instagram/callback (vercel.json reescreve para r=instagram-callback):
// volta do Facebook com code + state; troca pelo token e guarda cifrado.
//
// O token do Instagram Login e gerado pelo proprio usuario no painel da Meta
// (app dele, Standard Access, conta dele); a conexao pelo Facebook usa o
// Login do Facebook para Empresas do mesmo app (META_APP_ID, META_APP_SECRET,
// META_LOGIN_CONFIG_ID na Vercel). O estudio nunca pede senha de rede social.

// O token gerado no painel da Meta e de longa duracao (60 dias); a data exata
// so vem na primeira renovacao, feita pelo processador da fila.
const LONG_LIVED_SECONDS = 60 * 24 * 3600;
const STATE_COOKIE = "luna_ig_state";

function redirectUri(req: IncomingMessage): string {
  const fixed = process.env.INSTAGRAM_REDIRECT_URI?.trim();
  if (fixed) return fixed;
  const host = req.headers["x-forwarded-host"] ?? req.headers.host;
  return `https://${host}/api/instagram/callback`;
}

function back(res: ServerResponse, result: string) {
  res.statusCode = 302;
  res.setHeader("Location", `/?instagram=${encodeURIComponent(result)}#instagram`);
  res.end();
}

export async function instagramCallback(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Set-Cookie", `${STATE_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`);
  if (!(await isAuthenticated(req))) return back(res, "sessao-expirada");
  const params = new URL(req.url ?? "/", "http://local").searchParams;
  if (params.get("error")) return back(res, `recusado:${params.get("error_description") || params.get("error")}`);
  const state = params.get("state") ?? "";
  const expected = parseCookies(req)[STATE_COOKIE] ?? "";
  if (!state || !expected || !safeEqual(state, expected)) return back(res, "state-invalido");
  const code = params.get("code");
  if (!code) return back(res, "sem-codigo");
  if (!fbConfigured() || !encryptionConfigured() || !supabaseConfigured()) return back(res, "nao-configurado");
  try {
    const token = await fbExchangeCode(code, redirectUri(req));
    const current = await igConnectionRow().catch(() => null);
    const found = await fbFindInstagram(token.access_token, current?.handle);
    if (!found) return back(res, "sem-instagram-na-pagina");
    await igSaveFacebookConnection(found, token.access_token, token.expires_in);
    await audit("conectar", "instagram", found.igId, { username: found.username, via: "facebook", pagina: found.pageName });
    return back(res, "conectado");
  } catch (e) {
    return back(res, `erro:${(e instanceof Error ? e.message : String(e)).slice(0, 150)}`);
  }
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
  if (!supabaseConfigured()) return reply(501, { configured: false, detail: "Banco (Supabase) ainda não configurado na Vercel." });
  const params = new URL(req.url ?? "/", "http://local").searchParams;

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
      provider: row?.provider === "facebook_login" ? "facebook_login" : "instagram_login",
      hasBackup: Boolean(row?.backup_ciphertext),
      fbConfigured: fbConfigured(),
      redirectUri: redirectUri(req),
    };
  };

  try {
    if (req.method === "GET" && params.get("action") === "fb-connect") {
      if (!fbConfigured()) return reply(501, { detail: "Falta cadastrar META_APP_ID, META_APP_SECRET e META_LOGIN_CONFIG_ID na Vercel." });
      if (!encryptionConfigured()) return reply(501, { detail: "Falta TOKEN_ENCRYPTION_KEY na Vercel." });
      const state = randomBytes(24).toString("base64url");
      res.setHeader("Set-Cookie", `${STATE_COOKIE}=${state}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=600`);
      res.statusCode = 302;
      res.setHeader("Location", fbAuthorizeUrl(redirectUri(req), state));
      return res.end();
    }
    if (req.method === "GET" && params.get("action") === "audio") {
      const conn = await igConnection();
      if (!conn) return reply(400, { detail: "Instagram não conectado." });
      if (conn.provider !== "facebook_login") return reply(400, { detail: "A música do Instagram precisa da conexão pelo Facebook (página Instagram)." });
      return reply(200, { audio: await igAudioSearch(conn, params.get("q") ?? "") });
    }
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
    if (body.action === "restore") {
      if (!(await igRestoreInstagramLogin())) return reply(400, { detail: "Não há conexão anterior guardada." });
      await audit("conectar", "instagram", null, { via: "instagram_login (restaurada)" });
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
