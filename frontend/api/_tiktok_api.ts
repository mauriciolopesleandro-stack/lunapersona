import type { IncomingMessage, ServerResponse } from "http";
import { randomBytes } from "crypto";
import { isAuthenticated, parseCookies, safeEqual } from "./_auth.js";
import { encryptionConfigured } from "./_crypto.js";
import { audit, supabaseConfigured } from "./_supabase.js";
import { authorizeUrl, disconnectTiktok, exchangeCode, publishStatus, saveTiktok, tiktokAccessToken, tiktokConfigured, tiktokConnectionRow, uploadPhotoDraft, userInfo } from "./_tiktok.js";

// /api/studio?r=tiktok
//   GET                     estado da conexao (sem tokens)
//   GET  &action=connect    vai para a autorizacao do TikTok (OAuth, Login Kit)
//   GET  &action=status&publish_id=...   status de um envio (teste)
//   POST { action: "disconnect" }
//   POST { action: "test-photo" }   manda a foto de teste fixa do site (/tiktok-teste.jpg,
//                                   1080x1920) como rascunho, para isolar problemas de formato
// /api/tiktok/callback (vercel.json reescreve para r=tiktok-callback): volta do
// TikTok com code + state; confere o state (cookie), troca o code pelos tokens
// e guarda cifrado. Precisa de TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET.

const STATE_COOKIE = "luna_tt_state";

function redirectUri(req: IncomingMessage): string {
  const fixed = process.env.TIKTOK_REDIRECT_URI?.trim();
  if (fixed) return fixed;
  const host = req.headers["x-forwarded-host"] ?? req.headers.host;
  return `https://${host}/api/tiktok/callback`;
}

function back(res: ServerResponse, result: string) {
  res.statusCode = 302;
  res.setHeader("Location", `/?tiktok=${encodeURIComponent(result)}#tiktok`);
  res.end();
}

export async function tiktokCallback(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Cache-Control", "no-store");
  // limpa o cookie do state em qualquer caso
  res.setHeader("Set-Cookie", `${STATE_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`);
  if (!(await isAuthenticated(req))) return back(res, "sessao-expirada");
  const params = new URL(req.url ?? "/", "http://local").searchParams;
  if (params.get("error")) return back(res, `recusado:${params.get("error_description") || params.get("error")}`);
  const state = params.get("state") ?? "";
  const expected = parseCookies(req)[STATE_COOKIE] ?? "";
  if (!state || !expected || !safeEqual(state, expected)) return back(res, "state-invalido");
  const code = params.get("code");
  if (!code) return back(res, "sem-codigo");
  if (!tiktokConfigured() || !encryptionConfigured() || !supabaseConfigured()) return back(res, "nao-configurado");
  try {
    const tokens = await exchangeCode(code, redirectUri(req));
    if (!tokens.scope.split(",").includes("video.upload")) return back(res, "sem-permissao-video-upload");
    const user = await userInfo(tokens.access_token).catch(() => ({ open_id: tokens.open_id, display_name: "" }));
    await saveTiktok(tokens, user.display_name);
    await audit("conectar", "tiktok", tokens.open_id, { nome: user.display_name });
    return back(res, "conectado");
  } catch (e) {
    return back(res, `erro:${(e instanceof Error ? e.message : String(e)).slice(0, 150)}`);
  }
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
  if (!supabaseConfigured()) return reply(501, { configured: false, detail: "Banco (Supabase) ainda não configurado na Vercel." });
  const params = new URL(req.url ?? "/", "http://local").searchParams;

  if (req.method === "GET" && params.get("action") === "connect") {
    if (!tiktokConfigured()) return reply(501, { detail: "Falta cadastrar TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET na Vercel." });
    if (!encryptionConfigured()) return reply(501, { detail: "Falta TOKEN_ENCRYPTION_KEY na Vercel." });
    const state = randomBytes(24).toString("base64url");
    res.setHeader("Set-Cookie", `${STATE_COOKIE}=${state}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=600`);
    res.statusCode = 302;
    res.setHeader("Location", authorizeUrl(redirectUri(req), state));
    return res.end();
  }

  if (req.method === "GET" && params.get("action") === "status") {
    const token = await tiktokAccessToken();
    if (!token) return reply(400, { detail: "TikTok não conectado." });
    try {
      return reply(200, await publishStatus(token, params.get("publish_id") ?? ""));
    } catch (e) {
      return reply(502, { detail: e instanceof Error ? e.message : String(e) });
    }
  }

  if (req.method === "GET") {
    const row = await tiktokConnectionRow();
    return reply(200, {
      appConfigured: tiktokConfigured(),
      encryption: encryptionConfigured(),
      redirectUri: redirectUri(req),
      connected: row?.status === "conectado",
      status: row?.status ?? "desconectado",
      name: row?.handle ?? null,
      refreshedAt: row?.token_refreshed_at ?? null,
      validUntil: row?.token_expires_at ?? null,
      lastError: row?.last_error ?? null,
    });
  }

  if (req.method === "POST") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw || "{}") as { action?: string };
    if (body.action === "test-photo") {
      const token = await tiktokAccessToken();
      if (!token) return reply(400, { detail: "Conecte o TikTok primeiro." });
      const host = req.headers["x-forwarded-host"] ?? req.headers.host;
      const photo = `https://${host}/tiktok-teste.jpg`;
      try {
        const publishId = await uploadPhotoDraft(token, [photo], "Teste do Luna Vox Studio");
        await audit("tiktok_teste_foto", "tiktok", publishId, { photo });
        return reply(200, { publish_id: publishId, photo });
      } catch (e) {
        return reply(502, { detail: e instanceof Error ? e.message : String(e) });
      }
    }
    if (body.action === "disconnect") {
      await disconnectTiktok();
      await audit("desconectar", "tiktok", null, {});
      return reply(200, { ok: true });
    }
    return reply(400, { detail: "Ação desconhecida." });
  }
  return reply(405, { detail: "Use GET ou POST." });
}
