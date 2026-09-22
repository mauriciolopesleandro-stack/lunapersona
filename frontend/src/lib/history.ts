// Historico de geracoes guardado no navegador (localStorage). O backend nao
// tem um endpoint de "listar geracoes passadas" ainda, entao isso e o que
// alimenta as paginas de Historico/Galeria e a tira de "Historico recente"
// na tela de Gerar - fica so neste navegador, nao sincroniza entre
// dispositivos nem sobrevive a limpar dados do site.
import type { GenerateRequestBody, GenerateResponse } from "../api/client";

const STORAGE_KEY = "luna_generation_history";
const MAX_ENTRIES = 200;

export interface HistoryEntry {
  id: string;
  createdAt: number;
  prompt: string;
  personaId: string | null;
  personaName: string | null;
  imageUrl: string;
  result: GenerateResponse;
  requestBody: GenerateRequestBody;
}

function readAll(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeAll(entries: HistoryEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
  } catch {
    // localStorage indisponivel (modo privado, cota cheia, etc.) - ignora
  }
}

export function getHistory(): HistoryEntry[] {
  return readAll().sort((a, b) => b.createdAt - a.createdAt);
}

export function addHistoryEntry(entry: Omit<HistoryEntry, "id" | "createdAt">): HistoryEntry {
  const full: HistoryEntry = {
    ...entry,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    createdAt: Date.now(),
  };
  writeAll([full, ...readAll()]);
  return full;
}

export function removeHistoryEntry(id: string) {
  writeAll(readAll().filter((e) => e.id !== id));
}

export function clearHistory() {
  writeAll([]);
}
