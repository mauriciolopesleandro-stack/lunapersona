"""Mascaras do Persona Replacement (contorno real, nunca retangulo).

  person          contorno da pessoa (SAM2)
  hair            cabelo (Florence "hair") dentro/colado na pessoa
  face_full       rosto pelos 5 pontos (olhos, nariz, boca) - identidade
  face_inner      miolo do rosto (olhos, nariz, boca) - refinamento
  face_transition faixa rosto/pescoco/linha do cabelo - integracao
  skin            pixels de pele dentro da pessoa (cor de pele em YCrCb)
  body_skin       pele do corpo (bracos, pernas, colo) - sem rosto, cabelo e protegidos
  clothing        pessoa - pele - cabelo - rosto: PROTEGIDA (nenhuma etapa mexe)
  protect         objetos a preservar (ex.: oculos escuros)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.core.persona_replacement.contracts import RawSegments


@dataclass
class MaskSet:
    person: np.ndarray
    hair: np.ndarray
    face_full: np.ndarray
    face_inner: np.ndarray
    face_transition: np.ndarray
    skin: np.ndarray
    body_skin: np.ndarray
    tattoos: np.ndarray
    clothing: np.ndarray
    protect: np.ndarray

    def get(self, name: str) -> np.ndarray:
        return getattr(self, name)

    def areas(self) -> dict[str, float]:
        total = float(self.person.size)
        return {k: round(float((getattr(self, k) > 0.5).sum()) / total, 5) for k in
                ("person", "hair", "face_full", "face_inner", "face_transition", "skin", "body_skin", "tattoos",
                 "clothing", "protect")}


def ellipse(h: int, w: int, cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    return ((((xx - cx) / max(rx, 1.0)) ** 2 + ((yy - cy) / max(ry, 1.0)) ** 2) <= 1.0).astype(np.float32)


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Max-filter quadrado de raio r (numpy puro, separavel)."""
    if r <= 0:
        return mask.copy()
    out = mask.astype(np.float32)
    h, w = out.shape
    # linhas e depois colunas, com borda zero (sem "dar a volta" na imagem)
    padded = np.pad(out, ((r, r), (0, 0)))
    out = np.max(np.stack([padded[s:s + h] for s in range(2 * r + 1)]), axis=0)
    padded = np.pad(out, ((0, 0), (r, r)))
    return np.max(np.stack([padded[:, s:s + w] for s in range(2 * r + 1)]), axis=0)


def erode(mask: np.ndarray, r: int) -> np.ndarray:
    return 1.0 - dilate(1.0 - mask, r)


def skin_pixels(rgb: np.ndarray) -> np.ndarray:
    """Classificador classico de pele em YCrCb (funciona em varios tons de pele)."""
    x = rgb.astype(np.float32)
    y = 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]
    cr = (x[..., 0] - y) * 0.713 + 128
    cb = (x[..., 2] - y) * 0.564 + 128
    return ((cr > 133) & (cr < 180) & (cb > 77) & (cb < 135) & (y > 35)).astype(np.float32)


def face_masks(h: int, w: int, bbox, kps) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x1, y1, x2, y2 = bbox
    fw, fh = x2 - x1, y2 - y1
    if kps and len(kps) >= 5:
        (lx, ly), (rx, ry), (nx, ny), (mlx, mly), (mrx, mry) = kps[:5]
        cx, cy = (lx + rx + mlx + mrx) / 4, (ly + ry + mly + mry) / 4
        iod = max(1.0, ((rx - lx) ** 2 + (ry - ly) ** 2) ** 0.5)
        full = ellipse(h, w, cx, cy + iod * 0.2, iod * 1.45, iod * 1.85)  # inclui maxilar e bochechas
        inner = ellipse(h, w, cx, cy, iod * 0.95, iod * 1.0)
    else:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        full = ellipse(h, w, cx, cy + fh * 0.06, fw * 0.55, fh * 0.62)
        inner = ellipse(h, w, cx, cy, fw * 0.36, fh * 0.38)
    band = max(3, int(min(fw, fh) * 0.12))
    transition = np.clip(dilate(full, band) - erode(full, band), 0, 1)
    # desce pelo pescoco: a juncao mandibula/pescoco e a mais critica
    neck = ellipse(h, w, (x1 + x2) / 2, y2 + fh * 0.15, fw * 0.32, fh * 0.28)
    transition = np.clip(transition + neck, 0, 1)
    return full, inner, transition


def tattoo_holes(skin: np.ndarray, person: np.ndarray, radius: int) -> np.ndarray:
    """Tatuagem = buraco dentro da pele: fechamento (dilata e erode) da pele menos a pele.
    Faixas largas (roupa) nao fecham com raio pequeno; tinta fina fecha."""
    closed = erode(dilate(skin, radius), radius) * person
    return np.clip(closed - skin, 0, 1)


def build_masks(raw: RawSegments, face_bbox, face_kps, rgb: np.ndarray) -> MaskSet:
    h, w = rgb.shape[:2]
    person = (raw.person > 0.5).astype(np.float32)
    hair = (raw.hair > 0.5).astype(np.float32) if raw.hair is not None else np.zeros((h, w), np.float32)
    hair = hair * dilate(person, 8)  # cabelo DELA (nao o de outra pessoa)
    full, inner, transition = face_masks(h, w, face_bbox, face_kps)
    protect = np.zeros((h, w), np.float32)
    for bx1, by1, bx2, by2 in raw.protect_boxes:
        protect = np.maximum(protect, ellipse(h, w, (bx1 + bx2) / 2, (by1 + by2) / 2, (bx2 - bx1) * 0.6, (by2 - by1) * 0.65))
    full = full * person
    inner = inner * person
    transition = transition * person * (1 - hair)
    skin = skin_pixels(rgb) * person
    face_zone = dilate(full, max(2, int((face_bbox[3] - face_bbox[1]) * 0.08)))
    radius = max(3, int(min(h, w) * 0.008))
    tattoos = np.clip(tattoo_holes(skin, person, radius) - face_zone - hair - protect, 0, 1)
    body_skin = np.clip(skin + tattoos - face_zone - hair - protect, 0, 1)
    clothing = np.clip(person - skin - tattoos - hair - face_zone, 0, 1)
    # nada que esteja protegido entra em etapa nenhuma
    full, inner, transition = (np.clip(m - protect, 0, 1) for m in (full, inner, transition))
    return MaskSet(person, hair, full, inner, transition, skin, body_skin, tattoos, clothing, protect)


__all__ = ["MaskSet", "build_masks", "tattoo_holes", "dilate", "ellipse", "erode", "face_masks", "skin_pixels"]
