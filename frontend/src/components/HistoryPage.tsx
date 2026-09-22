import { useEffect, useState } from "react";
import { clearHistory, getHistory, removeHistoryEntry, type HistoryEntry } from "../lib/history";

export function HistoryPage() {
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  useEffect(() => {
    setHistory(getHistory());
  }, []);

  function handleRemove(id: string) {
    removeHistoryEntry(id);
    setHistory(getHistory());
  }

  function handleClear() {
    clearHistory();
    setHistory([]);
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Histórico</h1>
        <p>Todas as gerações feitas neste navegador, mais recentes primeiro.</p>
      </div>

      {history.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <button type="button" className="danger small" onClick={handleClear}>
            Limpar histórico
          </button>
        </div>
      )}

      {history.length === 0 ? (
        <div className="empty-state">Nenhuma geração ainda. Vá em "Gerar" para criar sua primeira imagem.</div>
      ) : (
        <div className="history-list">
          {history.map((entry) => (
            <div className="panel history-row" key={entry.id}>
              <img src={entry.imageUrl} alt={entry.prompt} />
              <div className="history-row-body">
                <div className="history-row-prompt">{entry.prompt}</div>
                <div className="history-row-meta">
                  {entry.personaName ?? "Sem persona"} · {new Date(entry.createdAt).toLocaleString("pt-BR")}
                </div>
              </div>
              <button type="button" className="small danger" onClick={() => handleRemove(entry.id)}>
                Remover
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
