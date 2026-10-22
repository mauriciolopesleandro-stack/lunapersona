import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  copyIntoFolder,
  fileSha256,
  folderSupported,
  mediaTypeOf,
  permissionOf,
  pickFolder,
  readData,
  rememberFolder,
  rememberedFolder,
  scanMedia,
  writeData,
  type DirHandle,
} from "./localFolder";
import { prepareForInstagram } from "./mediaPrep";
import {
  cloudInstagram,
  cloudInstagramAction,
  cloudTiktok,
  cloudTiktokDisconnect,
  cloudLoad,
  cloudPostAction,
  cloudPostImport,
  cloudPostSave,
  cloudPosts,
  cloudRegister,
  cloudRunJobs,
  cloudSaveCampaign,
  cloudSaveCharacter,
  cloudUpdateMedia,
  cloudUploadUrls,
  cloudUsage,
  putSignedUpload,
  type InstagramStatus,
  type TikTokStatus,
  type MediaRow,
  type PostDto,
  type WorkerStatus,
} from "./cloud";

export type ItemStatus = "rascunho" | "aprovado" | "arquivado";
export type Network = "instagram" | "tiktok" | "telegram";
// Modo pasta usa so rascunho/agendado/publicado. Modo banco (Fase 2) usa o
// fluxo completo, com aprovacao obrigatoria da versao.
export type PostStatus =
  | "rascunho"
  | "aguardando_aprovacao"
  | "aprovado"
  | "agendado"
  | "publicando"
  | "publicado"
  | "falhou"
  | "cancelado";
export type Rating = "livre" | "sensivel" | "adulto";
export type Audience = "promocional" | "assinantes";

// Dados de cada arquivo (chave = caminho em conteudos/). No modo pasta ficam
// no luna-vox.json; no modo banco, na tabela media_assets do Supabase.
export interface ItemMeta {
  id?: string; // id no banco (so no modo banco)
  title: string;
  status: ItemStatus;
  addedAt: string;
  description?: string;
  characterId?: string | null;
  category?: string;
  campaignId?: string | null;
  caption?: string;
  hashtags?: string[];
  destinations?: Network[];
  rating?: Rating;
  audience?: Audience;
}

export type MetaPatch = Partial<Omit<ItemMeta, "id" | "addedAt">>;

export interface Character {
  id: string;
  name: string;
  description: string | null;
  active: boolean;
}

export interface Campaign {
  id: string;
  name: string;
  description: string | null;
  startsOn: string | null;
  endsOn: string | null;
  status: "ativa" | "pausada" | "encerrada";
}

export interface Post {
  id: string;
  network: Network;
  formats: string[];
  media: string[]; // caminhos em conteudos/
  caption: string;
  hashtags: string[];
  scheduledAt: string | null; // ISO
  status: PostStatus;
  createdAt: string;
  updatedAt: string;
  // so no modo banco
  publishMode?: "manual" | "api";
  version?: number;
  approvedVersion?: number | null;
  failureReason?: string | null;
  publishedAt?: string | null;
  destinations?: { format: string; status: string; url: string | null; confirmedAt: string | null; confirmedBy: string | null }[];
  approvals?: { version: number; decision: string; note: string | null; at: string }[];
  jobs?: { runAt: string; status: string; attempts: number; maxAttempts: number; lastError: string | null }[];
  attempts?: { channel: string; outcome: string; detail: string | null; at: string }[];
}

export interface PostInput {
  publishMode?: "manual" | "api";
  network: Network;
  formats: string[];
  media: string[];
  caption: string;
  hashtags: string[];
  scheduledAt: string | null;
}

export type PostAction = "submit" | "approve" | "reject" | "schedule" | "confirm" | "fail" | "cancel";

function fromDto(d: PostDto, pathOf: Map<string, string>): Post {
  const formats = d.destinations.filter((x) => x.status !== "cancelado").map((x) => x.format);
  return {
    id: d.id,
    network: d.network,
    formats,
    // midia que nao esta na pasta deste computador aparece como "#id"
    media: d.media_ids.map((id) => pathOf.get(id) ?? `#${id}`),
    caption: d.caption,
    hashtags: d.hashtags,
    scheduledAt: d.scheduled_at,
    status: d.status as PostStatus,
    createdAt: d.created_at,
    updatedAt: d.updated_at,
    publishMode: d.publish_mode,
    version: d.version,
    approvedVersion: d.approved_version,
    failureReason: d.failure_reason,
    publishedAt: d.published_at,
    destinations: d.destinations.map((x) => ({
      format: x.format,
      status: x.status,
      url: x.external_url,
      confirmedAt: x.confirmed_at,
      confirmedBy: x.confirmed_by,
    })),
    approvals: d.approvals.map((a) => ({ version: a.version, decision: a.decision, note: a.note, at: a.decided_at })),
    jobs: d.jobs.map((j) => ({ runAt: j.run_at, status: j.status, attempts: j.attempts, maxAttempts: j.max_attempts, lastError: j.last_error })),
    attempts: d.attempts.map((a) => ({ channel: a.channel, outcome: a.outcome, detail: a.detail, at: a.started_at })),
  };
}

export interface LogEntry {
  at: string;
  action: string;
  detail: string;
}

interface StudioData {
  version: 1;
  items: Record<string, ItemMeta>;
  posts: Post[];
  characters: Character[];
  campaigns: Campaign[];
  log: LogEntry[];
}

// Arquivo na pasta + os dados dele, pronto para a tela.
export interface Item extends ItemMeta {
  path: string;
  kind: "image" | "video";
  size: number;
  modifiedAt: number;
  url: string;
  // Status mostrado: agendado/publicado vem das publicacoes que usam o arquivo.
  display: "rascunho" | "aprovado" | "agendado" | "publicado" | "arquivado";
}

export type FolderState = "loading" | "unsupported" | "none" | "needs-permission" | "ready" | "error";
// Onde ficam os dados (os arquivos ficam sempre no computador).
export type DataSource = "pasta" | "banco";

const LUNA: Character = { id: "luna", name: "Luna Vox", description: "Influenciadora virtual", active: true };
const EMPTY: StudioData = { version: 1, items: {}, posts: [], characters: [LUNA], campaigns: [], log: [] };
const LOG_LIMIT = 500;
const REGISTER_BATCH = 100;

function normalize(raw: unknown): StudioData {
  const d = (raw ?? {}) as Partial<StudioData>;
  return {
    version: 1,
    items: d.items && typeof d.items === "object" ? d.items : {},
    posts: Array.isArray(d.posts) ? d.posts : [],
    characters: Array.isArray(d.characters) && d.characters.length ? d.characters : [LUNA],
    campaigns: Array.isArray(d.campaigns) ? d.campaigns : [],
    log: Array.isArray(d.log) ? d.log : [],
  };
}

function titleFrom(path: string) {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.[^.]+$/, "");
}

function fromRow(r: MediaRow): ItemMeta {
  return {
    id: r.id,
    title: r.title,
    status: r.status,
    addedAt: r.created_at,
    description: r.description ?? "",
    characterId: r.character_id,
    category: r.category ?? "",
    campaignId: r.campaign_id,
    caption: r.caption ?? "",
    hashtags: r.hashtags ?? [],
    destinations: (r.allowed_destinations ?? []) as Network[],
    rating: r.content_rating,
    audience: r.audience,
  };
}

// Mesma regra do banco (constraint media_adult_only_telegram), conferida
// antes de salvar tambem no modo pasta.
export function metaProblem(meta: Pick<ItemMeta, "rating" | "destinations" | "title">): string | null {
  if (!meta.title?.trim()) return "Preencha o título.";
  if (meta.rating === "adulto" && meta.destinations?.some((d) => d === "instagram" || d === "tiktok")) {
    return "Conteúdo adulto só pode ter o Telegram como destino (Instagram e TikTok proíbem).";
  }
  return null;
}

interface Studio {
  state: FolderState;
  source: DataSource;
  error: string | null;
  folderName: string | null;
  items: Item[];
  posts: Post[];
  characters: Character[];
  campaigns: Campaign[];
  log: LogEntry[];
  totalBytes: number;
  connect: () => Promise<void>;
  reconnect: () => Promise<void>;
  forget: () => Promise<void>;
  refresh: () => Promise<void>;
  addFiles: (files: File[]) => Promise<{ added: number; skipped: string[] }>;
  updateItem: (path: string, patch: MetaPatch) => Promise<void>;
  savePost: (post: Post) => Promise<void>;
  deletePost: (id: string) => Promise<void>;
  saveCharacter: (c: Omit<Character, "id"> & { id?: string }) => Promise<void>;
  saveCampaign: (c: Omit<Campaign, "id"> & { id?: string }) => Promise<void>;
  // Fase 2 (modo banco)
  worker: WorkerStatus | null;
  postsError: string | null;
  reloadPosts: () => Promise<void>;
  instagram: InstagramStatus | null;
  instagramAction: (action: "connect" | "disconnect", token?: string) => Promise<void>;
  tiktok: TikTokStatus | null;
  reloadTiktok: () => Promise<void>;
  disconnectTiktok: () => Promise<void>;
  // modo automatico: envia a copia temporaria das midias da versao e aprova
  approveAutomatic: (post: Post, onStep?: (text: string) => void) => Promise<void>;
  saveCloudPost: (input: PostInput, current?: Post | null) => Promise<{ post: Post; needsApproval: boolean }>;
  postAction: (action: PostAction, id: string, extra?: object) => Promise<Post>;
  runJobs: () => Promise<{ processadas: number; ok: number; falhas: number; novas_tentativas: number }>;
}

const StudioContext = createContext<Studio | null>(null);

export function useStudio(): Studio {
  const ctx = useContext(StudioContext);
  if (!ctx) throw new Error("useStudio fora do StudioProvider");
  return ctx;
}

function localId() {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function StudioProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<FolderState>("loading");
  const [source, setSource] = useState<DataSource>("pasta");
  const [error, setError] = useState<string | null>(null);
  const [root, setRoot] = useState<DirHandle | null>(null);
  const [data, setData] = useState<StudioData>(EMPTY);
  const [files, setFiles] = useState<{ path: string; file: File; url: string }[]>([]);
  const dataRef = useRef<StudioData>(EMPTY);
  const sourceRef = useRef<DataSource>("pasta");
  const [remotePosts, setRemotePosts] = useState<Post[] | null>(null);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [postsError, setPostsError] = useState<string | null>(null);
  const [instagram, setInstagram] = useState<InstagramStatus | null>(null);
  const [tiktok, setTiktok] = useState<TikTokStatus | null>(null);
  const writeChain = useRef<Promise<void>>(Promise.resolve());
  // Libera as pre-visualizacoes so ao sair (trocar de pasta ja libera em load/forget).
  const filesRef = useRef(files);
  filesRef.current = files;
  useEffect(() => () => filesRef.current.forEach((f) => URL.revokeObjectURL(f.url)), []);

  function apply(next: StudioData) {
    dataRef.current = next;
    setData(next);
  }

  // Cadastra no banco os arquivos da pasta que ele ainda nao conhece, levando
  // titulo/status/data que estavam no luna-vox.json (importacao).
  async function registerMissing(found: { path: string; file: File }[], local: Record<string, ItemMeta>, known: Set<string>) {
    const missing = found.filter((f) => !known.has(f.path));
    const inserted: MediaRow[] = [];
    for (let i = 0; i < missing.length; i += REGISTER_BATCH) {
      const batch = await Promise.all(
        missing.slice(i, i + REGISTER_BATCH).map(async (f) => {
          const type = mediaTypeOf(f.path)!;
          const old = local[f.path];
          return {
            local_path: f.path,
            sha256: await fileSha256(f.file),
            kind: type.kind,
            mime_type: f.file.type || type.mime,
            size_bytes: f.file.size,
            file_modified_at: new Date(f.file.lastModified).toISOString(),
            title: old?.title || titleFrom(f.path),
            status: old?.status ?? "rascunho",
            created_at: old?.addedAt ?? new Date(f.file.lastModified).toISOString(),
          };
        })
      );
      inserted.push(...(await cloudRegister(batch)));
    }
    return inserted;
  }

  const load = useCallback(async (handle: DirHandle) => {
    try {
      const [raw, found] = await Promise.all([readData(handle), scanMedia(handle)]);
      const next = normalize(raw);
      const cloud = await cloudLoad();
      if (cloud) {
        // Modo banco: os dados dos arquivos vem do Supabase; as publicacoes
        // continuam no json (a Fase 2 leva para o banco).
        // Importacao do que foi criado no modo pasta: personagens e campanhas
        // (pelo nome) e, depois do cadastro, os campos de cada arquivo.
        const ids = new Map<string, string | null>();
        for (const c of next.characters) {
          let row = cloud.characters.find((x) => x.name.toLowerCase() === c.name.toLowerCase());
          if (!row) cloud.characters.push((row = await cloudSaveCharacter({ name: c.name, description: c.description, active: c.active })));
          ids.set(c.id, row.id);
        }
        for (const c of next.campaigns) {
          let row = cloud.campaigns.find((x) => x.name.toLowerCase() === c.name.toLowerCase());
          if (!row) cloud.campaigns.push((row = await cloudSaveCampaign({ ...c, id: undefined })));
          ids.set(c.id, row.id);
        }
        const known = new Set(cloud.media.map((m) => m.local_path));
        const inserted = await registerMissing(found, next.items, known);
        for (const [i, row] of inserted.entries()) {
          const old = next.items[row.local_path];
          if (!old) continue;
          const patch: MetaPatch = {};
          if (old.description) patch.description = old.description;
          if (old.characterId && ids.get(old.characterId)) patch.characterId = ids.get(old.characterId);
          if (old.category) patch.category = old.category;
          if (old.campaignId && ids.get(old.campaignId)) patch.campaignId = ids.get(old.campaignId);
          if (old.caption) patch.caption = old.caption;
          if (old.hashtags?.length) patch.hashtags = old.hashtags;
          if (old.destinations?.length) patch.destinations = old.destinations;
          if (old.rating && old.rating !== "livre") patch.rating = old.rating;
          if (old.audience && old.audience !== "promocional") patch.audience = old.audience;
          if (Object.keys(patch).length) inserted[i] = await cloudUpdateMedia(row.id, patch);
        }
        const items: Record<string, ItemMeta> = {};
        for (const row of [...cloud.media, ...inserted]) items[row.local_path] = fromRow(row);
        next.items = items;
        next.characters = cloud.characters.map((c) => ({ id: c.id, name: c.name, description: c.description, active: c.active }));
        next.campaigns = cloud.campaigns.map((c) => ({
          id: c.id,
          name: c.name,
          description: c.description,
          startsOn: c.starts_on,
          endsOn: c.ends_on,
          status: c.status,
        }));
        // Publicacoes antigas do json entram no banco uma vez (legacy_id).
        const pathOf = new Map(Object.entries(items).map(([path, meta]) => [meta.id!, path]));
        // Publicacoes falhando (ex.: migracao 0002 ainda nao rodada) nao
        // derrubam o resto do estudio: a biblioteca segue funcionando.
        let remote: Awaited<ReturnType<typeof cloudPosts>> = { posts: [], worker: null };
        let postsProblem: string | null = null;
        try {
          remote = await cloudPosts();
        } catch (e) {
          postsProblem = `Publicações indisponíveis no banco (${e instanceof Error ? e.message : e}). Confira se a migração 0002_publicacoes.sql foi rodada no Supabase.`;
        }
        setPostsError(postsProblem);
        setInstagram(await cloudInstagram().catch(() => null));
        setTiktok(await cloudTiktok().catch(() => null));
        const imported = new Set(remote.posts.map((p) => p.legacy_id).filter(Boolean));
        const legacy = next.posts
          .filter((p) => !imported.has(p.id))
          .map((p) => ({
            legacy_id: p.id,
            network: p.network,
            formats: p.formats,
            media_ids: p.media.map((m) => items[m]?.id).filter(Boolean),
            caption: p.caption,
            hashtags: p.hashtags,
            scheduled_at: p.scheduledAt,
            status: p.status,
          }));
        if (!postsProblem && legacy.length && (await cloudPostImport(legacy))) remote = await cloudPosts();
        setRemotePosts(remote.posts.map((d) => fromDto(d, pathOf)));
        setWorker(remote.worker);
        sourceRef.current = "banco";
        setSource("banco");
      } else {
        setRemotePosts(null);
        // Modo pasta: arquivo novo (salvo direto pelo Explorer) entra como rascunho.
        for (const f of found) {
          if (!next.items[f.path]) {
            next.items[f.path] = { title: titleFrom(f.path), status: "rascunho", addedAt: new Date(f.file.lastModified).toISOString() };
          }
        }
        sourceRef.current = "pasta";
        setSource("pasta");
      }
      apply(next);
      filesRef.current.forEach((f) => URL.revokeObjectURL(f.url));
      setFiles(found.map((f) => ({ path: f.path, file: f.file, url: URL.createObjectURL(f.file) })));
      setRoot(handle);
      setError(null);
      setState("ready");
      // No modo banco o json vira uma copia local dos dados do Supabase (se o
      // banco sair do ar ou for desligado, o modo pasta continua com tudo).
      await writeData(handle, next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }, []);

  useEffect(() => {
    if (!folderSupported()) {
      setState("unsupported");
      return;
    }
    rememberedFolder().then(async (handle) => {
      if (!handle) return setState("none");
      setRoot(handle);
      const perm = await permissionOf(handle, false).catch(() => "prompt" as PermissionState);
      if (perm === "granted") await load(handle);
      else setState("needs-permission");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Grava o json da pasta, uma gravacao de cada vez (duas ao mesmo tempo
  // poderiam se atropelar).
  const persist = useCallback(() => {
    const handle = root;
    const run = writeChain.current.then(async () => {
      if (!handle) return;
      try {
        await writeData(handle, dataRef.current);
      } catch (e) {
        setError(`Não consegui salvar na pasta: ${e instanceof Error ? e.message : e}`);
      }
    });
    writeChain.current = run;
    return run;
  }, [root]);

  const commit = useCallback(
    (change: (d: StudioData) => StudioData, log?: Omit<LogEntry, "at">) => {
      let next = change(dataRef.current);
      if (log) next = { ...next, log: [...next.log, { at: new Date().toISOString(), ...log }].slice(-LOG_LIMIT) };
      apply(next);
      return persist();
    },
    [persist]
  );

  const connect = useCallback(async () => {
    try {
      const handle = await pickFolder();
      setState("loading");
      await load(handle);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return; // fechou a janela
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }, [load]);

  const reconnect = useCallback(async () => {
    if (!root) return connect();
    const perm = await permissionOf(root, true).catch(() => "denied" as PermissionState);
    if (perm === "granted") {
      setState("loading");
      await load(root);
    } else {
      setError("O navegador não liberou a pasta. Clique em Reconectar e escolha Permitir.");
    }
  }, [root, connect, load]);

  const forget = useCallback(async () => {
    await rememberFolder(null);
    filesRef.current.forEach((f) => URL.revokeObjectURL(f.url));
    setFiles([]);
    apply(EMPTY);
    setRoot(null);
    setState("none");
  }, []);

  const refresh = useCallback(async () => {
    if (root) await load(root);
  }, [root, load]);

  const addFiles = useCallback(
    async (list: File[]) => {
      if (!root) return { added: 0, skipped: list.map((f) => f.name) };
      const skipped: string[] = [];
      const added: { path: string; file: File }[] = [];
      for (const file of list) {
        if (!mediaTypeOf(file.name)) {
          skipped.push(file.name);
          continue;
        }
        const path = await copyIntoFolder(root, file);
        added.push({ path, file });
      }
      if (added.length) {
        const fresh = added.map((a) => ({ path: a.path, file: a.file, url: URL.createObjectURL(a.file) }));
        setFiles((old) => [...old, ...fresh]);
        let metas: Record<string, ItemMeta>;
        if (sourceRef.current === "banco") {
          const rows = await registerMissing(added, {}, new Set());
          metas = Object.fromEntries(rows.map((r) => [r.local_path, fromRow(r)]));
        } else {
          metas = Object.fromEntries(
            added.map((a) => [a.path, { title: titleFrom(a.path), status: "rascunho" as const, addedAt: new Date().toISOString() }])
          );
        }
        await commit((d) => ({ ...d, items: { ...d.items, ...metas } }), {
          action: "adicionar",
          detail: added.map((a) => a.path).join(", "),
        });
      }
      return { added: added.length, skipped };
    },
    [root, commit]
  );

  const updateItem = useCallback(
    async (path: string, patch: MetaPatch) => {
      const current = dataRef.current.items[path];
      if (!current) return;
      const merged = { ...current, ...patch };
      const problem = metaProblem(merged);
      if (problem) throw new Error(problem);
      if (sourceRef.current === "banco") {
        if (!current.id) throw new Error("Arquivo ainda não cadastrado no banco. Clique em Atualizar.");
        const row = await cloudUpdateMedia(current.id, patch);
        await commit((d) => ({ ...d, items: { ...d.items, [path]: fromRow(row) } }));
        return;
      }
      await commit((d) => ({ ...d, items: { ...d.items, [path]: merged } }), {
        action: patch.status ? `status:${patch.status}` : "editar",
        detail: path,
      });
    },
    [commit]
  );

  const savePost = useCallback(
    async (post: Post) => {
      const before = dataRef.current.posts.find((p) => p.id === post.id);
      await commit(
        (d) => {
          const saved = { ...post, updatedAt: new Date().toISOString() };
          return { ...d, posts: before ? d.posts.map((p) => (p.id === post.id ? saved : p)) : [...d.posts, saved] };
        },
        { action: `publicacao:${post.status}`, detail: `${post.network} ${post.scheduledAt ?? ""}`.trim() }
      );
      // Historico de utilizacao: registra no banco quando a publicacao e marcada como feita.
      if (sourceRef.current === "banco" && post.status === "publicado" && before?.status !== "publicado") {
        const ids = post.media.map((m) => dataRef.current.items[m]?.id).filter((id): id is string => Boolean(id));
        if (ids.length) await cloudUsage(ids, post.network, "post", post.id).catch((e) => setError(String(e)));
      }
    },
    [commit]
  );

  const deletePost = useCallback(
    (id: string) =>
      commit((d) => ({ ...d, posts: d.posts.filter((p) => p.id !== id) }), { action: "publicacao:excluir", detail: id }),
    [commit]
  );

  const saveCharacter = useCallback(
    async (c: Omit<Character, "id"> & { id?: string }) => {
      if (!c.name.trim()) throw new Error("Preencha o nome.");
      if (sourceRef.current === "banco") {
        const row = await cloudSaveCharacter(c);
        const saved: Character = { id: row.id, name: row.name, description: row.description, active: row.active };
        await commit((d) => ({
          ...d,
          characters: d.characters.some((x) => x.id === saved.id) ? d.characters.map((x) => (x.id === saved.id ? saved : x)) : [...d.characters, saved],
        }));
        return;
      }
      if (dataRef.current.characters.some((x) => x.id !== c.id && x.name.toLowerCase() === c.name.trim().toLowerCase())) {
        throw new Error("Já existe uma personagem com esse nome.");
      }
      const saved: Character = { ...c, id: c.id ?? localId(), name: c.name.trim() };
      await commit(
        (d) => ({
          ...d,
          characters: c.id ? d.characters.map((x) => (x.id === c.id ? saved : x)) : [...d.characters, saved],
        }),
        { action: c.id ? "personagem:editar" : "personagem:criar", detail: saved.name }
      );
    },
    [commit]
  );

  const saveCampaign = useCallback(
    async (c: Omit<Campaign, "id"> & { id?: string }) => {
      if (!c.name.trim()) throw new Error("Preencha o nome.");
      if (c.startsOn && c.endsOn && c.endsOn < c.startsOn) throw new Error("A data de fim vem antes do início.");
      if (sourceRef.current === "banco") {
        const row = await cloudSaveCampaign(c);
        const saved: Campaign = {
          id: row.id,
          name: row.name,
          description: row.description,
          startsOn: row.starts_on,
          endsOn: row.ends_on,
          status: row.status,
        };
        await commit((d) => ({
          ...d,
          campaigns: d.campaigns.some((x) => x.id === saved.id) ? d.campaigns.map((x) => (x.id === saved.id ? saved : x)) : [saved, ...d.campaigns],
        }));
        return;
      }
      if (dataRef.current.campaigns.some((x) => x.id !== c.id && x.name.toLowerCase() === c.name.trim().toLowerCase())) {
        throw new Error("Já existe uma campanha com esse nome.");
      }
      const saved: Campaign = { ...c, id: c.id ?? localId(), name: c.name.trim() };
      await commit(
        (d) => ({
          ...d,
          campaigns: c.id ? d.campaigns.map((x) => (x.id === c.id ? saved : x)) : [saved, ...d.campaigns],
        }),
        { action: c.id ? "campanha:editar" : "campanha:criar", detail: saved.name }
      );
    },
    [commit]
  );

  const pathOfId = useCallback(
    () => new Map(Object.entries(dataRef.current.items).map(([path, meta]) => [meta.id ?? "", path])),
    []
  );

  const reloadPosts = useCallback(async () => {
    if (sourceRef.current !== "banco") return;
    const remote = await cloudPosts();
    setRemotePosts(remote.posts.map((d) => fromDto(d, pathOfId())));
    setWorker(remote.worker);
    setPostsError(null);
  }, [pathOfId]);

  const saveCloudPost = useCallback(
    async (input: PostInput, current?: Post | null) => {
      const ids = input.media.map((m) => dataRef.current.items[m]?.id ?? (m.startsWith("#") ? m.slice(1) : ""));
      if (ids.some((id) => !id)) throw new Error("Uma das mídias ainda não está no banco. Clique em Atualizar em Conteúdos.");
      const res = await cloudPostSave({
        id: current?.id,
        version: current?.version,
        network: input.network,
        formats: input.formats,
        media_ids: ids,
        caption: input.caption,
        hashtags: input.hashtags,
        scheduled_at: input.scheduledAt,
        publish_mode: input.publishMode ?? "manual",
      });
      await reloadPosts();
      return { post: fromDto({ ...res.post, media_ids: ids, destinations: [], approvals: [], jobs: [], attempts: [] }, pathOfId()), needsApproval: Boolean(res.needsApproval) };
    },
    [reloadPosts, pathOfId]
  );

  const postAction = useCallback(
    async (action: PostAction, id: string, extra: object = {}) => {
      const res = await cloudPostAction(action, id, extra);
      await reloadPosts();
      return fromDto({ ...res.post, media_ids: [], destinations: [], approvals: [], jobs: [], attempts: [] }, pathOfId());
    },
    [reloadPosts, pathOfId]
  );

  const instagramAction = useCallback(async (action: "connect" | "disconnect", token?: string) => {
    setInstagram(await cloudInstagramAction(action, token));
  }, []);

  const reloadTiktok = useCallback(async () => {
    setTiktok(await cloudTiktok().catch(() => null));
  }, []);

  const disconnectTiktok = useCallback(async () => {
    await cloudTiktokDisconnect();
    setTiktok(await cloudTiktok().catch(() => null));
  }, []);

  const approveAutomatic = useCallback(
    async (post: Post, onStep?: (text: string) => void) => {
      if (!post.version) throw new Error("Publicação sem versão.");
      const files = post.media.map((path) => filesRef.current.find((f) => f.path === path)?.file);
      if (files.some((f) => !f)) throw new Error("Uma das mídias não está na pasta deste computador. Abra a pasta do estúdio e tente de novo.");
      onStep?.(post.network === "tiktok" ? "Preparando o vídeo para o TikTok..." : "Preparando as mídias para o Instagram...");
      const prepared = [];
      for (const file of files) prepared.push(await prepareForInstagram(file!));
      const urls = await cloudUploadUrls(
        post.id,
        post.version,
        prepared.map((p, position) => ({ position, mime_type: p.mime, size_bytes: p.blob.size }))
      );
      for (const [i, u] of urls.entries()) {
        onStep?.(`Enviando mídia ${i + 1} de ${urls.length}...`);
        await putSignedUpload(u.url, prepared[u.position].blob);
      }
      onStep?.("Aprovando...");
      await cloudPostAction("approve", post.id, { version: post.version });
      await reloadPosts();
    },
    [reloadPosts]
  );

  const runJobs = useCallback(async () => {
    const result = await cloudRunJobs();
    await reloadPosts();
    return result;
  }, [reloadPosts]);

  const posts = remotePosts ?? data.posts;

  const items = useMemo<Item[]>(() => {
    const usage = new Map<string, "agendado" | "publicado">();
    for (const p of posts) {
      for (const m of p.media) {
        const cur = usage.get(m);
        if (p.status === "publicado") usage.set(m, "publicado");
        else if ((p.status === "agendado" || p.status === "publicando") && cur !== "publicado") usage.set(m, "agendado");
      }
    }
    return files
      .map((f) => {
        const meta = data.items[f.path] ?? { title: titleFrom(f.path), status: "rascunho" as const, addedAt: new Date().toISOString() };
        const used = usage.get(f.path);
        const display: Item["display"] =
          meta.status === "arquivado" ? "arquivado" : used === "publicado" || used === "agendado" ? used : meta.status;
        return {
          ...meta,
          path: f.path,
          kind: mediaTypeOf(f.path)!.kind,
          size: f.file.size,
          modifiedAt: f.file.lastModified,
          url: f.url,
          display,
        };
      })
      .sort((a, b) => (a.addedAt < b.addedAt ? 1 : -1));
  }, [files, data, posts]);

  const value: Studio = {
    state,
    source,
    error,
    folderName: root?.name ?? null,
    items,
    posts,
    characters: data.characters,
    campaigns: data.campaigns,
    log: data.log,
    totalBytes: files.reduce((s, f) => s + f.file.size, 0),
    connect,
    reconnect,
    forget,
    refresh,
    addFiles,
    updateItem,
    savePost,
    deletePost,
    saveCharacter,
    saveCampaign,
    worker,
    postsError,
    reloadPosts,
    instagram,
    instagramAction,
    tiktok,
    reloadTiktok,
    disconnectTiktok,
    approveAutomatic,
    saveCloudPost,
    postAction,
    runJobs,
  };
  return <StudioContext.Provider value={value}>{children}</StudioContext.Provider>;
}

export function newPostId(): string {
  return localId();
}
