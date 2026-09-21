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
  duration_seconds: number;
  images: GenerationImage[];
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
