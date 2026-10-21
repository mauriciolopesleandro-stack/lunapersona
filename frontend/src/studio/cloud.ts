// Chamadas ao banco (Supabase) pela funcao /api/library da Vercel. O navegador
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
  return fetch("/api/library", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  }).then((r) => json<T>(r));
}

// null = banco ainda nao configurado (o estudio segue no modo pasta).
export async function cloudLoad(): Promise<{ media: MediaRow[]; characters: CharacterRow[]; campaigns: CampaignRow[] } | null> {
  const res = await fetch("/api/library");
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
  const res = await fetch("/api/library?audit=1");
  return (await json<{ logs: AuditRow[] }>(res)).logs;
}
