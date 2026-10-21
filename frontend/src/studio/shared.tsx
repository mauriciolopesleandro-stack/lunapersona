import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "./Icon";
import { useStudio, type Item, type Network } from "./store";

export const NETWORKS: Record<Network, { label: string; icon: string; captionMax: number; formats: string[] }> = {
  instagram: { label: "Instagram", icon: "instagram", captionMax: 2200, formats: ["Feed", "Reels", "Stories"] },
  tiktok: { label: "TikTok", icon: "tiktok", captionMax: 4000, formats: ["Vídeo", "Foto"] },
  telegram: { label: "Telegram", icon: "telegram", captionMax: 1024, formats: ["Canal", "Grupo"] },
};

const STATUS_LABEL: Record<Item["display"], string> = {
  rascunho: "Rascunho",
  aprovado: "Aprovado",
  agendado: "Agendado",
  publicado: "Publicado",
  arquivado: "Arquivado",
};

export function StatusBadge({ status }: { status: Item["display"] }) {
  return <span className={`st-badge st-badge-${status}`}>{STATUS_LABEL[status]}</span>;
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function formatDate(iso: string | number): string {
  return new Date(iso).toLocaleDateString("pt-BR");
}

function formatDuration(s: number) {
  return `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
}

// Miniatura quadrada: foto ou o primeiro quadro do video (com a duracao).
export function Thumb({ item, onClick }: { item: Item; onClick?: () => void }) {
  const [duration, setDuration] = useState<number | null>(null);
  return (
    <button type="button" className="st-thumb" onClick={onClick} aria-label={`Abrir ${item.title}`}>
      {item.kind === "image" ? (
        <img src={item.url} alt="" loading="lazy" />
      ) : (
        <video src={`${item.url}#t=0.1`} muted preload="metadata" playsInline onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)} />
      )}
      <span className="st-thumb-kind">
        <Icon name={item.kind === "image" ? "image" : "video"} size={14} />
      </span>
      {item.kind === "video" && duration !== null && Number.isFinite(duration) && (
        <span className="st-thumb-duration">{formatDuration(duration)}</span>
      )}
    </button>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="st-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`st-modal${wide ? " st-modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="st-modal-head">
          <h2>{title}</h2>
          <button type="button" className="st-icon-btn" onClick={onClose} aria-label="Fechar">
            <Icon name="close" size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// Mostra o conteudo so com a pasta conectada; senao, explica como conectar.
export function FolderGate({ children, compact }: { children: ReactNode; compact?: boolean }) {
  const { state, error, folderName, connect, reconnect } = useStudio();
  if (state === "ready") return <>{children}</>;
  if (state === "loading") return <div className="st-empty">Abrindo a pasta...</div>;
  if (state === "unsupported") {
    return (
      <div className="st-panel st-gate">
        <Icon name="folder" size={28} />
        <h2>Abra no Chrome ou no Edge do computador</h2>
        <p>Os arquivos ficam numa pasta do seu computador, e só esses navegadores deixam o site abrir uma pasta local.</p>
      </div>
    );
  }
  return (
    <div className={`st-panel st-gate${compact ? " st-gate-compact" : ""}`}>
      <Icon name="folder" size={28} />
      {state === "needs-permission" ? (
        <>
          <h2>Reconectar a pasta “{folderName}”</h2>
          <p>Por segurança, o navegador pede sua permissão de novo a cada visita.</p>
          <button type="button" className="st-btn st-btn-primary" onClick={reconnect}>
            Reconectar pasta
          </button>
        </>
      ) : (
        <>
          <h2>Escolha a pasta do estúdio</h2>
          <p>
            Imagens, vídeos e agendamentos ficam guardados nela, no seu computador. Nada é enviado para a internet. Dentro
            dela o estúdio cria a pasta <b>conteudos</b> e o arquivo <b>luna-vox.json</b>.
          </p>
          <button type="button" className="st-btn st-btn-primary" onClick={connect}>
            Escolher pasta
          </button>
        </>
      )}
      {error && <p className="st-error">{error}</p>}
    </div>
  );
}

// Botoes que so fazem sentido com a pasta aberta (sem repetir o aviso).
export function WhenReady({ children }: { children: ReactNode }) {
  return useStudio().state === "ready" ? <>{children}</> : null;
}

export function ComingSoon({ title, text, icon }: { title: string; text: string; icon: string }) {
  return (
    <div>
      <div className="st-page-head">
        <h1>{title}</h1>
      </div>
      <div className="st-panel st-gate">
        <Icon name={icon} size={28} />
        <h2>Em breve</h2>
        <p>{text}</p>
      </div>
    </div>
  );
}
