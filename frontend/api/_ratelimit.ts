// Limite de tentativas de senha (login e troca de senha), por IP: depois de
// MAX_FAILURES erros, aquele IP espera ate o fim da janela. Contador no
// Upstash (_store.ts); o IP vai como hash, nunca em texto. Sem armazenamento
// configurado nao ha limite (igual a antes).
import { createHash } from "crypto";
import type { IncomingMessage } from "http";
import { storeConfigured, storeDelete, storeGet, storeIncrWithTtl, storeTtl } from "./_store.js";

const MAX_FAILURES = 5;
const WINDOW_SECONDS = 15 * 60;

// Na Vercel, x-real-ip e x-forwarded-for sao escritos pela propria Vercel (o
// valor mandado pelo cliente e substituido), entao nao da para forjar outro IP.
function clientIp(req: IncomingMessage): string {
  const header = (name: string) => {
    const value = req.headers[name];
    return (Array.isArray(value) ? value[0] : value)?.split(",")[0].trim();
  };
  return header("x-real-ip") || header("x-forwarded-for") || req.socket?.remoteAddress || "desconhecido";
}

function failureKey(req: IncomingMessage): string {
  const ip = createHash("sha256").update(clientIp(req)).digest("hex").slice(0, 32);
  return `luna:auth_fail:${ip}`;
}

// Segundos que este IP ainda precisa esperar (0 = pode tentar).
export async function blockedFor(req: IncomingMessage): Promise<number> {
  if (!storeConfigured()) return 0;
  const key = failureKey(req);
  if (Number(await storeGet(key)) < MAX_FAILURES) return 0;
  const ttl = await storeTtl(key);
  return ttl > 0 ? ttl : WINDOW_SECONDS;
}

export async function recordFailure(req: IncomingMessage): Promise<void> {
  if (storeConfigured()) await storeIncrWithTtl(failureKey(req), WINDOW_SECONDS);
}

export async function clearFailures(req: IncomingMessage): Promise<void> {
  if (storeConfigured()) await storeDelete(failureKey(req));
}

export function tooManyAttemptsMessage(seconds: number): string {
  return `Muitas tentativas erradas. Tente de novo em ${Math.max(1, Math.ceil(seconds / 60))} min.`;
}
