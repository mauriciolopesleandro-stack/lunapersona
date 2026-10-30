"""Borra so as partes intimas de fotos e videos (previas do bot de vendas).

O NudeNet (gratis, roda no PC, modelo de 12 MB) acha seios, genitais, anus e
bumbum a mostra; cada ponto vira um borrao forte (pixelado + desfocado) com a
borda suave, e o resto da foto fica igual. No video, cada quadro e analisado e
o borrao segura alguns quadros antes e depois, para nao piscar quando a deteccao
falha num quadro. O som do video e mantido.

Uso pelo robo: a pasta conteudos/vendas/previa-borrar (o estudio copia para la
quando "Borrar partes intimas" esta marcado, ou voce solta o arquivo pelo
Explorer); o resultado vai para conteudos/vendas/previa e o original para
conteudos/vendas/_originais.

Uso a mao:
  python borrar.py entrada.jpg [saida.jpg]
  python borrar.py entrada.mp4 [saida.mp4]
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

# o que e borrado (o resto do que o NudeNet acha, como rosto e barriga, fica)
LABELS = {
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED",
    "BUTTOCKS_EXPOSED",
}
THRESHOLD = 0.25  # baixo de proposito: melhor borrar a mais do que deixar passar
GROW = 0.35  # aumenta cada caixa 35% para cobrir a borda
HOLD = 4  # video: o borrao segura 4 quadros antes e depois de cada deteccao
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}

_detector = None


def detector():
    global _detector
    if _detector is None:
        from nudenet import NudeDetector

        _detector = NudeDetector()
    return _detector


def boxes(img) -> list[tuple[int, int, int, int]]:
    """Caixas (x, y, w, h) das partes intimas, ja aumentadas e dentro da imagem."""
    h, w = img.shape[:2]
    out = []
    for d in detector().detect(img):
        if d["class"] not in LABELS or d["score"] < THRESHOLD:
            continue
        x, y, bw, bh = d["box"]
        gx, gy = int(bw * GROW / 2), int(bh * GROW / 2)
        x0, y0 = max(0, x - gx), max(0, y - gy)
        x1, y1 = min(w, x + bw + gx), min(h, y + bh + gy)
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def apply(img, regions):
    """Borrao forte (pixelado + desfocado) em cada regiao, com borda suave em elipse."""
    import cv2
    import numpy as np

    if not regions:
        return img
    out = img.copy()
    h, w = img.shape[:2]
    for x, y, bw, bh in regions:
        roi = out[y : y + bh, x : x + bw]
        # pixela (blocos de ~1/8 da caixa) e desfoca por cima: nao da para "desborrar"
        sw, sh = max(1, bw // 8), max(1, bh // 8)
        small = cv2.resize(roi, (sw, sh), interpolation=cv2.INTER_LINEAR)
        pix = cv2.resize(small, (bw, bh), interpolation=cv2.INTER_NEAREST)
        k = max(3, (min(bw, bh) // 3) | 1)
        blurred = cv2.GaussianBlur(pix, (k, k), 0)
        mask = np.zeros((bh, bw), np.uint8)
        cv2.ellipse(mask, (bw // 2, bh // 2), (max(1, bw // 2), max(1, bh // 2)), 0, 0, 360, 255, -1)
        feather = max(3, (min(bw, bh) // 6) | 1)
        mask = cv2.GaussianBlur(mask, (feather, feather), 0).astype(np.float32)[..., None] / 255.0
        out[y : y + bh, x : x + bw] = (blurred * mask + roi * (1 - mask)).astype(np.uint8)
    return out


def _read(path: Path):
    import cv2
    import numpy as np

    # cv2.imread nao abre caminho com acento no Windows ("Área de Trabalho")
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"não consegui abrir {path.name}")
    return img


def borrar_imagem(src: Path, dst: Path) -> int:
    """Salva a foto borrada em JPEG; devolve quantas partes borrou."""
    import cv2

    img = _read(src)
    regions = boxes(img)
    ok, buf = cv2.imencode(".jpg", apply(img, regions), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError("não consegui salvar a foto")
    dst.parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(dst))
    return len(regions)


def borrar_video(src: Path, dst: Path) -> tuple[int, int]:
    """Salva o video borrado (H.264, com o som original); devolve (quadros, quadros com borrao)."""
    import imageio_ffmpeg
    import numpy as np

    # 1a passada: so as caixas de cada quadro (guardar os quadros gastaria GB de memoria)
    reader = imageio_ffmpeg.read_frames(str(src), pix_fmt="bgr24")
    meta = next(reader)
    w, h = meta["size"]
    fps = meta.get("fps") or 30
    has_audio = bool(meta.get("audio_codec"))
    found = [boxes(np.frombuffer(raw, np.uint8).reshape(h, w, 3)) for raw in reader]
    reader.close()

    # 2a passada: borra cada quadro com as caixas dele e dos vizinhos (HOLD quadros)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".parcial.mp4")
    writer = imageio_ffmpeg.write_frames(
        str(tmp),
        (w, h),
        pix_fmt_in="bgr24",
        fps=fps,
        codec="libx264",
        quality=None,
        macro_block_size=2,
        ffmpeg_log_level="error",
        output_params=["-crf", "20", "-preset", "veryfast", "-movflags", "+faststart"],
        # o som original volta junto (sem som: o video sai mudo, sem erro)
        **({"audio_path": str(src), "audio_codec": "aac"} if has_audio else {}),
    )
    writer.send(None)
    with_blur = 0
    reader = imageio_ffmpeg.read_frames(str(src), pix_fmt="bgr24")
    next(reader)
    for i, raw in enumerate(reader):
        regions = [b for k in range(max(0, i - HOLD), min(len(found), i + HOLD + 1)) for b in found[k]]
        if regions:
            with_blur += 1
        writer.send(apply(np.frombuffer(raw, np.uint8).reshape(h, w, 3), regions))
    reader.close()
    writer.close()
    tmp.replace(dst)
    return len(found), with_blur


def borrar(src: Path, dst: Path | None = None) -> tuple[Path, str]:
    """Borra foto ou video; devolve o arquivo salvo e um resumo para o log."""
    ext = src.suffix.lower()
    if ext in IMAGE_EXT:
        dst = dst or src.with_name(src.stem + "-borrada.jpg")
        n = borrar_imagem(src, dst)
        return dst, (f"{n} parte(s) borrada(s)" if n else "não achei partes íntimas: confira antes de usar")
    if ext in VIDEO_EXT:
        dst = dst or src.with_name(src.stem + "-borrado.mp4")
        total, blurred = borrar_video(src, dst)
        return dst, (f"borrão em {blurred} de {total} quadros" if blurred else "não achei partes íntimas: confira antes de usar")
    raise ValueError(f"tipo não aceito: {src.name}")


def processar_pasta(conteudos: Path, log=print) -> int:
    """Robo: borra o que estiver em vendas/previa-borrar e poe em vendas/previa."""
    entrada = conteudos / "vendas" / "previa-borrar"
    if not entrada.is_dir():
        return 0
    feitos = 0
    for src in sorted(entrada.iterdir()):
        ext = src.suffix.lower()
        if not src.is_file() or src.name.startswith(".") or ".parcial." in src.name or ext not in IMAGE_EXT | VIDEO_EXT:
            continue
        # arquivo ainda sendo copiado: espera o proximo ciclo
        if time.time() - src.stat().st_mtime < 5:
            continue
        stem = src.stem
        dst = conteudos / "vendas" / "previa" / (f"{stem}-borrada.jpg" if ext in IMAGE_EXT else f"{stem}-borrado.mp4")
        try:
            log(f"Borrando {src.name}...")
            _, resumo = borrar(src, dst)
            originais = conteudos / "vendas" / "_originais"
            originais.mkdir(parents=True, exist_ok=True)
            alvo = originais / src.name
            if alvo.exists():
                alvo = originais / f"{stem}-{int(time.time())}{src.suffix}"
            shutil.move(str(src), str(alvo))
            log(f"Prévia borrada: vendas/previa/{dst.name} ({resumo}). Original em vendas/_originais.")
            feitos += 1
        except Exception as e:  # noqa: BLE001 - um arquivo ruim nao para o robo
            log(f"Não consegui borrar {src.name}: {e}")
    return feitos


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    origem = Path(sys.argv[1])
    saida, resumo = borrar(origem, Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    print(f"{saida}: {resumo}")
