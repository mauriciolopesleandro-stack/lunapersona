import { aspectRatio, type Aspect } from "../../api/_flags";

// Prepara as midias para a publicacao automatica no Instagram: a API aceita
// foto so em JPEG; outros formatos sao convertidos no navegador (o original na
// pasta nao e tocado). Videos vao como estao (MP4/MOV).
const MAX_SIDE = 1440;
const MAX_BYTES = 50 * 1024 * 1024;

export function prepareForInstagram(file: File, aspect?: Aspect): Promise<{ blob: Blob; mime: string }> {
  return prepare(file, "Instagram", (w, h) => MAX_SIDE / Math.max(w, h), 0, aspect);
}

// TikTok: foto em JPEG de no maximo 1080 x 1920 (retrato) ou 1920 x 1080
// (paisagem) e no minimo 360 px de cada lado; video igual ao do Instagram.
export function prepareForTiktok(file: File, aspect?: Aspect): Promise<{ blob: Blob; mime: string }> {
  return prepare(file, "TikTok", (w, h) => Math.min(1080 / Math.min(w, h), 1920 / Math.max(w, h)), 360, aspect);
}

// Recorte central para a proporcao pedida (aspect) - so fotos.
function cropRect(w: number, h: number, aspect?: Aspect) {
  if (!aspect) return { sx: 0, sy: 0, sw: w, sh: h };
  const r = aspectRatio(aspect);
  if (w / h > r) {
    const sw = Math.round(h * r);
    return { sx: Math.round((w - sw) / 2), sy: 0, sw, sh: h };
  }
  const sh = Math.round(w / r);
  return { sx: 0, sy: Math.round((h - sh) / 2), sw: w, sh };
}

async function prepare(
  file: File,
  label: string,
  fit: (w: number, h: number) => number,
  minSide = 0,
  aspect?: Aspect
): Promise<{ blob: Blob; mime: string }> {
  if (file.type.startsWith("video/")) {
    const mime = file.type === "video/quicktime" ? "video/quicktime" : "video/mp4";
    if (!/\.(mp4|mov|m4v)$/i.test(file.name)) throw new Error(`“${file.name}”: o ${label} aceita vídeo MP4 ou MOV.`);
    if (file.size > MAX_BYTES) throw new Error(`“${file.name}” tem mais de 50 MB (limite do plano grátis).`);
    return { blob: file, mime };
  }
  const bitmap = await createImageBitmap(file);
  const crop = cropRect(bitmap.width, bitmap.height, aspect);
  const cropped = crop.sw !== bitmap.width || crop.sh !== bitmap.height;
  if (Math.min(crop.sw, crop.sh) < minSide) {
    bitmap.close();
    throw new Error(`“${file.name}” é pequena demais para o ${label} (mínimo ${minSide} px de cada lado).`);
  }
  const scale = Math.min(1, fit(crop.sw, crop.sh));
  // so manda o arquivo como esta se for JPEG de verdade (confere os bytes, nao o
  // nome: foto salva do WhatsApp/da web pode ser WebP/AVIF com nome .jpeg)
  const head = new Uint8Array(await file.slice(0, 3).arrayBuffer());
  const realJpeg = head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff;
  if (realJpeg && !cropped && scale === 1 && file.size <= MAX_BYTES) {
    bitmap.close();
    return { blob: file, mime: "image/jpeg" };
  }
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(crop.sw * scale);
  canvas.height = Math.round(crop.sh * scale);
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#ffffff"; // PNG com transparencia vira fundo branco, nao preto
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) throw new Error(`Não consegui converter “${file.name}” para JPEG.`);
  return { blob, mime: "image/jpeg" };
}
