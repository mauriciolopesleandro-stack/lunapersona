"""Integracao de luz, cor, textura e cabelo - SO dentro da regiao alterada.

A referencia e sempre a propria foto: a regiao nova assume a exposicao, o
contraste e a temperatura que a PESSOA ORIGINAL tinha no mesmo lugar (luz dura
continua dura, luz baixa continua baixa). A cor e casada so em parte (chroma)
para manter o tom de pele da persona. Nada fora da mascara muda.
"""
from __future__ import annotations

import numpy as np


def to_ycc(rgb: np.ndarray) -> np.ndarray:
    x = rgb.astype(np.float32)
    y = 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]
    cb = (x[..., 2] - y) * 0.564 + 128
    cr = (x[..., 0] - y) * 0.713 + 128
    return np.stack([y, cb, cr], axis=-1)


def from_ycc(ycc: np.ndarray) -> np.ndarray:
    y, cb, cr = ycc[..., 0], ycc[..., 1] - 128, ycc[..., 2] - 128
    r = y + 1.403 * cr
    b = y + 1.773 * cb
    g = (y - 0.299 * r - 0.114 * b) / 0.587
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)


def _stats(ycc: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    sel = mask > 0.5
    if sel.sum() < 30:
        return None
    vals = ycc[sel]
    return vals.mean(axis=0), vals.std(axis=0) + 1e-3


def match_lighting(new: np.ndarray, original: np.ndarray, region: np.ndarray, luma: float = 1.0,
                   chroma: float = 0.5) -> np.ndarray:
    """Exposicao/contraste (luma) e temperatura (chroma, parcial) da regiao nova
    casados com os da PESSOA ORIGINAL na mesma regiao. Mistura pela mascara."""
    a, b = to_ycc(new), to_ycc(original)
    sa, sb = _stats(a, region), _stats(b, region)
    if sa is None or sb is None:
        return new.copy()
    (ma, da), (mb, db) = sa, sb
    out = a.copy()
    target_y = (a[..., 0] - ma[0]) / da[0] * db[0] + mb[0]
    out[..., 0] = a[..., 0] + (target_y - a[..., 0]) * luma
    out[..., 1:] = a[..., 1:] + (mb[1:] - ma[1:]) * chroma
    adjusted = from_ycc(out).astype(np.float32)
    m = np.clip(region, 0, 1)[..., None]
    return (adjusted * m + new.astype(np.float32) * (1 - m)).round().astype(np.uint8)


def needs_hair_recolor(rgb: np.ndarray, hair: np.ndarray, max_luma: float, percentile: float = 75.0) -> bool:
    """Cabelo fora do da persona: quando uma parte relevante dele (percentil, nao a media) e clara -
    pontas loiras e luzes contam."""
    sel = hair > 0.5
    if sel.sum() < 30:
        return False
    return float(np.percentile(to_ycc(rgb)[..., 0][sel], percentile)) > max_luma


def recolor_hair(rgb: np.ndarray, hair: np.ndarray, target_luma: float, target_cb: float, target_cr: float,
                 strength: float = 0.85) -> np.ndarray:
    """Cabelo da persona (ex.: castanho muito escuro) SEM regenerar: mantem fios,
    brilho relativo e volume da foto; so muda tom e cor. Sem halo, sem borrao."""
    ycc = to_ycc(rgb)
    s = _stats(ycc, hair)
    if s is None:
        return rgb.copy()
    (m, d) = s
    out = ycc.copy()
    scale = min(1.0, (target_luma * 0.6) / max(d[0], 1.0))  # cabelo escuro tem menos variacao absoluta
    out[..., 0] = (ycc[..., 0] - m[0]) * scale + target_luma
    out[..., 1] = (ycc[..., 1] - m[1]) * 0.4 + target_cb
    out[..., 2] = (ycc[..., 2] - m[2]) * 0.4 + target_cr
    recolored = from_ycc(out).astype(np.float32)
    k = (np.clip(hair, 0, 1) * strength)[..., None]
    return (recolored * k + rgb.astype(np.float32) * (1 - k)).round().astype(np.uint8)


def box_blur(gray: np.ndarray, r: int = 1) -> np.ndarray:
    p = np.pad(gray, r, mode="edge")
    acc = np.zeros_like(gray, dtype=np.float32)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            acc += p[dy:dy + gray.shape[0], dx:dx + gray.shape[1]]
    return acc / float((2 * r + 1) ** 2)


def noise_level(rgb: np.ndarray, mask: np.ndarray) -> float | None:
    """Grao/nitidez: desvio do residuo de alta frequencia na regiao."""
    y = to_ycc(rgb)[..., 0]
    resid = y - box_blur(y, 1)
    sel = mask > 0.5
    if sel.sum() < 30:
        return None
    return float(resid[sel].std())


def match_grain(new: np.ndarray, region: np.ndarray, reference_noise: float | None, seed: int = 0) -> np.ndarray:
    """Se a regiao nova esta mais lisa que a foto em volta, devolve o grao que falta
    (ruido gaussiano so na regiao). Se esta mais "nitida/ruidosa", suaviza de leve."""
    current = noise_level(new, region)
    if reference_noise is None or current is None:
        return new.copy()
    out = new.astype(np.float32)
    m = np.clip(region, 0, 1)[..., None]
    if current < reference_noise:
        missing = float(np.sqrt(max(reference_noise ** 2 - current ** 2, 0.0)))
        noise = np.random.default_rng(seed).normal(0.0, missing, new.shape[:2]).astype(np.float32)[..., None]
        out = out + noise * m
    elif current > reference_noise * 1.4:
        soft = np.stack([box_blur(out[..., c], 1) for c in range(3)], axis=-1)
        out = out * (1 - m * 0.5) + soft * (m * 0.5)
    return np.clip(out, 0, 255).round().astype(np.uint8)


__all__ = ["box_blur", "from_ycc", "match_grain", "match_lighting", "needs_hair_recolor", "noise_level",
           "recolor_hair", "to_ycc"]
