// Regras das publicacoes (Fase 2), usadas por api/_posts_api.ts e api/_jobs.ts.
import { createHash } from "crypto";
import { dbInsertIgnoringDuplicates, dbSelect, dbUpdateWhere, storageRemove } from "./_supabase.js";

export const UPLOAD_BUCKET = "publicar-temp";

export type Network = "instagram" | "tiktok" | "telegram";
export type PostStatus =
  | "rascunho"
  | "aguardando_aprovacao"
  | "aprovado"
  | "agendado"
  | "publicando"
  | "publicado"
  | "falhou"
  | "cancelado";

export interface PostRow {
  id: string;
  network: Network;
  caption: string;
  hashtags: string[];
  scheduled_at: string | null;
  status: PostStatus;
  publish_mode: "manual" | "api";
  version: number;
  content_hash: string;
  approved_version: number | null;
  approved_hash: string | null;
  failure_reason: string | null;
  published_at: string | null;
  cancelled_at: string | null;
  legacy_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MediaCheckRow {
  id: string;
  title: string;
  status: string;
  content_rating: string;
  allowed_destinations: string[];
}

// Limites de cada rede. Instagram (carrossel de ate 20) e Telegram (legenda de
// midia 1024, album de ate 10) seguem a documentacao oficial; os do TikTok sao
// provisorios e precisam ser conferidos na integracao (Fase 2 e manual).
export const NETWORK_RULES: Record<Network, { label: string; formats: string[]; captionMax: number; hashtagMax: number; mediaMax: number }> = {
  instagram: { label: "Instagram", formats: ["Feed", "Reels", "Stories"], captionMax: 2200, hashtagMax: 30, mediaMax: 20 },
  tiktok: { label: "TikTok", formats: ["Vídeo", "Foto"], captionMax: 4000, hashtagMax: 100, mediaMax: 35 },
  telegram: { label: "Telegram", formats: ["Canal", "Grupo"], captionMax: 1024, hashtagMax: 100, mediaMax: 10 },
};

// Impressao digital do que a aprovacao cobre: rede, destinos, midias (na
// ordem), legenda e hashtags. Mudou qualquer um -> nova versao, nova aprovacao.
// O horario fica de fora: remarcar nao muda o conteudo aprovado.
// O modo automatico entra na conta (trocar de manual para automatico pede nova
// aprovacao); posts manuais mantem a impressao de antes.
export function contentHash(p: { network: string; formats: string[]; mediaIds: string[]; caption: string; hashtags: string[]; mode?: string }): string {
  const parts: unknown[] = [p.network, [...p.formats].sort(), p.mediaIds, p.caption, p.hashtags];
  if (p.mode === "api") parts.push("api");
  const canonical = JSON.stringify(parts);
  return createHash("sha256").update(canonical).digest("hex");
}

// Confere cada midia contra a rede: existe, nao esta arquivada, adulto so no
// Telegram, e o destino esta liberado no conteudo. Devolve o problema ou null.
export async function mediaProblem(network: Network, mediaIds: string[]): Promise<string | null> {
  if (!mediaIds.length) return null;
  const rows = await dbSelect<MediaCheckRow>(
    "media_assets",
    `select=id,title,status,content_rating,allowed_destinations&id=in.(${mediaIds.join(",")})`
  );
  for (const id of mediaIds) {
    const m = rows.find((r) => r.id === id);
    if (!m) return "Uma das mídias não existe mais na biblioteca.";
    if (m.status === "arquivado") return `“${m.title}” está arquivado.`;
    if (m.content_rating === "adulto" && network !== "telegram") {
      return `“${m.title}” é conteúdo adulto: só pode ir para o Telegram.`;
    }
    if (m.allowed_destinations.length && !m.allowed_destinations.includes(network)) {
      return `“${m.title}” não está liberado para ${NETWORK_RULES[network].label}.`;
    }
  }
  return null;
}

// Tarefa no horario: publicar sozinho (modo automatico) ou lembrar (manual).
// A chave junta tipo, publicacao, versao e horario: agendar de novo a mesma
// coisa nunca cria tarefa repetida.
export async function scheduleReminder(post: Pick<PostRow, "id" | "version" | "scheduled_at" | "publish_mode" | "network">): Promise<void> {
  if (!post.scheduled_at) return;
  const runAt = new Date(post.scheduled_at).toISOString();
  const kind =
    post.publish_mode !== "api" ? "lembrete_publicacao" : post.network === "tiktok" ? "enviar_tiktok" : "publicar_instagram";
  await dbInsertIgnoringDuplicates(
    "scheduled_jobs",
    [
      {
        kind,
        post_id: post.id,
        post_version: post.version,
        run_at: runAt,
        idempotency_key: `${kind === "lembrete_publicacao" ? "lembrete" : kind === "enviar_tiktok" ? "tiktok" : "publicar"}:${post.id}:v${post.version}:${runAt}`,
        ...(kind !== "lembrete_publicacao" ? { max_attempts: 6 } : {}),
      },
    ],
    "idempotency_key"
  );
}

// Apaga as copias temporarias (do bucket e marca no banco). keepVersion: a
// versao aprovada que ainda vai ser publicada.
export async function cleanupUploads(postId: string, keepVersion?: number): Promise<number> {
  const rows = await dbSelect<{ id: string; storage_path: string; post_version: number }>(
    "post_uploads",
    `select=id,storage_path,post_version&post_id=eq.${postId}&deleted_at=is.null`
  );
  const gone = rows.filter((r) => r.post_version !== keepVersion);
  if (!gone.length) return 0;
  await storageRemove(UPLOAD_BUCKET, gone.map((r) => r.storage_path));
  await dbUpdateWhere("post_uploads", `id=in.(${gone.map((r) => r.id).join(",")})`, { deleted_at: new Date().toISOString() });
  return gone.length;
}

export async function cancelJobs(postId: string): Promise<void> {
  await dbUpdateWhere("scheduled_jobs", `post_id=eq.${postId}&status=in.(pendente,executando)`, {
    status: "cancelado",
    finished_at: new Date().toISOString(),
  });
}
