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
