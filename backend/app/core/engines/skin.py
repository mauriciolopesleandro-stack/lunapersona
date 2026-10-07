"""Reconstrucao de pele (spec 45.5): a pele final e GERADA pelo modelo como pele da Persona.

Aqui fica so a ENTRADA do inpaint: a regiao das marcas recebe um preenchimento suave (push-pull em
piramide) para o modelo nao "ver" a tinta e para a profundidade ser calculada na pele limpa. Nao e o
resultado: o RealVisXL + LoRA da Persona redesenha a regiao com denoise alto.

O preenchimento antigo (media em janela quadrada perto/longe) deixava DEGRAUS QUADRADOS que o modelo
copiava (quadrados cinza no braco no reteste de 2026-10-07). O push-pull nao tem janela: cada nivel da
piramide e a media ponderada pelos pixels conhecidos, e a volta interpola suave.
"""
from __future__ import annotations

import numpy as np


def _down(img: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, wd = w.shape
    h2, w2 = (h + 1) // 2, (wd + 1) // 2
    ip = np.zeros((h2 * 2, w2 * 2, img.shape[2]), np.float32)
    wp = np.zeros((h2 * 2, w2 * 2), np.float32)
    ip[:h, :wd], wp[:h, :wd] = img * w[..., None], w
    s = ip.reshape(h2, 2, w2, 2, -1).sum((1, 3))
    ws = wp.reshape(h2, 2, w2, 2).sum((1, 3))
    return s / np.maximum(ws, 1e-6)[..., None], np.minimum(ws, 1.0)


def _up(img: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Interpolacao bilinear (sem blocos) de volta ao tamanho `shape`."""
    h, w = shape
    sh, sw = img.shape[:2]
    ys = np.clip((np.arange(h) + 0.5) / 2 - 0.5, 0, sh - 1)
    xs = np.clip((np.arange(w) + 0.5) / 2 - 0.5, 0, sw - 1)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, sh - 1), np.minimum(x0 + 1, sw - 1)
    fy, fx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    a = img[y0][:, x0] * (1 - fx) + img[y0][:, x1] * fx
    b = img[y1][:, x0] * (1 - fx) + img[y1][:, x1] * fx
    return a * (1 - fy) + b * fy


def push_pull_fill(rgb: np.ndarray, hole: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Preenche `hole` a partir dos pixels `known` (pele limpa em volta), sem degraus. Fora do buraco: igual."""
    hole_b = hole > 0.5
    if not hole_b.any():
        return rgb.copy()
    w0 = ((known > 0.5) & ~hole_b).astype(np.float32)
    if w0.sum() == 0:
        return rgb.copy()
    levels = [(rgb.astype(np.float32), w0)]
    while min(levels[-1][1].shape) > 2 and levels[-1][1].min() < 1.0:
        levels.append(_down(*levels[-1]))
    filled = levels[-1][0]
    for img, w in reversed(levels[:-1]):
        up = _up(filled, w.shape)
        filled = img * w[..., None] + up * (1 - w[..., None])
    out = rgb.astype(np.float32).copy()
    out[hole_b] = filled[hole_b]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


__all__ = ["push_pull_fill"]
