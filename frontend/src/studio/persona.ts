// Varias personas no mesmo estudio (10/11/2026: Luna e Cleuza - Fase 1).
//
// A persona escolhida fica guardada neste navegador. Todo pedido do estudio
// ao servidor (/api/...) leva o cabecalho X-Persona, e o servidor separa os
// dados sozinho (api/_persona.ts). Trocar de persona recarrega a pagina, para
// nada da outra persona ficar na tela. Sem nada guardado: Luna, como sempre.

export interface PersonaInfo {
  id: string;
  name: string; // nome no estudio
  short: string; // nome curto nos textos ("Roupa da Luna", "Roupa da Cleuza")
  subtitle: string;
  avatar: string | null; // foto (null: a inicial)
}

export const PERSONAS: PersonaInfo[] = [
  { id: "luna", name: "Luna Vox", short: "Luna", subtitle: "Influenciadora Virtual", avatar: "/login/avatar-luna.webp" },
  { id: "cleuza", name: "Cleuza", short: "Cleuza", subtitle: "Coroa nordestina", avatar: null },
];

const KEY = "luna-studio-persona";
const DEFAULT = "luna";

function read(): string {
  try {
    const v = window.localStorage.getItem(KEY);
    return v && PERSONAS.some((p) => p.id === v) ? v : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

let current = typeof window === "undefined" ? DEFAULT : read();

export const personaId = () => current;
export const persona = (): PersonaInfo => PERSONAS.find((p) => p.id === current) ?? PERSONAS[0];
export const isDefaultPersona = () => current === DEFAULT;
export const personaShort = () => persona().short;

// Texto escrito para a Luna (pedidos que o estudio manda para a IA) virado para
// a persona aberta. Luna: igual, letra por letra.
export const personaClientText = (text: string) =>
  current === DEFAULT ? text : text.replace(/Luna Vox/g, persona().name).replace(/\bLuna\b/g, persona().short);

// Chaves do navegador que sao de uma persona so (conversas do chat, fotos ja
// pedidas, avisos limpos): a Luna usa as de sempre; as outras, com o nome no fim.
export const personaStorageKey = (key: string) => (current === DEFAULT ? key : `${key}:${current}`);

export function switchPersona(id: string): void {
  if (!PERSONAS.some((p) => p.id === id) || id === current) return;
  try {
    window.localStorage.setItem(KEY, id);
  } catch {
    // sem localStorage (aba anonima): vale so ate recarregar
  }
  current = id;
  window.location.reload();
}

// Pasta no PC: a Luna usa a pasta escolhida como sempre (conteudos/,
// luna-vox.json); as outras personas, uma subpasta com o nome delas
// (ex.: <pasta>/cleuza/conteudos/), criada sozinha.
export const personaFolder = () => (current === DEFAULT ? null : current);

// Todo fetch para /api/ deste site leva a persona (uma vez, no inicio do app).
let installed = false;
export function installPersonaFetch(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    try {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, window.location.href);
      if (url.origin === window.location.origin && url.pathname.startsWith("/api/")) {
        const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
        // (pedido que ja escolheu a persona fica com a dele)
        if (!headers.has("X-Persona")) headers.set("X-Persona", current);
        return original(input, { ...init, headers });
      }
    } catch {
      // endereco estranho: segue sem mexer
    }
    return original(input, init);
  };
}
