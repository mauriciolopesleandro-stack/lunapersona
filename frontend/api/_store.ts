// Armazenamento minimo para o que precisa mudar sem redeploy (hoje: so a
// senha de login). Usa um Redis com API REST (Upstash, criado pela aba
// Storage da Vercel), via fetch puro - sem dependencia nova. A Vercel
// injeta KV_REST_API_URL/KV_REST_API_TOKEN ao conectar o banco ao projeto;
// os nomes UPSTASH_REDIS_REST_* tambem valem, se criado direto na Upstash.

function storeCredentials(): { url: string; token: string } | null {
  const url = process.env.KV_REST_API_URL ?? process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN ?? process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return null;
  return { url: url.replace(/\/+$/, ""), token };
}

export function storeConfigured(): boolean {
  return storeCredentials() !== null;
}

async function command(args: string[]): Promise<unknown> {
  const creds = storeCredentials();
  if (!creds) throw new Error("Armazenamento nao configurado na Vercel.");
  const res = await fetch(creds.url, {
    method: "POST",
    headers: { Authorization: `Bearer ${creds.token}`, "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  const body = (await res.json().catch(() => ({}))) as { result?: unknown; error?: string };
  if (!res.ok || body.error) {
    throw new Error(`Armazenamento respondeu ${res.status}${body.error ? `: ${body.error}` : ""}`);
  }
  return body.result;
}

export async function storeGet(key: string): Promise<string | null> {
  const result = await command(["GET", key]);
  return typeof result === "string" ? result : null;
}

export async function storeSet(key: string, value: string): Promise<void> {
  await command(["SET", key, value]);
}

// Grava so se a chave nao existir, com validade em segundos. Devolve true
// se gravou - serve de trava simples entre funcoes serverless.
export async function storeSetIfAbsent(key: string, value: string, ttlSeconds: number): Promise<boolean> {
  const result = await command(["SET", key, value, "NX", "EX", String(ttlSeconds)]);
  return result === "OK";
}

export async function storeDelete(key: string): Promise<void> {
  await command(["DEL", key]);
}

export async function storeIncr(key: string): Promise<number> {
  return Number(await command(["INCR", key]));
}

// Soma 1 e, na primeira vez, poe a validade - numa operacao so (script Lua),
// para o contador nunca ficar sem validade se a funcao cair no meio.
export async function storeIncrWithTtl(key: string, ttlSeconds: number): Promise<number> {
  const script = "local n = redis.call('INCR', KEYS[1]) if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end return n";
  return Number(await command(["EVAL", script, "1", key, String(ttlSeconds)]));
}

// Segundos que faltam para a chave expirar (-2 = nao existe, -1 = sem validade).
export async function storeTtl(key: string): Promise<number> {
  return Number(await command(["TTL", key]));
}
