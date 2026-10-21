// Autenticacao simples de conta unica (sem banco de dados, sem multiplos
// usuarios): uma senha guardada em APP_LOGIN_PASSWORD na Vercel, e uma
// sessao guardada num cookie assinado com HMAC-SHA256 (SESSION_SECRET) -
// nao precisa de tabela de sessoes, o proprio cookie carrega a validade
// assinada, e so o servidor com o segredo consegue forjar um valido.
//
// Para derrubar sessoes (troca de senha, "sair de todos os aparelhos"), o
// cookie carrega tambem uma versao: vale so se for igual a luna:session_version
// no Upstash (_store.ts). Subir a versao invalida todos os cookies de uma vez.
import { createHmac, timingSafeEqual } from "crypto";
import type { IncomingMessage } from "http";
import { storeConfigured, storeGet, storeIncr } from "./_store.js";

const COOKIE_NAME = "luna_session";
const ONE_DAY_SECONDS = 24 * 60 * 60;
const VERSION_KEY = "luna:session_version";

function sessionSecret(): string {
  const secret = process.env.SESSION_SECRET;
  if (!secret) {
    throw new Error("SESSION_SECRET nao configurada na Vercel.");
  }
  return secret;
}

function sign(payload: string): string {
  return createHmac("sha256", sessionSecret()).update(payload).digest("hex");
}

function now(): number {
  return Math.floor(Date.now() / 1000);
}

// Compara em tempo constante via digest SHA-256 (mesmo tamanho sempre),
// evita timing attack sem exigir que as duas strings originais tenham o
// mesmo tamanho.
export function safeEqual(a: string, b: string): boolean {
  const ha = createHmac("sha256", "cmp").update(a).digest();
  const hb = createHmac("sha256", "cmp").update(b).digest();
  return timingSafeEqual(ha, hb);
}

export interface Session {
  version: string;
  expiresAt: number;
  // Cookie de 30 dias ("lembrar") ou so ate fechar o navegador.
  persistent: boolean;
}

// Sem armazenamento configurado a versao e sempre "0" (igual a antes: nao da
// para derrubar sessoes). Lanca erro se o armazenamento estiver fora do ar.
async function currentVersion(): Promise<string> {
  if (!storeConfigured()) return "0";
  return (await storeGet(VERSION_KEY)) ?? "0";
}

// Token = "<versao>.<expiraEm(epoch segundos)>.<p|s>.<assinatura hmac>".
function createSessionToken(session: Session): string {
  const payload = `${session.version}.${session.expiresAt}.${session.persistent ? "p" : "s"}`;
  return `${payload}.${sign(payload)}`;
}

// Confere so assinatura e validade; a versao e conferida em currentSession.
function readSessionToken(token: string | undefined): Session | null {
  if (!token) return null;
  const parts = token.split(".");
  let payload: string;
  let signature: string;
  let session: Session;
  if (parts.length === 2) {
    // Cookie de antes da versao de sessao ("<expiraEm>.<assinatura>"): vale
    // como versao "0", ate a primeira troca de senha ou "sair de todos".
    [payload, signature] = parts;
    const expiresAt = Number(payload);
    session = { version: "0", expiresAt, persistent: expiresAt - now() > ONE_DAY_SECONDS };
  } else if (parts.length === 4) {
    const [version, expiresAt, kind] = parts;
    payload = `${version}.${expiresAt}.${kind}`;
    signature = parts[3];
    session = { version, expiresAt: Number(expiresAt), persistent: kind === "p" };
  } else {
    return null;
  }
  if (!payload || !signature || !safeEqual(sign(payload), signature)) return null;
  if (!Number.isFinite(session.expiresAt) || session.expiresAt <= now()) return null;
  return session;
}

// Token do backend do pod (cabecalho X-Luna-Token, backend/app/security.py).
// Derivado do SESSION_SECRET: sem variavel nova na Vercel. O pod recebe o
// mesmo valor ao ser criado (podEnv em _runpod.ts) e o site so o recebe
// depois do login (/api/runpod-status).
export function backendApiToken(): string {
  return createHmac("sha256", sessionSecret()).update("luna-backend-api-v1").digest("hex");
}

export function parseCookies(req: IncomingMessage): Record<string, string> {
  const header = req.headers.cookie;
  if (!header) return {};
  const out: Record<string, string> = {};
  for (const part of header.split(";")) {
    const idx = part.indexOf("=");
    if (idx === -1) continue;
    const key = part.slice(0, idx).trim();
    const value = part.slice(idx + 1).trim();
    if (key) out[key] = decodeURIComponent(value);
  }
  return out;
}

// Sessao valida do pedido, ou null. Armazenamento fora do ar = null: melhor
// pedir login de novo do que aceitar uma sessao que pode ter sido derrubada.
export async function currentSession(req: IncomingMessage): Promise<Session | null> {
  const session = readSessionToken(parseCookies(req)[COOKIE_NAME]);
  if (!session) return null;
  try {
    return session.version === (await currentVersion()) ? session : null;
  } catch (e) {
    console.error("Nao deu para conferir a versao da sessao:", e);
    return null;
  }
}

export async function isAuthenticated(req: IncomingMessage): Promise<boolean> {
  return (await currentSession(req)) !== null;
}

// Invalida todos os cookies emitidos ate agora, em todos os aparelhos.
export async function revokeAllSessions(): Promise<void> {
  await storeIncr(VERSION_KEY);
}

// remember=true -> cookie persistente de 30 dias; remember=false -> cookie
// de sessao (sem Max-Age), some quando o navegador fecha. expiresAt mantem a
// validade de uma sessao que esta sendo renovada (troca de senha).
export async function buildSetCookie(remember: boolean, expiresAt?: number): Promise<string> {
  const session: Session = {
    version: await currentVersion(),
    expiresAt: expiresAt ?? now() + (remember ? 30 : 1) * ONE_DAY_SECONDS,
    persistent: remember,
  };
  const attrs = ["Path=/", "HttpOnly", "Secure", "SameSite=Lax"];
  if (remember) attrs.push(`Max-Age=${Math.max(0, session.expiresAt - now())}`);
  return `${COOKIE_NAME}=${encodeURIComponent(createSessionToken(session))}; ${attrs.join("; ")}`;
}

export function buildClearCookie(): string {
  return `${COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
}
