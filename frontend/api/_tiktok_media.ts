import type { IncomingMessage, ServerResponse } from "http";
import { createHmac } from "crypto";
import { safeEqual } from "./_auth.js";
import { UPLOAD_BUCKET } from "./_posts.js";
import { audit, storageReadUrl } from "./_supabase.js";

// /api/tiktok/media/<caminho-base64url>.<expira>.<assinatura>.jpg (vercel.json
// reescreve para /api/studio?r=tiktok-media&f=...). O TikTok so baixa fotos
// (PULL_FROM_URL) de um endereco verificado pelo app (URL prefix do site), nao
// segue redirecionamento e confere a extensao do link. Esta rota entrega a copia
// temporaria da foto pelo proprio site, so com link assinado que vence; a
// copia continua no armazenamento privado. Sem sessao: quem chama e o TikTok.

// Limite de resposta das funcoes da Vercel (4,5 MB); as fotos ja vao em JPEG
// de no maximo 1440 px.
export const TIKTOK_PHOTO_MAX_BYTES = 4 * 1024 * 1024;

function signature(path: string, expires: number): string {
  const key = process.env.TOKEN_ENCRYPTION_KEY ?? "";
  if (key.length < 32) throw new Error("TOKEN_ENCRYPTION_KEY nao configurada.");
  return createHmac("sha256", key).update(`tiktok-media-v1|${path}|${expires}`).digest("base64url");
}

export function tiktokMediaUrl(baseUrl: string, path: string, ttlSeconds: number): string {
  const expires = Math.floor(Date.now() / 1000) + ttlSeconds;
  return `${baseUrl}/api/tiktok/media/${Buffer.from(path).toString("base64url")}.${expires}.${signature(path, expires)}.jpg`;
}

// Largura x altura de um JPEG (marcador SOF); 0 x 0 se nao achar.
export function jpegSize(b: Uint8Array): { w: number; h: number } {
  for (let i = 2; i + 9 < b.length; ) {
    if (b[i] !== 0xff) break;
    const marker = b[i + 1];
    const len = (b[i + 2] << 8) | b[i + 3];
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      return { h: (b[i + 5] << 8) | b[i + 6], w: (b[i + 7] << 8) | b[i + 8] };
    }
    i += 2 + len;
  }
  return { w: 0, h: 0 };
}

function parseFile(file: string): { path: string; expires: number; sig: string } | null {
  const m = /^([A-Za-z0-9_-]+)\.(\d+)\.([A-Za-z0-9_-]+)\.jpg$/.exec(file);
  if (!m) return null;
  return { path: Buffer.from(m[1], "base64url").toString(), expires: Number(m[2]), sig: m[3] };
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  // diagnostico: cada download fica registrado (quem pediu, como e o que recebeu)
  const seen = {
    method: req.method,
    ua: String(req.headers["user-agent"] ?? "").slice(0, 200),
    range: req.headers.range ?? null,
    accept: String(req.headers.accept ?? "").slice(0, 100),
  };
  const fail = async (status: number, path: string | null = null) => {
    await audit("tiktok_media", "tiktok", path, { ...seen, status });
    res.statusCode = status;
    res.setHeader("Cache-Control", "no-store");
    res.end();
  };
  if (req.method !== "GET" && req.method !== "HEAD") return fail(405);
  const url = new URL(req.url ?? "/", "http://local");
  const parsed = parseFile(url.searchParams.get("f") ?? url.pathname.split("/").pop() ?? "");
  if (!parsed) return fail(404);
  const { path, expires } = parsed;
  // so fotos da pasta de copias temporarias: <post>/v<versao>/<posicao>.jpg
  if (!/^[0-9a-f-]{36}\/v\d+\/\d+\.jpg$/.test(path) || !Number.isInteger(expires)) return fail(404);
  if (expires < Date.now() / 1000) return fail(410, path);
  let valid = false;
  try {
    valid = safeEqual(parsed.sig, signature(path, expires));
  } catch {
    return fail(503);
  }
  if (!valid) return fail(403, path);
  const upstream = await fetch(await storageReadUrl(UPLOAD_BUCKET, path, 600), { signal: AbortSignal.timeout(25_000) }).catch(() => null);
  if (!upstream?.ok) return fail(upstream?.status === 400 || upstream?.status === 404 ? 404 : 502, path);
  const bytes = Buffer.from(await upstream.arrayBuffer());
  if (bytes.length > TIKTOK_PHOTO_MAX_BYTES) return fail(413, path);
  await audit("tiktok_media", "tiktok", path, { ...seen, status: 200, bytes: bytes.length, ...jpegSize(bytes), soi: bytes[0] === 0xff && bytes[1] === 0xd8 });
  res.statusCode = 200;
  res.setHeader("Content-Type", "image/jpeg");
  res.setHeader("Content-Length", String(bytes.length));
  res.setHeader("Cache-Control", "private, no-store");
  res.end(req.method === "HEAD" ? undefined : bytes);
}
