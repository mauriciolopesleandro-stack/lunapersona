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
  // trim: valor colado no painel da Vercel com espaco ou quebra de linha
  const url = process.env.SUPABASE_URL?.trim();
  const key = (process.env.SUPABASE_SECRET_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY)?.trim();
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
  if (res.status === 401 && /api key|apikey|jwt/i.test(text)) {
    // chave errada na Vercel (ex.: token sbp_ da conta ou chave publishable)
    const key = process.env.SUPABASE_SECRET_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY ?? "";
    const hint = key.trim().startsWith("sbp_")
      ? "isso é um token da conta (sbp_), não a chave do projeto"
      : key.trim().startsWith("sb_publishable_")
        ? "isso é a Publishable key; use a Secret key"
        : "copie de novo a Secret key (sb_secret_...) em Project Settings → API Keys";
    throw new Error(`O Supabase recusou a chave SUPABASE_SECRET_KEY da Vercel: ${hint}. Depois de corrigir, faça um Redeploy.`);
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

// --- Storage (so para as copias temporarias dos posts automaticos) ---------------

function objectPath(bucket: string, path: string): string {
  return `${encodeURIComponent(bucket)}/${path.split("/").map(encodeURIComponent).join("/")}`;
}

// Link para o navegador enviar o arquivo direto ao Storage (vale 2 horas).
export async function storageUploadUrl(bucket: string, path: string): Promise<string> {
  const data = await call<{ url: string }>(`/storage/v1/object/upload/sign/${objectPath(bucket, path)}`, { method: "POST", body: "{}" });
  return `${credentials()!.url}/storage/v1${data.url}`;
}

// Link de leitura com validade curta (o Instagram baixa por ele).
export async function storageReadUrl(bucket: string, path: string, expiresIn: number): Promise<string> {
  const data = await call<{ signedURL: string }>(`/storage/v1/object/sign/${objectPath(bucket, path)}`, {
    method: "POST",
    body: JSON.stringify({ expiresIn }),
  });
  return `${credentials()!.url}/storage/v1${data.signedURL}`;
}

// null = o arquivo nao existe.
export async function storageObjectSize(bucket: string, path: string): Promise<number | null> {
  try {
    const info = await call<{ size?: number; metadata?: { size?: number } }>(`/storage/v1/object/info/${objectPath(bucket, path)}`);
    return info?.size ?? info?.metadata?.size ?? 0;
  } catch (e) {
    if (e instanceof Error && /respondeu (400|404)/.test(e.message)) return null;
    throw e;
  }
}

// Copia um arquivo dentro do mesmo bucket (ex.: a foto escolhida vira a copia da nova versao).
export async function storageCopy(bucket: string, from: string, to: string): Promise<void> {
  await call(`/storage/v1/object/copy`, { method: "POST", body: JSON.stringify({ bucketId: bucket, sourceKey: from, destinationKey: to }) });
}

export async function storageRemove(bucket: string, paths: string[]): Promise<void> {
  if (!paths.length) return;
  await call(`/storage/v1/object/${encodeURIComponent(bucket)}`, { method: "DELETE", body: JSON.stringify({ prefixes: paths }) });
}

// Historico de operacoes. Nao derruba a operacao se falhar - so registra.
export async function audit(action: string, entity: string, entityId: string | null, details: object = {}): Promise<void> {
  await dbInsert("audit_logs", { action, entity, entity_id: entityId, details }).catch((e) =>
    console.error("audit_logs falhou:", e instanceof Error ? e.message : e)
  );
}
