import { useEffect, useState } from "react";
import type { GenerateResponse, HealthResponse, ModelInfo, PersonaSummary, WorkflowInfo } from "./api/client";
import { generateImage, getHealth, getModels, getPersonas, getPodStatus, getWorkflows, wakePod } from "./api/client";
import { GenerationForm } from "./components/GenerationForm";
import { PersonasView } from "./components/PersonasView";
import { PodStatusPanel } from "./components/PodStatusPanel";
import { ResultPanel } from "./components/ResultPanel";

const WAKE_POLL_INTERVAL_MS = 5_000;
const WAKE_MAX_WAIT_MS = 3 * 60_000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Garante que o pod esta ligado antes de gerar. Se ja estiver rodando,
// retorna na hora; senao, dispara o resume e fica consultando o status ate
// aparecer "running" (ou desistir apos WAKE_MAX_WAIT_MS).
async function ensurePodAwake(onMessage: (msg: string | null) => void): Promise<void> {
  try {
    const wake = await wakePod();
    if (wake.alreadyRunning) return;
  } catch (e) {
    onMessage(
      `Nao foi possivel ligar o pod automaticamente (${e instanceof Error ? e.message : e}). Tentando gerar mesmo assim...`
    );
    return;
  }

  onMessage("Ligando o pod... isso pode levar de 1 a 3 minutos.");
  const deadline = Date.now() + WAKE_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    await sleep(WAKE_POLL_INTERVAL_MS);
    try {
      const status = await getPodStatus();
      if (status.running) {
        onMessage("Pod ligado. Gerando imagem...");
        return;
      }
    } catch {
      // ignora falhas de polling isoladas e tenta de novo no proximo ciclo
    }
  }
  onMessage("O pod demorou mais que o esperado para ligar. Tentando gerar mesmo assim...");
}

type Tab = "geracao" | "personas";

export default function App() {
  const [tab, setTab] = useState<Tab>("geracao");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [podMessage, setPodMessage] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e) => setLoadError(String(e)));
    getModels().then(setModels).catch((e) => setLoadError(String(e)));
    getWorkflows().then(setWorkflows).catch((e) => setLoadError(String(e)));
    getPersonas().then(setPersonas).catch(() => undefined);
  }, []);

  async function handleGenerate(params: {
    prompt: string;
    modelId: string;
    workflowId: string;
    personaId: string;
    width: number;
    height: number;
    steps: number;
    guidance: number;
  }) {
    setLoading(true);
    setError(null);
    setPodMessage(null);
    try {
      await ensurePodAwake(setPodMessage);
      const res = await generateImage({
        prompt: params.prompt,
        model_id: params.modelId,
        workflow_id: params.workflowId,
        persona_id: params.personaId || undefined,
        width: params.width,
        height: params.height,
        steps: params.steps,
        guidance: params.guidance,
      });
      setResult(res);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!podMessage || loading) return;
    const timer = setTimeout(() => setPodMessage(null), 8000);
    return () => clearTimeout(timer);
  }, [podMessage, loading]);

  return (
    <div className="app">
      <header>
        <h1>Luna AI Studio</h1>
        <p className="status">
          Backend: {health?.backend ?? "..."} · ComfyUI:{" "}
          <span className={health?.comfyui.ok ? "ok" : "down"}>
            {health ? (health.comfyui.ok ? "conectado" : "indisponível") : "verificando..."}
          </span>
        </p>
        {health && !health.comfyui.ok && <p className="error small">{health.comfyui.message}</p>}
        {loadError && <p className="error small">{loadError}</p>}

        <PodStatusPanel />
        {podMessage && <p className="pod-toast">{podMessage}</p>}

        <nav className="top-tabs">
          <button type="button" className={tab === "geracao" ? "active" : ""} onClick={() => setTab("geracao")}>
            Geração
          </button>
          <button type="button" className={tab === "personas" ? "active" : ""} onClick={() => setTab("personas")}>
            Personas
          </button>
        </nav>
      </header>

      {tab === "geracao" ? (
        <main>
          <GenerationForm
            models={models}
            workflows={workflows}
            personas={personas}
            loading={loading}
            onSubmit={handleGenerate}
          />
          <ResultPanel result={result} error={error} />
        </main>
      ) : (
        <PersonasView models={models} workflows={workflows} />
      )}
    </div>
  );
}
