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
  // A imagem do resultado e baixada para o navegador assim que fica pronta: o botao Baixar vira um link local
  // (sem esperar rede depois do toque - o celular, principalmente o iPhone, ignora o download que chega atrasado)
  // e a imagem continua na tela mesmo depois que o pod desliga.
  const [localUrl, setLocalUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!result) return setLocalUrl(null);
    let url: string | null = null;
    let cancelled = false;
    fetch(result.image_url)
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((b) => {
        if (cancelled) return;
        url = URL.createObjectURL(b);
        setLocalUrl(url);
      })
      .catch(() => setLocalUrl(null));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [result]);

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
        <p>Envie uma foto: a pessoa ganha o rosto e o cabelo da Luna. Roupa, corpo, pose e cenário ficam iguais aos da foto.</p>
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
          Leva uns 4 minutos (gera 2 versões e fica com a mais parecida com a Luna). A primeira depois que o estúdio liga demora mais.
        </p>
      )}
      {error && <p className="error small">{error}</p>}

      {result && (
        <div className="engine-job">
          {result.status !== "PASS" && (
            <p className="error small">A Luna ficou menos parecida que o normal nesta foto. Você pode gerar de novo.</p>
          )}
          <div className="v2-compare">
            {preview && <img src={preview} alt="Original" />}
            <img src={localUrl ?? result.image_url} alt="Resultado" />
          </div>
          {localUrl ? (
            <a className="button-link" href={localUrl} download={`troca_${personaId}.png`}>
              Baixar
            </a>
          ) : (
            <button type="button" className="small" onClick={() => downloadFile(result.image_url, `troca_${personaId}.png`)}>
              Baixar
            </button>
          )}
          <p className="muted small">No celular, se não baixar: toque e segure a imagem e escolha “Salvar imagem”.</p>
        </div>
      )}
    </div>
  );
}
