// API oficial do Instagram, conta profissional do proprio administrador
// (Standard Access, sem App Review). Duas formas de conexao:
//  - Instagram Login (token colado no estudio) -> graph.instagram.com
//  - Login do Facebook para Empresas (botao "Conectar pelo Facebook", conta
//    ligada a uma Pagina) -> graph.facebook.com; so ela libera a musica da
//    biblioteca do Instagram nos Reels (Instagram Audio API).
// Documentacao: developers.facebook.com/docs/instagram-platform (content-publishing,
// instagram-api-with-facebook-login, content-publishing/audio-api).
import { decrypt, encrypt } from "./_crypto.js";
import { dbInsert, dbSelect, dbUpdate } from "./_supabase.js";

const GRAPH = "https://graph.instagram.com/v26.0";
const GRAPH_BASE = "https://graph.instagram.com";
const FB_GRAPH = "https://graph.facebook.com/v26.0";

export type IgProvider = "instagram_login" | "facebook_login";

export function hostFor(provider: IgProvider | null | undefined): string {
  return provider === "facebook_login" ? FB_GRAPH : GRAPH;
}

export class InstagramError extends Error {
  constructor(
    message: string,
    public retryable: boolean,
    // midia ainda processando (9007): tentar de novo logo, sem contar tentativa
    public notReady = false
  ) {
    super(message);
  }
}

async function graph<T>(url: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { ...init, signal: AbortSignal.timeout(30_000) });
  } catch (e) {
    throw new InstagramError(`Sem resposta do Instagram (${e instanceof Error ? e.message : e}).`, true);
  }
  const body = (await res.json().catch(() => ({}))) as T & { error?: { message?: string; code?: number; is_transient?: boolean } };
  if (!res.ok || body.error) {
    const err = body.error ?? {};
    // 190 = token invalido/expirado: nao adianta tentar de novo sozinho
    // 9007 = "Media ID is not available": o conteiner ainda esta processando
    const notReady = err.code === 9007;
    const retryable = err.code !== 190 && (notReady || res.status >= 500 || res.status === 429 || Boolean(err.is_transient) || err.code === 4 || err.code === 2);
    throw new InstagramError(`Instagram: ${err.message ?? `HTTP ${res.status}`}`, retryable, notReady);
  }
  return body;
}

// Como nos exemplos oficiais: token no cabecalho, corpo em JSON.
function auth(token: string): RequestInit {
  return { headers: { Authorization: `Bearer ${token}` } };
}

function postJson(token: string, body: Record<string, string>): RequestInit {
  return { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export interface IgProfile {
  user_id: string;
  username: string;
  account_type?: string;
}

export function igProfile(token: string): Promise<IgProfile> {
  return graph<IgProfile>(`${GRAPH}/me?fields=user_id,username,account_type`, auth(token));
}

export function igRefresh(token: string): Promise<{ access_token: string; expires_in: number }> {
  return graph(`${GRAPH_BASE}/refresh_access_token?grant_type=ig_refresh_token&access_token=${encodeURIComponent(token)}`);
}

export async function igCreateContainer(userId: string, token: string, params: Record<string, string>, host = GRAPH): Promise<string> {
  const res = await graph<{ id: string }>(`${host}/${userId}/media`, postJson(token, params));
  return res.id;
}

export async function igContainerStatus(containerId: string, token: string, host = GRAPH): Promise<string> {
  const res = await graph<{ status_code?: string }>(`${host}/${containerId}?fields=status_code`, auth(token));
  return res.status_code ?? "IN_PROGRESS";
}

// O Instagram processa a midia depois de criar o conteiner (foto leva alguns
// segundos, video mais). Consulta ate ficar pronto ou acabar o tempo.
export async function igWaitReady(containerId: string, token: string, tries = 8, everyMs = 3000, host = GRAPH): Promise<string> {
  let status = "IN_PROGRESS";
  for (let i = 0; i < tries; i++) {
    status = await igContainerStatus(containerId, token, host);
    if (status !== "IN_PROGRESS") return status;
    await new Promise((r) => setTimeout(r, everyMs));
  }
  return status;
}

export async function igPublish(userId: string, token: string, containerId: string, host = GRAPH): Promise<string> {
  const res = await graph<{ id: string }>(`${host}/${userId}/media_publish`, postJson(token, { creation_id: containerId }));
  return res.id;
}

export async function igPermalink(mediaId: string, token: string, host = GRAPH): Promise<string | null> {
  const res = await graph<{ permalink?: string }>(`${host}/${mediaId}?fields=permalink`, auth(token)).catch(() => null);
  return res?.permalink ?? null;
}

// --- Login do Facebook para Empresas -----------------------------------------------------

export function fbConfigured(): boolean {
  return Boolean(process.env.META_APP_ID?.trim() && process.env.META_APP_SECRET?.trim() && process.env.META_LOGIN_CONFIG_ID?.trim());
}

// Dialogo do Login do Facebook para Empresas: as permissoes vem da
// "configuracao" criada no painel (config_id), nao de scope.
export function fbAuthorizeUrl(redirectUri: string, state: string): string {
  const q = new URLSearchParams({
    client_id: process.env.META_APP_ID!.trim(),
    redirect_uri: redirectUri,
    state,
    config_id: process.env.META_LOGIN_CONFIG_ID!.trim(),
    response_type: "code",
    override_default_response_type: "true",
  });
  return `https://www.facebook.com/v26.0/dialog/oauth?${q}`;
}

async function fbToken(params: Record<string, string>): Promise<{ access_token: string; expires_in?: number }> {
  const q = new URLSearchParams({ client_id: process.env.META_APP_ID!.trim(), client_secret: process.env.META_APP_SECRET!.trim(), ...params });
  return graph(`${FB_GRAPH}/oauth/access_token?${q}`);
}

// code -> token curto -> token longo (cerca de 60 dias).
export async function fbExchangeCode(code: string, redirectUri: string): Promise<{ access_token: string; expires_in: number }> {
  const short = await fbToken({ code, redirect_uri: redirectUri });
  return fbLongLived(short.access_token);
}

// Troca por um token longo novo (tambem serve para renovar antes de vencer).
export async function fbLongLived(token: string): Promise<{ access_token: string; expires_in: number }> {
  const long = await fbToken({ grant_type: "fb_exchange_token", fb_exchange_token: token });
  return { access_token: long.access_token, expires_in: long.expires_in ?? 60 * 24 * 3600 };
}

export interface FbInstagram {
  igId: string;
  username: string;
  pageName: string;
}

// Contas do Instagram ligadas as Paginas que a pessoa administra. Prefere a
// conta que ja estava conectada (mesmo usuario), senao a primeira.
export async function fbFindInstagram(token: string, preferUsername?: string | null): Promise<FbInstagram | null> {
  const res = await graph<{ data?: { name: string; instagram_business_account?: { id: string; username?: string } }[] }>(
    `${FB_GRAPH}/me/accounts?fields=name,instagram_business_account{id,username}&limit=50`,
    auth(token)
  );
  const found = (res.data ?? [])
    .filter((p) => p.instagram_business_account?.id)
    .map((p) => ({ igId: p.instagram_business_account!.id, username: p.instagram_business_account!.username ?? p.name, pageName: p.name }));
  return found.find((f) => f.username === preferUsername) ?? found[0] ?? null;
}

export interface IgAudio {
  id: string;
  title: string;
  artist: string;
  durationMs: number | null;
  cover: string | null;
  previewLink: string | null;
}

// Musicas da biblioteca do Instagram liberadas para uso por terceiros (sem
// busca = em alta). So pela conexao do Facebook.
export async function igAudioSearch(conn: IgConnection, query: string): Promise<IgAudio[]> {
  const q = new URLSearchParams({ audio_type: "music", user_id: conn.userId });
  if (query.trim()) q.set("search_query", query.trim().slice(0, 80));
  const res = await graph<{ audio?: Record<string, unknown>[] }>(`${FB_GRAPH}/ig_audio?${q}`, auth(conn.token));
  return (res.audio ?? [])
    .filter((a) => typeof a.audio_id === "string")
    .slice(0, 30)
    .map((a) => ({
      id: String(a.audio_id),
      title: String(a.title ?? ""),
      artist: String(a.display_artist ?? ""),
      durationMs: typeof a.duration_in_ms === "number" ? a.duration_in_ms : null,
      cover: typeof a.cover_artwork_thumbnail_uri === "string" ? a.cover_artwork_thumbnail_uri : null,
      previewLink: typeof a.on_platform_audio_preview_link === "string" ? a.on_platform_audio_preview_link : null,
    }));
}

// --- Conexao guardada no banco (social_accounts + platform_connections) ---------------

export interface IgConnection {
  accountId: string;
  connectionId: string;
  username: string;
  userId: string;
  token: string;
  provider: IgProvider;
  host: string;
  expiresAt: string | null;
  refreshedAt: string | null;
  createdAt: string;
}

interface ConnectionRow {
  id: string;
  account_id: string;
  status: string;
  token_ciphertext: string | null;
  token_expires_at: string | null;
  token_refreshed_at: string | null;
  external_user_id: string | null;
  provider: IgProvider | null;
  backup_ciphertext: string | null;
  backup_user_id: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export async function igConnectionRow(): Promise<(ConnectionRow & { handle: string }) | null> {
  const accounts = await dbSelect<{ id: string; handle: string }>("social_accounts", "select=id,handle&network=eq.instagram&active=eq.true&limit=1");
  if (!accounts.length) return null;
  const [conn] = await dbSelect<ConnectionRow>("platform_connections", `select=*&account_id=eq.${accounts[0].id}`);
  return conn ? { ...conn, handle: accounts[0].handle } : null;
}

export async function igConnection(): Promise<IgConnection | null> {
  const row = await igConnectionRow();
  if (!row || row.status !== "conectado" || !row.token_ciphertext || !row.external_user_id) return null;
  const provider: IgProvider = row.provider === "facebook_login" ? "facebook_login" : "instagram_login";
  return {
    accountId: row.account_id,
    connectionId: row.id,
    username: row.handle,
    userId: row.external_user_id,
    token: decrypt(row.token_ciphertext),
    provider,
    host: hostFor(provider),
    expiresAt: row.token_expires_at,
    refreshedAt: row.token_refreshed_at,
    createdAt: row.created_at,
  };
}

// Deixa ativa a conta do Instagram com esse usuario (cria se preciso) e
// desliga as outras. Devolve o id da conta.
async function activateAccount(username: string): Promise<string> {
  const accounts = await dbSelect<{ id: string }>("social_accounts", `select=id&network=eq.instagram&handle=eq.${encodeURIComponent(username)}`);
  // so uma conta do Instagram ativa por vez
  const others = await dbSelect<{ id: string }>("social_accounts", "select=id&network=eq.instagram&active=eq.true");
  for (const o of others) {
    if (o.id !== accounts[0]?.id) {
      await dbUpdate("social_accounts", `id=eq.${o.id}`, { active: false });
      await dbUpdate("platform_connections", `account_id=eq.${o.id}`, { status: "desconectado", token_ciphertext: null });
    }
  }
  const account =
    accounts[0] ?? (await dbInsert<{ id: string }>("social_accounts", { network: "instagram", handle: username, display_name: username }));
  if (accounts[0]) await dbUpdate("social_accounts", `id=eq.${account.id}`, { active: true });
  return account.id;
}

export async function igSaveConnection(profile: IgProfile, token: string, expiresInSeconds: number): Promise<void> {
  const accountId = await activateAccount(profile.username);
  const fields = {
    mode: "api",
    status: "conectado",
    provider: "instagram_login",
    token_ciphertext: encrypt(token),
    token_expires_at: new Date(Date.now() + expiresInSeconds * 1000).toISOString(),
    token_refreshed_at: new Date().toISOString(),
    scopes: ["instagram_business_basic", "instagram_business_content_publish"],
    external_user_id: profile.user_id,
    last_error: null,
  };
  const [existing] = await dbSelect<{ id: string }>("platform_connections", `select=id&account_id=eq.${accountId}`);
  if (existing) await dbUpdate("platform_connections", `id=eq.${existing.id}`, fields);
  else await dbInsert("platform_connections", { account_id: accountId, ...fields });
}

// Conexao pelo Facebook. A conexao anterior pelo Instagram Login (se havia)
// fica guardada em backup_* para poder voltar sem gerar token de novo.
export async function igSaveFacebookConnection(found: FbInstagram, token: string, expiresInSeconds: number): Promise<void> {
  const accountId = await activateAccount(found.username);
  const [existing] = await dbSelect<ConnectionRow>("platform_connections", `select=*&account_id=eq.${accountId}`);
  const keepBackup =
    existing && existing.provider !== "facebook_login" && existing.token_ciphertext && existing.external_user_id
      ? { backup_ciphertext: existing.token_ciphertext, backup_user_id: existing.external_user_id }
      : {};
  const fields = {
    mode: "api",
    status: "conectado",
    provider: "facebook_login",
    token_ciphertext: encrypt(token),
    token_expires_at: new Date(Date.now() + expiresInSeconds * 1000).toISOString(),
    token_refreshed_at: new Date().toISOString(),
    scopes: ["instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement", "business_management"],
    external_user_id: found.igId,
    last_error: null,
    ...keepBackup,
  };
  if (existing) await dbUpdate("platform_connections", `id=eq.${existing.id}`, fields);
  else await dbInsert("platform_connections", { account_id: accountId, ...fields });
}

// Volta para a conexao anterior (Instagram Login) guardada no backup.
export async function igRestoreInstagramLogin(): Promise<boolean> {
  const row = await igConnectionRow();
  if (!row?.backup_ciphertext || !row.backup_user_id) return false;
  await dbUpdate("platform_connections", `id=eq.${row.id}`, {
    provider: "instagram_login",
    status: "conectado",
    token_ciphertext: row.backup_ciphertext,
    external_user_id: row.backup_user_id,
    backup_ciphertext: null,
    backup_user_id: null,
    // a data certa volta na proxima renovacao semanal
    token_refreshed_at: new Date(Date.now() - 8 * 24 * 3600_000).toISOString(),
    last_error: null,
  });
  return true;
}

export async function igDisconnect(): Promise<void> {
  const row = await igConnectionRow();
  if (row) await dbUpdate("platform_connections", `id=eq.${row.id}`, { status: "desconectado", token_ciphertext: null });
}

// Token de longa duracao vale 60 dias; renova quando passou de 7 dias da ultima
// renovacao (a Meta exige pelo menos 24 h de idade). Chamado pelo processador.
export async function igRefreshIfDue(): Promise<string | null> {
  const conn = await igConnection().catch(() => null);
  if (!conn) return null;
  const last = new Date(conn.refreshedAt ?? conn.createdAt).getTime();
  if (Date.now() - last < 7 * 24 * 3600_000) return null;
  try {
    const fresh = conn.provider === "facebook_login" ? await fbLongLived(conn.token) : await igRefresh(conn.token);
    await dbUpdate("platform_connections", `id=eq.${conn.connectionId}`, {
      token_ciphertext: encrypt(fresh.access_token),
      token_expires_at: new Date(Date.now() + fresh.expires_in * 1000).toISOString(),
      token_refreshed_at: new Date().toISOString(),
      last_error: null,
    });
    return "renovado";
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    await dbUpdate("platform_connections", `id=eq.${conn.connectionId}`, {
      last_error: msg.slice(0, 300),
      ...(e instanceof InstagramError && !e.retryable ? { status: "expirado" } : {}),
    });
    return `erro: ${msg}`;
  }
}
