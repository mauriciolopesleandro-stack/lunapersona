"""Bordas: composicao com borda suave proporcional e medidas de halo/recorte.

O fundo fora da pessoa e SEMPRE o da foto original (composicao final contra a
original), entao halo so pode aparecer na faixa da borda - e e la que se mede.
"""
from __future__ import annotations

import numpy as np

from app.core.persona_replacement.lighting import box_blur, to_ycc
from app.core.persona_replacement.segmentation import dilate, erode


def feather(mask: np.ndarray, radius: int) -> np.ndarray:
    m = np.clip(mask.astype(np.float32), 0, 1)
    for _ in range(max(1, radius // 2)):
        m = box_blur(m, 2)
    return np.clip(m, 0, 1)


def composite(base: np.ndarray, top: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
    """`top` dentro da mascara (borda suave, so para DENTRO: nao vaza para o fundo)."""
    soft = feather(erode(mask, max(1, radius // 2)), radius) * (mask > 0.5)
    m = soft[..., None]
    return (top.astype(np.float32) * m + base.astype(np.float32) * (1 - m)).round().astype(np.uint8)


def edge_band(mask: np.ndarray, width: int) -> tuple[np.ndarray, np.ndarray]:
    """Faixa externa (fundo colado na pessoa) e interna (pessoa colada no fundo)."""
    m = (mask > 0.5).astype(np.float32)
    return np.clip(dilate(m, width) - m, 0, 1), np.clip(m - erode(m, width), 0, 1)


def _laplacian_var(y: np.ndarray, sel: np.ndarray) -> float | None:
    lap = (np.roll(y, 1, 0) + np.roll(y, -1, 0) + np.roll(y, 1, 1) + np.roll(y, -1, 1) - 4 * y)
    return float(lap[sel].var()) if sel.sum() >= 30 else None


def edge_metrics(original: np.ndarray, result: np.ndarray, person: np.ndarray, width: int) -> dict[str, float | None]:
    """halo: mudanca media de luminancia na faixa EXTERNA (deveria ser ~0);
    sharpness_ratio: nitidez da faixa interna do resultado / da original (1 = igual;
    << 1 = borda borrada; >> 1 = recorte duro)."""
    outer, inner = edge_band(person, width)
    yo, yr = to_ycc(original)[..., 0], to_ycc(result)[..., 0]
    sel_o = outer > 0.5
    halo = float(np.abs(yr - yo)[sel_o].mean()) if sel_o.sum() >= 30 else None
    so, sr = _laplacian_var(yo, inner > 0.5), _laplacian_var(yr, inner > 0.5)
    ratio = round(sr / so, 3) if so and sr is not None and so > 1e-6 else None
    return {"halo": None if halo is None else round(halo, 3), "sharpness_ratio": ratio}


__all__ = ["composite", "edge_band", "edge_metrics", "feather"]
