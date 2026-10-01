"""Quadros principais de um video para a troca de personagem: antes de gastar
30-40 min no video, a persona e gerada em cada um deles e a pessoa escolhe
a melhor (ou corrige a descricao).

Roda com o Python do ComfyUI (tem PyAV e Pillow):

  python video_keyframes.py entrada.mp4 prefixo QUANTOS MAX_SEGUNDOS

Salva prefixo_1.png ... prefixo_N.png (quadros espalhados entre o inicio e
o fim do trecho usado, ja em pe se o celular gravou deitado, ~1 MP).
Imprime JSON: {"frames": [nomes], "seconds", "width", "height"}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import av
from PIL import Image

AREA = 1024 * 1024


def main() -> int:
    src, prefix = sys.argv[1], sys.argv[2]
    count, max_seconds = int(sys.argv[3]), float(sys.argv[4])

    inp = av.open(src)
    vin = inp.streams.video[0]
    rot = int(float(vin.metadata.get("rotate", 0) or 0)) % 360
    duration = float(inp.duration) / av.time_base if inp.duration else max_seconds
    used = min(duration, max_seconds)
    # Um pouco depois do inicio e antes do fim: quadros pretos/borrados de
    # abertura e fechamento atrapalham.
    targets = [used * (0.05 + 0.85 * i / max(1, count - 1)) for i in range(count)]

    saved: list[str] = []
    size = (0, 0)
    for frame in inp.decode(video=0):
        if len(saved) == count:
            break
        t = float(frame.pts * vin.time_base) if frame.pts is not None else 0.0
        if t + 1e-6 < targets[len(saved)]:
            continue
        img = frame.to_image()
        angle = rot or int(getattr(frame, "rotation", 0) or 0) % 360
        if angle:
            img = img.rotate(-angle, expand=True)
        scale = (AREA / (img.width * img.height)) ** 0.5
        if scale < 1:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        name = f"{prefix}_{len(saved) + 1}.png"
        img.save(Path(src).parent / name)
        saved.append(name)
        size = img.size
    inp.close()

    print(json.dumps({"frames": saved, "seconds": round(used, 2), "width": size[0], "height": size[1]}))
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
