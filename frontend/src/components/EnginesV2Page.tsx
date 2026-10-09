import { useEffect, useRef, useState } from "react";
import { runSimpleReplacement, type EngineV2Result, type PersonaSummary } from "../api/client";
import { downloadFile } from "../lib/download";

interface Props {
  personas: PersonaSummary[];
  ensureAwake: () => Promise<unknown>;
}

function errorText(e: unknown) {
  return String(e instanceof Error ? e.message : e);
}

// Troca de pessoa: a pessoa da foto vira a persona. Tela simples de proposito - foto, Gerar, resultado.
// Versao (V3.1), modo e modelo ficam fixos em runSimpleReplacement; toda decisao tecnica e do backend.
export function EnginesV2Page({ personas, ensureAwake }: Props) {
  const [personaId, setPersonaId] = useState(personas.find((p) => p.id === "luna")?.id ?? personas[0]?.id ?? "luna"); // pod desligado = lista vazia: Gerar liga o pod
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EngineV2Result | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas.find((p) => p.id === "luna")?.id ?? personas[0].id);
  }, [personas, personaId]);

  useEffect(() => {
    if (!file) return setPreview(null);
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  async function handleRun() {
    if (!file || !personaId) return;
    setRunning(true);
    setError(null);
    setResult(null);
    setElapsed(0);
    const started = Date.now();
    timer.current = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    try {
      await ensureAwake();
      setResult(await runSimpleReplacement(file, personaId));
    } catch (e) {
      setError(errorText(e));
    } finally {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
      setRunning(false);
    }
  }

  const minutes = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="panel">
      <div className="page-header">
        <h1>Trocar pessoa</h1>
        <p>Envie uma foto: a pessoa dela vira a persona, com a mesma pose, roupa e cenário.</p>
      </div>

      {personas.length > 1 && (
        <div className="identity-grid">
          <div>
            <label htmlFor="v2-persona">Persona</label>
            <select id="v2-persona" value={personaId} onChange={(e) => setPersonaId(e.target.value)} disabled={running}>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      <label htmlFor="v2-file">Foto</label>
      <input
        id="v2-file"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        disabled={running}
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null);
          setResult(null);
          setError(null);
        }}
      />
      {preview && !result && <img className="v2-preview" src={preview} alt="Foto escolhida" />}

      <button type="button" className="primary" onClick={handleRun} disabled={running || !file || !personaId}>
        {running ? `Gerando... ${minutes}` : "Gerar"}
      </button>
      {running && (
        <p className="muted small">
          Leva alguns minutos. A primeira troca depois que o estúdio liga demora mais (prepara os modelos).
        </p>
      )}
      {error && <p className="error small">{error}</p>}

      {result && (
        <div className="engine-job">
          {result.status === "REJECT" && (
            <p className="error small">A conferência automática reprovou esta imagem. Você pode gerar de novo.</p>
          )}
          <div className="v2-compare">
            {preview && <img src={preview} alt="Original" />}
            <img src={result.image_url} alt="Resultado" />
          </div>
          <button type="button" className="small" onClick={() => downloadFile(result.image_url, `troca_${personaId}.png`)}>
            Baixar
          </button>
        </div>
      )}
    </div>
  );
}
