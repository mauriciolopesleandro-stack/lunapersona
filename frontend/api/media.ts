import type { IncomingMessage, ServerResponse } from "http";
import { randomUUID } from "crypto";
import { isAuthenticated } from "./_auth.js";
import {
  audit,
  dbInsert,
  dbSelect,
  dbUpdate,
  maxUploadBytes,
  objectSize,
  signedUploadUrl,
  signedUrls,
  supabaseConfigured,
} from "./_supabase.js";

interface MediaRow {
  id: string;
  created_at: string;
  kind: "image" | "video";
  status: "uploading" | "ready" | "archived";
  storage_path: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  sha256: string | null;
  title: string | null;
  notes: string | null;
}

// Tipos aceitos (os mesmos do bucket em supabase/migrations/0001_biblioteca.sql).
const TYPES: Record<string, { kind: "image" | "video"; ext: string }> = {
  "image/jpeg": { kind: "image", ext: "jpg" },
  "image/png": { kind: "image", ext: "png" },
  "image/webp": { kind: "image", ext: "webp" },
  "video/mp4": { kind: "video", ext: "mp4" },
  "video/quicktime": { kind: "video", ext: "mov" },
  "video/webm": { kind: "video", ext: "webm" },
};

const READ_URL_SECONDS = 60 * 60;
const PAGE_SIZE = 60;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;

function text(value: unknown, max: number): string | null {
  if (typeof value !== "string") return null;
  const t = value.trim().slice(0, max);
  return t || null;
}

// /api/media - biblioteca de conteudo (pagina "Conteúdos"). Uma funcao so
// para nao passar do limite de funcoes do plano da Vercel.
// GET ?status=ready|archived&kind=image|video&before=<data>: lista, com URL
//   de leitura assinada de cada arquivo.
// POST { action }:
//   upload-url { name, type, size, sha256? }  cria a linha e devolve a URL de envio
//   confirm { id }                             confere que o arquivo chegou e libera
//   update { id, title?, notes? }
//   archive { id } / restore { id }            esconde / volta (nunca apaga)
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessao expirada. Entre de novo." });
  if (!supabaseConfigured()) {
    return reply(501, { configured: false, detail: "Supabase ainda nao configurado na Vercel." });
  }

  try {
    if (req.method === "GET") {
      const params = new URL(req.url ?? "/", "http://local").searchParams;
      const status = params.get("status") === "archived" ? "archived" : "ready";
      const filters = [`status=eq.${status}`];
      const kind = params.get("kind");
      if (kind === "image" || kind === "video") filters.push(`kind=eq.${kind}`);
      const before = params.get("before");
      if (before && !Number.isNaN(Date.parse(before))) filters.push(`created_at=lt.${encodeURIComponent(before)}`);
      const rows = await dbSelect<MediaRow>(
        "media",
        `select=*&${filters.join("&")}&order=created_at.desc&limit=${PAGE_SIZE}`
      );
      const urls = await signedUrls(
        rows.map((r) => r.storage_path),
        READ_URL_SECONDS
      );
      return reply(200, {
        configured: true,
        maxBytes: maxUploadBytes(),
        items: rows.map((r) => ({ ...r, url: urls[r.storage_path] ?? null })),
        more: rows.length === PAGE_SIZE,
      });
    }
    if (req.method !== "POST") return reply(405, { detail: "Use GET ou POST." });

    let raw = "";
    for await (const chunk of req) raw += chunk;
    let body: Record<string, unknown>;
    try {
      body = JSON.parse(raw || "{}");
    } catch {
      return reply(400, { detail: "JSON invalido." });
    }

    if (body.action === "upload-url") {
      const type = TYPES[String(body.type)];
      if (!type) return reply(400, { detail: "Tipo de arquivo nao aceito. Use JPG, PNG, WebP, MP4, MOV ou WebM." });
      const size = Number(body.size);
      if (!Number.isInteger(size) || size <= 0) return reply(400, { detail: "Tamanho do arquivo invalido." });
      const max = maxUploadBytes();
      if (size > max) {
        return reply(413, { detail: `Arquivo maior que o limite de ${Math.round(max / 1024 / 1024)} MB.` });
      }
      const sha256 = typeof body.sha256 === "string" && SHA256.test(body.sha256) ? body.sha256 : null;
      if (sha256 && body.allowDuplicate !== true) {
        const same = await dbSelect<MediaRow>(
          "media",
          `select=id,original_name,status&sha256=eq.${sha256}&status=neq.uploading&limit=1`
        );
        if (same.length) {
          return reply(409, {
            duplicate: true,
            detail: `Esse arquivo já está na biblioteca (${same[0].original_name}${same[0].status === "archived" ? ", arquivado" : ""}).`,
          });
        }
      }
      const name = text(body.name, 200) ?? `arquivo.${type.ext}`;
      const now = new Date();
      const id = randomUUID();
      // Caminho sem o nome original (que pode ter acento, espaco ou dado
      // pessoal); o nome fica so na linha do banco.
      const path = `${now.getUTCFullYear()}/${String(now.getUTCMonth() + 1).padStart(2, "0")}/${id}.${type.ext}`;
      await dbInsert<MediaRow>("media", {
        id,
        kind: type.kind,
        status: "uploading",
        storage_path: path,
        original_name: name,
        mime_type: String(body.type),
        size_bytes: size,
        sha256,
      });
      return reply(200, { id, uploadUrl: await signedUploadUrl(path) });
    }

    const id = typeof body.id === "string" && UUID.test(body.id) ? body.id : null;
    if (!id) return reply(400, { detail: "Arquivo nao informado." });
    const [row] = await dbSelect<MediaRow>("media", `select=*&id=eq.${id}`);
    if (!row) return reply(404, { detail: "Arquivo nao encontrado." });
    const now = new Date().toISOString();

    if (body.action === "confirm") {
      if (row.status !== "uploading") return reply(200, { item: row });
      const size = await objectSize(row.storage_path);
      if (size === null) return reply(409, { detail: "O arquivo ainda nao chegou no armazenamento. Envie de novo." });
      const item = await dbUpdate<MediaRow>("media", `id=eq.${id}`, {
        status: "ready",
        size_bytes: size || row.size_bytes,
        updated_at: now,
      });
      await audit("upload", id, { name: row.original_name, size: size || row.size_bytes, type: row.mime_type });
      return reply(200, { item });
    }
    if (body.action === "update") {
      const patch: Record<string, unknown> = { updated_at: now };
      if ("title" in body) patch.title = text(body.title, 200);
      if ("notes" in body) patch.notes = text(body.notes, 4000);
      const item = await dbUpdate<MediaRow>("media", `id=eq.${id}`, patch);
      await audit("update", id, { fields: Object.keys(patch).filter((k) => k !== "updated_at") });
      return reply(200, { item });
    }
    if (body.action === "archive" || body.action === "restore") {
      if (row.status === "uploading") return reply(409, { detail: "Esse envio nao terminou." });
      const status = body.action === "archive" ? "archived" : "ready";
      const item = await dbUpdate<MediaRow>("media", `id=eq.${id}`, { status, updated_at: now });
      await audit(body.action, id, { name: row.original_name });
      return reply(200, { item });
    }
    return reply(400, { detail: "Acao desconhecida." });
  } catch (e) {
    return reply(502, { detail: e instanceof Error ? e.message : String(e) });
  }
}
