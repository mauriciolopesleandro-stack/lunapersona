import { useEffect, useState } from "react";
import type { GenerateResponse, HealthResponse, ModelInfo, PersonaSummary, WorkflowInfo } from "./api/client";
import {
  bootstrapPod,
  generateImage,
  getHealth,
  getModels,
  getPersonas,
  getPodStatus,
  getWorkflows,
  wakePod,
} from "./api/client";
import { ChatAssistant } from "./components/ChatAssistant";
import { GenerationForm } from "./components/GenerationForm";
import { PersonasView } from "./components/PersonasView";
import { PodStatusPanel } from "./components/PodStatusPanel";
import { ResultPanel } from "./components/ResultPanel";

const WAKE_POLL_INTERVAL_MS = 5_000;
const WAKE_MAX_WAIT_MS = 3 * 60_000;
const BOOTSTRAP_RETRY_INTERVAL_MS = 8_000;
const BOOTSTRAP_MAX_ATTEMPTS = 5;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Roda o bootstrap (Ollama + backend) dentro do pod via SSH. Logo apos o pod
// entrar em "running" a porta SSH ainda pode nao estar mapeada - por isso
// tenta de novo algumas vezes antes de desistir.
async function runBootstrapWithRetry(onMessage: (msg: string | null) => void): Promise<void> {
  for (let attempt = 1; attempt <= BOOTSTRAP_MAX_ATTEMPTS; attempt++) {
    try {
      const result = await bootstrapPod();
      if (result.ok) return;
      throw new Error(`comando saiu com codigo ${result.exitCode}`);
    } catch (e) {
      if (attempt === BOOTSTRAP_MAX_ATTEMPTS) throw e;
      await sleep(BOOTSTRAP_RETRY_INTERVAL_MS);
    }
  }
}

// Garante que o pod esta ligado antes de gerar. Se ja estiver rodando,
// retorna na hora; senao, dispara o resume e fica consultando o status ate
// aparecer "running" (ou desistir apos WAKE_MAX_WAIT_MS).
// Retorna true se o pod estava desligado e precisou ser ligado agora (ou
// seja: um "cold start", onde listas de persona/modelo podem ter ficado
// desatualizadas e vale a pena recarrega-las antes de gerar).
async function ensurePodAwake(onMessage: (msg: string | null) => void): Promise<boolean> {
  try {
    const wake = await wakePod();
    if (wake.alreadyRunning) return false;
  } catch (e) {
    onMessage(
      `Nao foi possivel ligar o pod automaticamente (${e instanceof Error ? e.message : e}). Tentando gerar mesmo assim...`
    );
    return false;
  }

  onMessage("Ligando o pod... isso pode levar de 1 a 3 minutos.");
  const deadline = Date.now() + WAKE_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    await sleep(WAKE_POLL_INTERVAL_MS);
    try {
      const status = await getPodStatus();
      if (status.running) {
        onMessage("Pod ligado. Preparando chat e geração (git pull + Ollama + backend)...");
        try {
          await runBootstrapWithRetry(onMessage);
          onMessage("Pod pronto.");
        } catch (e) {
          onMessage(
            `Pod ligado, mas não consegui preparar automaticamente (${e instanceof Error ? e.message : e}). Chat/geração podem levar mais um pouco para responder.`
          );
        }
        return true;
      }
    } catch {
      // ignora falhas de polling isoladas e tenta de novo no proximo ciclo
    }
  }
  onMessage("O pod demorou mais que o esperado para ligar. Tentando gerar mesmo assim...");
  return true;
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
  const [waking, setWaking] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [personaId, setPersonaId] = useState("");

  async function handleWakePod() {
    setWaking(true);
    setPodMessage(null);
    try {
      const wasColdStart = await ensurePodAwake(setPodMessage);
      if (wasColdStart) {
        await loadConfig();
      }
    } finally {
      setWaking(false);
    }
  }

  async function loadConfig() {
    getHealth().then(setHealth).catch((e) => setLoadError(String(e)));
    getModels().then(setModels).catch((e) => setLoadError(String(e)));
    getWorkflows().then(setWorkflows).catch((e) => setLoadError(String(e)));
    return getPersonas()
      .then((list) => {
        setPersonas(list);
        return list;
      })
      .catch(() => [] as PersonaSummary[]);
  }

  useEffect(() => {
    loadConfig();
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
      const wasColdStart = await ensurePodAwake(setPodMessage);
      if (wasColdStart) {
        const freshPersonas = await loadConfig();
        if (!params.personaId && freshPersonas.length > 0) {
          setPodMessage(
            "Pod ligado. A lista de personas acabou de carregar - selecione a Luna acima e clique em Gerar de novo."
          );
          setLoading(false);
          return;
        }
      }
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

        <PodStatusPanel onWake={handleWakePod} waking={waking} />
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
        <main className="geracao-main">
          <ChatAssistant personas={personas} personaId={personaId} onUsePrompt={setPrompt} />
          <div className="geracao-row">
            <GenerationForm
              models={models}
              workflows={workflows}
              personas={personas}
              loading={loading}
              prompt={prompt}
              onPromptChange={setPrompt}
              personaId={personaId}
              onPersonaChange={setPersonaId}
              onSubmit={handleGenerate}
            />
            <ResultPanel result={result} error={error} />
          </div>
        </main>
      ) : (
        <PersonasView models={models} workflows={workflows} />
      )}
    </div>
  );
}
