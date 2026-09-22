import type { GenerateResponse } from "../api/client";
import type { HistoryEntry } from "../lib/history";

interface Props {
  result: GenerateResponse | null;
  error: string | null;
  loading: boolean;
  resultPrompt: string;
  history: HistoryEntry[];
  activeHistoryId: string | null;
  onEditPrompt: (prompt: string) => void;
  onRegenerate: () => void;
  onSelectHistoryEntry: (entry: HistoryEntry) => void;
}

export function ResultPanel({
  result,
  error,
  loading,
  resultPrompt,
  history,
  activeHistoryId,
  onEditPrompt,
  onRegenerate,
  onSelectHistoryEntry,
}: Props) {
  const image = result?.images[0];

  return (
    <div>
      <div className="panel result-stage">
        <div className="result-image-wrap">
          {loading && !image && <p className="muted">Gerando imagem...</p>}
          {!loading && error && !image && <p className="error" style={{ padding: 24 }}>{error}</p>}
          {!loading && !error && !image && (
            <div className="result-empty">
              <p>Nenhuma geração ainda.</p>
              <p className="small">Preencha os passos ao lado e clique em Gerar imagem.</p>
            </div>
          )}
          {image && (
            <>
              <img src={image.url} alt={image.filename} />
              <a className="result-download" href={image.url} download={image.filename}>
                ⬇ Baixar
              </a>
            </>
          )}
        </div>

        {image && (
          <div className="result-actions">
            <button type="button" onClick={onRegenerate} disabled={loading}>
              ↻ Gerar novamente
            </button>
            <button type="button" onClick={() => onEditPrompt(resultPrompt)}>
              ✎ Editar prompt
            </button>
          </div>
        )}

        {result && (
          <dl className="meta" style={{ padding: image ? "0 18px 16px" : 0 }}>
            {result.persona_id && (
              <>
                <dt>Persona</dt>
                <dd>{result.persona_id}</dd>
              </>
            )}
            <dt>Duração</dt>
            <dd>{result.duration_seconds.toFixed(1)}s</dd>
          </dl>
        )}
      </div>

      <div className="history-strip-header">
        <h3>Histórico recente</h3>
      </div>
      {history.length === 0 ? (
        <p className="muted small">Suas gerações aparecem aqui.</p>
      ) : (
        <div className="history-strip">
          {history.slice(0, 12).map((entry) => (
            <button
              key={entry.id}
              type="button"
              className={entry.id === activeHistoryId ? "history-thumb active" : "history-thumb"}
              onClick={() => onSelectHistoryEntry(entry)}
              title={entry.prompt}
            >
              <img src={entry.imageUrl} alt={entry.prompt} />
              <div className="history-thumb-meta">
                <strong>{entry.personaName ?? "Sem persona"}</strong>
                <span>{new Date(entry.createdAt).toLocaleDateString("pt-BR")}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
