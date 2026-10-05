import { useEffect, useRef, useState } from "react";
import type { GenerationJob, PersonaSheetSummary, ProviderInfo } from "../api/client";
import {
  getEngineGeneration,
  getEngineProviders,
  getPersonaSheet,
  startEngineGeneration,
  uploadGenerationReference,
} from "../api/client";
import { downloadFile } from "../lib/download";
import { ValidationReportView } from "./ValidationReportView";

const POLL_MS = 3000;

interface Props {
  personaId: string;
  personaName: string;
  ensureAwake?: () => Promise<unknown>;
}

const STATUS_TEXT: Record<GenerationJob["status"], string> = {
  RUNNING: "Gerando e conferindo...",
  ACCEPTED: "Aprovada na validação da persona",
  FAILED: "Nenhuma tentativa passou na validação",
  ERROR: "Não foi possível gerar",
};

// Persona Engine V1: cena (Z-Image + LoRA) -> rosto da master (Qwen BFS) ->
// validacao -> nova tentativa conforme a falha. Toda decisao e do backend;
// aqui so se monta o pedido e se mostra o resultado.
export function PersonaEngineGenerate({ personaId, personaName, ensureAwake }: Props) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState("");
  const [sheet, setSheet] = useState<PersonaSheetSummary | null>(null);
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [scene, setScene] = useState("");
  const [style, setStyle] = useState("");
  const [mode, setMode] = useState<"FREE" | "POSE_CONTROLLED">("FREE");
  const [poseFile, setPoseFile] = useState<File | null>(null);
  const [overrideThreshold, setOverrideThreshold] = useState(false);
  const [threshold, setThreshold] = useState(55);
  const [maxAttempts, setMaxAttempts] = useState(3);
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

  useEffect(() => {
    setSheet(null);
    setSheetError(null);
    getPersonaSheet(personaId)
      .then(setSheet)
      .catch((e) => setSheetError(String(e instanceof Error ? e.message : e)));
  }, [personaId]);

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
      // A imagem de pose vai para o pod; o backend guarda so o nome.
      const poseReference = mode === "POSE_CONTROLLED" && poseFile ? await uploadGenerationReference(poseFile) : undefined;
      const started = await startEngineGeneration({
        persona_id: personaId,
        scene_prompt: scene,
        provider: provider || undefined,
        style_overrides: style.trim() ? { estetica: style.trim() } : {},
        mode,
        pose_reference: poseReference,
        validation_threshold: overrideThreshold ? threshold / 100 : undefined,
        max_attempts: maxAttempts,
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
  const canGenerate = !!scene.trim() && !!sheet && (mode === "FREE" || !!poseFile);

  return (
    <div>
      <h3>Gerar com validação (Persona Engine V1)</h3>
      {sheet ? (
        <p className="muted small">
          Persona Sheet v{sheet.persona_version} · pipeline {sheet.pipeline_version}: Z-Image + LoRA → rosto da master
          (Qwen BFS) → validação. O rosto de {personaName} vem sempre da foto master; a cena, a roupa e a pose vêm do
          pedido.
        </p>
      ) : (
        <p className={sheetError ? "error small" : "muted small"}>{sheetError ?? "Carregando a Persona Sheet..."}</p>
      )}

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
          <label htmlFor="eng-gen-mode">Pose</label>
          <select id="eng-gen-mode" value={mode} onChange={(e) => setMode(e.target.value as "FREE" | "POSE_CONTROLLED")}>
            <option value="FREE">Livre (pelo texto)</option>
            <option value="POSE_CONTROLLED">Controlada por uma foto de pose</option>
          </select>
        </div>
        {mode === "POSE_CONTROLLED" && (
          <div>
            <label htmlFor="eng-gen-pose">Foto com a pose (descreva a mesma pose na cena)</label>
            <input
              id="eng-gen-pose"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(e) => setPoseFile(e.target.files?.[0] ?? null)}
            />
          </div>
        )}
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
          <input id="eng-gen-style" value={style} placeholder="cinematic, warm tones" onChange={(e) => setStyle(e.target.value)} />
        </div>
        <div>
          <label htmlFor="eng-gen-attempts">Máximo de tentativas</label>
          <select id="eng-gen-attempts" value={maxAttempts} onChange={(e) => setMaxAttempts(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>
      <label className="checkbox-row">
        <input type="checkbox" checked={overrideThreshold} onChange={(e) => setOverrideThreshold(e.target.checked)} />
        Usar outro limiar de rosto só neste pedido (o padrão vem da Persona Sheet)
      </label>
      {overrideThreshold && (
        <>
          <label htmlFor="eng-gen-threshold">Limiar de rosto: {(threshold / 100).toFixed(2)}</label>
          <input
            id="eng-gen-threshold"
            type="range"
            min={30}
            max={90}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
        </>
      )}

      <button type="button" className="primary" onClick={handleGenerate} disabled={running || !canGenerate}>
        {running ? "Gerando..." : "Gerar"}
      </button>
      {error && <p className="error small">{error}</p>}

      {job && (
        <div className="engine-job">
          <p className={job.status === "ACCEPTED" ? "saved-hint" : job.status === "RUNNING" ? "muted" : "error small"}>
            {STATUS_TEXT[job.status]}
            {job.attempt > 0 && ` · tentativa ${job.attempt} de ${job.max_attempts}`}
            {` · limiar ${job.threshold.toFixed(2)} (${job.threshold_source === "pedido" ? "deste pedido" : "da ficha"})`}
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
                  </p>
                  <ValidationReportView result={r} failures={job.failures} />
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
