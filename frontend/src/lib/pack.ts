// Sequencia de fotos de uma historia (o "pack"): cada geracao nova entra no
// fim, numerada, e as anteriores continuam visiveis para conferir se a
// historia esta coerente. Fica no navegador ate a pessoa comecar um pack novo.
import type { GenerateResponse } from "../api/client";

const PACK_KEY = "luna_pack";

export interface PackItem {
  id: string;
  result: GenerateResponse;
  prompt: string;
  at: number;
}

export function loadPack(): PackItem[] {
  try {
    const raw = localStorage.getItem(PACK_KEY);
    const items = raw ? (JSON.parse(raw) as PackItem[]) : [];
    return Array.isArray(items) ? items : [];
  } catch {
    return [];
  }
}

export function savePack(items: PackItem[]) {
  try {
    localStorage.setItem(PACK_KEY, JSON.stringify(items));
  } catch {
    // navegador sem armazenamento: o pack so vale ate recarregar a pagina
  }
}

// As fotos ficam no volume, mas o endereco muda a cada pod novo
// (https://<pod>-8188.proxy.runpod.net): aponta todas para o pod atual.
const POD_ORIGIN = /^https:\/\/[a-z0-9]+-8188\.proxy\.runpod\.net/;

export function rehostPack(items: PackItem[], currentUrl: string): PackItem[] {
  const origin = currentUrl.match(POD_ORIGIN)?.[0];
  if (!origin) return items;
  return items.map((item) => ({
    ...item,
    result: {
      ...item.result,
      images: item.result.images.map((img) => ({ ...img, url: img.url.replace(POD_ORIGIN, origin) })),
    },
  }));
}
