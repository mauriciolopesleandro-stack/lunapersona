// Endereco do backend. O pod do estudio muda (sobe em outra maquina ou no
// outro volume quando falta GPU), entao em producao o endereco vem de
// /api/runpod-status (apiBase do pod atual). VITE_API_BASE_URL so vale como
// reserva - ex: rodando local, onde /api/runpod-status nao existe.
const FALLBACK_API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";
let podApiBase: string | null = null;
let apiBaseLookup: Promise<void> | null = null;

function currentApiBase(): string {
  return podApiBase ?? FALLBACK_API_BASE;
}

async function apiBase(): Promise<string> {
  if (!podApiBase) {
    apiBaseLookup ??= getPodStatus()
      .then(() => undefined)
      .catch(() => undefined)
      .finally(() => {
        apiBaseLookup = null;
      });
    await apiBaseLookup;
  }
  return currentApiBase();
}

// --- Autenticacao (funcoes serverless da Vercel, nao do backend no pod -
// precisa funcionar mesmo com o pod desligado) --------------------------

export async function getSession(): Promise<{ authenticated: boolean }> {
  const res = await fetch("/api/session");
  if (!res.ok) return { authenticated: false };
  return res.json();
}

export async function login(username: string, password: string, remember: boolean): Promise<void> {
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, remember }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Erro HTTP ${res.status}`);
  }
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST" });
}

export interface Profile {
  email: string;
  canChangePassword: boolean;
  minPasswordLength: number;
}

export async function getProfile(): Promise<Profile> {
  const res = await fetch("/api/profile");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `Erro HTTP ${res.status}`);
  return body;
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  const res = await fetch("/api/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ currentPassword, newPassword }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Erro HTTP ${res.status}`);
  }
}

export interface HealthResponse {
  backend: string;
  comfyui: {
    ok: boolean;
    message: string;
    stats: Record<string, unknown>;
  };
}

export interface ModelInfo {
  id: string;
  name: string;
  engine: string;
  type: string;
  status: string;
  defaults: Record<string, number | string>;
  notes: string;
}

export interface WorkflowInfo {
  id: string;
  title: string;
  description: string;
  compatible_models: string[];
}

export interface GenerationImage {
  filename: string;
  subfolder: string;
  type: string;
  url: string;
}

export interface GenerateRequestBody {
  prompt: string;
  model_id?: string;
  workflow_id?: string;
  persona_id?: string;
  width?: number;
  height?: number;
  steps?: number;
  guidance?: number;
  seed?: number;
}

export interface GenerateResponse {
  prompt_id: string;
  model_id: string;
  workflow_id: string;
  persona_id: string | null;
  duration_seconds: number;
  images: GenerationImage[];
}

export const FIXED_IDENTITY_FIELDS = [
  "formato_rosto",
  "caracteristicas_faciais",
  "olhos",
  "sobrancelhas",
  "nariz",
  "boca",
  "formato_cabelo",
  "cor_cabelo",
  "textura_cabelo",
  "tom_pele",
  "caracteristicas_corporais",
  "caracteristicas_visuais_permanentes",
] as const;

export const VARIABLE_DEFAULT_FIELDS = [
  "roupa",
  "cenario",
  "iluminacao",
  "pose",
  "expressao",
  "camera",
] as const;

export interface PersonaSummary {
  id: string;
  name: string;
  description: string;
  reference_count: number;
}

export interface PersonaReference {
  id: string;
  filename: string;
  original_filename: string;
  uploaded_at: string;
  label: string;
  is_primary: boolean;
}

export interface PersonaDetail {
  id: string;
  name: string;
  description: string;
  identity: {
    fixed: Record<string, string>;
    variable_defaults: Record<string, string>;
  };
  generation: {
    model_id: string;
    workflow_id: string;
  };
  references: PersonaReference[];
  identity_methods: {
    planned: string[];
    active: string[];
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? `Erro HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${await apiBase()}/health`);
  return handleResponse<HealthResponse>(res);
}

export async function getModels(): Promise<ModelInfo[]> {
  const res = await fetch(`${await apiBase()}/models`);
  const data = await handleResponse<{ models: ModelInfo[] }>(res);
  return data.models;
}

export async function getWorkflows(): Promise<WorkflowInfo[]> {
  const res = await fetch(`${await apiBase()}/workflows`);
  const data = await handleResponse<{ workflows: WorkflowInfo[] }>(res);
  return data.workflows;
}

export async function generateImage(body: GenerateRequestBody): Promise<GenerateResponse> {
  const res = await fetch(`${await apiBase()}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<GenerateResponse>(res);
}

export async function getPersonas(): Promise<PersonaSummary[]> {
  const res = await fetch(`${await apiBase()}/personas`);
  const data = await handleResponse<{ personas: PersonaSummary[] }>(res);
  return data.personas;
}

export async function getPersona(personaId: string): Promise<PersonaDetail> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}`);
  return handleResponse<PersonaDetail>(res);
}

export async function updatePersonaIdentity(
  personaId: string,
  body: { fixed?: Record<string, string>; variable_defaults?: Record<string, string> }
): Promise<PersonaDetail> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/identity`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<PersonaDetail>(res);
}

export async function updatePersonaGeneration(
  personaId: string,
  body: { model_id?: string; workflow_id?: string }
): Promise<PersonaDetail> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/generation`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<PersonaDetail>(res);
}

export async function uploadPersonaReference(personaId: string, file: File): Promise<PersonaReference> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${await apiBase()}/personas/${personaId}/references`, {
    method: "POST",
    body: formData,
  });
  return handleResponse<PersonaReference>(res);
}

export async function deletePersonaReference(personaId: string, referenceId: string): Promise<void> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/references/${referenceId}`, {
    method: "DELETE",
  });
  await handleResponse<{ deleted: string }>(res);
}

export async function setPrimaryPersonaReference(personaId: string, referenceId: string): Promise<PersonaReference[]> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/references/${referenceId}/primary`, {
    method: "PUT",
  });
  const data = await handleResponse<{ references: PersonaReference[] }>(res);
  return data.references;
}

export function personaReferenceFileUrl(personaId: string, referenceId: string): string {
  return `${currentApiBase()}/personas/${personaId}/references/${referenceId}/file`;
}

// --- Chat (assistente de criacao de prompts) --------------------------
// Roda no backend (pod), diferente do runpod-status/wake que rodam na
// Vercel - por isso usa apiBase() como as outras chamadas ao backend.

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

// O nome do modelo e definido so no backend (LLM_MODEL); o frontend apenas
// o recebe de volta para exibir qual modelo respondeu.
export interface ChatReply extends ChatMessage {
  model?: string;
}

export async function sendChatMessage(
  personaId: string | undefined,
  messages: ChatMessage[]
): Promise<ChatReply> {
  let res: Response;
  try {
    res = await fetch(`${await apiBase()}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId || undefined, messages }),
    });
  } catch {
    // fetch so rejeita sem resposta HTTP: pod desligado, backend reiniciando
    // ou o proxy da RunPod cortou a conexao (~100s) sem cabecalho CORS.
    throw new Error(
      "Sem resposta do backend. O pod pode estar desligado ou o backend reiniciando - " +
        "ou a resposta passou do limite de ~100s do proxy da RunPod. Tente novamente em instantes."
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail = "";
    try {
      detail = JSON.parse(text).detail ?? "";
    } catch {
      // resposta nao-JSON (ex: pagina HTML de erro do proxy)
    }
    if (!detail && (res.status === 502 || res.status === 504 || res.status === 524)) {
      detail = "O proxy da RunPod nao obteve resposta do backend a tempo. Tente novamente em instantes.";
    }
    throw new Error(`Erro ${res.status} no chat: ${detail || res.statusText || "sem detalhes"}`);
  }
  return res.json() as Promise<ChatReply>;
}

// --- Status/ligar o pod RunPod --------------------------------------------
// Essas duas chamam funcoes serverless da propria Vercel (nao o backend no
// pod), entao usam caminho relativo em vez de apiBase(): precisam responder
// mesmo com o pod desligado.

export interface PodStatus {
  running: boolean;
  // O pod pode estar "running" minutos antes do backend responder (imagem
  // baixando, autostart sincronizando o volume e subindo Ollama/backend).
  backendReady: boolean;
  desiredStatus: string;
  podId: string | null;
  dataCenterId: string | null;
  gpu: string | null;
  apiBase: string | null;
  costPerHr: number;
  uptimeSeconds: number;
  liveSpend: number;
  balance: number;
}

export async function getPodStatus(): Promise<PodStatus> {
  const res = await fetch("/api/runpod-status");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? body.detail ?? `Erro HTTP ${res.status}`);
  }
  const status = (await res.json()) as PodStatus;
  if (status.apiBase) podApiBase = status.apiBase;
  return status;
}

export interface WakeResult {
  alreadyRunning: boolean;
  action: "running" | "resumed" | "created" | "pending";
  podId: string | null;
  dataCenterId: string | null;
}

export async function wakePod(): Promise<WakeResult> {
  const res = await fetch("/api/runpod-wake", { method: "POST" });
  if (!res.ok) {
    // runpod-wake.ts devolve {error: "..."} (nao {detail: "..."} como o
    // resto da API) - sem isso, a mensagem real da RunPod (ex: "nao ha
    // instancias disponiveis") virava um generico "Erro HTTP 502".
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? body.detail ?? `Erro HTTP ${res.status}`);
  }
  return res.json();
}

export async function stopPodRequest(): Promise<{ alreadyStopped: boolean }> {
  const res = await fetch("/api/runpod-stop", { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Erro HTTP ${res.status}`);
  }
  return res.json();
}

export interface BootstrapResult {
  ok: boolean;
  exitCode: number | null;
  stdout: string;
  stderr: string;
}

// Roda o bootstrap (Ollama + backend) dentro do pod via SSH. So faz sentido
// chamar depois que o pod ja esta "running" (ver ensurePodAwake em App.tsx).
export async function bootstrapPod(): Promise<BootstrapResult> {
  const res = await fetch("/api/runpod-bootstrap", { method: "POST" });
  return handleResponse<BootstrapResult>(res);
}
