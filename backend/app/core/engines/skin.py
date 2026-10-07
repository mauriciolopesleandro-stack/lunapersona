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


def _gray_dilate(img: np.ndarray, r: int) -> np.ndarray:
    out = img
    for _ in range(max(0, r)):  # cruz repetida = disco aproximado (sem cantos retos)
        p = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        out = np.maximum.reduce([p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
    return out


def gray_closing(rgb: np.ndarray, r: int) -> np.ndarray:
    """Fechamento em tons de cinza: some o TRACO escuro fino (< ~2r px) e fica o sombreado maior."""
    x = rgb.astype(np.float32)
    return 255.0 - _gray_dilate(255.0 - _gray_dilate(x, r), r)


def _blur(img: np.ndarray, r: int) -> np.ndarray:
    out = img.astype(np.float32)
    k = 2 * r + 1
    for _ in range(2):
        p = np.pad(out, ((r, r), (r, r), (0, 0)), mode="edge")
        c = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0), (0, 0)))
        out = (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)
    return out


def structure_preserving_fill(rgb: np.ndarray, zone: np.ndarray, known: np.ndarray, line_radius: int = 4,
                              solid_delta: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Entrada do inpaint que tira a marca SEM apagar a anatomia (teste 2026-10-07: o push-pull puro virou a
    mao fechada num "bloco" liso, a profundidade calculada nele virou luva e o modelo perdeu os dedos).

    Traco fino (rosa desenhada na mao): fechamento em cinza - some a linha, ficam juntas/dedos/sombras.
    Tinta CHEIA (o fechamento continua mais escuro que a pele em volta): push-pull.
    Devolve (imagem, mascara da tinta cheia)."""
    from app.core.persona_replacement.segmentation import dilate, skin_pixels

    z = zone > 0.5
    if not z.any():
        return rgb.copy(), np.zeros(z.shape, np.float32)
    lum = np.array([0.299, 0.587, 0.114], np.float32)
    closed = gray_closing(rgb, line_radius)
    smooth_skin = push_pull_fill(rgb, z.astype(np.float32), known).astype(np.float32)
    solid = z & (((smooth_skin @ lum) - (closed @ lum) > solid_delta) | (skin_pixels(np.clip(closed, 0, 255).astype(np.uint8)) < 0.5))
    solid = (dilate(solid.astype(np.float32), 2) > 0.5) & z
    mix = np.where(solid[..., None], smooth_skin, closed)
    out = np.where(z[..., None], _blur(mix, 1), rgb.astype(np.float32))
    return np.clip(out + 0.5, 0, 255).astype(np.uint8), solid.astype(np.float32)


def drop_small_blobs(mask: np.ndarray, min_radius: int, max_iter: int = 400) -> np.ndarray:
    """Tira manchinhas isoladas (pinta/poro marcado como tinta) por reconstrucao morfologica: so sobrevive o
    componente que tem um miolo de raio >= min_radius. Os componentes que ficam voltam INTEIROS."""
    from app.core.persona_replacement.transfer import dilate_round, erode_round

    m = mask > 0.5
    seed = erode_round(m.astype(np.float32), min_radius) > 0.5
    cur = seed
    for _ in range(max_iter):
        nxt = (dilate_round(cur.astype(np.float32), 1) > 0.5) & m
        if (nxt == cur).all():
            break
        cur = nxt
    return cur.astype(np.float32)


__all__ = ["drop_small_blobs", "gray_closing", "push_pull_fill", "structure_preserving_fill"]
