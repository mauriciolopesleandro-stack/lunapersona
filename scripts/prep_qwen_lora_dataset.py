"""Monta o dataset da LoRA da Luna para o Qwen-Image-Edit 2511 (pack com a persona).

Cada foto de treino da Luna (ComfyUI/input/lora_luna) vira um par igual ao que
o pack manda para o Qwen:
- control 1: a propria foto com o cabelo e a cabeca escondidos (borrado e
  acinzentado) e o rosto so levemente borrado - como o image 1 do pack;
- control 2: o recorte do rosto da foto principal da persona - como o image 2;
- alvo: a foto original; legenda: a instrucao do pack + "lunavox".
Assim a LoRA aprende a pintar a Luna exatamente nessa situacao.

Roda no pod com o venv do ComfyUI (PIL + insightface do no LunaFaces):
  .venv-cu128/bin/python scripts/prep_qwen_lora_dataset.py DESTINO
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFilter

COMFY = Path("/workspace/runpod-slim/ComfyUI")
SRC = COMFY / "input" / "lora_luna"
PERSONA = Path("/workspace/lunapersona/personas/luna/references")
CAPTION = (
    "lunavox. Replace the woman in image 1 with the woman from image 2: paint the face of the woman from image 2 "
    "with her long dark brown hair and tanned skin where her hair is covered by gray and her face is slightly "
    "blurred. Keep the head angle, gaze, expression, pose, clothes and scene of image 1."
)

spec = importlib.util.spec_from_file_location("lf", COMFY / "custom_nodes" / "luna_faces" / "__init__.py")
lf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lf)  # type: ignore[union-attr]


def faces(img: Image.Image) -> list:
    t = torch.from_numpy(np.asarray(img.convert("RGB")).astype(np.float32) / 255)
    return lf._faces(t, 1024)


def hidden(img: Image.Image, box) -> Image.Image:
    """Cabeca e cabelo escondidos como no pack (cinza 0.9 sobre borrado forte).
    O rosto vai bem mais borrado que no pack: aqui ele E a Luna, e com o
    borrado leve a LoRA so aprenderia a copiar o rosto de baixo (no pack ali
    esta o rosto da original). Sobra so a direcao da cabeca e dos olhos."""
    x1, y1, x2, y2 = (float(v) for v in box)
    fw, fh = x2 - x1, y2 - y1
    gray = Image.new("RGB", img.size, (128, 128, 128))
    strong = Image.blend(img.filter(ImageFilter.GaussianBlur(10)), gray, 0.9)
    soft = Image.blend(img.filter(ImageFilter.GaussianBlur(max(6, fw * 0.06))), gray, 0.65)
    hair = Image.new("L", img.size, 0)
    ImageDraw.Draw(hair).ellipse((x1 - fw * 1.3, y1 - fh * 1.0, x2 + fw * 1.3, y2 + fh * 3.0), fill=255)
    hair = hair.filter(ImageFilter.GaussianBlur(12))
    face = Image.new("L", img.size, 0)
    ImageDraw.Draw(face).rectangle((x1 - fw * 0.1, y1 - fh * 0.1, x2 + fw * 0.1, y2 + fh * 0.05), fill=255)
    face = face.filter(ImageFilter.GaussianBlur(12))
    out = Image.composite(strong, img, hair)
    return Image.composite(soft, out, face)


def main(dest: Path) -> None:
    for sub in ("target", "control1", "control2"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    ref_path = next(p for p in sorted(PERSONA.glob("*.png")))
    ref = Image.open(ref_path).convert("RGB")
    rf = faces(ref)[0].bbox
    fw, fh = rf[2] - rf[0], rf[3] - rf[1]
    ref_crop = ref.crop((max(0, int(rf[0] - fw)), max(0, int(rf[1] - fh * 0.6)), min(ref.width, int(rf[2] + fw)), min(ref.height, int(rf[3] + fh))))
    n = 0
    for path in sorted(SRC.glob("*.jpg")):
        img = Image.open(path).convert("RGB")
        found = faces(img)
        if not found:
            print("sem rosto, fica de fora:", path.name)
            continue
        img.save(dest / "target" / f"{path.stem}.jpg", quality=95)
        hidden(img, found[0].bbox).save(dest / "control1" / f"{path.stem}.jpg", quality=95)
        ref_crop.save(dest / "control2" / f"{path.stem}.jpg", quality=95)
        (dest / "target" / f"{path.stem}.txt").write_text(CAPTION)
        n += 1
    print(f"{n} pares em {dest}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
