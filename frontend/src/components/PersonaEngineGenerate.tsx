import { useEffect, useRef, useState } from "react";
import type { GenerationJob, ProviderInfo } from "../api/client";
import { getEngineGeneration, getEngineProviders, startEngineGeneration } from "../api/client";
import { downloadFile } from "../lib/download";
import { IdentityScore } from "./IdentityScore";

const POLL_MS = 3000;

interface Props {
  personaId: string;
  personaName: string;
  ensureAwake?: () => Promise<unknown>;
}

const STATUS_TEXT: Record<GenerationJob["status"], string> = {
  RUNNING: "Gerando e conferindo...",
  ACCEPTED: "Aprovada na validação de identidade",
  FAILED: "Nenhuma tentativa passou na validação",
  ERROR: "Não foi possível gerar",
};

// Gerar com validacao: o backend gera, confere se e a persona e, se nao for,
// ajusta e gera de novo (ate o maximo de tentativas). Mostra cada tentativa
// com a nota.
export function PersonaEngineGenerate({ personaId, personaName, ensureAwake }: Props) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState("");
  const [scene, setScene] = useState("");
  const [style, setStyle] = useState("");
  const [threshold, setThreshold] = useState(90);
  const [maxAttempts, setMaxAttempts] = useState(4);
  const [faceRestore, setFaceRestore] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    getEngineProviders()
      .then((data) => {
        setProviders(data.providers);
        setProvider(data.default);
      })
      .catch(() => setProviders([]));
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  function poll(jobId: string) {
    timer.current = window.setTimeout(async () => {
      try {
        const current = await getEngineGeneration(jobId);
        setJob(current);
        if (current.status === "RUNNING") poll(jobId);
      } catch (e) {
        setError(String(e instanceof Error ? e.message : e));
      }
    }, POLL_MS);
  }

  async function handleGenerate() {
    setStarting(true);
    setError(null);
    setJob(null);
    try {
      await ensureAwake?.();
      const started = await startEngineGeneration({
        persona_id: personaId,
        scene_prompt: scene,
        provider: provider || undefined,
        style_overrides: style.trim() ? { estetica: style.trim() } : {},
        validation_threshold: threshold / 100,
        max_attempts: maxAttempts,
        face_restore: faceRestore,
      });
      setJob(started);
      poll(started.id);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setStarting(false);
    }
  }

  const running = starting || job?.status === "RUNNING";
  const results = job?.results ?? [];

  return (
    <div>
      <h3>Gerar com validação de identidade</h3>
      <p className="muted small">
        Cada imagem é comparada com as fotos de rosto de {personaName}. Abaixo do limiar, o estúdio ajusta (mais força
        da identidade, retoque do rosto, reforço do traço que faltou) e tenta de novo.
      </p>

      <label htmlFor="eng-gen-scene">Cena</label>
      <textarea
        id="eng-gen-scene"
        rows={3}
        value={scene}
        placeholder="Pessoa caminhando em uma cafeteria em São Paulo, fim de tarde"
        onChange={(e) => setScene(e.target.value)}
      />

      <div className="identity-grid">
        <div>
          <label htmlFor="eng-gen-provider">Provider</label>
          <select id="eng-gen-provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {providers.map((p) => (
              <option key={p.name} value={p.name}>
                {p.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="eng-gen-style">Estilo (opcional)</label>
          <input
            id="eng-gen-style"
            value={style}
            placeholder="cinematic, warm tones"
            onChange={(e) => setStyle(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="eng-gen-threshold">Limiar de qualidade: {threshold}%</label>
          <input
            id="eng-gen-threshold"
            type="range"
            min={50}
            max={100}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
        </div>
        <div>
          <label htmlFor="eng-gen-attempts">Máximo de tentativas</label>
          <select id="eng-gen-attempts" value={maxAttempts} onChange={(e) => setMaxAttempts(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>
      <label className="checkbox-row">
        <input type="checkbox" checked={faceRestore} onChange={(e) => setFaceRestore(e.target.checked)} />
        Retocar o rosto já na primeira tentativa (mais lento)
      </label>

      <button type="button" className="primary" onClick={handleGenerate} disabled={running || !scene.trim()}>
        {running ? "Gerando..." : "Gerar"}
      </button>
      {error && <p className="error small">{error}</p>}

      {job && (
        <div className="engine-job">
          <p className={job.status === "ACCEPTED" ? "saved-hint" : job.status === "RUNNING" ? "muted" : "error small"}>
            {STATUS_TEXT[job.status]}
            {job.attempt > 0 && ` · tentativa ${job.attempt} de ${job.max_attempts}`}
            {job.error && ` · ${job.error}`}
          </p>
          <div className="engine-attempts">
            {[...results].reverse().map((r) => (
              <div key={r.id} className="engine-attempt">
                {r.image_url ? (
                  <img src={r.image_url} alt={`Tentativa ${r.attempt}`} loading="lazy" />
                ) : (
                  <div className="engine-attempt-empty">sem imagem</div>
                )}
                <div>
                  <p className="muted small">
                    Tentativa {r.attempt}
                    {r.id === job.best_result_id && job.status !== "RUNNING" ? " · melhor" : ""}
                    {` · força da identidade ${Math.round(r.parameters.identity_strength * 100)}%`}
                    {r.parameters.face_restore ? " · com retoque do rosto" : ""}
                  </p>
                  <IdentityScore result={r} failures={job.failures} threshold={job.threshold} />
                  {r.image_url && (
                    <button
                      type="button"
                      className="small"
                      onClick={() => downloadFile(r.image_url!, `persona_${personaId}_${r.attempt}.png`)}
                    >
                      Baixar
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
