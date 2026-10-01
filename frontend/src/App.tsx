import { useEffect, useState } from "react";
import type { GenerateResponse, HealthResponse, ModelInfo, PersonaSummary, WorkflowInfo } from "./api/client";
import {
  generateImage,
  uploadGenerationReference,
  getHealth,
  getModels,
  getPersona,
  getPersonas,
  getPodStatus,
  getSession,
  getWorkflows,
  logout,
  personaReferenceFileUrl,
  wakePod,
} from "./api/client";
import { useChatSession } from "./components/ChatAssistant";
import { GalleryPage } from "./components/GalleryPage";
import { LoginPage } from "./components/LoginPage";
import { GeneratePanel } from "./components/GeneratePanel";
import { HistoryPage } from "./components/HistoryPage";
import { PersonasView } from "./components/PersonasView";
import { ProfilePage } from "./components/ProfilePage";
import { ResultPanel } from "./components/ResultPanel";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar, type Tab } from "./components/Sidebar";
import { VoicePage } from "./components/VoicePage";
import { ContentPage } from "./components/ContentPage";
import { loadLastResult, saveLastResult } from "./lib/lastResult";
import { TopBar } from "./components/TopBar";
import { addHistoryEntry } from "./lib/history";
import { getSettings } from "./lib/settings";

const WAKE_POLL_INTERVAL_MS = 5_000;
// Pod novo: baixar a imagem, sincronizar o volume e subir Ollama + backend.
const WAKE_MAX_WAIT_MS = 10 * 60_000;
const THEME_STORAGE_KEY = "luna_theme";

// fetch rejeita com TypeError ("Failed to fetch") quando nem chega a falar
// com o backend - na pratica, o pod desligado ou ainda subindo. Qualquer
// outro erro (o backend respondeu com erro) passa com a mensagem original.
function describeLoadError(e: unknown): string {
  if (e instanceof TypeError) {
    return "Pod desligado ou ainda ligando: ligue o pod no topo da tela para carregar modelos, workflows e personas.";
  }
  return e instanceof Error ? e.message : String(e);
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Garante que o estudio esta pronto antes de gerar. Se o backend ja
// responde, retorna na hora; senao, liga (religa um pod parado ou cria um
// novo onde houver GPU - ver /api/runpod-wake) e fica consultando o status
// ate o backend responder. O proprio pod sobe Ollama + backend no boot
// (scripts/pod_autostart.sh), entao aqui so se espera.
// Retorna true se o estudio precisou ser ligado agora (um "cold start",
// onde listas de persona/modelo podem ter ficado desatualizadas e vale a
// pena recarrega-las antes de gerar).
async function ensurePodAwake(onMessage: (msg: string | null) => void): Promise<boolean> {
  try {
    const status = await getPodStatus();
    if (status.running && status.backendReady) return false;
  } catch {
    // segue e tenta ligar mesmo assim
  }

  try {
    const wake = await wakePod();
    onMessage(
      wake.action === "created"
        ? `Ligando uma GPU nova (${wake.dataCenterId ?? "RunPod"})... pode levar alguns minutos.`
        : "Ligando o estúdio... pode levar alguns minutos."
    );
  } catch (e) {
    onMessage(
      `Não foi possível ligar o estúdio automaticamente (${e instanceof Error ? e.message : e}). Tentando gerar mesmo assim...`
    );
    return false;
  }

  const deadline = Date.now() + WAKE_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    await sleep(WAKE_POLL_INTERVAL_MS);
    try {
      const status = await getPodStatus();
      if (status.running && status.backendReady) {
        onMessage("Estúdio pronto.");
        return true;
      }
      if (status.running) {
        onMessage("GPU ligada. Preparando chat e backend...");
      }
    } catch {
      // ignora falhas de polling isoladas e tenta de novo no proximo ciclo
    }
  }
  onMessage("O estúdio demorou mais que o esperado para ligar. Tentando gerar mesmo assim...");
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
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [tab, setTab] = useState<Tab>("gerar");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(loadTheme);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [personaThumbnails, setPersonaThumbnails] = useState<Record<string, string>>({});
  // A ultima imagem volta sozinha se a pagina recarregar (ex.: salvar no celular).
  const [saved] = useState(() => loadLastResult<Parameters<typeof generateImage>[0]>());
  const [result, setResult] = useState<GenerateResponse | null>(saved?.result ?? null);
  const [resultPrompt, setResultPrompt] = useState(saved?.prompt ?? "");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [podMessage, setPodMessage] = useState<string | null>(null);
  const [waking, setWaking] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [personaId, setPersonaId] = useState("");
  const chat = useChatSession();
  const [lastGenerateParams, setLastGenerateParams] = useState<Parameters<typeof generateImage>[0] | null>(
    saved?.params ?? null
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // ignora - preferencia nao critica
    }
  }, [theme]);

  useEffect(() => {
    getSession()
      .then((s) => setAuthenticated(s.authenticated))
      .finally(() => setAuthChecked(true));
  }, []);

  async function handleLogout() {
    await logout();
    setAuthenticated(false);
  }

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
    setLoadError(null);
    getHealth().then(setHealth).catch((e) => setLoadError(describeLoadError(e)));
    getModels().then(setModels).catch((e) => setLoadError(describeLoadError(e)));
    getWorkflows().then(setWorkflows).catch((e) => setLoadError(describeLoadError(e)));
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

  async function executeGenerate(
    body: Parameters<typeof generateImage>[0],
    displayPrompt: string,
    referenceFile?: File
  ) {
    setLoading(true);
    setError(null);
    setPodMessage(null);
    try {
      const wasColdStart = await ensurePodAwake(setPodMessage);
      if (wasColdStart) {
        const freshPersonas = await loadConfig();
        // Sem persona com a lista ainda vazia = a pessoa nao teve como
        // escolher; com a lista ja carregada, "Sem persona" e escolha dela.
        if (!body.persona_id && personas.length === 0 && freshPersonas.length > 0) {
          setPodMessage(
            "Estúdio ligado. A lista de personas acabou de carregar - escolha a Luna (ou \"Sem persona\") acima e clique em Gerar de novo."
          );
          setLoading(false);
          return;
        }
      }
      // A foto so pode subir com o pod ligado (vai para o ComfyUI dele); o
      // nome devolvido fica em body, entao "Gerar novamente" reaproveita.
      if (referenceFile) {
        body = { ...body, reference_image: await uploadGenerationReference(referenceFile) };
      }
      setLastGenerateParams(body);
      const res = await generateImage(body);
      setResult(res);
      setResultPrompt(displayPrompt);
      saveLastResult({ result: res, prompt: displayPrompt, params: body });
      const image = res.images[0];
      if (image) {
        const personaName = personas.find((p) => p.id === body.persona_id)?.name ?? null;
        addHistoryEntry({
          prompt: displayPrompt,
          personaId: body.persona_id || null,
          personaName,
          imageUrl: image.url,
          result: res,
          requestBody: body,
        });
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  function runGenerate(params: {
    prompt: string;
    displayPrompt: string;
    modelId: string;
    workflowId: string;
    personaId: string;
    width: number;
    height: number;
    steps: number;
    guidance: number;
    referenceFile?: File;
    denoise?: number;
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
        denoise: params.denoise,
      },
      params.displayPrompt,
      params.referenceFile
    );
  }

  function handleRegenerate() {
    if (lastGenerateParams) executeGenerate(lastGenerateParams, resultPrompt);
  }

  useEffect(() => {
    if (!podMessage || loading) return;
    const timer = setTimeout(() => setPodMessage(null), 8000);
    return () => clearTimeout(timer);
  }, [podMessage, loading]);

  const settings = getSettings();

  if (!authChecked) {
    return <div className="shell" style={{ background: "#05040a" }} />;
  }

  if (!authenticated) {
    return <LoginPage onReady={() => setAuthenticated(true)} />;
  }

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
          onOpenProfile={() => setTab("perfil")}
          onLogout={handleLogout}
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
                onEditPrompt={setPrompt}
                onRegenerate={handleRegenerate}
              />
            </div>
          )}

          {tab === "personas" && (
            <PersonasView
              models={models}
              workflows={workflows}
              onReferencesChanged={() => loadPersonaThumbnails(personas)}
            />
          )}
          {tab === "conteudo" && (
            <ContentPage
              personas={personas}
              ensureAwake={() => ensurePodAwake(setPodMessage)}
              onUsePhoto={(id, photoPrompt) => {
                setPersonaId(id);
                setPrompt(photoPrompt);
                setTab("gerar");
              }}
            />
          )}
          {tab === "voz" && <VoicePage personas={personas} ensureAwake={() => ensurePodAwake(setPodMessage)} />}
          {tab === "historico" && <HistoryPage />}
          {tab === "galeria" && <GalleryPage />}
          {tab === "configuracoes" && <SettingsPage models={models} workflows={workflows} health={health} />}
          {tab === "perfil" && <ProfilePage onLogout={handleLogout} />}
        </div>
      </div>
    </div>
  );
}
