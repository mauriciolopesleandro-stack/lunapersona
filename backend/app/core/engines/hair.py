"""Mascara de cabelo com sanidade (Replacement V2).

Espelho com o braco na cabeca (2026-10-08): o segmentador devolveu "cabelo" = 78% da imagem (a pessoa inteira,
top branco e braco inclusos). A identidade herdou isso e o passe de rosto/cabelo redesenhou a foto toda (top branco
virou preto, braco inventado, colar trocado). Quando a mascara de cabelo e grande demais para o rosto, ela e refeita
pela COR do cabelo de verdade (amostrada logo acima da testa) e so vale o que estiver ligado a essa amostra.
"""
from __future__ import annotations

import numpy as np

from app.core.persona_replacement.segmentation import dilate, skin_pixels


def _lab(rgb: np.ndarray) -> np.ndarray:
    x = rgb.astype(np.float32) / 255.0
    lin = np.where(x > 0.04045, ((x + 0.055) / 1.055) ** 2.4, x / 12.92)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)  # noqa: E731
    fx, fy, fz = f(X), f(Y), f(Z)
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)


def clean_hair_mask(original: np.ndarray, hair: np.ndarray, face_bbox, face_full: np.ndarray,
                    max_face_ratio: float = 3.0) -> tuple[np.ndarray, dict]:
    """Devolve (mascara, info). Mascara plausivel (<= max_face_ratio x area do rosto) volta como esta."""
    h, w = hair.shape
    hm = hair > 0.5
    face_area = max(1.0, float((face_full > 0.5).sum()))
    ratio = float(hm.sum()) / face_area
    info = {"ratio_before": round(ratio, 2), "cleaned": False}
    if ratio <= max_face_ratio:
        return hair, info
    x1, y1, x2, y2 = face_bbox
    fh, fw = max(1.0, y2 - y1), max(1.0, x2 - x1)
    # amostra: cabelo logo acima/ao lado da testa (topo da cabeca), fora da pele
    band = np.zeros((h, w), bool)
    band[max(0, int(y1 - fh * 0.35)):max(1, int(y1 + fh * 0.15)), max(0, int(x1 - fw * 0.1)):min(w, int(x2 + fw * 0.1))] = True
    sample = band & hm & ~(skin_pixels(original) > 0.5)
    if sample.sum() < 30:
        info["note"] = "sem amostra de cabelo acima da testa: mascara mantida"
        return hair, info
    lab = _lab(original)
    ref = np.median(lab[sample], axis=0)
    dist = np.linalg.norm(lab - ref, axis=-1)
    thr = max(18.0, float(np.percentile(dist[sample], 90)) * 1.5)
    ok = hm & (dist <= thr)
    # so o que liga a amostra (crescimento geodesico dentro do "parece cabelo")
    cur = sample & ok
    for _ in range(max(h, w)):
        nxt = (dilate(cur.astype(np.float32), 2) > 0.5) & ok
        if (nxt == cur).all():
            break
        cur = nxt
    out = cur.astype(np.float32)
    info.update(cleaned=True, ratio_after=round(float(cur.sum()) / face_area, 2), threshold=round(thr, 1))
    return out, info


__all__ = ["clean_hair_mask"]
