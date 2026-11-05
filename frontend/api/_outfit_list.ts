// Roupas de "Minhas roupas" da Naiaflow (05/11): o robo do PC le a galeria ao
// abrir (e a cada 6 h) e manda os nomes; o Telegram e o estudio mostram esta
// lista. Sem a lista ainda (robo antigo), vale a lista fixa de api/_outfits.ts.
import { OUTFITS } from "./_outfits.js";
import { storeConfigured, storeGet, storeSet } from "./_store.js";

const KEY = "luna:naia-outfits";
const MAX = 40;

// foto da roupa mandada no pedido (vai so para aquela dancinha, como a referencia)
export const OUTFIT_REF = /^refs\/[0-9a-f-]{36}\.jpg$/;

export async function outfitList(): Promise<string[]> {
  if (!storeConfigured()) return OUTFITS;
  try {
    const list = JSON.parse((await storeGet(KEY)) ?? "null") as unknown;
    if (Array.isArray(list)) {
      const names = list.filter((s): s is string => typeof s === "string" && s.trim().length > 0).slice(0, MAX);
      if (names.length) return names;
    }
  } catch {
    // lista estragada: usa a fixa
  }
  return OUTFITS;
}

export async function saveOutfitList(raw: unknown): Promise<string[]> {
  const names: string[] = [];
  for (const s of Array.isArray(raw) ? raw : []) {
    const name = typeof s === "string" ? s.trim().slice(0, 60) : "";
    if (name && !names.includes(name)) names.push(name);
  }
  if (names.length && storeConfigured()) await storeSet(KEY, JSON.stringify(names.slice(0, MAX)));
  return names.slice(0, MAX);
}
