"""Skin Continuity + Photometric Integration (spec V2.1, secoes 11-15, 30-34).

PERSONA = o que a pele E (tom/subtom/textura da Luna: vem do rosto reconstruido com a identidade)
FOTO    = como a luz BATE na pele (a diferenca de luz entre rosto, pescoco, ombros, bracos, maos e pernas
          medida na FOTO ORIGINAL)

Para cada regiao de pele exposta r, o alvo e:  F_rosto + (O_r - O_rosto)  em Lab
(F = imagem final, O = foto original). Ou seja: a pele inteira com o tom da Luna, e a variacao de luz entre as
regioes igual a da foto. A correcao e so de BAIXA frequencia (um campo de deslocamento Lab suave sobre a pele):
textura, poros, sombras e brilhos locais ficam - nao e recolorir RGB nem pintar o corpo. Pele coberta pela roupa
nao e tocada.

Validadores: SkinContinuity (transicoes rosto->pescoco->ombros->bracos->maos, corpo->pernas), SkinIdentity (tom do
rosto/corpo x master, so como aviso) e Photometric (grao/nitidez e brilho maximo da pele x foto). Limites sao
PRELIMINARES (sem benchmark): ver config skin_continuity.thresholds.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.segmentation import dilate, erode, skin_pixels
from app.core.validation.geometry import point

REGIONS = ("face", "neck", "shoulders", "torso", "arms", "hands", "legs")
TRANSITIONS = (("face", "neck"), ("neck", "shoulders"), ("shoulders", "arms"), ("arms", "hands"), ("torso", "legs"),
               ("face", "torso"))
MIN_PX = 150
# a relacao de COR entre regioes na foto original carrega maquiagem/base da pessoa original (quarto 2026-10-08:
# base clara no rosto); da foto vem a luz inteira na luminancia e so metade na cor
AB_FROM_PHOTO = 0.5


# --- Lab ----------------------------------------------------------------------------------------------

def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    x = rgb.astype(np.float32) / 255.0
    lin = np.where(x > 0.04045, ((x + 0.055) / 1.055) ** 2.4, x / 12.92)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)  # noqa: E731
    fx, fy, fz = f(X), f(Y), f(Z)
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1).astype(np.float32)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    inv = lambda t: np.where(t ** 3 > 0.008856, t ** 3, (t - 16 / 116) / 7.787)  # noqa: E731
    X, Y, Z = inv(fx) * 0.95047, inv(fy), inv(fz) * 1.08883
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    bb = 0.0557 * X - 0.2040 * Y + 1.0570 * Z
    lin = np.clip(np.stack([r, g, bb], axis=-1), 0, 1)
    srgb = np.where(lin > 0.0031308, 1.055 * np.power(lin, 1 / 2.4) - 0.055, 12.92 * lin)
    return np.clip(srgb * 255 + 0.5, 0, 255).astype(np.uint8)


def _box(y: np.ndarray, r: int) -> np.ndarray:
    k = 2 * r + 1
    c = np.pad(np.pad(y.astype(np.float64), ((r, r), (r, r)) + ((0, 0),) * (y.ndim - 2), mode="edge")
               .cumsum(0).cumsum(1), ((1, 0), (1, 0)) + ((0, 0),) * (y.ndim - 2))
    return ((c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)).astype(np.float32)


# --- regioes ------------------------------------------------------------------------------------------

def exposed_skin(img: np.ndarray, masks: dict[str, np.ndarray]) -> np.ndarray:
    """Pele visivel: detector de pele, dentro da pessoa, fora da roupa/acessorio/cabelo ORIGINAL, e com a cor da pele
    do rosto (o cabelo NOVO da Persona fica onde era pele na foto e o detector o confunde com pele morena)."""
    h, w = img.shape[:2]
    z = np.zeros((h, w))
    skin = (skin_pixels(img) > 0.5) & (masks["person_mask"] > 0.5) & ~(masks.get("clothing_mask", z) > 0.5)
    skin &= ~(masks.get("hair_mask", z) > 0.5) & ~(masks.get("accessory_mask", z) > 0.5)
    face = erode(((masks["face_mask"] > 0.5) & skin).astype(np.float32), 2) > 0.5
    if face.sum() >= MIN_PX:
        lab = rgb_to_lab(img)
        ref = np.median(lab[face], axis=0)
        chroma_ref = float(np.hypot(ref[1], ref[2]))
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        skin &= (lab[..., 0] > ref[0] - 30) & (chroma > chroma_ref * 0.45)  # cabelo: bem mais escuro e menos saturado
    return skin


def skin_regions(img: np.ndarray, masks: dict[str, np.ndarray], kp, face_bbox) -> dict[str, np.ndarray]:
    """Regioes de pele EXPOSTA (pele detectada na imagem, fora da roupa) por parte do corpo."""
    h, w = img.shape[:2]
    skin = exposed_skin(img, masks)
    face = (masks["face_mask"] > 0.5) & skin
    face = erode(face.astype(np.float32), 2) > 0.5  # sem a borda (cabelo/fundo misturados)
    out = {"face": face}
    neck = (masks.get("neck_mask", np.zeros((h, w))) > 0.5) & skin & ~face
    out["neck"] = neck
    hands = (masks.get("hand_mask", np.zeros((h, w))) > 0.5) & skin
    out["hands"] = hands
    arms = (masks.get("arm_mask", np.zeros((h, w))) > 0.5) & skin & ~hands
    legs = (masks.get("leg_mask", np.zeros((h, w))) > 0.5) & skin
    shoulders = np.zeros((h, w), bool)
    if kp:
        rs, ls = point(kp, "rsho"), point(kp, "lsho")
        if rs and ls:
            r = max(4.0, abs(rs[0] - ls[0]) * 0.22)
            yy, xx = np.mgrid[0:h, 0:w]
            for p in (rs, ls):
                shoulders |= (xx - p[0]) ** 2 + (yy - p[1]) ** 2 <= r * r
    shoulders &= skin & ~face & ~neck
    out["shoulders"] = shoulders
    out["arms"] = arms & ~shoulders
    out["legs"] = legs & ~hands
    taken = face | neck | hands | out["arms"] | shoulders | out["legs"]
    out["torso"] = skin & ~taken & ~(dilate((masks["face_mask"] > 0.5).astype(np.float32), 6) > 0.5)
    return out


def region_stats(lab: np.ndarray, regions: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: np.median(lab[m], axis=0) for k, m in regions.items() if m.sum() >= MIN_PX}


# --- motor --------------------------------------------------------------------------------------------

@dataclass
class ContinuityResult:
    pixels: np.ndarray
    applied: bool
    offsets: dict[str, list[float]] = field(default_factory=dict)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"applied": self.applied, "offsets": self.offsets, "before": self.before, "after": self.after,
                "note": self.note}


def continuity_residuals(final: np.ndarray, original: np.ndarray, masks: dict[str, np.ndarray], kp,
                         face_bbox) -> dict[str, Any]:
    """Para cada transicao a->b: |(F_b - F_a) - (O_b - O_a)| em Lab (dE). 0 = a variacao de pele entre as duas
    regioes e exatamente a variacao de luz que a foto ja tinha."""
    fr, orr = skin_regions(final, masks, kp, face_bbox), skin_regions(original, masks, kp, face_bbox)
    fs, os_ = region_stats(rgb_to_lab(final), fr), region_stats(rgb_to_lab(original), orr)
    res = {}
    for a, b in TRANSITIONS:
        if a in fs and b in fs and a in os_ and b in os_:
            d = (fs[b] - fs[a]) - (os_[b] - os_[a]) * np.array([1.0, AB_FROM_PHOTO, AB_FROM_PHOTO], np.float32)
            res[f"{a}->{b}"] = {"dE": round(float(np.linalg.norm(d)), 2), "dL": round(float(d[0]), 2),
                                "da": round(float(d[1]), 2), "db": round(float(d[2]), 2)}
    worst = max((v["dE"] for v in res.values()), default=None)
    return {"transitions": res, "worst": worst, "regions": sorted(fs)}


def harmonize(final: np.ndarray, original: np.ndarray, masks: dict[str, np.ndarray], kp, face_bbox,
              max_dl: float = 14.0, max_dab: float = 8.0, tol_dl: float = 3.0, tol_dab: float = 2.0,
              smooth_frac: float = 0.03, boost: float = 1.0) -> ContinuityResult:
    """SkinContinuityEngine + SkinToneTransfer: campo de deslocamento Lab SUAVE so na pele exposta (rosto = ref.)."""
    h, w = final.shape[:2]
    fr, orr = skin_regions(final, masks, kp, face_bbox), skin_regions(original, masks, kp, face_bbox)
    lab = rgb_to_lab(final)
    fs, os_ = region_stats(lab, fr), region_stats(rgb_to_lab(original), orr)
    before = continuity_residuals(final, original, masks, kp, face_bbox)
    if "face" not in fs or "face" not in os_:
        return ContinuityResult(final, False, before=before, note="rosto sem pele medivel: sem referencia de tom")
    acc = np.zeros((h, w, 3), np.float32)
    wsum = np.zeros((h, w), np.float32)
    offsets = {}
    for r in REGIONS:
        if r == "face" or r not in fs or r not in os_:
            continue
        target = fs["face"] + (os_[r] - os_["face"]) * np.array([1.0, AB_FROM_PHOTO, AB_FROM_PHOTO], np.float32)
        d = target - fs[r]
        d = np.array([np.clip(d[0], -max_dl * boost, max_dl * boost), np.clip(d[1], -max_dab * boost, max_dab * boost),
                      np.clip(d[2], -max_dab * boost, max_dab * boost)], np.float32)
        if abs(d[0]) < tol_dl and abs(d[1]) < tol_dab and abs(d[2]) < tol_dab:
            d = np.zeros(3, np.float32)  # ja continua: entra no campo como "nao mexe" (nao herda o vizinho)
        else:
            offsets[r] = [round(float(v), 2) for v in d]
        m = fr[r].astype(np.float32)
        acc += m[..., None] * d[None, None, :]
        wsum += m
    if not offsets:
        return ContinuityResult(final, False, before=before, after=before, note="pele ja continua")
    rad = max(3, int(min(h, w) * smooth_frac))
    known = wsum > 0
    # o rosto entra como "deslocamento zero" no campo (e a referencia): o campo vai a zero perto dele, sem degrau
    face_w = fr["face"].astype(np.float32)
    field_num = _box(acc, rad)
    field_den = _box(wsum + face_w, rad)
    fld = field_num / np.maximum(field_den, 1e-3)[..., None]
    skin = exposed_skin(final, masks)
    skin &= ~(fr["face"]) & (dilate(known.astype(np.float32), rad) > 0.5)
    wgt = np.clip(feather(skin.astype(np.float32), max(2, rad // 3)), 0, 1)[..., None] * skin[..., None]
    out_lab = lab + fld * wgt
    out = lab_to_rgb(out_lab)
    # so onde mexeu: o resto e o pixel exato (lab_to_rgb tem arredondamento)
    out = np.where(wgt > 0.002, out, final)
    after = continuity_residuals(out, original, masks, kp, face_bbox)
    return ContinuityResult(out, True, offsets, before, after)


# --- medidas para os validadores ----------------------------------------------------------------------

def master_skin_lab(master_png: bytes | None) -> np.ndarray | None:
    if not master_png:
        return None
    try:
        from PIL import Image

        img = np.asarray(Image.open(io.BytesIO(master_png)).convert("RGB"))
    except Exception:  # noqa: BLE001
        return None
    sk = skin_pixels(img) > 0.5
    if sk.sum() < 500:
        return None
    return np.median(rgb_to_lab(img)[sk], axis=0)


def skin_identity(final: np.ndarray, regions: dict[str, np.ndarray], master_lab: np.ndarray | None) -> dict[str, Any]:
    """Subtom (angulo de matiz a*b*) da pele final x master. A luz da cena muda o brilho; o matiz muda menos."""
    if master_lab is None:
        return {"hue_diff_deg": None, "note": "master sem pele medivel"}
    lab = rgb_to_lab(final)
    sel = np.zeros(final.shape[:2], bool)
    for k in ("face", "neck", "arms", "torso", "legs"):
        if k in regions:
            sel |= regions[k]
    if sel.sum() < MIN_PX:
        return {"hue_diff_deg": None, "note": "pouca pele exposta"}
    f = np.median(lab[sel], axis=0)
    hue = lambda v: float(np.degrees(np.arctan2(v[2], v[1])))  # noqa: E731
    chroma = lambda v: float(np.hypot(v[1], v[2]))  # noqa: E731
    return {"hue_diff_deg": round(abs(hue(f) - hue(master_lab)), 2), "chroma_final": round(chroma(f), 2),
            "chroma_master": round(chroma(master_lab), 2), "L_final": round(float(f[0]), 1), "L_master": round(float(master_lab[0]), 1)}


def photometric(final: np.ndarray, original: np.ndarray, modified: np.ndarray, regions_final: dict[str, np.ndarray],
                regions_orig: dict[str, np.ndarray]) -> dict[str, Any]:
    """Grao/nitidez da regiao refeita x resto da foto, e brilho maximo (p95) da pele do rosto x foto."""
    from app.core.persona_replacement.lighting import noise_level

    mod = modified > 0.5
    rest = ~(dilate(mod.astype(np.float32), 6) > 0.5)
    nm, nr = noise_level(final, mod.astype(np.float32)), noise_level(final, rest.astype(np.float32))
    ratio = None if not nm or not nr else round(nm / nr, 3)
    hl = None
    if "face" in regions_final and "face" in regions_orig and regions_final["face"].sum() >= MIN_PX \
            and regions_orig["face"].sum() >= MIN_PX:
        lf, lo = rgb_to_lab(final)[..., 0], rgb_to_lab(original)[..., 0]
        hl = round(float(np.percentile(lf[regions_final["face"]], 95) - np.percentile(lo[regions_orig["face"]], 95)), 2)
    return {"grain_ratio": ratio, "face_highlight_dL": hl}


__all__ = ["ContinuityResult", "REGIONS", "TRANSITIONS", "continuity_residuals", "harmonize", "lab_to_rgb",
           "master_skin_lab", "photometric", "rgb_to_lab", "skin_identity", "skin_regions"]
