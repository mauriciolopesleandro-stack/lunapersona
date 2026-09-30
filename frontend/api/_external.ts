// Estudio num servidor fixo fora da RunPod (ex: PC proprio com GPU exposto
// por um tunel https). Com LUNA_BACKEND_URL definida na Vercel, o site usa
// esse backend direto: nao lista, liga nem desliga pods da RunPod.
//
//   LUNA_BACKEND_URL=https://meu-pc.exemplo.com      (com ou sem /api no fim)
//
// Vazia = comportamento normal da RunPod.
export function externalApiBase(): string | null {
  const raw = (process.env.LUNA_BACKEND_URL ?? "").trim().replace(/\/+$/, "");
  if (!raw) return null;
  return raw.endsWith("/api") ? raw : `${raw}/api`;
}
