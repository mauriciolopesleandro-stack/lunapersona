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
  // Nome devolvido por uploadGenerationReference: a cena da foto e mantida
  // e a pessoa vira a persona. denoise = quanto a foto e redesenhada.
  reference_image?: string;
  denoise?: number;
  // Pack: troca so a pessoa da foto de referencia (o resto fica identico).
  person_swap?: boolean;
  // Pack: "recreate" redesenha a foto inteira; "swap" troca so a pessoa.
  pack_mode?: "recreate" | "swap";
  // Historia: rosto de referencia do outro personagem ("nome [output]") e a
  // descricao dele - o rosto dele fica igual em todas as fotos.
  other_face_ref?: string;
  other_face_prompt?: string;
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

export async function uploadGenerationReference(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${await apiBase()}/generate/reference`, { method: "POST", body: formData });
  const data = await handleResponse<{ name: string }>(res);
  return data.name;
}

const GENERATE_POLL_MS = 3_000;
const GENERATE_MAX_MS = 15 * 60_000;
// Falhas de rede seguidas toleradas ao consultar (proxy instavel, celular
// trocando de rede) antes de desistir.
const GENERATE_MAX_POLL_FAILURES = 5;

// O proxy da RunPod corta respostas com mais de ~100 s e uma geracao numa L4
// passa disso: inicia um job no backend e consulta ate ele terminar.
async function runJob<T>(path: string, body: unknown, maxMs: number, what: string, pollPath = path): Promise<T> {
  const base = await apiBase();
  const start = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const { job_id } = await handleResponse<{ job_id: string }>(start);

  const deadline = Date.now() + maxMs;
  let failures = 0;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, GENERATE_POLL_MS));
    let res: Response | null = null;
    try {
      res = await fetch(`${base}${pollPath}/${job_id}`);
    } catch {
      // sem resposta: rede do celular ou proxy - tenta de novo abaixo
    }
    // 5xx aqui e o proxy da RunPod oscilando, nao o job: tambem tenta de novo.
    if (!res || res.status >= 500) {
      if (++failures >= GENERATE_MAX_POLL_FAILURES) {
        throw new Error(`Perdi a conexao com o backend durante ${what}. Tente novamente.`);
      }
      continue;
    }
    failures = 0;
    const job = await handleResponse<
      { status: "running" } | { status: "done"; result: T } | { status: "error"; detail: string }
    >(res);
    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.detail);
  }
  throw new Error(`Passou de ${Math.round(maxMs / 60_000)} minutos esperando ${what}. Tente novamente.`);
}

export function generateImage(body: GenerateRequestBody): Promise<GenerateResponse> {
  return runJob<GenerateResponse>("/generate/jobs", body, GENERATE_MAX_MS, "a geracao");
}

export interface VideoRequestBody {
  image: string;
  image_type?: "output" | "input";
  image_subfolder?: string;
  prompt: string;
  seconds: number;
  quality: "480p" | "720p";
  source_width?: number;
  source_height?: number;
  // Continuar um video anterior: o novo trecho parte do ultimo quadro dele.
  continue_video?: string;
  continue_last_frame?: string;
  continue_width?: number;
  continue_height?: number;
  continue_seconds?: number;
}

export interface VideoResponse {
  prompt_id: string;
  seconds: number;
  width: number;
  height: number;
  duration_seconds: number;
  videos: GenerationImage[];
  last_frame?: GenerationImage | null;
  // Movimento usado: o digitado (traduzido) ou o criado pela IA olhando a foto.
  motion?: string;
  // Troca sem foto: a persona criada a partir do 1o quadro do video.
  reference?: GenerationImage | null;
}

export interface TalkRequestBody {
  persona_id: string;
  image: string;
  image_type?: "output" | "input";
  image_subfolder?: string;
  text: string;
  extra_prompt?: string;
  quality: "480p" | "720p";
  source_width?: number;
  source_height?: number;
}

// Persona falando: voz dela + video com a boca sincronizada (~10 min a cada
// 5 s de fala). A duracao sai do tamanho do texto.
export function talkVideo(body: TalkRequestBody): Promise<VideoResponse> {
  return runJob<VideoResponse>("/video/talk/jobs", body, 60 * 60_000, "o video falado", "/video/jobs");
}

// Troca de personagem: o video vai direto para o pod (pode ter centenas de MB).
export async function uploadSwapVideo(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${await apiBase()}/video/swap/upload`, { method: "POST", body: formData });
  return (await handleResponse<{ name: string }>(res)).name;
}

export interface SwapRequestBody {
  video: string;
  // Vazio: a persona e criada a partir do video (com persona_id).
  image: string;
  persona_id?: string;
  image_type?: "output" | "input";
  image_subfolder?: string;
  prompt?: string;
  quality: "480p" | "720p";
  max_seconds: number;
}

// Troca em etapas: quadros principais do video -> a persona em cada um
// (aprovar/corrigir) -> so entao o video, com a foto escolhida.
export interface SwapKeyframes {
  frames: GenerationImage[];
  seconds: number;
  width: number;
  height: number;
}

export async function swapKeyframes(video: string, maxSeconds: number): Promise<SwapKeyframes> {
  const res = await fetch(`${await apiBase()}/video/swap/keyframes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video, max_seconds: maxSeconds }),
  });
  return handleResponse<SwapKeyframes>(res);
}

// Conferencia da IA: a foto bate com o quadro (roupa, pose, cenario)?
// coherent null = nao deu para conferir.
export interface FrameCheck {
  coherent: boolean | null;
  problems_pt: string;
  fix_en: string;
  attempts?: number;
}

export async function personaInFrame(body: {
  persona_id: string;
  frame: string;
  width: number;
  height: number;
  extra?: string;
  seed?: number;
}): Promise<{ image: GenerationImage; check: FrameCheck }> {
  return runJob<{ image: GenerationImage; check: FrameCheck }>(
    "/video/swap/reference/jobs",
    body,
    20 * 60_000,
    "a foto da persona"
  );
}

// ~12 min (480p) a 25 min (720p) a cada 5 s de video, mais a foto automatica.
export function swapVideo(body: SwapRequestBody): Promise<VideoResponse> {
  const perFiveMs = (body.quality === "720p" ? 30 : 15) * 60_000;
  const maxMs = perFiveMs * Math.ceil(body.max_seconds / 5) + 15 * 60_000;
  return runJob<VideoResponse>("/video/swap/jobs", body, maxMs, "a troca de personagem", "/video/jobs");
}

// Cada 5 s de video levam alguns minutos (mais em 720p).
export function animateImage(body: VideoRequestBody): Promise<VideoResponse> {
  const perSegmentMs = (body.quality === "720p" ? 20 : 10) * 60_000;
  return runJob<VideoResponse>("/video/jobs", body, perSegmentMs * Math.ceil(body.seconds / 5), "o video");
}

export interface PersonaVoice {
  file: string;
  text: string;
  description: string;
}

export interface VoiceOption extends GenerationImage {
  seed: number;
}

export interface VoiceDesignResponse {
  text: string;
  instruct: string;
  options: VoiceOption[];
  duration_seconds: number;
}

export interface VoiceSpeakResponse {
  text: string;
  audios: GenerationImage[];
  duration_seconds: number;
}

// Criar voz gera 3 opcoes (a 1a vez tambem carrega o modelo na GPU).
export function designVoice(description: string, text: string): Promise<VoiceDesignResponse> {
  return runJob<VoiceDesignResponse>("/voice/design/jobs", { description, text }, 10 * 60_000, "a voz", "/voice/jobs");
}

export function speakWithVoice(personaId: string, text: string): Promise<VoiceSpeakResponse> {
  return runJob<VoiceSpeakResponse>(
    "/voice/speak/jobs",
    { persona_id: personaId, text },
    10 * 60_000,
    "a fala",
    "/voice/jobs"
  );
}

export async function getPersonaVoice(personaId: string): Promise<PersonaVoice | null> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/voice`);
  return (await handleResponse<{ voice: PersonaVoice | null }>(res)).voice;
}

export async function personaVoiceFileUrl(personaId: string): Promise<string> {
  return `${await apiBase()}/personas/${personaId}/voice/file`;
}

export async function savePersonaVoice(
  personaId: string,
  body: { filename: string; subfolder: string; text: string; description: string }
): Promise<PersonaVoice> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/voice`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await handleResponse<{ voice: PersonaVoice }>(res)).voice;
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

// O assistente raciocina antes de responder (entende melhor pedidos longos):
// passa do limite de ~100 s do proxy, entao vira job e o site consulta.
export function sendChatMessage(personaId: string | undefined, messages: ChatMessage[]): Promise<ChatReply> {
  return runJob<ChatReply>("/chat/jobs", { persona_id: personaId || undefined, messages }, 10 * 60_000, "a resposta");
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

// Assistente de conteudo da persona (aba Conteudo).
export interface ContentProfile {
  bio: string;
  personalidade: string;
  jeito_de_falar: string;
  publico: string;
  redes: string;
  nicho: string;
  limites: string;
}

export interface ContentMemory {
  text: string;
  at: number;
}

export interface ContentMessage {
  role: "user" | "assistant";
  content: string;
  at: number;
}

export interface ContentReply {
  reply: string;
  model: string;
  memory_added: string[];
}

export async function getContentProfile(personaId: string): Promise<ContentProfile> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/content/profile`);
  return (await handleResponse<{ profile: ContentProfile }>(res)).profile;
}

export async function saveContentProfile(personaId: string, profile: ContentProfile): Promise<ContentProfile> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/content/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
  return (await handleResponse<{ profile: ContentProfile }>(res)).profile;
}

export async function getContentMemory(personaId: string): Promise<ContentMemory[]> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/content/memory`);
  return (await handleResponse<{ memory: ContentMemory[] }>(res)).memory;
}

export async function deleteContentMemory(personaId: string, index: number): Promise<ContentMemory[]> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/content/memory/${index}`, { method: "DELETE" });
  return (await handleResponse<{ memory: ContentMemory[] }>(res)).memory;
}

export async function getContentConversation(personaId: string): Promise<ContentMessage[]> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/content/conversation`);
  return (await handleResponse<{ messages: ContentMessage[] }>(res)).messages;
}

export async function clearContentConversation(personaId: string): Promise<void> {
  const res = await fetch(`${await apiBase()}/personas/${personaId}/content/conversation`, { method: "DELETE" });
  await handleResponse<unknown>(res);
}

export function sendContentMessage(personaId: string, text: string): Promise<ContentReply> {
  return runJob<ContentReply>(
    `/personas/${personaId}/content/jobs`,
    { text },
    5 * 60_000,
    "a resposta",
    "/content/jobs"
  );
}

// Historia em fotos: o modelo de chat do pod planeja a serie (biblia fixa +
// um prompt por foto). As fotos saem pela geracao normal (generateImage).
export interface StoryScene {
  title: string;
  summary: string;
  prompt: string;
}

export interface StoryPlan {
  bible: {
    locations?: string[];
    outfits?: string[];
    characters?: string[];
    light?: string;
    camera?: string;
  };
  scenes: StoryScene[];
  model: string;
}

export function planStory(personaId: string, story: string, count: number, shape = "9:16"): Promise<StoryPlan> {
  return runJob<StoryPlan>(`/personas/${personaId}/story/jobs`, { story, count, shape }, 15 * 60_000, "o planejamento", "/story/jobs");
}

// Pack de fotos -> historia: as fotos (ja enviadas por uploadGenerationReference)
// sao descritas e viram cenas novas, geradas do zero com a persona.
export function planStoryFromPhotos(personaId: string, images: string[], shape = "9:16"): Promise<StoryPlan> {
  return runJob<StoryPlan>(
    `/personas/${personaId}/story/from-photos/jobs`,
    { images, shape },
    25 * 60_000,
    "a leitura das fotos",
    "/story/jobs"
  );
}
