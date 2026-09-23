import { useEffect, useState } from "react";
import type { GenerateResponse, HealthResponse, ModelInfo, PersonaSummary, WorkflowInfo } from "./api/client";
import {
  bootstrapPod,
  generateImage,
  getHealth,
  getModels,
  getPersona,
  getPersonas,
  getPodStatus,
  getWorkflows,
  personaReferenceFileUrl,
  wakePod,
} from "./api/client";
import { useChatSession } from "./components/ChatAssistant";
import { GalleryPage } from "./components/GalleryPage";
import { GeneratePanel } from "./components/GeneratePanel";
import { HistoryPage } from "./components/HistoryPage";
import { PersonasView } from "./components/PersonasView";
import { ResultPanel } from "./components/ResultPanel";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar, type Tab } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { addHistoryEntry, getHistory, type HistoryEntry } from "./lib/history";
import { getSettings } from "./lib/settings";

const WAKE_POLL_INTERVAL_MS = 5_000;
const WAKE_MAX_WAIT_MS = 3 * 60_000;
const BOOTSTRAP_RETRY_INTERVAL_MS = 8_000;
const BOOTSTRAP_MAX_ATTEMPTS = 5;
const THEME_STORAGE_KEY = "luna_theme";

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
      `Não foi possível ligar o pod automaticamente (${e instanceof Error ? e.message : e}). Tentando gerar mesmo assim...`
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
        onMessage("Pod ligado. Disparando preparação do chat e backend (git pull + Ollama)...");
        try {
          await runBootstrapWithRetry(onMessage);
          onMessage("Preparação disparada - chat e geração devem ficar prontos em 1-2 minutos.");
        } catch (e) {
          onMessage(
            `Pod ligado, mas não consegui disparar a preparação automaticamente (${e instanceof Error ? e.message : e}). Chat/geração podem levar mais um pouco para responder.`
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

function loadTheme(): "dark" | "light" {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    return saved === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>("gerar");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(loadTheme);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [personaThumbnails, setPersonaThumbnails] = useState<Record<string, string>>({});
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [resultPrompt, setResultPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [podMessage, setPodMessage] = useState<string | null>(null);
  const [waking, setWaking] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [personaId, setPersonaId] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const chat = useChatSession();
  const [lastGenerateParams, setLastGenerateParams] = useState<Parameters<typeof generateImage>[0] | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // ignora - preferencia nao critica
    }
  }, [theme]);

  useEffect(() => {
    setHistory(getHistory());
  }, []);

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
        loadPersonaThumbnails(list);
        return list;
      })
      .catch(() => [] as PersonaSummary[]);
  }

  function loadPersonaThumbnails(list: PersonaSummary[]) {
    list.forEach((p) => {
      getPersona(p.id)
        .then((detail) => {
          const primary = detail.references.find((r) => r.is_primary) ?? detail.references[0];
          if (!primary) return;
          setPersonaThumbnails((prev) => ({ ...prev, [p.id]: personaReferenceFileUrl(p.id, primary.id) }));
        })
        .catch(() => {
          // sem foto de referencia ainda - card mostra so o nome
        });
    });
  }

  useEffect(() => {
    loadConfig();
  }, []);

  async function executeGenerate(body: Parameters<typeof generateImage>[0], displayPrompt: string) {
    setLoading(true);
    setError(null);
    setPodMessage(null);
    try {
      const wasColdStart = await ensurePodAwake(setPodMessage);
      if (wasColdStart) {
        const freshPersonas = await loadConfig();
        if (!body.persona_id && freshPersonas.length > 0) {
          setPodMessage(
            "Pod ligado. A lista de personas acabou de carregar - selecione a Luna acima e clique em Gerar de novo."
          );
          setLoading(false);
          return;
        }
      }
      setLastGenerateParams(body);
      const res = await generateImage(body);
      setResult(res);
      setResultPrompt(displayPrompt);
      const image = res.images[0];
      if (image) {
        const personaName = personas.find((p) => p.id === body.persona_id)?.name ?? null;
        const entry = addHistoryEntry({
          prompt: displayPrompt,
          personaId: body.persona_id || null,
          personaName,
          imageUrl: image.url,
          result: res,
          requestBody: body,
        });
        setHistory(getHistory());
        setActiveHistoryId(entry.id);
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  function runGenerate(params: {
    prompt: string;
    modelId: string;
    workflowId: string;
    personaId: string;
    width: number;
    height: number;
    steps: number;
    guidance: number;
  }) {
    return executeGenerate(
      {
        prompt: params.prompt,
        model_id: params.modelId,
        workflow_id: params.workflowId,
        persona_id: params.personaId || undefined,
        width: params.width,
        height: params.height,
        steps: params.steps,
        guidance: params.guidance,
      },
      params.prompt
    );
  }

  function handleRegenerate() {
    if (lastGenerateParams) executeGenerate(lastGenerateParams, resultPrompt);
  }

  function handleSelectHistoryEntry(entry: HistoryEntry) {
    setResult(entry.result);
    setResultPrompt(entry.prompt);
    setActiveHistoryId(entry.id);
    setLastGenerateParams(entry.requestBody);
    // Sem isso, o card de persona na tela de Gerar ficava com a Luna
    // marcada mesmo depois de abrir um item do historico sem persona (ou de
    // outra persona) - "Gerar novamente" usava o persona_id do historico,
    // divergindo do que a tela mostrava selecionado.
    setPersonaId(entry.requestBody.persona_id || "");
  }

  useEffect(() => {
    if (!podMessage || loading) return;
    const timer = setTimeout(() => setPodMessage(null), 8000);
    return () => clearTimeout(timer);
  }, [podMessage, loading]);

  const settings = getSettings();

  return (
    <div className="shell">
      {sidebarOpen && (
        <div
          onClick={() => setSidebarOpen(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 20 }}
        />
      )}
      <Sidebar tab={tab} onTabChange={setTab} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="main-col">
        <TopBar
          onWake={handleWakePod}
          waking={waking}
          theme={theme}
          onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          onOpenSidebar={() => setSidebarOpen(true)}
        />

        <div className="page">
          {podMessage && <p className="page-toast">{podMessage}</p>}
          {loadError && <p className="error small" style={{ marginBottom: 12 }}>{loadError}</p>}
          {health && !health.comfyui.ok && (
            <p className="error small" style={{ marginBottom: 12 }}>
              {health.comfyui.message}
            </p>
          )}

          {tab === "gerar" && (
            <div className="generate-grid">
              <GeneratePanel
                personas={personas}
                personaThumbnails={personaThumbnails}
                models={models}
                workflows={workflows}
                loading={loading}
                prompt={prompt}
                onPromptChange={setPrompt}
                personaId={personaId}
                onPersonaChange={setPersonaId}
                settings={settings}
                onNavigatePersonas={() => setTab("personas")}
                chat={chat}
                onSubmit={runGenerate}
              />
              <ResultPanel
                result={result}
                error={error}
                loading={loading}
                resultPrompt={resultPrompt}
                history={history}
                activeHistoryId={activeHistoryId}
                onEditPrompt={setPrompt}
                onRegenerate={handleRegenerate}
                onSelectHistoryEntry={handleSelectHistoryEntry}
              />
            </div>
          )}

          {tab === "personas" && <PersonasView models={models} workflows={workflows} />}
          {tab === "historico" && <HistoryPage />}
          {tab === "galeria" && <GalleryPage />}
          {tab === "configuracoes" && <SettingsPage models={models} workflows={workflows} health={health} />}
        </div>
      </div>
    </div>
  );
}
