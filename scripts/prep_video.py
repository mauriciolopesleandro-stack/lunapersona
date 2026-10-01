"""Prepara um video enviado para a troca de personagem (Wan 2.2 Animate).

Roda com o Python do ComfyUI (tem PyAV e Pillow):

  python prep_video.py entrada.mp4 saida.mp4 primeiro_quadro.png AREA MAX_SEGUNDOS

AREA = pixels alvo (ex.: 399360 = 480x832); a proporcao vem do video.

- reamostra para 16 fps (o Animate trabalha a 16; video de celular vem a 30 e
  sairia acelerado) e corta em MAX_SEGUNDOS;
- mantem o audio (o video final volta com a fala/musica original);
- calcula largura x altura (multiplos de 16) na AREA com a proporcao do video;
- salva o 1o quadro ja no tamanho/corte do grafo (ImageScale center), para a
  deteccao da pessoa devolver coordenadas validas no video processado.
Imprime JSON: {"frames", "fps", "seconds", "width", "height"} do video de saida.
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction

import av
from PIL import Image

FPS = 16


def center_crop_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = img.size
    target = w / h
    if sw / sh > target:
        cw = round(sh * target)
        img = img.crop(((sw - cw) // 2, 0, (sw - cw) // 2 + cw, sh))
    else:
        ch = round(sw / target)
        img = img.crop((0, (sh - ch) // 2, sw, (sh - ch) // 2 + ch))
    return img.resize((w, h), Image.LANCZOS)


def main() -> int:
    src, dst, first_png = sys.argv[1:4]
    area, max_seconds = int(sys.argv[4]), float(sys.argv[5])

    inp = av.open(src)
    vin = inp.streams.video[0]
    # Celular gravando em pe grava deitado + marcacao de rotacao.
    rot = int(float(vin.metadata.get("rotate", 0) or 0)) % 360
    sw, sh = vin.codec_context.width, vin.codec_context.height
    if rot in (90, 270):
        sw, sh = sh, sw
    scale = (area / (sw * sh)) ** 0.5
    w, h = max(256, round(sw * scale / 16) * 16), max(256, round(sh * scale / 16) * 16)
    ain = inp.streams.audio[0] if inp.streams.audio else None

    out = av.open(dst, "w")
    vout = out.add_stream("libx264", rate=FPS)
    vout.width, vout.height, vout.pix_fmt = sw - sw % 2, sh - sh % 2, "yuv420p"
    vout.options = {"crf": "16"}
    aout = out.add_stream("aac", rate=ain.codec_context.sample_rate or 44100) if ain else None

    frames = 0
    next_t = 0.0
    saved_first = False
    for frame in inp.decode(video=0):
        t = float(frame.pts * vin.time_base) if frame.pts is not None else frames / FPS
        if t > max_seconds:
            break
        if t + 1e-6 < next_t:
            continue  # reamostragem: pega o quadro mais proximo de cada 1/16 s
        next_t += 1.0 / FPS
        img = frame.to_image()
        angle = rot or int(getattr(frame, "rotation", 0) or 0) % 360
        if angle:
            img = img.rotate(-angle, expand=True)
        img = img.crop((0, 0, sw - sw % 2, sh - sh % 2))
        if not saved_first:
            center_crop_resize(img, w, h).save(first_png)
            saved_first = True
        nf = av.VideoFrame.from_image(img)
        nf.pts = frames
        nf.time_base = Fraction(1, FPS)
        for packet in vout.encode(nf):
            out.mux(packet)
        frames += 1
    for packet in vout.encode():
        out.mux(packet)

    if ain:
        inp.seek(0)
        for aframe in inp.decode(audio=0):
            if aframe.pts is not None and float(aframe.pts * ain.time_base) > frames / FPS:
                break
            aframe.pts = None
            for packet in aout.encode(aframe):
                out.mux(packet)
        for packet in aout.encode():
            out.mux(packet)
    out.close()
    inp.close()

    print(json.dumps({"frames": frames, "fps": FPS, "seconds": round(frames / FPS, 2), "width": w, "height": h}))
    return 0 if frames else 1


if __name__ == "__main__":
    sys.exit(main())
