import { useEffect, useState } from "react";
import type { GenerationJob } from "../api/client";
import { getEngineGeneration, getPersonaGenerations, retryEngineGeneration } from "../api/client";
import { IdentityScore } from "./IdentityScore";

const STATUS: Record<GenerationJob["status"], string> = {
  RUNNING: "⏳ gerando",
  ACCEPTED: "✓ aceita",
  FAILED: "✕ reprovada",
  ERROR: "✕ erro",
};

interface Props {
  personaId: string;
  ensureAwake?: () => Promise<unknown>;
}

// Historico das geracoes com validacao: cada pedido, quantas tentativas, a
// melhor nota; aberto, mostra todas as tentativas com o motivo de cada recusa.
export function PersonaGenerationHistory({ personaId, ensureAwake }: Props) {
  const [jobs, setJobs] = useState<GenerationJob[] | null>(null);
  const [open, setOpen] = useState<GenerationJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setError(null);
    getPersonaGenerations(personaId)
      .then(setJobs)
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }

  useEffect(() => {
    setJobs(null);
    setOpen(null);
    load();
  }, [personaId]);

  async function toggle(job: GenerationJob) {
    if (open?.id === job.id) return setOpen(null);
    try {
      setOpen(await getEngineGeneration(job.id));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function retry(job: GenerationJob) {
    try {
      await ensureAwake?.();
      await retryEngineGeneration(job.id);
      load();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <div>
      <div className="engine-history-head">
        <h3>Histórico de gerações</h3>
        <button type="button" className="small" onClick={load}>
          Atualizar
        </button>
      </div>
      {error && <p className="error small">{error}</p>}
      {jobs === null && !error && <p className="muted">Carregando...</p>}
      {jobs?.length === 0 && <p className="muted">Nenhuma geração com validação ainda.</p>}

      <div className="history-list">
        {jobs?.map((job) => (
          <div key={job.id} className="engine-history-item">
            <button type="button" className="history-row engine-history-row" onClick={() => toggle(job)}>
              {job.best_result?.image_url ? (
                <img src={job.best_result.image_url} alt="" loading="lazy" />
              ) : (
                <div className="engine-attempt-empty">—</div>
              )}
              <div className="history-row-body">
                <p className="history-row-prompt">{job.scene_prompt}</p>
                <p className="history-row-meta">
                  {STATUS[job.status]} · {job.attempt}/{job.max_attempts} tentativas · melhor{" "}
                  {job.best_result?.identity_score != null
                    ? `${(job.best_result.identity_score * 100).toFixed(1)}%`
                    : "—"}{" "}
                  · limiar {Math.round(job.threshold * 100)}% · {new Date(job.created_at).toLocaleString("pt-BR")}
                </p>
              </div>
            </button>
            {open?.id === job.id && (
              <div className="engine-history-detail">
                {job.status !== "RUNNING" && (
                  <button type="button" className="small" onClick={() => retry(job)}>
                    Gerar de novo com o mesmo pedido
                  </button>
                )}
                {open.error && <p className="error small">{open.error}</p>}
                <div className="engine-attempts">
                  {(open.results ?? []).map((r) => (
                    <div key={r.id} className="engine-attempt">
                      {r.image_url ? (
                        <img src={r.image_url} alt={`Tentativa ${r.attempt}`} loading="lazy" />
                      ) : (
                        <div className="engine-attempt-empty">sem imagem</div>
                      )}
                      <div>
                        <p className="muted small">Tentativa {r.attempt}</p>
                        <IdentityScore result={r} failures={open.failures} compact />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
