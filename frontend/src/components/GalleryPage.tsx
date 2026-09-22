import { useEffect, useState } from "react";
import { getHistory, type HistoryEntry } from "../lib/history";

export function GalleryPage() {
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  useEffect(() => {
    setHistory(getHistory());
  }, []);

  return (
    <div className="page">
      <div className="page-header">
        <h1>Galeria</h1>
        <p>Todas as imagens geradas neste navegador, em grade.</p>
      </div>

      {history.length === 0 ? (
        <div className="empty-state">Nenhuma imagem ainda. Vá em "Gerar" para criar sua primeira imagem.</div>
      ) : (
        <div className="gallery-grid">
          {history.map((entry) => (
            <a
              key={entry.id}
              className="gallery-item"
              href={entry.imageUrl}
              target="_blank"
              rel="noreferrer"
              title={entry.prompt}
            >
              <img src={entry.imageUrl} alt={entry.prompt} />
              <div className="gallery-item-overlay">
                <span>{entry.personaName ?? "Sem persona"}</span>
                <span>{new Date(entry.createdAt).toLocaleDateString("pt-BR")}</span>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
