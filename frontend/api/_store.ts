// Armazenamento das configuracoes pequenas (tokens dos bots, precos, textos,
// travas). 25/11/2026: saiu do Upstash (o plano gratis limita 500 mil acessos
// por mes e estourou) para uma tabela "kv" no Supabase (plano gratis sem
// limite de acessos). Mesmas funcoes de antes; as chaves continuam passando
// por personaKey (a Luna com as dela, a Cleuza com "cleuza:...").
// Tabela e funcoes: supabase/migrations/0011_kv.sql.

import { personaKey } from "./_persona.js";

function creds(): { url: string; headers: Record<string, string> } | null {
  const url = process.env.SUPABASE_URL?.trim();
  const key = (process.env.SUPABASE_SECRET_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY)?.trim();
  if (!url || !key) return null;
  const headers: Record<string, string> = key.startsWith("sb_secret_") ? { apikey: key } : { apikey: key, Authorization: `Bearer ${key}` };
  return { url: url.replace(/\/+$/, ""), headers };
}

export function storeConfigured(): boolean {
  return creds() !== null;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const c = creds();
  if (!c) throw new Error("Armazenamento nao configurado na Vercel.");
  const res = await fetch(`${c.url}${path}`, {
    ...init,
    headers: { ...c.headers, "Content-Type": "application/json", ...(init.headers as Record<string, string> | undefined) },
    signal: AbortSignal.timeout(15_000),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Armazenamento respondeu ${res.status}: ${text.slice(0, 200)}`);
  return (text ? JSON.parse(text) : null) as T;
}

const rpc = <T>(fn: string, args: Record<string, unknown>) => call<T>(`/rest/v1/rpc/${fn}`, { method: "POST", body: JSON.stringify(args) });
const k = (key: string) => encodeURIComponent(personaKey(key));

export async function storeGet(key: string): Promise<string | null> {
  const rows = await call<{ value: string; expires_at: string | null }[]>(`/rest/v1/kv?select=value,expires_at&key=eq.${k(key)}`);
  const row = rows[0];
  if (!row || (row.expires_at && Date.parse(row.expires_at) <= Date.now())) return null;
  return row.value;
}

export async function storeSet(key: string, value: string): Promise<void> {
  await call("/rest/v1/kv?on_conflict=key", {
    method: "POST",
    headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify({ key: personaKey(key), value, expires_at: null }),
  });
}

// Grava so se a chave nao existir (ou ja venceu), com validade em segundos.
// Devolve true se gravou - serve de trava simples entre funcoes serverless.
export async function storeSetIfAbsent(key: string, value: string, ttlSeconds: number): Promise<boolean> {
  return Boolean(await rpc<boolean>("kv_set_nx", { p_key: personaKey(key), p_value: value, p_ttl: ttlSeconds }));
}

export async function storeDelete(key: string): Promise<void> {
  await call(`/rest/v1/kv?key=eq.${k(key)}`, { method: "DELETE", headers: { Prefer: "return=minimal" } });
}

export async function storeIncr(key: string): Promise<number> {
  return Number(await rpc<number>("kv_incr", { p_key: personaKey(key), p_ttl: 0 }));
}

// Soma 1 e, na primeira vez, poe a validade.
export async function storeIncrWithTtl(key: string, ttlSeconds: number): Promise<number> {
  return Number(await rpc<number>("kv_incr", { p_key: personaKey(key), p_ttl: ttlSeconds }));
}

// Segundos que faltam para a chave expirar (-2 = nao existe, -1 = sem validade).
export async function storeTtl(key: string): Promise<number> {
  const rows = await call<{ expires_at: string | null }[]>(`/rest/v1/kv?select=expires_at&key=eq.${k(key)}`);
  const row = rows[0];
  if (!row) return -2;
  if (!row.expires_at) return -1;
  const s = Math.floor((Date.parse(row.expires_at) - Date.now()) / 1000);
  return s > 0 ? s : -2;
}
