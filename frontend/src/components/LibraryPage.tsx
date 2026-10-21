import { useEffect, useRef, useState } from "react";
import {
  MediaDuplicate,
  MediaNotConfigured,
  listMedia,
  mediaAction,
  uploadMedia,
  type MediaItem,
} from "../api/client";

type Filter = "todos" | "imagens" | "videos" | "arquivados";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "todos", label: "Todos" },
  { id: "imagens", label: "Imagens" },
  { id: "videos", label: "Vídeos" },
  { id: "arquivados", label: "Arquivados" },
];

const ACCEPT = "image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm";

interface Upload {
  key: string;
  file: File;
  progress: number;
  error: string | null;
  duplicate: boolean;
  // Erro de envio (rede, servidor): tentar de novo pode resolver. Tipo ou
  // tamanho recusado aqui mesmo: nao adianta.
  retryable: boolean;
  done: boolean;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function query(filter: Filter) {
  if (filter === "arquivados") return { status: "archived" as const };
  if (filter === "imagens") return { kind: "image" as const };
  if (filter === "videos") return { kind: "video" as const };
  return {};
}

export function LibraryPage() {
  const [filter, setFilter] = useState<Filter>("todos");
  const [items, setItems] = useState<MediaItem[]>([]);
  const [more, setMore] = useState(false);
  const [maxBytes, setMaxBytes] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [notConfigured, setNotConfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function load(append = false) {
    setLoading(true);
    setError(null);
    try {
      const before = append ? items[items.length - 1]?.created_at : undefined;
      const list = await listMedia({ ...query(filter), before });
      setItems((prev) => (append ? [...prev, ...list.items] : list.items));
      setMore(list.more);
      setMaxBytes(list.maxBytes);
      setNotConfigured(false);
    } catch (e) {
      if (e instanceof MediaNotConfigured) setNotConfigured(true);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  function patchUpload(key: string, change: Partial<Upload>) {
    setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...change } : u)));
  }

  async function send(upload: Upload, allowDuplicate = false) {
    patchUpload(upload.key, { error: null, duplicate: false, progress: 0 });
    try {
      await uploadMedia(upload.file, (p) => patchUpload(upload.key, { progress: p }), allowDuplicate);
      patchUpload(upload.key, { done: true, progress: 1 });
      return true;
    } catch (e) {
      patchUpload(upload.key, {
        error: e instanceof Error ? e.message : String(e),
        duplicate: e instanceof MediaDuplicate,
        retryable: true,
      });
      return false;
    }
  }

  async function addFiles(files: FileList | File[]) {
    const list = [...files].map((file, i) => {
      let error: string | null = null;
      if (!ACCEPT.split(",").includes(file.type)) error = "Tipo não aceito. Use JPG, PNG, WebP, MP4, MOV ou WebM.";
      else if (maxBytes && file.size > maxBytes) error = `Maior que o limite de ${formatSize(maxBytes)}.`;
      return { key: `${Date.now()}-${i}-${file.name}`, file, progress: 0, error, duplicate: false, retryable: false, done: false };
    });
    setUploads((prev) => [...list, ...prev.filter((u) => !u.done)]);
    // Um de cada vez: no celular, varios videos juntos disputam a conexao.
    let sent = false;
    for (const upload of list) {
      if (!upload.error && (await send(upload))) sent = true;
    }
    if (sent) {
      if (filter === "arquivados") setFilter("todos");
      else load();
    }
  }

  async function retry(upload: Upload, allowDuplicate: boolean) {
    if (await send(upload, allowDuplicate)) load();
  }

  async function toggleArchive(item: MediaItem) {
    setBusyId(item.id);
    try {
      await mediaAction(item.status === "archived" ? "restore" : "archive", item.id);
      setItems((list) => list.filter((i) => i.id !== item.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (notConfigured) {
    return (
      <div>
        <div className="page-header">
          <h1>Conteúdos</h1>
          <p>Biblioteca de imagens e vídeos.</p>
        </div>
        <div className="panel">
          <h2>Falta ligar o armazenamento</h2>
          <p className="library-help">
            A biblioteca guarda os arquivos num projeto Supabase privado. Para ativar: crie o projeto, rode o arquivo
            <code> supabase/migrations/0001_biblioteca.sql</code> no SQL Editor e cadastre na Vercel as variáveis
            <code> SUPABASE_URL</code> e <code>SUPABASE_SECRET_KEY</code> (só de servidor). Depois, faça um novo deploy.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1>Conteúdos</h1>
        <p>Imagens e vídeos gerados fora e enviados para cá. Ficam num armazenamento privado.</p>
      </div>

      <div
        className={`library-drop${dragging ? " dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
        }}
      >
        <p>Arraste imagens e vídeos para cá</p>
        <button type="button" className="primary" onClick={() => inputRef.current?.click()}>
          Escolher arquivos
        </button>
        <span className="library-limit">
          JPG, PNG, WebP, MP4, MOV ou WebM{maxBytes ? ` · até ${formatSize(maxBytes)} cada` : ""}
        </span>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {uploads.length > 0 && (
        <div className="library-uploads">
          {uploads.map((u) => (
            <div key={u.key} className="library-upload">
              <div className="library-upload-row">
                <span className="library-upload-name">{u.file.name}</span>
                <span className="library-upload-state">
                  {u.done ? "Enviado" : u.error ? "Erro" : `${Math.round(u.progress * 100)}%`}
                </span>
              </div>
              {!u.done && !u.error && (
                <div className="library-progress">
                  <div style={{ width: `${Math.round(u.progress * 100)}%` }} />
                </div>
              )}
              {u.error && (
                <div className="library-upload-error">
                  <span className="error small">{u.error}</span>
                  {u.duplicate ? (
                    <button type="button" className="small" onClick={() => retry(u, true)}>
                      Enviar mesmo assim
                    </button>
                  ) : (
                    u.retryable && (
                      <button type="button" className="small" onClick={() => retry(u, false)}>
                        Tentar de novo
                      </button>
                    )
                  )}
                </div>
              )}
            </div>
          ))}
          {uploads.some((u) => u.done || u.error) && (
            <button
              type="button"
              className="small"
              onClick={() => setUploads((list) => list.filter((u) => !u.done && !u.error))}
            >
              Limpar lista
            </button>
          )}
        </div>
      )}

      <nav className="sub-tabs">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" className={filter === f.id ? "active" : ""} onClick={() => setFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </nav>

      {error && <p className="error small">{error}</p>}

      {!loading && items.length === 0 && !error ? (
        <div className="empty-state">
          {filter === "arquivados" ? "Nada arquivado." : "Nenhum arquivo ainda. Envie o primeiro acima."}
        </div>
      ) : (
        <div className="library-grid">
          {items.map((item) => (
            <div key={item.id} className="library-card">
              <div className="library-thumb">
                {item.url && item.kind === "image" && <img src={item.url} alt={item.title ?? item.original_name} loading="lazy" />}
                {item.url && item.kind === "video" && <video src={item.url} preload="metadata" muted controls playsInline />}
                {item.kind === "video" && <span className="library-kind">Vídeo</span>}
              </div>
              <div className="library-card-body">
                <div className="library-card-name" title={item.original_name}>
                  {item.title ?? item.original_name}
                </div>
                <div className="library-card-meta">
                  {new Date(item.created_at).toLocaleDateString("pt-BR")} · {formatSize(item.size_bytes)}
                </div>
                <div className="library-card-actions">
                  {item.url && (
                    <a className="button-link" href={item.url} target="_blank" rel="noreferrer">
                      Abrir
                    </a>
                  )}
                  <button type="button" className="small" disabled={busyId === item.id} onClick={() => toggleArchive(item)}>
                    {item.status === "archived" ? "Restaurar" : "Arquivar"}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {loading && <p className="library-loading">Carregando...</p>}
      {more && !loading && (
        <button type="button" className="library-more" onClick={() => load(true)}>
          Carregar mais
        </button>
      )}
    </div>
  );
}
