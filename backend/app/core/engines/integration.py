"""Integracao fotografica (CPU, numpy): a Luna "capturada pela mesma camera" da foto.

So dentro da regiao alterada; tudo medido na PROPRIA foto (nada de filtro, LUT ou grao pesado):
  1. degrau de luz/cor na fronteira PELE x PELE (pescoco/ombros): a diferenca entre a pele gerada
     logo dentro da borda e a pele original logo fora e levada para dentro, sumindo com a distancia
     (cabelo x fundo nunca e comparado - seria errado casar cabelo com parede);
  2. harmonia rosto x corpo: o tom do rosto puxado PARCIALMENTE para o da pele do corpo da foto
     (no teste de 2026-10-07 o rosto saiu bronzeado/maquiado e destoava do colo);
  3. grao: so o que falta para igualar o grao da pele original (nunca mais que ela).
"""
from __future__ import annotations

import numpy as np

from app.core.persona_replacement.lighting import noise_level
from app.core.persona_replacement.segmentation import dilate, erode, skin_pixels
from app.core.persona_replacement.transfer import local_mean


def distance_ramp(mask: np.ndarray, width: int, step: int = 6) -> np.ndarray:
    """~1 na borda de dentro da mascara, caindo a 0 a `width` px para dentro (erosoes sucessivas)."""
    m = (mask > 0.5).astype(np.float32)
    n = max(1, width // step)
    survived = np.zeros_like(m)
    cur = m
    for _ in range(n):
        cur = erode(cur, step)
        survived += cur
    return np.clip(1.0 - survived / n, 0, 1) * m


def integrate(original: np.ndarray, current: np.ndarray, region: np.ndarray, face: np.ndarray | None = None,
              body_skin: np.ndarray | None = None, band: int = 12, radius: int = 24, tone_harmony: float = 0.5,
              seed: int = 0) -> tuple[np.ndarray, dict]:
    """Devolve (imagem, relatorio). Fora de `region` a imagem fica IDENTICA a `current`."""
    reg = region > 0.5
    rel: dict = {"aplicado": False}
    if not reg.any():
        return current.copy(), rel
    out = current.astype(np.float32)
    regf = reg.astype(np.float32)
    # 1. degrau pele x pele na fronteira
    if body_skin is not None:
        skin_now = skin_pixels(current) > 0.5
        inner = reg & ~(erode(regf, band) > 0.5) & skin_now
        outer = (dilate(regf, band) > 0.5) & ~reg & (body_skin > 0.5)
        if inner.sum() > 50 and outer.sum() > 50:
            a = local_mean(out, outer, radius)
            b = local_mean(out, inner, radius)
            step = local_mean(a - b, inner, radius * 3)
            w = distance_ramp(regf, radius * 3)[..., None]
            out = out + step * w * regf[..., None]
            rel["degrau_pele"] = [round(float(v), 2) for v in (out[outer].mean(axis=0) - current.astype(np.float32)[inner].mean(axis=0))]
    # 2. harmonia rosto x corpo (parcial)
    if face is not None and body_skin is not None and tone_harmony > 0:
        f = (face > 0.5) & reg & (skin_pixels(current) > 0.5)
        b = (body_skin > 0.5) & ~reg
        if f.sum() > 100 and b.sum() > 100:
            delta = out[b].mean(axis=0) - out[f].mean(axis=0)
            rel["delta_rosto_corpo"] = [round(float(v), 2) for v in delta]
            fw = dilate((face > 0.5).astype(np.float32), 4) * regf
            out = out + delta * tone_harmony * fw[..., None]
    # 3. grao: so o que falta (referencia = pele original fora da regiao)
    if body_skin is not None:
        ref_region = ((body_skin > 0.5) & ~reg).astype(np.float32)
        ref_noise = noise_level(original, ref_region)
        cur_noise = noise_level(np.clip(out, 0, 255).astype(np.uint8), regf)
        if ref_noise is not None and cur_noise is not None and cur_noise < ref_noise:
            add = float(np.sqrt(max(ref_noise ** 2 - cur_noise ** 2, 0.0)))
            out = out + np.random.default_rng(seed).normal(0, add, reg.shape).astype(np.float32)[..., None] * regf[..., None]
            rel["grao_adicionado"] = round(add, 2)
    final = np.where(reg[..., None], out, current.astype(np.float32))
    rel["aplicado"] = True
    return np.clip(final + 0.5, 0, 255).astype(np.uint8), rel


__all__ = ["distance_ramp", "integrate"]
