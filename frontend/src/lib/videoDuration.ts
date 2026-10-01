// Duracao real de um video escolhido no aparelho (antes de subir), em segundos.
export function videoDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const el = document.createElement("video");
    el.preload = "metadata";
    const done = (value: number) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    el.onloadedmetadata = () => done(Number.isFinite(el.duration) ? el.duration : 0);
    el.onerror = () => done(0);
    el.src = url;
  });
}

// Troca de personagem: limite do backend (swap_service.MAX_SECONDS).
export const SWAP_MAX_SECONDS = 30;

// Segundos a pedir para usar o video inteiro (ou o maximo, se for maior).
export function swapSecondsFor(duration: number): number {
  if (!duration) return SWAP_MAX_SECONDS; // desconhecida: o backend para no fim do video
  return Math.max(2, Math.min(SWAP_MAX_SECONDS, Math.ceil(duration)));
}
