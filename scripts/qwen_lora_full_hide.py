"""Refaz o control 1 do dataset da LoRA do Qwen com a cabeca 100% cinza.

O primeiro treino (2026-10-03) tinha o rosto 70% cinza e o cabelo 90%: por
baixo aparecia a propria Luna, e a LoRA aprendeu o atalho "refazer o que esta
debaixo do borrado" - no pack, debaixo esta a mulher original e ela voltava
loira. Aqui a area escondida (onde o control 1 difere do alvo, menos o
retangulo vermelho) vira cinza puro: o rosto so pode vir do image 2.
O pack com a LoRA esconde igual (person_swap.LORA_FACE_HIDE).

uso (no pod, venv do ComfyUI): qwen_lora_full_hide.py PASTA_DO_DATASET
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

GRAY = 128


def full_hide(control: Image.Image, target: Image.Image) -> Image.Image:
    c = np.asarray(control.convert("RGB")).astype(np.int16)
    t = np.asarray(target.convert("RGB").resize(control.size)).astype(np.int16)
    diff = np.abs(c - t).max(axis=2)
    ring = (c[..., 0] > 200) & (c[..., 1] < 60) & (c[..., 2] < 60)
    hidden = (diff > 6) & ~ring
    mask = Image.fromarray((hidden * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(3))
    gray = Image.new("RGB", control.size, (GRAY, GRAY, GRAY))
    return Image.composite(gray, control.convert("RGB"), mask)


def main(ds: Path) -> None:
    backup = ds / "control1_v1"
    if not backup.exists():
        shutil.copytree(ds / "control1", backup)
    for path in sorted(backup.glob("*.png")):
        out = full_hide(Image.open(path), Image.open(ds / "target" / path.name))
        out.save(ds / "control1" / path.name)
    print("control1 refeito:", len(list((ds / "control1").glob("*.png"))))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
