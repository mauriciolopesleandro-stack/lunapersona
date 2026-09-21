import type { GenerateResponse } from "../api/client";

interface Props {
  result: GenerateResponse | null;
  error: string | null;
}

export function ResultPanel({ result, error }: Props) {
  return (
    <div className="panel">
      <h2>Resultado</h2>

      {error && <p className="error">{error}</p>}

      {!result && !error && <p className="muted">Nenhuma geração ainda.</p>}

      {result && (
        <div>
          {result.images.map((img) => (
            <img key={img.filename} src={img.url} alt={img.filename} className="result-image" />
          ))}
          <dl className="meta">
            <dt>Modelo</dt>
            <dd>{result.model_id}</dd>
            <dt>Workflow</dt>
            <dd>{result.workflow_id}</dd>
            {result.persona_id && (
              <>
                <dt>Persona</dt>
                <dd>{result.persona_id}</dd>
              </>
            )}
            <dt>Prompt ID</dt>
            <dd>{result.prompt_id}</dd>
            <dt>Duração</dt>
            <dd>{result.duration_seconds.toFixed(1)}s</dd>
          </dl>
        </div>
      )}
    </div>
  );
}
