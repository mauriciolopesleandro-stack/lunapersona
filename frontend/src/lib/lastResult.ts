// Guarda no navegador a ultima imagem gerada (e o video feito dela) para
// sobreviver a um recarregamento da pagina - ex.: no celular, sair para salvar
// a foto e voltar. Os arquivos ficam no pod, entao so vale enquanto ele existir;
// passado MAX_AGE_MS, ou se a imagem nao carregar mais, some.
import type { GenerateResponse, VideoResponse } from "../api/client";

const RESULT_KEY = "luna_last_result";
const VIDEO_KEY = "luna_last_video";
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface SavedResult<P> {
  result: GenerateResponse;
  prompt: string;
  params: P | null;
}

function read<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const data = JSON.parse(raw) as { savedAt: number; value: T };
    if (Date.now() - data.savedAt > MAX_AGE_MS) {
      localStorage.removeItem(key);
      return null;
    }
    return data.value;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), value }));
  } catch {
    // navegador sem armazenamento: so nao restaura
  }
}

export function loadLastResult<P>(): SavedResult<P> | null {
  return read<SavedResult<P>>(RESULT_KEY);
}

export function saveLastResult<P>(saved: SavedResult<P> | null) {
  write(RESULT_KEY, saved);
}

// O video e ligado a imagem de onde saiu: outra imagem, outro video.
export function loadLastVideo(imageUrl: string): VideoResponse | null {
  const saved = read<{ imageUrl: string; video: VideoResponse }>(VIDEO_KEY);
  return saved && saved.imageUrl === imageUrl ? saved.video : null;
}

export function saveLastVideo(imageUrl: string, video: VideoResponse) {
  write(VIDEO_KEY, { imageUrl, video });
}
