// Modo do estudio, escolhido no build (VITE_STUDIO_MODE na Vercel):
// - "manual" (padrao): as imagens e videos sao gerados fora e enviados a
//   mao. O app nao procura nem liga pod do RunPod, e as telas de geracao
//   ficam escondidas.
// - "gpu": o estudio antigo, com o pod do RunPod (login liga a GPU, telas
//   de geracao, status do pod no topo). Voltar para ele = mudar a variavel
//   para "gpu" e fazer um novo deploy; nenhum codigo foi apagado.
export const STUDIO_MODE: "manual" | "gpu" = import.meta.env.VITE_STUDIO_MODE === "gpu" ? "gpu" : "manual";

export const GPU_ENABLED = STUDIO_MODE === "gpu";
