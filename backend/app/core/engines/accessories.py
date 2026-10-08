"""Acessorios como CAMADAS (spec 46.2/46.3/46.8): cada objeto detectado (oculos, brinco, pulseira, relogio...)
vira uma camada com mascara, caixa, ordem de oclusao e politica. A pessoa e reconstruida POR BAIXO e as camadas
PRESERVE voltam por cima, na ordem (oculos na frente do rosto).

Oculos (46.3): lente ESCURA (oculos de sol) volta inteira; lente CLARA volta so a ARMACAO - o rosto atras da lente
e o da Persona, com o tom da lente reaplicado (interacao optica). Assim o rosto original nunca "sobra" so porque
estava atras do vidro.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.segmentation import dilate, erode, skin_pixels

# ordem de oclusao: maior = mais na frente (pintado por ultimo)
ORDER = {"glasses": 30, "hat": 30, "earrings": 20, "necklace": 20, "watch": 10, "bracelet": 10, "ring": 10, "bag": 5}


@dataclass
class AccessoryLayer:
    label: str
    item: str | None
    policy: str
    bbox: tuple[float, float, float, float]
    mask: np.ndarray  # pixels do OBJETO (o que volta por cima)
    order: int
    kind: str = "solid"  # "solid" | "glasses_dark" | "glasses_clear"
    lens: np.ndarray | None = None  # area da lente clara (o rosto novo aparece atras, com o tom da lente)
    tint: np.ndarray | None = None  # transmitancia da lente por canal (cor/escurecimento)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "item": self.item, "policy": self.policy, "bbox": [round(v, 1) for v in self.bbox],
                "order": self.order, "kind": self.kind, "area_px": int((self.mask > 0.5).sum()),
                "tint": None if self.tint is None else [round(float(v), 3) for v in self.tint]}


def _box(h: int, w: int, b) -> np.ndarray:
    x1, y1, x2, y2 = b
    m = np.zeros((h, w), np.float32)
    m[max(0, int(y1)):int(y2) + 1, max(0, int(x1)):int(x2) + 1] = 1
    return m


def _luma(x: np.ndarray) -> np.ndarray:
    return x.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def build_layer(original: np.ndarray, bbox, label: str, item: str | None, policy: str) -> AccessoryLayer:
    h, w = original.shape[:2]
    box = _box(h, w, bbox)
    obj = erode(dilate(box * (1 - skin_pixels(original)), 2), 2) * box  # nao-pele dentro da caixa
    layer = AccessoryLayer(label, item, policy, tuple(float(v) for v in bbox), obj, ORDER.get(item or "", 15))
    if item != "glasses":
        return layer
    x1, y1, x2, y2 = (int(v) for v in bbox)
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    inner = _box(h, w, (x1 + bw * 0.12, y1 + bh * 0.2, x2 - bw * 0.12, y2 - bh * 0.2)) > 0.5
    lum = _luma(original)
    if not inner.any():
        return layer
    lens_lum = float(np.median(lum[inner]))
    ring = (dilate(box, max(3, bh // 2)) > 0.5) & ~(box > 0.5) & (skin_pixels(original) > 0.5)
    skin_lum = float(np.median(lum[ring])) if ring.any() else 160.0
    if lens_lum < 0.45 * skin_lum:  # lente escura: oculos de sol - volta tudo (lente + armacao)
        layer.kind = "glasses_dark"
        layer.mask = np.clip(np.maximum(obj, erode(box, 1) * (lum < 0.6 * skin_lum)), 0, 1)
        return layer
    # lente clara: so a ARMACAO (traco escuro fino) volta; atras da lente fica o rosto novo com o tom da lente
    loc = np.zeros_like(lum)
    r = max(2, bh // 10)
    pad = np.pad(lum, r, mode="edge")
    c = np.pad(pad.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    k = 2 * r + 1
    loc = (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)
    dark = ((box > 0.5) & (lum < loc - 18)).astype(np.float32)
    dark = erode(dilate(dark, 1), 1) * box
    # armacao = traco escuro LIGADO a borda dos oculos; mancha escura solta dentro da lente (o OLHO original) nao e
    # armacao - se fosse colada de volta, o olho da pessoa original apareceria atras da lente (spec 46.3)
    edge = ((box > 0.5) & ~(erode(box, max(3, min(bw, bh) // 6)) > 0.5)).astype(np.float32)  # faixa da borda (caixa com folga)
    cur = (dark * edge) > 0.5
    for _ in range(max(bw, bh)):
        nxt = (dilate(cur.astype(np.float32), 1) > 0.5) & (dark > 0.5)
        if (nxt == cur).all():
            break
        cur = nxt
    frame = cur.astype(np.float32)
    lens = np.clip(box - dilate(frame, 1), 0, 1) * inner
    layer.kind, layer.mask, layer.lens = "glasses_clear", frame, lens
    if lens.any() and ring.any():
        tin = original.astype(np.float32)[lens > 0.5].mean(axis=0)
        tout = original.astype(np.float32)[ring].mean(axis=0)
        layer.tint = np.clip(tin / np.maximum(tout, 1.0), 0.45, 1.05)
    return layer


def composite_layers(pixels: np.ndarray, original: np.ndarray, layers: list[AccessoryLayer],
                     within: np.ndarray | None = None) -> np.ndarray:
    """Camadas PRESERVE por cima, da mais funda para a mais da frente. `within`: so onde a passada mexeu."""
    out = pixels.astype(np.float32)
    for layer in sorted((x for x in layers if x.policy == "PRESERVE"), key=lambda x: x.order):
        lim = 1.0 if within is None else (within > 0.02)[..., None]
        if layer.lens is not None and layer.tint is not None:  # interacao optica: tom da lente sobre o rosto novo
            lw = np.clip(feather(layer.lens, 2), 0, 1)[..., None] * lim
            out = out * (1 - lw) + out * layer.tint[None, None, :] * lw
        # miolo do objeto 100% opaco (armacao fina nao some) e borda suave so para FORA
        core = (layer.mask > 0.5).astype(np.float32)
        a = np.clip(np.maximum(core, feather(dilate(core, 1), 2)), 0, 1)[..., None] * lim
        out = original.astype(np.float32) * a + out * (1 - a)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def preserved_mask(layers: list[AccessoryLayer]) -> np.ndarray | None:
    ms = [x.mask for x in layers if x.policy == "PRESERVE" and (x.mask > 0.5).any()]
    return None if not ms else np.clip(np.maximum.reduce(ms), 0, 1)


__all__ = ["AccessoryLayer", "build_layer", "composite_layers", "preserved_mask"]
