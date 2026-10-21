import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { audit, dbInsert, dbInsertIgnoringDuplicates, dbSelect, dbSelectAll, dbUpdate, supabaseConfigured } from "./_supabase.js";

// /api/library - Fase 1: biblioteca de conteudo, personagens, campanhas e
// historico de operacoes, no Supabase. Os arquivos continuam no computador;
// aqui so os dados (local_path aponta para o arquivo na pasta conteudos/).
//
// GET                    -> { media, characters, campaigns }
// GET ?audit=1           -> ultimas operacoes
// POST { action }:
//   register { files: [...] }           cadastra arquivos novos da pasta (ignora os ja cadastrados)
//   update-media { id, patch }          edita dados de um conteudo (nunca apaga)
//   usage { media_ids, channel, context, reference? }   registra uso
//   save-character { id?, name, description?, active? }
//   save-campaign { id?, name, description?, starts_on?, ends_on?, status? }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DESTINATIONS = ["instagram", "tiktok", "telegram"];
const MAX_REGISTER = 200;

class BadRequest extends Error {}

function text(value: unknown, max: number, field: string, required = false): string | null {
  if (value === null || value === undefined || value === "") {
    if (required) throw new BadRequest(`Preencha ${field}.`);
    return null;
  }
  if (typeof value !== "string") throw new BadRequest(`${field} invalido.`);
  const t = value.trim();
  if (!t && required) throw new BadRequest(`Preencha ${field}.`);
  if (t.length > max) throw new BadRequest(`${field} passou de ${max} caracteres.`);
  return t || null;
}

function oneOf<T extends string>(value: unknown, options: readonly T[], field: string): T {
  if (typeof value !== "string" || !options.includes(value as T)) throw new BadRequest(`${field} invalido.`);
  return value as T;
}

function uuidOrNull(value: unknown, field: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string" || !UUID.test(value)) throw new BadRequest(`${field} invalido.`);
  return value;
}

function stringList(value: unknown, field: string, max: number, each: number): string[] {
  if (!Array.isArray(value)) throw new BadRequest(`${field} invalido.`);
  if (value.length > max) throw new BadRequest(`${field}: no maximo ${max}.`);
  return [...new Set(value.map((v) => text(v, each, field, true)!))];
}

function hashtags(value: unknown): string[] {
  return stringList(value, "Hashtags", 60, 100).map((t) => `#${t.replace(/^#+/, "").replace(/\s+/g, "")}`);
}

// Campos que o painel pode editar num conteudo, com a validacao de cada um.
const MEDIA_FIELDS: Record<string, (v: unknown) => unknown> = {
  title: (v) => text(v, 200, "Titulo", true),
  description: (v) => text(v, 4000, "Descricao"),
  character_id: (v) => uuidOrNull(v, "Personagem"),
  category: (v) => text(v, 80, "Categoria"),
  campaign_id: (v) => uuidOrNull(v, "Campanha"),
  caption: (v) => text(v, 4000, "Legenda"),
  hashtags,
  allowed_destinations: (v) => {
    const list = stringList(v, "Destinos", 3, 20);
    for (const d of list) if (!DESTINATIONS.includes(d)) throw new BadRequest("Destino invalido.");
    return list;
  },
  content_rating: (v) => oneOf(v, ["livre", "sensivel", "adulto"] as const, "Classificacao"),
  audience: (v) => oneOf(v, ["promocional", "assinantes"] as const, "Publico"),
  status: (v) => oneOf(v, ["rascunho", "aprovado", "arquivado"] as const, "Status"),
};

function friendly(e: unknown): { status: number; detail: string } {
  const err = e as Error & { status?: number; code?: string };
  if (err.code === "23514" && /media_adult_only_telegram/.test(err.message)) {
    return { status: 400, detail: "Conteúdo adulto só pode ter o Telegram como destino (Instagram e TikTok proíbem)." };
  }
  if (err.code === "23505") return { status: 409, detail: "Já existe um registro com esse nome." };
  if (err.code === "23503") return { status: 400, detail: "Personagem ou campanha não encontrada." };
  if (err.code === "23514") return { status: 400, detail: "Algum valor não é aceito pelo banco." };
  return { status: 502, detail: err.message || String(e) };
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessao expirada. Entre de novo." });
  if (!supabaseConfigured()) {
    return reply(501, { configured: false, detail: "Banco (Supabase) ainda nao configurado na Vercel." });
  }

  try {
    if (req.method === "GET") {
      const params = new URL(req.url ?? "/", "http://local").searchParams;
      if (params.get("audit")) {
        const logs = await dbSelect("audit_logs", "select=at,action,entity,entity_id,details&order=at.desc&limit=100");
        return reply(200, { logs });
      }
      const [media, characters, campaigns] = await Promise.all([
        dbSelectAll("media_assets", "select=*&order=created_at.desc"),
        dbSelectAll("characters", "select=*&order=name.asc"),
        dbSelectAll("campaigns", "select=*&order=created_at.desc"),
      ]);
      return reply(200, { configured: true, media, characters, campaigns });
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

    if (body.action === "register") {
      if (!Array.isArray(body.files) || body.files.length === 0) throw new BadRequest("Nenhum arquivo.");
      if (body.files.length > MAX_REGISTER) throw new BadRequest(`No maximo ${MAX_REGISTER} por vez.`);
      const rows = body.files.map((f: Record<string, unknown>) => {
        const path = text(f.local_path, 500, "Caminho", true)!;
        if (path.includes("..") || path.startsWith("/")) throw new BadRequest("Caminho invalido.");
        const row: Record<string, unknown> = {
          local_path: path,
          sha256: typeof f.sha256 === "string" && SHA256.test(f.sha256) ? f.sha256 : null,
          kind: oneOf(f.kind, ["image", "video"] as const, "Tipo"),
          mime_type: text(f.mime_type, 100, "Formato", true),
          size_bytes: Number.isSafeInteger(f.size_bytes) && (f.size_bytes as number) >= 0 ? f.size_bytes : 0,
          file_modified_at: typeof f.file_modified_at === "string" && !Number.isNaN(Date.parse(f.file_modified_at)) ? f.file_modified_at : null,
          title: text(f.title, 200, "Titulo", true),
          // Importacao do luna-vox.json: mantem o status e a data que ja
          // existiam. Sempre presentes: o PostgREST exige as mesmas chaves em
          // todas as linhas de uma insercao em lote.
          status: f.status === undefined ? "rascunho" : MEDIA_FIELDS.status(f.status),
          created_at:
            typeof f.created_at === "string" && !Number.isNaN(Date.parse(f.created_at)) ? f.created_at : new Date().toISOString(),
        };
        return row;
      });
      const inserted = await dbInsertIgnoringDuplicates("media_assets", rows, "local_path");
      if (inserted.length) await audit("cadastrar", "media_assets", null, { quantidade: inserted.length });
      return reply(200, { inserted });
    }

    if (body.action === "update-media") {
      const id = uuidOrNull(body.id, "Conteudo");
      if (!id) throw new BadRequest("Conteudo nao informado.");
      const input = (body.patch ?? {}) as Record<string, unknown>;
      const patch: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(input)) {
        const check = MEDIA_FIELDS[key];
        if (!check) throw new BadRequest(`Campo nao editavel: ${key}`);
        patch[key] = check(value);
      }
      if (!Object.keys(patch).length) throw new BadRequest("Nada para alterar.");
      const item = await dbUpdate("media_assets", `id=eq.${id}`, patch);
      if (!item) return reply(404, { detail: "Conteudo nao encontrado." });
      await audit("editar", "media_assets", id, { campos: Object.keys(patch), status: patch.status });
      return reply(200, { item });
    }

    if (body.action === "usage") {
      const ids = Array.isArray(body.media_ids) ? body.media_ids.map((v) => uuidOrNull(v, "Conteudo")).filter(Boolean) : [];
      if (!ids.length || ids.length > 50) throw new BadRequest("Conteudos invalidos.");
      const channel = oneOf(body.channel, ["instagram", "tiktok", "telegram", "outro"] as const, "Canal");
      const context = oneOf(body.context, ["post", "pack", "manual"] as const, "Contexto");
      const reference = text(body.reference, 200, "Referencia");
      for (const media_id of ids) await dbInsert("media_usage", { media_id, channel, context, reference });
      return reply(200, { ok: true });
    }

    if (body.action === "save-character") {
      const id = uuidOrNull(body.id, "Personagem");
      const row = {
        name: text(body.name, 80, "Nome", true),
        description: text(body.description, 2000, "Descricao"),
        active: body.active === undefined ? true : Boolean(body.active),
      };
      const item = id ? await dbUpdate("characters", `id=eq.${id}`, row) : await dbInsert("characters", row);
      if (!item) return reply(404, { detail: "Personagem nao encontrada." });
      await audit(id ? "editar" : "criar", "characters", (item as { id: string }).id, { name: row.name });
      return reply(200, { item });
    }

    if (body.action === "save-campaign") {
      const id = uuidOrNull(body.id, "Campanha");
      const date = (v: unknown, field: string) => {
        if (v === null || v === undefined || v === "") return null;
        if (typeof v !== "string" || !DATE.test(v)) throw new BadRequest(`${field} invalida.`);
        return v;
      };
      const row = {
        name: text(body.name, 120, "Nome", true),
        description: text(body.description, 2000, "Descricao"),
        starts_on: date(body.starts_on, "Data de inicio"),
        ends_on: date(body.ends_on, "Data de fim"),
        status: body.status === undefined ? "ativa" : oneOf(body.status, ["ativa", "pausada", "encerrada"] as const, "Status"),
      };
      if (row.starts_on && row.ends_on && row.ends_on < row.starts_on) throw new BadRequest("A data de fim vem antes do inicio.");
      const item = id ? await dbUpdate("campaigns", `id=eq.${id}`, row) : await dbInsert("campaigns", row);
      if (!item) return reply(404, { detail: "Campanha nao encontrada." });
      await audit(id ? "editar" : "criar", "campaigns", (item as { id: string }).id, { name: row.name, status: row.status });
      return reply(200, { item });
    }

    return reply(400, { detail: "Acao desconhecida." });
  } catch (e) {
    if (e instanceof BadRequest) return reply(400, { detail: e.message });
    const f = friendly(e);
    return reply(f.status, { detail: f.detail });
  }
}
