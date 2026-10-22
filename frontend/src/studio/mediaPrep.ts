// Prepara as midias para a publicacao automatica no Instagram: a API aceita
// foto so em JPEG; outros formatos sao convertidos no navegador (o original na
// pasta nao e tocado). Videos vao como estao (MP4/MOV).
const MAX_SIDE = 1440;
const MAX_BYTES = 50 * 1024 * 1024;

export async function prepareForInstagram(file: File): Promise<{ blob: Blob; mime: string }> {
  if (file.type.startsWith("video/")) {
    const mime = file.type === "video/quicktime" ? "video/quicktime" : "video/mp4";
    if (!/\.(mp4|mov|m4v)$/i.test(file.name)) throw new Error(`“${file.name}”: o Instagram aceita vídeo MP4 ou MOV.`);
    if (file.size > MAX_BYTES) throw new Error(`“${file.name}” tem mais de 50 MB (limite do plano grátis).`);
    return { blob: file, mime };
  }
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  if (file.type === "image/jpeg" && scale === 1 && file.size <= MAX_BYTES) {
    bitmap.close();
    return { blob: file, mime: "image/jpeg" };
  }
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#ffffff"; // PNG com transparencia vira fundo branco, nao preto
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) throw new Error(`Não consegui converter “${file.name}” para JPEG.`);
  return { blob, mime: "image/jpeg" };
}
