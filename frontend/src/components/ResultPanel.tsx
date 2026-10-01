import { useEffect, useState } from "react";
import type { GenerateResponse } from "../api/client";
import { downloadFile } from "../lib/download";
import type { PackItem } from "../lib/pack";
import { AnimatePanel } from "./AnimatePanel";

interface Props {
  result: GenerateResponse | null;
  error: string | null;
  loading: boolean;
  resultPrompt: string;
  onEditPrompt: (prompt: string) => void;
  onRegenerate: () => void;
  // Sequencia da historia: as fotos anteriores ficam visiveis embaixo.
  pack: PackItem[];
  onSelectPack: (item: PackItem) => void;
  onRemovePack: (id: string) => void;
  onClearPack: () => void;
}

export function ResultPanel({
  result,
  error,
  loading,
  resultPrompt,
  onEditPrompt,
  onRegenerate,
  pack,
  onSelectPack,
  onRemovePack,
  onClearPack,
}: Props) {
  const image = result?.images[0];
  // Imagem restaurada de outro pod (desligado/recriado) nao carrega mais.
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [image?.url]);

  return (
    <div>
      <div className="panel result-stage">
        {/* A foto anterior nunca pode parecer o resultado novo: some durante a
            geracao e, se a nova falhar, o erro aparece em cima dela. */}
        {!loading && error && image && (
          <p className="error result-error">
            A nova imagem não foi gerada: {error}
            <br />
            <span className="small">Abaixo continua a imagem anterior.</span>
          </p>
        )}
        <div className="result-image-wrap">
          {loading && <p className="muted">Gerando nova imagem...</p>}
          {!loading && error && !image && <p className="error" style={{ padding: 24 }}>{error}</p>}
          {!loading && !error && !image && (
            <div className="result-empty">
              <p>Nenhuma geração ainda.</p>
              <p className="small">Preencha os passos ao lado e clique em Gerar imagem.</p>
            </div>
          )}
          {!loading && image && broken && (
            <div className="result-empty">
              <p>A última imagem não está mais disponível.</p>
              <p className="small">Ela ficava no servidor anterior, que foi desligado. Gere de novo.</p>
            </div>
          )}
          {!loading && image && !broken && (
            <>
              <img src={image.url} alt={image.filename} onError={() => setBroken(true)} />
              <button type="button" className="result-download" onClick={() => downloadFile(image.url, image.filename)}>
                ⬇ Baixar
              </button>
            </>
          )}
        </div>

        {!loading && image && (
          <div className="result-actions">
            <button type="button" onClick={onRegenerate} disabled={loading}>
              ↻ Gerar novamente
            </button>
            <button type="button" onClick={() => onEditPrompt(resultPrompt)}>
              ✎ Editar prompt
            </button>
          </div>
        )}

        {!loading && result && (
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
      {pack.length > 0 && (
        <div className="panel pack-panel">
          <div className="pack-header">
            <h3>📚 Sequência ({pack.length})</h3>
            <div className="pack-actions">
              <button
                type="button"
                className="small"
                onClick={async () => {
                  for (const [i, item] of pack.entries()) {
                    const img = item.result.images[0];
                    if (img) await downloadFile(img.url, `pack_${String(i + 1).padStart(2, "0")}_${img.filename}`);
                  }
                }}
              >
                ⬇ Baixar todas
              </button>
              <button
                type="button"
                className="small"
                onClick={() => {
                  if (window.confirm("Começar um pack novo? A sequência atual sai daqui (as fotos continuam no Histórico).")) {
                    onClearPack();
                  }
                }}
              >
                Novo pack
              </button>
            </div>
          </div>
          <p className="muted small">Cada foto nova entra no fim. Clique numa para ver grande.</p>
          <div className="pack-strip">
            {pack.map((item, i) => {
              const img = item.result.images[0];
              if (!img) return null;
              const current = !loading && image?.url === img.url;
              return (
                <div key={item.id} className={current ? "pack-item current" : "pack-item"}>
                  <button type="button" className="pack-thumb" title={item.prompt} onClick={() => onSelectPack(item)}>
                    <img src={img.url} alt="" loading="lazy" />
                    <span className="pack-number">{i + 1}</span>
                  </button>
                  <button type="button" className="pack-remove" title="Tirar da sequência" onClick={() => onRemovePack(item.id)}>
                    ✕
                  </button>
                </div>
              );
            })}
            {loading && (
              <div className="pack-item">
                <div className="pack-thumb pack-pending">
                  <span>Gerando {pack.length + 1}...</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      {image && !broken && !loading && <AnimatePanel image={image} personaId={result?.persona_id ?? null} />}
    </div>
  );
}
