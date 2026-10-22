// API oficial do Instagram (Instagram Login, conta profissional do proprio
// administrador - Standard Access, sem App Review). Documentacao:
// developers.facebook.com/docs/instagram-platform (content-publishing, business-login).
import { decrypt, encrypt } from "./_crypto.js";
import { dbInsert, dbSelect, dbUpdate } from "./_supabase.js";

const GRAPH = "https://graph.instagram.com/v26.0";
const GRAPH_BASE = "https://graph.instagram.com";

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

export async function igCreateContainer(userId: string, token: string, params: Record<string, string>): Promise<string> {
  const res = await graph<{ id: string }>(`${GRAPH}/${userId}/media`, postJson(token, params));
  return res.id;
}

export async function igContainerStatus(containerId: string, token: string): Promise<string> {
  const res = await graph<{ status_code?: string }>(`${GRAPH}/${containerId}?fields=status_code`, auth(token));
  return res.status_code ?? "IN_PROGRESS";
}

// O Instagram processa a midia depois de criar o conteiner (foto leva alguns
// segundos, video mais). Consulta ate ficar pronto ou acabar o tempo.
export async function igWaitReady(containerId: string, token: string, tries = 8, everyMs = 3000): Promise<string> {
  let status = "IN_PROGRESS";
  for (let i = 0; i < tries; i++) {
    status = await igContainerStatus(containerId, token);
    if (status !== "IN_PROGRESS") return status;
    await new Promise((r) => setTimeout(r, everyMs));
  }
  return status;
}

export async function igPublish(userId: string, token: string, containerId: string): Promise<string> {
  const res = await graph<{ id: string }>(`${GRAPH}/${userId}/media_publish`, postJson(token, { creation_id: containerId }));
  return res.id;
}

export async function igPermalink(mediaId: string, token: string): Promise<string | null> {
  const res = await graph<{ permalink?: string }>(`${GRAPH}/${mediaId}?fields=permalink`, auth(token)).catch(() => null);
  return res?.permalink ?? null;
}

// --- Conexao guardada no banco (social_accounts + platform_connections) ---------------

export interface IgConnection {
  accountId: string;
  connectionId: string;
  username: string;
  userId: string;
  token: string;
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
  return {
    accountId: row.account_id,
    connectionId: row.id,
    username: row.handle,
    userId: row.external_user_id,
    token: decrypt(row.token_ciphertext),
    expiresAt: row.token_expires_at,
    refreshedAt: row.token_refreshed_at,
    createdAt: row.created_at,
  };
}

export async function igSaveConnection(profile: IgProfile, token: string, expiresInSeconds: number): Promise<void> {
  const accounts = await dbSelect<{ id: string }>("social_accounts", `select=id&network=eq.instagram&handle=eq.${encodeURIComponent(profile.username)}`);
  // so uma conta do Instagram ativa por vez
  const others = await dbSelect<{ id: string }>("social_accounts", "select=id&network=eq.instagram&active=eq.true");
  for (const o of others) {
    if (o.id !== accounts[0]?.id) {
      await dbUpdate("social_accounts", `id=eq.${o.id}`, { active: false });
      await dbUpdate("platform_connections", `account_id=eq.${o.id}`, { status: "desconectado", token_ciphertext: null });
    }
  }
  const account =
    accounts[0] ??
    (await dbInsert<{ id: string }>("social_accounts", { network: "instagram", handle: profile.username, display_name: profile.username }));
  if (accounts[0]) await dbUpdate("social_accounts", `id=eq.${account.id}`, { active: true });
  const fields = {
    mode: "api",
    status: "conectado",
    token_ciphertext: encrypt(token),
    token_expires_at: new Date(Date.now() + expiresInSeconds * 1000).toISOString(),
    token_refreshed_at: new Date().toISOString(),
    scopes: ["instagram_business_basic", "instagram_business_content_publish"],
    external_user_id: profile.user_id,
    last_error: null,
  };
  const [existing] = await dbSelect<{ id: string }>("platform_connections", `select=id&account_id=eq.${account.id}`);
  if (existing) await dbUpdate("platform_connections", `id=eq.${existing.id}`, fields);
  else await dbInsert("platform_connections", { account_id: account.id, ...fields });
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
    const fresh = await igRefresh(conn.token);
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
