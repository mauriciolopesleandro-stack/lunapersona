import { useEffect, useState } from "react";
import type { GenerateResponse } from "../api/client";
import { downloadFile } from "../lib/download";
import { AnimatePanel } from "./AnimatePanel";

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
  // Imagem restaurada de outro pod (desligado/recriado) nao carrega mais.
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [image?.url]);

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
          {image && broken && (
            <div className="result-empty">
              <p>A última imagem não está mais disponível.</p>
              <p className="small">Ela ficava no servidor anterior, que foi desligado. Gere de novo.</p>
            </div>
          )}
          {image && !broken && (
            <>
              <img src={image.url} alt={image.filename} onError={() => setBroken(true)} />
              <button type="button" className="result-download" onClick={() => downloadFile(image.url, image.filename)}>
                ⬇ Baixar
              </button>
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
      {image && !broken && !loading && <AnimatePanel image={image} />}
    </div>
  );
}
