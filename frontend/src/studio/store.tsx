import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  copyIntoFolder,
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

export type ItemStatus = "rascunho" | "aprovado" | "arquivado";
export type Network = "instagram" | "tiktok" | "telegram";
export type PostStatus = "rascunho" | "agendado" | "publicado";

// O que fica gravado no luna-vox.json por arquivo (chave = caminho em conteudos/).
export interface ItemMeta {
  title: string;
  status: ItemStatus;
  addedAt: string;
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
}

interface LogEntry {
  at: string;
  action: string;
  detail: string;
}

interface StudioData {
  version: 1;
  items: Record<string, ItemMeta>;
  posts: Post[];
  log: LogEntry[];
}

// Arquivo na pasta + o que esta no json, pronto para a tela.
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

const EMPTY: StudioData = { version: 1, items: {}, posts: [], log: [] };
const LOG_LIMIT = 500;

function normalize(raw: unknown): StudioData {
  const d = (raw ?? {}) as Partial<StudioData>;
  return {
    version: 1,
    items: d.items && typeof d.items === "object" ? d.items : {},
    posts: Array.isArray(d.posts) ? d.posts : [],
    log: Array.isArray(d.log) ? d.log : [],
  };
}

function titleFrom(path: string) {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.[^.]+$/, "");
}

interface Studio {
  state: FolderState;
  error: string | null;
  folderName: string | null;
  items: Item[];
  posts: Post[];
  totalBytes: number;
  connect: () => Promise<void>;
  reconnect: () => Promise<void>;
  forget: () => Promise<void>;
  refresh: () => Promise<void>;
  addFiles: (files: File[]) => Promise<{ added: number; skipped: string[] }>;
  updateItem: (path: string, patch: Partial<Pick<ItemMeta, "title" | "status">>) => Promise<void>;
  savePost: (post: Post) => Promise<void>;
  deletePost: (id: string) => Promise<void>;
}

const StudioContext = createContext<Studio | null>(null);

export function useStudio(): Studio {
  const ctx = useContext(StudioContext);
  if (!ctx) throw new Error("useStudio fora do StudioProvider");
  return ctx;
}

export function StudioProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<FolderState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [root, setRoot] = useState<DirHandle | null>(null);
  const [data, setData] = useState<StudioData>(EMPTY);
  const [files, setFiles] = useState<{ path: string; file: File; url: string }[]>([]);
  const dataRef = useRef<StudioData>(EMPTY);
  const writeChain = useRef<Promise<void>>(Promise.resolve());
  // Libera as pre-visualizacoes so ao sair (trocar de pasta ja libera em load/forget).
  const filesRef = useRef(files);
  filesRef.current = files;
  useEffect(() => () => filesRef.current.forEach((f) => URL.revokeObjectURL(f.url)), []);

  const load = useCallback(async (handle: DirHandle) => {
    try {
      const [raw, found] = await Promise.all([readData(handle), scanMedia(handle)]);
      const next = normalize(raw);
      // Arquivo novo na pasta (salvo direto pelo Explorer) entra como rascunho.
      let changed = raw === null;
      for (const f of found) {
        if (!next.items[f.path]) {
          next.items[f.path] = { title: titleFrom(f.path), status: "rascunho", addedAt: new Date(f.file.lastModified).toISOString() };
          changed = true;
        }
      }
      dataRef.current = next;
      setData(next);
      filesRef.current.forEach((f) => URL.revokeObjectURL(f.url));
      setFiles(found.map((f) => ({ path: f.path, file: f.file, url: URL.createObjectURL(f.file) })));
      setRoot(handle);
      setError(null);
      setState("ready");
      if (changed) await writeData(handle, next);
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


  // Toda mudanca passa por aqui: atualiza a tela e grava o json, uma
  // gravacao de cada vez (duas ao mesmo tempo poderiam se atropelar).
  const commit = useCallback(
    (change: (d: StudioData) => StudioData, log?: Omit<LogEntry, "at">) => {
      let next = change(dataRef.current);
      if (log) next = { ...next, log: [...next.log, { at: new Date().toISOString(), ...log }].slice(-LOG_LIMIT) };
      dataRef.current = next;
      setData(next);
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
    },
    [root]
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
    dataRef.current = EMPTY;
    setData(EMPTY);
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
        await commit(
          (d) => {
            const items = { ...d.items };
            for (const a of added) items[a.path] = { title: titleFrom(a.path), status: "rascunho", addedAt: new Date().toISOString() };
            return { ...d, items };
          },
          { action: "adicionar", detail: added.map((a) => a.path).join(", ") }
        );
      }
      return { added: added.length, skipped };
    },
    [root, commit]
  );

  const updateItem = useCallback(
    (path: string, patch: Partial<Pick<ItemMeta, "title" | "status">>) =>
      commit(
        (d) => ({ ...d, items: { ...d.items, [path]: { ...d.items[path], ...patch } } }),
        { action: patch.status ? `status:${patch.status}` : "editar", detail: path }
      ),
    [commit]
  );

  const savePost = useCallback(
    (post: Post) =>
      commit(
        (d) => {
          const exists = d.posts.some((p) => p.id === post.id);
          const saved = { ...post, updatedAt: new Date().toISOString() };
          return { ...d, posts: exists ? d.posts.map((p) => (p.id === post.id ? saved : p)) : [...d.posts, saved] };
        },
        { action: `publicacao:${post.status}`, detail: `${post.network} ${post.scheduledAt ?? ""}`.trim() }
      ),
    [commit]
  );

  const deletePost = useCallback(
    (id: string) =>
      commit((d) => ({ ...d, posts: d.posts.filter((p) => p.id !== id) }), { action: "publicacao:excluir", detail: id }),
    [commit]
  );

  const items = useMemo<Item[]>(() => {
    const usage = new Map<string, PostStatus>();
    for (const p of data.posts) {
      for (const m of p.media) {
        const cur = usage.get(m);
        if (p.status === "publicado" || (p.status === "agendado" && cur !== "publicado")) usage.set(m, p.status);
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
  }, [files, data]);

  const value: Studio = {
    state,
    error,
    folderName: root?.name ?? null,
    items,
    posts: data.posts,
    totalBytes: files.reduce((s, f) => s + f.file.size, 0),
    connect,
    reconnect,
    forget,
    refresh,
    addFiles,
    updateItem,
    savePost,
    deletePost,
  };
  return <StudioContext.Provider value={value}>{children}</StudioContext.Provider>;
}

export function newPostId(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
