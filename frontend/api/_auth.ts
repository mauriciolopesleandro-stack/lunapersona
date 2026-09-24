// Autenticacao simples de conta unica (sem banco de dados, sem multiplos
// usuarios): uma senha guardada em APP_LOGIN_PASSWORD na Vercel, e uma
// sessao guardada num cookie assinado com HMAC-SHA256 (SESSION_SECRET) -
// nao precisa de tabela de sessoes, o proprio cookie carrega a validade
// assinada, e so o servidor com o segredo consegue forjar um valido.
import { createHmac, timingSafeEqual } from "crypto";
import type { IncomingMessage } from "http";

const COOKIE_NAME = "luna_session";
const THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60;

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

// Compara em tempo constante via digest SHA-256 (mesmo tamanho sempre),
// evita timing attack sem exigir que as duas strings originais tenham o
// mesmo tamanho.
export function safeEqual(a: string, b: string): boolean {
  const ha = createHmac("sha256", "cmp").update(a).digest();
  const hb = createHmac("sha256", "cmp").update(b).digest();
  return timingSafeEqual(ha, hb);
}

// Token = "<expiraEm(epoch segundos)>.<assinatura hmac>". Sem estado no
// servidor - validar so confere a assinatura e se ainda nao expirou.
export function createSessionToken(days: number): string {
  const expiresAt = Math.floor(Date.now() / 1000) + days * 24 * 60 * 60;
  const payload = String(expiresAt);
  return `${payload}.${sign(payload)}`;
}

export function verifySessionToken(token: string | undefined): boolean {
  if (!token) return false;
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return false;
  if (!safeEqual(sign(payload), signature)) return false;
  const expiresAt = Number(payload);
  if (!Number.isFinite(expiresAt)) return false;
  return expiresAt > Math.floor(Date.now() / 1000);
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

export function isAuthenticated(req: IncomingMessage): boolean {
  const cookies = parseCookies(req);
  return verifySessionToken(cookies[COOKIE_NAME]);
}

// remember=true -> cookie persistente de 30 dias; remember=false -> cookie
// de sessao (sem Max-Age), some quando o navegador fecha.
export function buildSetCookie(remember: boolean): string {
  const token = createSessionToken(remember ? 30 : 1);
  const attrs = ["Path=/", "HttpOnly", "Secure", "SameSite=Lax"];
  if (remember) attrs.push(`Max-Age=${THIRTY_DAYS_SECONDS}`);
  return `${COOKIE_NAME}=${encodeURIComponent(token)}; ${attrs.join("; ")}`;
}

export function buildClearCookie(): string {
  return `${COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
}
