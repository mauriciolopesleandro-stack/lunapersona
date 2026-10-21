// Banco Supabase (Postgres via PostgREST) so do lado do servidor, via fetch
// puro - sem dependencia nova, como o _store.ts. A chave secreta fica nas
// variaveis da Vercel e nunca vai para o navegador. Os arquivos (imagens e
// videos) NAO vem para ca: ficam no computador do usuario; o banco guarda so
// os dados sobre eles.
//
// Variaveis (todas so de servidor):
//   SUPABASE_URL                         https://<projeto>.supabase.co
//   SUPABASE_SECRET_KEY                  sb_secret_... (chave nova)
//     ou SUPABASE_SERVICE_ROLE_KEY       JWT service_role (chave antiga)

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
    const b = body as { message?: string; error?: string; code?: string } | null;
    const err = new Error(`Supabase respondeu ${res.status}${b?.message || b?.error ? `: ${b.message ?? b.error}` : ""}`);
    (err as Error & { status?: number; code?: string }).status = res.status;
    (err as Error & { status?: number; code?: string }).code = b?.code;
    throw err;
  }
  return body as T;
}

export function dbSelect<T>(table: string, query: string): Promise<T[]> {
  return call<T[]>(`/rest/v1/${table}?${query}`);
}

// O PostgREST do Supabase devolve no maximo 1000 linhas por pedido: busca em
// paginas ate acabar.
export async function dbSelectAll<T>(table: string, query: string, page = 1000): Promise<T[]> {
  const out: T[] = [];
  for (let offset = 0; ; offset += page) {
    const rows = await dbSelect<T>(table, `${query}&limit=${page}&offset=${offset}`);
    out.push(...rows);
    if (rows.length < page) return out;
  }
}

export async function dbInsert<T>(table: string, row: object): Promise<T> {
  const rows = await call<T[]>(`/rest/v1/${table}`, {
    method: "POST",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify(row),
  });
  return rows[0];
}

// Insere varias linhas; as que batem numa chave unica (onConflict) sao
// ignoradas - nunca sobrescreve o que ja existe. Devolve so as inseridas.
export function dbInsertIgnoringDuplicates<T>(table: string, rows: object[], onConflict: string): Promise<T[]> {
  return call<T[]>(`/rest/v1/${table}?on_conflict=${encodeURIComponent(onConflict)}`, {
    method: "POST",
    headers: { Prefer: "return=representation,resolution=ignore-duplicates" },
    body: JSON.stringify(rows),
  });
}

export async function dbUpdate<T>(table: string, query: string, patch: object): Promise<T | null> {
  const rows = await call<T[]>(`/rest/v1/${table}?${query}`, {
    method: "PATCH",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify(patch),
  });
  return rows[0] ?? null;
}

// Atualiza todas as linhas que batem no filtro (sem devolver nada).
export async function dbUpdateWhere(table: string, query: string, patch: object): Promise<void> {
  await call(`/rest/v1/${table}?${query}`, { method: "PATCH", headers: { Prefer: "return=minimal" }, body: JSON.stringify(patch) });
}

// Insere ou atualiza pela chave unica (onConflict).
export async function dbUpsert(table: string, row: object, onConflict: string): Promise<void> {
  await call(`/rest/v1/${table}?on_conflict=${encodeURIComponent(onConflict)}`, {
    method: "POST",
    headers: { Prefer: "return=minimal,resolution=merge-duplicates" },
    body: JSON.stringify(row),
  });
}

export async function dbDelete(table: string, query: string): Promise<void> {
  if (!query) throw new Error("dbDelete sem filtro");
  await call(`/rest/v1/${table}?${query}`, { method: "DELETE", headers: { Prefer: "return=minimal" } });
}

// Funcao do banco (ex.: claim_jobs), chamada pelo PostgREST.
export function dbRpc<T>(fn: string, args: object): Promise<T> {
  return call<T>(`/rest/v1/rpc/${fn}`, { method: "POST", body: JSON.stringify(args) });
}

// Historico de operacoes. Nao derruba a operacao se falhar - so registra.
export async function audit(action: string, entity: string, entityId: string | null, details: object = {}): Promise<void> {
  await dbInsert("audit_logs", { action, entity, entity_id: entityId, details }).catch((e) =>
    console.error("audit_logs falhou:", e instanceof Error ? e.message : e)
  );
}
