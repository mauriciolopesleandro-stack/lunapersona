// Supabase (Postgres + Storage) so do lado do servidor, via fetch puro - sem
// dependencia nova, como o _store.ts. A chave secreta fica nas variaveis da
// Vercel e nunca vai para o navegador: o navegador so recebe URLs assinadas
// (upload direto para o Storage e leitura com validade curta).
//
// Variaveis (todas so de servidor):
//   SUPABASE_URL                         https://<projeto>.supabase.co
//   SUPABASE_SECRET_KEY                  sb_secret_... (chave nova)
//     ou SUPABASE_SERVICE_ROLE_KEY       JWT service_role (chave antiga)
//   SUPABASE_MEDIA_BUCKET                opcional, padrao luna-media
//   MEDIA_MAX_MB                         opcional, padrao 50 (limite do plano Free)

interface Credentials {
  url: string;
  headers: Record<string, string>;
}

function credentials(): Credentials | null {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) return null;
  // Chave nova (sb_secret_) vai so no apikey - nao e JWT. A antiga
  // (service_role) tambem precisa ir no Authorization.
  const headers: Record<string, string> = key.startsWith("sb_secret_")
    ? { apikey: key }
    : { apikey: key, Authorization: `Bearer ${key}` };
  return { url: url.replace(/\/+$/, ""), headers };
}

export function supabaseConfigured(): boolean {
  return credentials() !== null;
}

export function mediaBucket(): string {
  return process.env.SUPABASE_MEDIA_BUCKET ?? "luna-media";
}

export function maxUploadBytes(): number {
  const mb = Number(process.env.MEDIA_MAX_MB ?? "50");
  return (Number.isFinite(mb) && mb > 0 ? mb : 50) * 1024 * 1024;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const creds = credentials();
  if (!creds) throw new Error("Supabase nao configurado na Vercel.");
  const res = await fetch(`${creds.url}${path}`, {
    ...init,
    headers: { ...creds.headers, ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const b = body as { message?: string; error?: string } | null;
    throw new Error(`Supabase respondeu ${res.status}${b?.message || b?.error ? `: ${b.message ?? b.error}` : ""}`);
  }
  return body as T;
}

// --- Banco (PostgREST) -----------------------------------------------------

export function dbSelect<T>(table: string, query: string): Promise<T[]> {
  return call<T[]>(`/rest/v1/${table}?${query}`);
}

export async function dbInsert<T>(table: string, row: object): Promise<T> {
  const rows = await call<T[]>(`/rest/v1/${table}`, {
    method: "POST",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify(row),
  });
  return rows[0];
}

export async function dbUpdate<T>(table: string, query: string, patch: object): Promise<T | null> {
  const rows = await call<T[]>(`/rest/v1/${table}?${query}`, {
    method: "PATCH",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify(patch),
  });
  return rows[0] ?? null;
}

// Historico de operacoes. Nao derruba a operacao se falhar - so registra.
export async function audit(action: string, mediaId: string | null, details: object = {}): Promise<void> {
  await dbInsert("audit_log", { action, media_id: mediaId, details }).catch((e) =>
    console.error("audit_log falhou:", e instanceof Error ? e.message : e)
  );
}

// --- Storage -----------------------------------------------------------------

function objectPath(path: string): string {
  return `${encodeURIComponent(mediaBucket())}/${path.split("/").map(encodeURIComponent).join("/")}`;
}

// URL para o navegador enviar o arquivo direto ao Storage (vale 2 horas).
export async function signedUploadUrl(path: string): Promise<string> {
  const creds = credentials();
  const data = await call<{ url: string }>(`/storage/v1/object/upload/sign/${objectPath(path)}`, {
    method: "POST",
    body: "{}",
  });
  return `${creds!.url}/storage/v1${data.url}`;
}

// URLs de leitura com validade curta, varias numa chamada so.
export async function signedUrls(paths: string[], expiresIn: number): Promise<Record<string, string>> {
  if (paths.length === 0) return {};
  const creds = credentials();
  const data = await call<{ path: string | null; signedURL: string | null }[]>(
    `/storage/v1/object/sign/${encodeURIComponent(mediaBucket())}`,
    { method: "POST", body: JSON.stringify({ expiresIn, paths }) }
  );
  const out: Record<string, string> = {};
  for (const item of data) {
    if (item.path && item.signedURL) out[item.path] = `${creds!.url}/storage/v1${item.signedURL}`;
  }
  return out;
}

// Confere se o arquivo chegou no Storage. null = nao existe.
export async function objectSize(path: string): Promise<number | null> {
  try {
    const info = await call<{ size?: number; metadata?: { size?: number } }>(
      `/storage/v1/object/info/${objectPath(path)}`
    );
    return info?.size ?? info?.metadata?.size ?? 0;
  } catch (e) {
    if (e instanceof Error && /respondeu (400|404)/.test(e.message)) return null;
    throw e;
  }
}
