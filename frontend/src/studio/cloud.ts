// Chamadas ao banco (Supabase) pela funcao /api/studio da Vercel. O navegador
// nunca fala direto com o Supabase nem ve a chave dele.
import type { Audience, ItemStatus, MetaPatch, Network, Rating } from "./store";

export interface MediaRow {
  id: string;
  local_path: string;
  kind: "image" | "video";
  title: string;
  status: ItemStatus;
  created_at: string;
  description: string | null;
  character_id: string | null;
  category: string | null;
  campaign_id: string | null;
  caption: string | null;
  hashtags: string[];
  allowed_destinations: string[];
  content_rating: Rating;
  audience: Audience;
}

export interface CharacterRow {
  id: string;
  name: string;
  description: string | null;
  active: boolean;
}

export interface CampaignRow {
  id: string;
  name: string;
  description: string | null;
  starts_on: string | null;
  ends_on: string | null;
  status: "ativa" | "pausada" | "encerrada";
}

export interface AuditRow {
  at: string;
  action: string;
  entity: string;
  entity_id: string | null;
  details: Record<string, unknown>;
}

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `Erro HTTP ${res.status}`);
  return body as T;
}

function post<T>(action: string, payload: object): Promise<T> {
  return fetch("/api/studio?r=library", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  }).then((r) => json<T>(r));
}

// null = banco ainda nao configurado (o estudio segue no modo pasta).
export async function cloudLoad(): Promise<{ media: MediaRow[]; characters: CharacterRow[]; campaigns: CampaignRow[] } | null> {
  const res = await fetch("/api/studio?r=library");
  if (res.status === 501) return null;
  return json(res);
}

export async function cloudRegister(files: object[]): Promise<MediaRow[]> {
  if (!files.length) return [];
  return (await post<{ inserted: MediaRow[] }>("register", { files })).inserted;
}

const PATCH_FIELDS: Record<keyof MetaPatch, string> = {
  title: "title",
  status: "status",
  description: "description",
  characterId: "character_id",
  category: "category",
  campaignId: "campaign_id",
  caption: "caption",
  hashtags: "hashtags",
  destinations: "allowed_destinations",
  rating: "content_rating",
  audience: "audience",
};

export async function cloudUpdateMedia(id: string, patch: MetaPatch): Promise<MediaRow> {
  const body: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(patch)) {
    const field = PATCH_FIELDS[k as keyof MetaPatch];
    if (field) body[field] = v ?? null;
  }
  return (await post<{ item: MediaRow }>("update-media", { id, patch: body })).item;
}

export async function cloudUsage(mediaIds: string[], channel: Network, context: "post" | "pack" | "manual", reference: string) {
  await post("usage", { media_ids: mediaIds, channel, context, reference });
}

export async function cloudSaveCharacter(c: { id?: string; name: string; description: string | null; active: boolean }): Promise<CharacterRow> {
  return (await post<{ item: CharacterRow }>("save-character", c)).item;
}

export async function cloudSaveCampaign(c: {
  id?: string;
  name: string;
  description: string | null;
  startsOn: string | null;
  endsOn: string | null;
  status: string;
}): Promise<CampaignRow> {
  return (
    await post<{ item: CampaignRow }>("save-campaign", {
      id: c.id,
      name: c.name,
      description: c.description,
      starts_on: c.startsOn,
      ends_on: c.endsOn,
      status: c.status,
    })
  ).item;
}

export async function cloudAudit(): Promise<AuditRow[]> {
  const res = await fetch("/api/studio?r=library&audit=1");
  return (await json<{ logs: AuditRow[] }>(res)).logs;
}

// --- Publicacoes (Fase 2, r=posts) e fila (r=jobs) ------------------------------------

export interface PostDto {
  id: string;
  network: Network;
  caption: string;
  hashtags: string[];
  scheduled_at: string | null;
  status: string;
  publish_mode: "manual" | "api";
  version: number;
  approved_version: number | null;
  failure_reason: string | null;
  published_at: string | null;
  legacy_id: string | null;
  created_at: string;
  updated_at: string;
  media_ids: string[];
  destinations: { format: string; status: string; external_url: string | null; confirmed_at: string | null; confirmed_by: string | null }[];
  approvals: { version: number; decision: string; note: string | null; decided_at: string }[];
  jobs: { id: string; run_at: string; status: string; attempts: number; max_attempts: number; last_error: string | null }[];
  attempts: { channel: string; outcome: string; detail: string | null; started_at: string }[];
}

export interface WorkerStatus {
  last_run_at: string;
  last_result: { origem?: string; processadas?: number; falhas?: number; erro?: string | null };
}

function postsCall<T>(action: string, payload: object): Promise<T> {
  return fetch("/api/studio?r=posts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  }).then((r) => json<T>(r));
}

export async function cloudPosts(): Promise<{ posts: PostDto[]; worker: WorkerStatus | null }> {
  return json(await fetch("/api/studio?r=posts"));
}

export async function cloudPostSave(payload: {
  id?: string;
  version?: number;
  network: Network;
  formats: string[];
  media_ids: string[];
  caption: string;
  hashtags: string[];
  scheduled_at: string | null;
  publish_mode: "manual" | "api";
}): Promise<{ post: PostDto; needsApproval?: boolean }> {
  return postsCall("save", payload);
}

export async function cloudPostAction(action: string, id: string, extra: object = {}): Promise<{ post: PostDto }> {
  return postsCall(action, { id, ...extra });
}

export async function cloudPostImport(posts: object[]): Promise<number> {
  if (!posts.length) return 0;
  return (await postsCall<{ imported: number }>("import", { posts })).imported;
}

export async function cloudRunJobs(): Promise<{ processadas: number; ok: number; falhas: number; novas_tentativas: number }> {
  return json(await fetch("/api/studio?r=jobs", { method: "POST" }));
}

// --- Instagram (publicacao automatica) ---------------------------------------------------

export interface InstagramStatus {
  encryption: boolean;
  connected: boolean;
  status: string;
  username: string | null;
  expiresAt: string | null;
  refreshedAt: string | null;
  lastError: string | null;
}

export async function cloudInstagram(): Promise<InstagramStatus> {
  return json(await fetch("/api/studio?r=instagram"));
}

export async function cloudInstagramAction(action: "connect" | "disconnect", token?: string): Promise<InstagramStatus> {
  return json(
    await fetch("/api/studio?r=instagram", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, token }),
    })
  );
}

// --- TikTok (rascunho automatico) -------------------------------------------------------

export interface TikTokStatus {
  appConfigured: boolean;
  encryption: boolean;
  redirectUri: string;
  connected: boolean;
  status: string;
  name: string | null;
  refreshedAt: string | null;
  validUntil: string | null;
  lastError: string | null;
}

export async function cloudTiktok(): Promise<TikTokStatus> {
  return json(await fetch("/api/studio?r=tiktok"));
}

export async function cloudTiktokDisconnect(): Promise<void> {
  await json(
    await fetch("/api/studio?r=tiktok", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "disconnect" }),
    })
  );
}

export async function cloudUploadUrls(
  id: string,
  version: number,
  items: { position: number; mime_type: string; size_bytes: number }[]
): Promise<{ position: number; url: string }[]> {
  return (await postsCall<{ uploads: { position: number; url: string }[] }>("upload-urls", { id, version, items })).uploads;
}

// Mesmo formato do uploadToSignedUrl do supabase-js (PUT multipart).
export async function putSignedUpload(url: string, file: Blob): Promise<void> {
  const form = new FormData();
  form.append("cacheControl", "3600");
  form.append("", file);
  const res = await fetch(url, { method: "PUT", headers: { "x-upsert": "true" }, body: form });
  if (!res.ok) throw new Error(`O armazenamento recusou o envio (HTTP ${res.status}).`);
}
