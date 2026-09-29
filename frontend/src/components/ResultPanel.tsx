import type { GenerateResponse } from "../api/client";

interface Props {
  result: GenerateResponse | null;
  error: string | null;
  loading: boolean;
  resultPrompt: string;
  onEditPrompt: (prompt: string) => void;
  onRegenerate: () => void;
}

export function ResultPanel({ result, error, loading, resultPrompt, onEditPrompt, onRegenerate }: Props) {
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
    </div>
  );
}
