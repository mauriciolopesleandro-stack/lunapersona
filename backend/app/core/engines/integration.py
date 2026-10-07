"""Integracao fotografica (CPU, numpy): a Luna "capturada pela mesma camera" da foto.

So dentro da regiao alterada; tudo medido na PROPRIA foto (nada de filtro, LUT ou grao pesado):
  1. degrau de luz/cor na fronteira PELE x PELE (pescoco/ombros): a diferenca entre a pele gerada
     logo dentro da borda e a pele original logo fora e levada para dentro, sumindo com a distancia
     (cabelo x fundo nunca e comparado - seria errado casar cabelo com parede);
  2. harmonia rosto x corpo: o tom do rosto puxado PARCIALMENTE para o da pele do corpo da foto
     (no teste de 2026-10-07 o rosto saiu bronzeado/maquiado e destoava do colo);
Correcao do smoke test (2026-10-07): o degrau era aplicado em TODA a borda da regiao (cabelo x parede
inclusive) e sem limite (+21) -> cabelo clareado com silhueta dura e mancha clara no colo. Agora o degrau
so entra na pele perto da fronteira pele x pele, limitado a `max_step`; a harmonia tem transicao suave
e limite. Degrau maior que o limite nao e "consertado" aqui: fica para a validacao (emenda) apontar.
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
              seed: int = 0, max_step: float = 12.0, max_harmony: float = 8.0) -> tuple[np.ndarray, dict]:
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
            raw = local_mean(a - b, inner, radius * 3)
            rel["degrau_pele"] = [round(float(v), 2) for v in raw[inner].mean(axis=0)]
            step = np.clip(raw, -max_step, max_step)
            rel["degrau_limitado"] = bool(np.abs(raw[inner]).max() > max_step)
            # so pele gerada PERTO da pele original de fora (nunca cabelo x parede), sumindo com a distancia
            near = np.clip(local_mean(outer.astype(np.float32)[..., None], dilate(outer.astype(np.float32), radius * 3) > 0.5,
                                      radius)[..., 0] * 4.0, 0, 1)
            w = distance_ramp(regf, radius * 3) * near * (skin_now & reg)
            w = local_mean(w[..., None], reg, max(2, band // 3))[..., 0] * regf
            out = out + step * w[..., None]
    # 2. harmonia rosto x corpo (parcial)
    if face is not None and body_skin is not None and tone_harmony > 0:
        f = (face > 0.5) & reg & (skin_pixels(current) > 0.5)
        b = (body_skin > 0.5) & ~reg
        if f.sum() > 100 and b.sum() > 100:
            delta = out[b].mean(axis=0) - out[f].mean(axis=0)
            rel["delta_rosto_corpo"] = [round(float(v), 2) for v in delta]
            shift = np.clip(delta * tone_harmony, -max_harmony, max_harmony)
            # 1 no miolo do rosto, 0 na borda do rosto (sem degrau novo com cabelo/pescoco)
            fm = (face > 0.5).astype(np.float32)
            fw = (1.0 - distance_ramp(fm, radius)) * fm * (skin_pixels(current) > 0.5) * regf
            fw = local_mean(fw[..., None], fm > 0.5, max(2, band // 3))[..., 0] * fm * regf
            out = out + shift * fw[..., None]
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
