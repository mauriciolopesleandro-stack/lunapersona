const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

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
  const res = await fetch(`${API_BASE}/health`);
  return handleResponse<HealthResponse>(res);
}

export async function getModels(): Promise<ModelInfo[]> {
  const res = await fetch(`${API_BASE}/models`);
  const data = await handleResponse<{ models: ModelInfo[] }>(res);
  return data.models;
}

export async function getWorkflows(): Promise<WorkflowInfo[]> {
  const res = await fetch(`${API_BASE}/workflows`);
  const data = await handleResponse<{ workflows: WorkflowInfo[] }>(res);
  return data.workflows;
}

export async function generateImage(body: GenerateRequestBody): Promise<GenerateResponse> {
  const res = await fetch(`${API_BASE}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<GenerateResponse>(res);
}

export async function getPersonas(): Promise<PersonaSummary[]> {
  const res = await fetch(`${API_BASE}/personas`);
  const data = await handleResponse<{ personas: PersonaSummary[] }>(res);
  return data.personas;
}

export async function getPersona(personaId: string): Promise<PersonaDetail> {
  const res = await fetch(`${API_BASE}/personas/${personaId}`);
  return handleResponse<PersonaDetail>(res);
}

export async function updatePersonaIdentity(
  personaId: string,
  body: { fixed?: Record<string, string>; variable_defaults?: Record<string, string> }
): Promise<PersonaDetail> {
  const res = await fetch(`${API_BASE}/personas/${personaId}/identity`, {
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
  const res = await fetch(`${API_BASE}/personas/${personaId}/generation`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<PersonaDetail>(res);
}

export async function uploadPersonaReference(personaId: string, file: File): Promise<PersonaReference> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/personas/${personaId}/references`, {
    method: "POST",
    body: formData,
  });
  return handleResponse<PersonaReference>(res);
}

export async function deletePersonaReference(personaId: string, referenceId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/personas/${personaId}/references/${referenceId}`, {
    method: "DELETE",
  });
  await handleResponse<{ deleted: string }>(res);
}

export function personaReferenceFileUrl(personaId: string, referenceId: string): string {
  return `${API_BASE}/personas/${personaId}/references/${referenceId}/file`;
}
