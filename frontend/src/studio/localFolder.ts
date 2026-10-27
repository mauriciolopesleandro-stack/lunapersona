// Pasta do estudio no computador do usuario (File System Access API, Chrome
// e Edge no computador). Nada sai do computador: o site so le e grava nesta
// pasta, com a permissao que o navegador pede na hora.
//
//   <pasta escolhida>/
//     conteudos/            imagens e videos (o app copia para AAAA-MM/; o
//                           usuario tambem pode salvar arquivos aqui direto)
//     luna-vox.json         status, titulos, publicacoes e historico
//     luna-vox.backup.json  copia do json antes da primeira gravacao de cada sessao

// Tipos que o TypeScript ainda nao traz para essa API.
interface PermissionDescriptor {
  mode: "read" | "readwrite";
}
type DirHandle = FileSystemDirectoryHandle & {
  queryPermission(d: PermissionDescriptor): Promise<PermissionState>;
  requestPermission(d: PermissionDescriptor): Promise<PermissionState>;
  values(): AsyncIterable<FileSystemDirectoryHandle | FileSystemFileHandle>;
};
declare global {
  interface Window {
    showDirectoryPicker?: (options?: { id?: string; mode?: "read" | "readwrite" }) => Promise<DirHandle>;
  }
}

export type { DirHandle };

export const CONTENT_DIR = "conteudos";
const DATA_FILE = "luna-vox.json";
const BACKUP_FILE = "luna-vox.backup.json";
const MAX_DEPTH = 4;

export const MEDIA_TYPES: Record<string, { kind: "image" | "video"; mime: string }> = {
  jpg: { kind: "image", mime: "image/jpeg" },
  jpeg: { kind: "image", mime: "image/jpeg" },
  png: { kind: "image", mime: "image/png" },
  webp: { kind: "image", mime: "image/webp" },
  gif: { kind: "image", mime: "image/gif" },
  mp4: { kind: "video", mime: "video/mp4" },
  mov: { kind: "video", mime: "video/quicktime" },
  webm: { kind: "video", mime: "video/webm" },
  m4v: { kind: "video", mime: "video/mp4" },
};

export function mediaTypeOf(name: string) {
  return MEDIA_TYPES[name.split(".").pop()?.toLowerCase() ?? ""] ?? null;
}

export function folderSupported(): boolean {
  return typeof window !== "undefined" && typeof window.showDirectoryPicker === "function";
}

// --- Lembrar a pasta entre visitas (IndexedDB guarda o "handle") ----------

const DB_NAME = "luna-vox-studio";
const STORE = "handles";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function rememberFolder(handle: DirHandle | null): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      if (handle) tx.objectStore(STORE).put(handle, "root");
      else tx.objectStore(STORE).delete("root");
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    // sem IndexedDB (aba anonima): a pasta e pedida de novo na proxima visita
  }
}

export async function rememberedFolder(): Promise<DirHandle | null> {
  try {
    const db = await openDb();
    return await new Promise((resolve) => {
      const req = db.transaction(STORE).objectStore(STORE).get("root");
      req.onsuccess = () => resolve((req.result as DirHandle) ?? null);
      req.onerror = () => resolve(null);
    });
  } catch {
    return null;
  }
}

export async function pickFolder(): Promise<DirHandle> {
  const handle = await window.showDirectoryPicker!({ id: "luna-vox", mode: "readwrite" });
  await rememberFolder(handle);
  return handle;
}

// "granted" ja pode usar; "prompt" precisa de um clique do usuario (o
// navegador so pergunta dentro de um clique).
export async function permissionOf(handle: DirHandle, ask: boolean): Promise<PermissionState> {
  const mode = { mode: "readwrite" as const };
  const state = await handle.queryPermission(mode);
  if (state === "granted" || !ask) return state;
  return handle.requestPermission(mode);
}

// --- Arquivos de midia --------------------------------------------------------

export interface FoundFile {
  path: string; // relativo a conteudos/, com "/"
  handle: FileSystemFileHandle;
  file: File;
}

export async function scanMedia(root: DirHandle): Promise<FoundFile[]> {
  const dir = (await root.getDirectoryHandle(CONTENT_DIR, { create: true })) as DirHandle;
  const out: FoundFile[] = [];
  async function walk(d: DirHandle, prefix: string, depth: number) {
    for await (const entry of d.values()) {
      if (entry.kind === "directory") {
        if (depth < MAX_DEPTH && !entry.name.startsWith(".")) {
          await walk(entry as DirHandle, `${prefix}${entry.name}/`, depth + 1);
        }
      } else if (mediaTypeOf(entry.name) && !entry.name.startsWith(".")) {
        const handle = entry as FileSystemFileHandle;
        out.push({ path: `${prefix}${entry.name}`, handle, file: await handle.getFile() });
      }
    }
  }
  await walk(dir, "", 0);
  return out;
}

// Impressao digital do arquivo (acha repetidos e reconhece o arquivo no
// banco). Arquivo muito grande pula: ler tudo na memoria pesa.
export async function fileSha256(file: File): Promise<string | null> {
  if (file.size > 200 * 1024 * 1024 || !crypto?.subtle) return null;
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function subdirOf(root: DirHandle, rel: string): Promise<FileSystemDirectoryHandle> {
  let dir = await root.getDirectoryHandle(CONTENT_DIR, { create: true });
  for (const part of rel.split("/").filter(Boolean)) dir = await dir.getDirectoryHandle(part, { create: true });
  return dir;
}

// Copia um arquivo escolhido para conteudos/AAAA-MM/ (ou para a subpasta dada,
// ex. "vendas/ensaio"), sem sobrescrever nada: se o nome ja existe, ganha
// " (2)", " (3)"...
export async function copyIntoFolder(root: DirHandle, file: File, subdir?: string): Promise<string> {
  const now = new Date();
  const month = subdir ?? `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const dir = await subdirOf(root, month);
  const clean = file.name.replace(/[\\/:*?"<>|]/g, "_");
  const dot = clean.lastIndexOf(".");
  const base = dot > 0 ? clean.slice(0, dot) : clean;
  const ext = dot > 0 ? clean.slice(dot) : "";
  let name = clean;
  for (let n = 2; await exists(dir, name); n++) name = `${base} (${n})${ext}`;
  const handle = await dir.getFileHandle(name, { create: true });
  const writable = await handle.createWritable();
  await writable.write(file);
  await writable.close();
  return `${month}/${name}`;
}

// Move um arquivo de conteudos/ para outra subpasta (copia e so depois tira o
// original): nada e apagado de vez. Devolve o caminho novo.
export async function moveWithinContent(root: DirHandle, path: string, toSubdir: string): Promise<string> {
  const parts = path.split("/");
  const name = parts.pop()!;
  const from = await subdirOf(root, parts.join("/"));
  const file = await (await from.getFileHandle(name)).getFile();
  const moved = await copyIntoFolder(root, file, toSubdir);
  await from.removeEntry(name);
  return moved;
}

async function exists(dir: FileSystemDirectoryHandle, name: string): Promise<boolean> {
  try {
    await dir.getFileHandle(name);
    return true;
  } catch {
    return false;
  }
}

// --- Dados (json) ---------------------------------------------------------------

export async function readData(root: DirHandle): Promise<unknown | null> {
  try {
    const handle = await root.getFileHandle(DATA_FILE);
    const text = await (await handle.getFile()).text();
    return text.trim() ? JSON.parse(text) : null;
  } catch (e) {
    if (e instanceof DOMException && e.name === "NotFoundError") return null;
    throw new Error(`Não consegui ler ${DATA_FILE}: ${e instanceof Error ? e.message : e}`);
  }
}

async function writeText(root: DirHandle, name: string, text: string) {
  const handle = await root.getFileHandle(name, { create: true });
  const writable = await handle.createWritable();
  await writable.write(text);
  await writable.close(); // o navegador so troca o arquivo aqui (grava inteiro ou nada)
}

const backedUp = new WeakSet<object>();

export async function writeData(root: DirHandle, data: unknown): Promise<void> {
  if (!backedUp.has(root)) {
    try {
      const old = await (await root.getFileHandle(DATA_FILE)).getFile();
      await writeText(root, BACKUP_FILE, await old.text());
    } catch {
      // primeira vez: ainda nao ha o que copiar
    }
    backedUp.add(root);
  }
  await writeText(root, DATA_FILE, JSON.stringify(data, null, 1));
}
