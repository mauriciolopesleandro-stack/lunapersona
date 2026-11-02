// Opcoes da dancinha e do video longo usadas no estudio e no bot do Telegram
// (sem dependencia: o navegador tambem importa)

// roupas salvas na galeria da Naiaflow ("Minhas roupas"); o robo procura pelo nome
export const OUTFITS = [
  "Biquíni animal print",
  "Biquíni azul dourado",
  "Biquíni branco",
  "Body rendado vinho",
  "Conjunto casual",
  "Conjunto azul marinho",
  "Conjunto cropped jeans",
  "Conjunto biquíni crochê",
  "Conjunto estampado",
];

// video longo +18: trechos de 5 s (o mesmo de api/_plans.ts LONG_SEGMENT)
const LONG_PIECE = 5;
export const CUT_SECONDS = [5, 10, 15]; // so um pedaco do video longo (02/11)
export const longPieces = (duration: number) => Math.max(1, Math.ceil(duration / LONG_PIECE - 0.02));
// pedaco do video longo: do segundo "from", "seconds" arredondado para cima em
// trechos de 5 s; o comeco recua se passar do fim; o video todo = completo (undefined)
export function longCut(duration: number, from: number, seconds: number): { from: number; seconds: number } | undefined {
  const size = Math.max(1, Math.ceil(seconds / LONG_PIECE)) * LONG_PIECE;
  if (size / LONG_PIECE >= longPieces(duration)) return undefined;
  return { from: Math.min(Math.max(0, Math.round(from)), Math.max(0, Math.floor(duration - size))), seconds: size };
}

// link que da para usar no Copiar movimento (video de rede social)
export const DANCE_LINK = /https:\/\/(?:www\.|vm\.|vt\.|m\.)?(?:tiktok\.com|instagram\.com\/(?:reel|reels|p)\/|youtube\.com\/shorts\/|youtu\.be\/|x\.com\/|twitter\.com\/)\S*/i;
