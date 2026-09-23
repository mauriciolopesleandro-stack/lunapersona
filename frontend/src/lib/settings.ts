// Preferencias avancadas de geracao (modelo/workflow padrao, steps/guidance)
// guardadas no navegador - a tela de Gerar usa os defaults automaticamente,
// e a pagina de Configuracoes deixa ajustar sem poluir o fluxo principal.
const STORAGE_KEY = "luna_generation_settings";

export interface GenerationSettings {
  modelId: string;
  workflowId: string;
  steps: number;
  guidance: number;
}

export const DEFAULT_SETTINGS: GenerationSettings = {
  modelId: "",
  workflowId: "",
  steps: 26,
  guidance: 4.0,
};

export function getSettings(): GenerationSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: GenerationSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // ignora - preferencia nao critica
  }
}
