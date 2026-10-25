// TikTok (Content Posting API, envio como rascunho para a caixa de entrada do
// criador - escopo video.upload). Documentacao: developers.tiktok.com/doc
// (login-kit-web, oauth-user-access-token-management,
// content-posting-api-get-started-upload-content, media-transfer-guide,
// content-posting-api-reference-get-video-status).
import { decrypt, encrypt } from "./_crypto.js";
import { dbInsert, dbSelect, dbUpdate } from "./_supabase.js";

const API = "https://open.tiktokapis.com";
// video.list: ler os posts publicos da conta para marcar sozinho o rascunho
// que o criador publicou no app (o TikTok nao avisa o fim do rascunho).
export const TIKTOK_SCOPES = "user.info.basic,video.upload,video.list";

export class TikTokError extends Error {
  constructor(
    message: string,
    public retryable: boolean
  ) {
    super(message);
  }
}

export function tiktokConfigured(): boolean {
  return Boolean(process.env.TIKTOK_CLIENT_KEY?.trim() && process.env.TIKTOK_CLIENT_SECRET?.trim());
}

function clientKey() {
  return process.env.TIKTOK_CLIENT_KEY!.trim();
}

function clientSecret() {
  return process.env.TIKTOK_CLIENT_SECRET!.trim();
}

export function authorizeUrl(redirectUri: string, state: string): string {
  const q = new URLSearchParams({ client_key: clientKey(), response_type: "code", scope: TIKTOK_SCOPES, redirect_uri: redirectUri, state });
  return `https://www.tiktok.com/v2/auth/authorize/?${q}`;
}

interface TokenResponse {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  refresh_expires_in: number;
  open_id: string;
  scope: string;
  error?: string;
  error_description?: string;
}

async function tokenRequest(params: Record<string, string>): Promise<TokenResponse> {
  let res: Response;
  try {
    res = await fetch(`${API}/v2/oauth/token/`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_key: clientKey(), client_secret: clientSecret(), ...params }),
      signal: AbortSignal.timeout(20_000),
    });
  } catch (e) {
    throw new TikTokError(`Sem resposta do TikTok (${e instanceof Error ? e.message : e}).`, true);
  }
  const body = (await res.json().catch(() => ({}))) as TokenResponse;
  if (!res.ok || body.error || !body.access_token) {
    throw new TikTokError(`TikTok: ${body.error_description || body.error || `HTTP ${res.status}`}`, res.status >= 500);
  }
  return body;
}

export function exchangeCode(code: string, redirectUri: string) {
  return tokenRequest({ code, grant_type: "authorization_code", redirect_uri: redirectUri });
}

// Chamada a API v2 (corpo e resposta em JSON; erro vem em error.code != "ok").
async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json; charset=UTF-8", ...init.headers },
      signal: AbortSignal.timeout(30_000),
    });
  } catch (e) {
    throw new TikTokError(`Sem resposta do TikTok (${e instanceof Error ? e.message : e}).`, true);
  }
  const body = (await res.json().catch(() => ({}))) as { data?: T; error?: { code?: string; message?: string } };
  const code = body.error?.code ?? (res.ok ? "ok" : `http_${res.status}`);
  if (code !== "ok") {
    // token invalido/escopo negado: nao adianta repetir sozinho
    const fatal = ["access_token_invalid", "scope_not_authorized", "scope_permission_missed", "spam_risk_too_many_posts", "spam_risk_user_banned_from_posting"].includes(code);
    throw new TikTokError(`TikTok: ${body.error?.message || code}`, !fatal && (res.status >= 500 || res.status === 429 || code === "rate_limit_exceeded" || code === "internal_error"));
  }
  return body.data as T;
}

export async function userInfo(token: string): Promise<{ open_id: string; display_name: string }> {
  const data = await api<{ user: { open_id: string; display_name: string } }>("/v2/user/info/?fields=open_id,display_name", token);
  return data.user;
}

// Envia o video inteiro como rascunho (FILE_UPLOAD). Ate 64 MB vai num pedaco
// so; abaixo de 5 MB tem que ser num pedaco so (media transfer guide).
export async function uploadDraft(token: string, video: Uint8Array, mime: string): Promise<string> {
  if (video.byteLength > 64 * 1024 * 1024) throw new TikTokError("Vídeo maior que 64 MB.", false);
  const init = await api<{ publish_id: string; upload_url: string }>("/v2/post/publish/inbox/video/init/", token, {
    method: "POST",
    body: JSON.stringify({ source_info: { source: "FILE_UPLOAD", video_size: video.byteLength, chunk_size: video.byteLength, total_chunk_count: 1 } }),
  });
  let res: Response;
  try {
    res = await fetch(init.upload_url, {
      method: "PUT",
      headers: {
        "Content-Type": mime,
        // Content-Length o fetch do Node calcula sozinho a partir do corpo
        "Content-Range": `bytes 0-${video.byteLength - 1}/${video.byteLength}`,
      },
      body: video as unknown as BodyInit,
      signal: AbortSignal.timeout(240_000),
    });
  } catch (e) {
    throw new TikTokError(`O envio do vídeo para o TikTok caiu (${e instanceof Error ? e.message : e}).`, true);
  }
  if (!res.ok) throw new TikTokError(`O TikTok recusou o envio do vídeo (HTTP ${res.status}).`, res.status >= 500);
  return init.publish_id;
}

// Fotos (ate 35) como rascunho: o TikTok baixa cada foto pelo link (PULL_FROM_URL),
// que precisa estar no endereco verificado do app. A legenda vai junto.
export async function uploadPhotoDraft(
  token: string,
  photoUrls: string[],
  description: string,
  options: { aiGenerated?: boolean; coverIndex?: number } = {}
): Promise<string> {
  if (!photoUrls.length || photoUrls.length > 35) throw new TikTokError("De 1 a 35 fotos.", false);
  const cover = options.coverIndex && options.coverIndex < photoUrls.length ? options.coverIndex : 0;
  const data = await api<{ publish_id: string }>("/v2/post/publish/content/init/", token, {
    method: "POST",
    body: JSON.stringify({
      post_info: { description: description.slice(0, 4000) },
      source_info: { source: "PULL_FROM_URL", photo_images: photoUrls, photo_cover_index: cover },
      post_mode: "MEDIA_UPLOAD",
      media_type: "PHOTO",
      ...(options.aiGenerated ? { is_aigc: true } : {}),
    }),
  });
  return data.publish_id;
}

export interface TikTokVideo {
  id: string;
  create_time: number; // segundos
  title?: string;
  video_description?: string;
  share_url?: string;
}

// Posts publicos mais recentes da conta (Display API, escopo video.list).
export async function recentVideos(token: string): Promise<TikTokVideo[]> {
  const data = await api<{ videos?: TikTokVideo[] }>("/v2/video/list/?fields=id,create_time,title,video_description,share_url", token, {
    method: "POST",
    body: JSON.stringify({ max_count: 20 }),
  });
  return data.videos ?? [];
}

export interface PublishStatus {
  status: "PROCESSING_UPLOAD" | "PROCESSING_DOWNLOAD" | "SEND_TO_USER_INBOX" | "PUBLISH_COMPLETE" | "FAILED" | string;
  fail_reason?: string;
}

export function publishStatus(token: string, publishId: string): Promise<PublishStatus> {
  return api<PublishStatus>("/v2/post/publish/status/fetch/", token, { method: "POST", body: JSON.stringify({ publish_id: publishId }) });
}

// --- Conexao guardada no banco (tokens cifrados juntos num JSON) -------------------

interface Tokens {
  access_token: string;
  refresh_token: string;
  open_id: string;
  expires_at: number;
  refresh_expires_at: number;
}

interface ConnectionRow {
  id: string;
  account_id: string;
  status: string;
  token_ciphertext: string | null;
  scopes: string[] | null;
  token_expires_at: string | null;
  token_refreshed_at: string | null;
  last_error: string | null;
  created_at: string;
}

export async function tiktokConnectionRow(): Promise<(ConnectionRow & { handle: string }) | null> {
  const accounts = await dbSelect<{ id: string; handle: string }>("social_accounts", "select=id,handle&network=eq.tiktok&active=eq.true&limit=1");
  if (!accounts.length) return null;
  const [conn] = await dbSelect<ConnectionRow>("platform_connections", `select=*&account_id=eq.${accounts[0].id}`);
  return conn ? { ...conn, handle: accounts[0].handle } : null;
}

function sealed(t: TokenResponse): Record<string, unknown> {
  const now = Date.now();
  const tokens: Tokens = {
    access_token: t.access_token,
    refresh_token: t.refresh_token,
    open_id: t.open_id,
    expires_at: now + t.expires_in * 1000,
    refresh_expires_at: now + t.refresh_expires_in * 1000,
  };
  return {
    token_ciphertext: encrypt(JSON.stringify(tokens)),
    // o que importa para nao perder a conexao e o refresh token (365 dias)
    token_expires_at: new Date(tokens.refresh_expires_at).toISOString(),
    token_refreshed_at: new Date(now).toISOString(),
    last_error: null,
  };
}

export async function saveTiktok(t: TokenResponse, displayName: string): Promise<void> {
  const handle = displayName || t.open_id;
  const [same] = await dbSelect<{ id: string }>("social_accounts", `select=id&network=eq.tiktok&handle=eq.${encodeURIComponent(handle)}`);
  // uma conta do TikTok ativa por vez: a anterior perde a conexao (tokens apagados)
  for (const o of await dbSelect<{ id: string }>("social_accounts", "select=id&network=eq.tiktok&active=eq.true")) {
    if (o.id === same?.id) continue;
    await dbUpdate("social_accounts", `id=eq.${o.id}`, { active: false });
    await dbUpdate("platform_connections", `account_id=eq.${o.id}`, { status: "desconectado", token_ciphertext: null });
  }
  const account = same ?? (await dbInsert<{ id: string }>("social_accounts", { network: "tiktok", handle, display_name: displayName }));
  await dbUpdate("social_accounts", `id=eq.${account.id}`, { active: true });
  const fields = { mode: "api", status: "conectado", scopes: t.scope.split(","), ...sealed(t) };
  const [conn] = await dbSelect<{ id: string }>("platform_connections", `select=id&account_id=eq.${account.id}`);
  if (conn) await dbUpdate("platform_connections", `id=eq.${conn.id}`, fields);
  else await dbInsert("platform_connections", { account_id: account.id, ...fields });
}

export async function disconnectTiktok(): Promise<void> {
  const row = await tiktokConnectionRow();
  if (row) await dbUpdate("platform_connections", `id=eq.${row.id}`, { status: "desconectado", token_ciphertext: null });
}

// Token de acesso valido (vale 24 h; renova com o refresh token quando falta
// menos de 10 min). null = sem conexao.
export async function tiktokAccessToken(): Promise<string | null> {
  const row = await tiktokConnectionRow();
  if (!row || row.status !== "conectado" || !row.token_ciphertext) return null;
  const tokens = JSON.parse(decrypt(row.token_ciphertext)) as Tokens;
  if (tokens.expires_at - Date.now() > 10 * 60_000) return tokens.access_token;
  try {
    const fresh = await tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refresh_token });
    await dbUpdate("platform_connections", `id=eq.${row.id}`, sealed(fresh));
    return fresh.access_token;
  } catch (e) {
    const msg = (e instanceof Error ? e.message : String(e)).slice(0, 300);
    await dbUpdate("platform_connections", `id=eq.${row.id}`, {
      last_error: msg,
      ...(e instanceof TikTokError && !e.retryable ? { status: "expirado" } : {}),
    });
    throw e;
  }
}
