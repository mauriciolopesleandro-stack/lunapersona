"""Replacement V3.1: preservacao da roupa, pele so onde a segmentacao diz que e pele, e borda sem halo.

Auditoria de 09/10 (artefatos da V3 nas fotos do espelho e da rua):
- a roupa NAO foi aceita como roupa nas duas fotos (0,1% e 1,1% da pessoa): a mascara do Florence so valia se batesse
  com um teste de COR ("nao parece pele"); top claro e calca bege reprovam -> a roupa virou "pele";
- as manchas do braco e da calca foram criadas pela continuidade de pele, que escolhia pele pixel a pixel pela cor;
- o halo e uma faixa de fundo repintado (6-12 px) que a devolucao do fundo por tolerancia de cor (28) nao recupera
  em fundo com textura (cerca viva, muro);
- a reconstrucao (denoise 0,85) alisa o tecido (calca canelada ja sai lisa na reconstrucao inicial).

Tudo aqui e numpy/PIL (testavel sem GPU). Quem chama (replacement_v3) faz a segmentacao da imagem reconstruida e
o passe local leve nas emendas da roupa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.segmentation import dilate, erode, skin_pixels
from app.core.validation.geometry import point


# --- roupa confiavel (Florence) -----------------------------------------------------------------------

def trusted_clothes(raw_clothes: np.ndarray | None, person: np.ndarray, min_frac: float = 0.04,
                    min_inside: float = 0.8) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Mascara de roupa do Florence aceita pelo MATERIAL (o detector disse roupa), nao pela cor. Vale se ocupa uma
    parte razoavel da pessoa e fica dentro dela. Cor parecida com pele so vira AMBIGUIDADE registrada."""
    info: dict[str, Any] = {"source": "florence_referring_segmentation", "version": "v3.1"}
    if raw_clothes is None:
        return None, {**info, "status": "ausente"}
    c = raw_clothes > 0.5
    p = person > 0.5
    if not c.any() or not p.any():
        return None, {**info, "status": "vazia"}
    frac = float((c & p).sum()) / float(p.sum())
    inside = float((c & p).sum()) / float(c.sum())
    info.update(frac_of_person=round(frac, 3), inside_person=round(inside, 3))
    if frac < min_frac or inside < min_inside:
        return None, {**info, "status": "rejeitada", "confidence": round(min(frac / min_frac, inside / min_inside), 2)}
    out = (c & p).astype(np.float32)
    info.update(status="aceita", confidence=round(min(1.0, inside), 2))
    return out, info


def ambiguity(img: np.ndarray, clothes: np.ndarray | None, person: np.ndarray) -> dict[str, Any]:
    """Tecido com cor de pele (calca bege) e pele que a cor nao reconhece (sombra): so registro - nada e recolorido."""
    p = person > 0.5
    sk = skin_pixels(img) > 0.5
    c = (clothes > 0.5) if clothes is not None else np.zeros_like(p)
    fabric_like_skin = c & sk
    return {"tecido_cor_de_pele_px": int(fabric_like_skin.sum()),
            "tecido_cor_de_pele_frac": round(float(fabric_like_skin.sum()) / max(1.0, float(c.sum())), 3)}


# --- pecas de roupa -----------------------------------------------------------------------------------

def garment_masks(clothes: np.ndarray | None, kp) -> dict[str, np.ndarray]:
    """Separa a roupa em cima/baixo na linha do quadril (ou no vao entre as pecas); uma peca so = 'full'."""
    if clothes is None or not (clothes > 0.5).any():
        return {}
    m = clothes > 0.5
    h = m.shape[0]
    hip_y = None
    if kp:
        hips = [p for p in (point(kp, "rhip"), point(kp, "lhip")) if p]
        if hips:
            hip_y = int(sum(p[1] for p in hips) / len(hips))
    if hip_y is None:
        return {"full": m.astype(np.float32)}
    rows = m.mean(axis=1)
    ys = np.nonzero(rows > 0)[0]
    lo, hi = max(int(ys.min()), hip_y - h // 6), min(int(ys.max()), hip_y + h // 20)
    split = lo + int(np.argmin(rows[lo:hi])) if hi > lo else hip_y
    top = m & (np.arange(h)[:, None] < split)
    bottom = m & (np.arange(h)[:, None] >= split)
    if top.sum() < 0.08 * m.sum() or bottom.sum() < 0.08 * m.sum():
        return {"full": m.astype(np.float32)}
    return {"top": top.astype(np.float32), "bottom": bottom.astype(np.float32)}


def _bbox(m: np.ndarray):
    ys, xs = np.nonzero(m > 0.5)
    return float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)


@dataclass
class GarmentResult:
    part: str
    strategy: str  # pixel | aligned | reconstruct | none
    scale: tuple[float, float] = (1.0, 1.0)
    shift: tuple[float, float] = (0.0, 0.0)
    coverage: float = 0.0  # fracao da peca NOVA coberta pelos pixels originais
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"part": self.part, "strategy": self.strategy, "scale": [round(v, 3) for v in self.scale],
                "shift": [round(v, 1) for v in self.shift], "coverage": round(self.coverage, 3), "note": self.note}


@dataclass
class ClothingPreservation:
    pixels: np.ndarray
    pasted: np.ndarray  # onde os pixels ORIGINAIS da roupa entraram
    to_reconstruct: np.ndarray  # emendas + partes da roupa nova que os originais nao cobrem (passe local leve)
    garments: list[GarmentResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"garments": [g.to_dict() for g in self.garments],
                "pasted_px": int((self.pasted > 0.5).sum()), "reconstruct_px": int((self.to_reconstruct > 0.5).sum())}


def _warp(arr: np.ndarray, sx: float, sy: float, tx: float, ty: float, shape, resample) -> np.ndarray:
    """Imagem/mascara levada por x' = sx*x + tx, y' = sy*y + ty (PIL usa a inversa)."""
    h, w = shape
    im = Image.fromarray(arr)
    inv = (1.0 / sx, 0.0, -tx / sx, 0.0, 1.0 / sy, -ty / sy)
    return np.asarray(im.transform((w, h), Image.AFFINE, inv, resample=resample))


def preserve_clothing(current: np.ndarray, original: np.ndarray, clothes_o: np.ndarray | None,
                      clothes_n: np.ndarray | None, kp_o, kp_n, keep_out: np.ndarray | None = None,
                      max_scale_dev: float = 0.25, max_aspect_dev: float = 0.15, seam_px: int = 4) -> ClothingPreservation:
    """ClothingPreservationEngine. Para cada peca: A) pixels originais se a peca nova esta no mesmo lugar e tamanho;
    B) alinhamento geometrico controlado (escala e deslocamento pelas caixas, deformacao limitada) quando o corpo da
    Persona mudou o caimento; C) reconstrucao local so do que os originais nao cobrem + emendas. Nunca cola a roupa
    como textura plana fora da peca nova; cabelo, maos e acessorios (keep_out) ficam por cima como estao."""
    h, w = current.shape[:2]
    out = current.astype(np.float32).copy()
    pasted = np.zeros((h, w), np.float32)
    recon = np.zeros((h, w), np.float32)
    go, gn = garment_masks(clothes_o, kp_o), garment_masks(clothes_n, kp_n)
    results: list[GarmentResult] = []
    ko = np.zeros((h, w), bool) if keep_out is None else (keep_out > 0.5)
    for part, mn in gn.items():
        mo = go.get(part)
        if mo is None and "full" in go:
            mo = go["full"]
        if mo is None or (mo > 0.5).sum() < 200:
            results.append(GarmentResult(part, "reconstruct", note="peca nao achada na foto original"))
            continue
        ox1, oy1, ox2, oy2 = _bbox(mo)
        nx1, ny1, nx2, ny2 = _bbox(mn)
        sx, sy = (nx2 - nx1) / max(1.0, ox2 - ox1), (ny2 - ny1) / max(1.0, oy2 - oy1)
        if abs(sx - 1) > max_scale_dev or abs(sy - 1) > max_scale_dev or abs(sx / sy - 1) > max_aspect_dev:
            results.append(GarmentResult(part, "reconstruct", (sx, sy), note="deformacao grande demais para alinhar sem "
                                                                             "esticar o tecido: so reconstrucao local"))
            recon = np.maximum(recon, mn)
            continue
        tx, ty = nx1 - sx * ox1, ny1 - sy * oy1
        identity = abs(sx - 1) < 0.03 and abs(sy - 1) < 0.03 and abs(tx) < 2 and abs(ty) < 2
        if identity:
            wimg, wmask = original, (mo > 0.5)
        else:
            wimg = _warp(original, sx, sy, tx, ty, (h, w), Image.BICUBIC)
            wmask = _warp(((mo > 0.5) * 255).astype(np.uint8), sx, sy, tx, ty, (h, w), Image.NEAREST) > 127
        inner_new = erode((mn > 0.5).astype(np.float32), 2) > 0.5
        area = inner_new & (erode(wmask.astype(np.float32), 2) > 0.5) & ~ko
        a = np.clip(feather(area.astype(np.float32), 2), 0, 1) * area
        out = out * (1 - a[..., None]) + wimg.astype(np.float32) * a[..., None]
        pasted = np.maximum(pasted, area.astype(np.float32))
        covered = float(area.sum()) / max(1.0, float((mn > 0.5).sum()))
        # emenda (anel na borda do que foi colado, dentro da peca) + o que os originais nao cobrem
        ring = (dilate(area.astype(np.float32), seam_px) > 0.5) & ~(erode(area.astype(np.float32), seam_px) > 0.5)
        rest = (mn > 0.5) & ~area
        recon = np.maximum(recon, ((ring & (dilate((mn > 0.5).astype(np.float32), 2) > 0.5)) | rest).astype(np.float32))
        results.append(GarmentResult(part, "pixel" if identity else "aligned", (sx, sy), (tx, ty), covered))
    recon = recon * (1 - ko)
    return ClothingPreservation(np.clip(out + 0.5, 0, 255).astype(np.uint8), pasted, recon, results)


# --- pele validada pela segmentacao --------------------------------------------------------------------

def validated_skin(person: np.ndarray, clothes: np.ndarray | None, hair: np.ndarray | None,
                   accessory: np.ndarray | None) -> np.ndarray:
    """Pele = pessoa - roupa - cabelo - acessorio (pela SEGMENTACAO da imagem atual), fechada e sem furinhos.
    Nunca 'pele porque a cor parece pele' (calca bege) nem 'nao pele porque esta na sombra' (braco)."""
    p = person > 0.5
    for m, g in ((clothes, 3), (hair, 2), (accessory, 2)):
        if m is not None:
            p &= ~(dilate((m > 0.5).astype(np.float32), g) > 0.5)
    f = p.astype(np.float32)
    return (erode(dilate(f, 2), 2) > 0.5).astype(np.float32) * (person > 0.5)


# --- borda: fundo original exato fora da pessoa nova ----------------------------------------------------

def boundary_alpha(person_new: np.ndarray, person_orig: np.ndarray, region: np.ndarray, soft_px: int = 2) -> np.ndarray:
    """Onde fica o resultado reconstruido: a pessoa NOVA (com 1 px de folga) + o 'fantasma' (onde estava a pessoa
    original e agora e fundo gerado - nao ha fundo original para devolver ali). Fora disso, dentro da regiao refeita,
    volta o fundo ORIGINAL exato (sem tolerancia de cor: a cerca viva/muro texturizados eram o halo de 09/10)."""
    pn = dilate((person_new > 0.5).astype(np.float32), 1) > 0.5
    ghost = (person_orig > 0.5) & ~pn
    keep = (pn | ghost) & (region > 0.5)
    a = np.clip(feather(keep.astype(np.float32), soft_px), 0, 1)
    return a * (dilate(keep.astype(np.float32), soft_px) > 0.5)


def halo_multiscale(original: np.ndarray, final: np.ndarray, person_new: np.ndarray, region: np.ndarray) -> dict[str, Any]:
    """Faixa de fundo (dentro da regiao refeita, fora da pessoa nova) em 3 escalas: 1, 1/2, 1/4. Um halo de 2-3 px
    some na escala cheia e aparece na reduzida (e vice-versa para grao)."""
    from app.core.engines.skin_continuity import rgb_to_lab

    out = {}
    for s in (1, 2, 4):
        if s == 1:
            o, f, pn, rg = original, final, person_new > 0.5, region > 0.5
        else:
            hh, ww = original.shape[0] // s, original.shape[1] // s
            o = np.asarray(Image.fromarray(original).resize((ww, hh), Image.BOX))
            f = np.asarray(Image.fromarray(final).resize((ww, hh), Image.BOX))
            pn = np.asarray(Image.fromarray(((person_new > 0.5) * 255).astype(np.uint8)).resize((ww, hh))) > 127
            rg = np.asarray(Image.fromarray(((region > 0.5) * 255).astype(np.uint8)).resize((ww, hh))) > 127
        band = rg & ~(dilate(pn.astype(np.float32), 1) > 0.5) & (dilate(pn.astype(np.float32), 8) > 0.5)
        if band.sum() < 30:
            continue
        d = np.linalg.norm(rgb_to_lab(o) - rgb_to_lab(f), axis=-1)
        out[f"escala_1/{s}"] = {"dE": round(float(d[band].mean()), 2), "px": int(band.sum())}
    out["pior"] = max((v["dE"] for v in out.values() if isinstance(v, dict)), default=None)
    return out


__all__ = ["ClothingPreservation", "GarmentResult", "ambiguity", "boundary_alpha", "garment_masks", "halo_multiscale",
           "preserve_clothing", "trusted_clothes", "validated_skin"]
