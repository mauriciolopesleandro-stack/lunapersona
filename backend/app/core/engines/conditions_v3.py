"""Condicoes estruturais da Replacement V3 (Full Person Reconstruction).

A foto da a ESTRUTURA (onde, como, pose, olhar, luz, camera, roupa, acessorios, cena); o Persona Canon da QUEM.
Aqui ficam as condicoes e as medidas que comparam a foto com o resultado SEM exigir pixel igual:

- PoseCondition ......... esqueleto DWPose (corpo + maos + rosto) - entra no ControlNet de pose
- FaceGeometryCondition . rolagem (linha dos olhos), guinada (nariz x olhos), centro/tamanho do rosto
- GazeCondition ......... linha dos olhos + orientacao da cabeca. NAO ha rastreador de iris: o "olhar" e aproximado
                          pela orientacao da cabeca e dos olhos (os 68 pontos do rosto entram no mapa de pose)
- DepthCondition ........ profundidade da foto LIMPA (sem tatuagem) so como estrutura espacial, forca baixa
- ClothingCondition ..... cada peca (cima/baixo) com cor medida, cobertura, comprimento e alcas - vira TEXTO e
                          medida; a roupa e re-renderizada no corpo da Persona, nao colada por pixel
- AccessoryCondition .... camadas de acessorio PRESERVE (composicao controlada depois)
- OcclusionMap .......... objetos na frente da pessoa (celular, acessorios mantidos) voltam por cima

Medidas: geometria do rosto, consistencia da roupa (sem pixel matching), alca/peca inventada na pele, silhueta
(larguras na linha do ombro/cintura/quadril), halo na borda da pessoa.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from app.core.engines.skin_continuity import rgb_to_lab
from app.core.persona_replacement.segmentation import dilate, erode, skin_pixels
from app.core.validation.geometry import point

_COLOR_NAMES = (("black", (12, 0, 0)), ("white", (95, 0, 0)), ("light gray", (75, 0, 0)), ("gray", (55, 0, 0)),
                ("beige", (78, 4, 16)), ("brown", (35, 12, 22)), ("red", (45, 60, 40)), ("pink", (70, 30, 5)),
                ("blue", (40, 10, -45)), ("light blue", (70, -5, -25)), ("green", (45, -40, 30)), ("yellow", (85, -5, 70)),
                ("orange", (65, 40, 65)), ("purple", (35, 40, -40)), ("navy", (20, 10, -35)), ("denim blue", (50, 0, -25)))


def color_name(lab: np.ndarray) -> str:
    L, a, b = (float(v) for v in lab)
    if abs(a) < 6 and abs(b) < 8:  # neutro: pela luminancia
        return "black" if L < 25 else "dark gray" if L < 45 else "gray" if L < 65 else "light gray" if L < 85 else "white"
    return min(_COLOR_NAMES, key=lambda c: (c[1][0] - L) ** 2 * 0.3 + (c[1][1] - a) ** 2 + (c[1][2] - b) ** 2)[0]


# --- pose / rosto / olhar -------------------------------------------------------------------------------

@dataclass
class FaceGeometryCondition:
    center: tuple[float, float]
    size: float
    roll_deg: float | None  # inclinacao da linha dos olhos
    yaw: float | None  # -1..1: nariz entre os olhos (0 = de frente)
    eye_line: tuple[tuple[float, float], tuple[float, float]] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def face_geometry(face) -> FaceGeometryCondition | None:
    if face is None:
        return None
    x1, y1, x2, y2 = face.bbox
    kps = list(getattr(face, "kps", []) or [])
    roll = yaw = eyes = None
    if len(kps) >= 3:
        (lx, ly), (rx, ry), (nx, _ny) = kps[0][:2], kps[1][:2], kps[2][:2]
        roll = round(math.degrees(math.atan2(ry - ly, rx - lx)), 2)
        half = max(1.0, abs(rx - lx) / 2)
        yaw = round(float(np.clip((nx - (lx + rx) / 2) / half, -1, 1)), 3)
        eyes = ((float(lx), float(ly)), (float(rx), float(ry)))
    return FaceGeometryCondition(((x1 + x2) / 2, (y1 + y2) / 2), float(max(x2 - x1, y2 - y1)), roll, yaw, eyes)


@dataclass
class GazeCondition:
    """Aproximacao: orientacao da cabeca (guinada/rolagem) + linha dos olhos. Sem rastreador de iris."""
    yaw: float | None
    roll_deg: float | None
    toward_camera: bool | None
    method: str = "head_orientation+eye_line (sem iris)"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gaze(geom: FaceGeometryCondition | None) -> GazeCondition:
    if geom is None or geom.yaw is None:
        return GazeCondition(None, None, None)
    return GazeCondition(geom.yaw, geom.roll_deg, abs(geom.yaw) < 0.25)


def compare_face_geometry(src: FaceGeometryCondition | None, out: FaceGeometryCondition | None) -> dict[str, Any]:
    if src is None or out is None:
        return {"status": "UNKNOWN"}
    d = {"center_shift": round(math.dist(src.center, out.center) / max(1.0, src.size), 3),
         "size_ratio": round(out.size / max(1.0, src.size), 3)}
    if src.roll_deg is not None and out.roll_deg is not None:
        d["roll_diff_deg"] = round(abs(src.roll_deg - out.roll_deg), 2)
    if src.yaw is not None and out.yaw is not None:
        d["yaw_diff"] = round(abs(src.yaw - out.yaw), 3)
    return d


# --- roupa ----------------------------------------------------------------------------------------------

@dataclass
class Garment:
    part: str  # top | bottom | full
    color: str
    lab: list[float]
    area_frac: float
    bbox: list[float]
    straps: bool | None = None  # so para "top": ha tecido sobre os ombros?
    length: str = ""

    def text(self) -> str:
        bits = [self.color]
        if self.part == "top" and self.straps is False:
            bits.append("strapless")
        if self.length:
            bits.append(self.length)
        return " ".join(bits)


@dataclass
class ClothingCondition:
    garments: list[Garment] = field(default_factory=list)
    caption: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"garments": [asdict(g) for g in self.garments], "caption": self.caption, "prompt": self.prompt()}

    def prompt(self) -> str:
        """Texto da roupa para a reconstrucao: legenda da cena (tipo/material) + o que foi MEDIDO (cor, alcas, corte)."""
        parts = []
        if self.caption:
            parts.append(self.caption)
        for g in self.garments:
            noun = {"top": "top", "bottom": "bottom", "full": "outfit"}[g.part]
            parts.append(f"{g.text()} {noun}")
        if any(g.part == "top" and g.straps is False for g in self.garments):
            parts.append("bare shoulders, no straps")
        return ", ".join(dict.fromkeys(p for p in parts if p))

    def negative(self) -> list[str]:
        out = ["different clothes", "changed outfit color", "extra garment", "added pattern", "logo"]
        if any(g.part == "top" and g.straps is False for g in self.garments):
            out += ["straps", "shoulder strap", "spaghetti straps", "bra strap"]
        return out


def clothing_condition(original: np.ndarray, clothes: np.ndarray | None, kp, caption: str = "") -> ClothingCondition:
    cond = ClothingCondition(caption=caption)
    if clothes is None or not (clothes > 0.5).any():
        return cond
    h, w = clothes.shape
    m = clothes > 0.5
    hip_y = None
    if kp:
        hips = [p for p in (point(kp, "rhip"), point(kp, "lhip")) if p]
        if hips:
            hip_y = int(sum(p[1] for p in hips) / len(hips))
    # cima x baixo: corte na linha do quadril (ou na faixa sem roupa entre as duas pecas)
    rows = m.mean(axis=1)
    ys = np.nonzero(rows > 0)[0]
    split = None
    if hip_y is not None:
        lo, hi = max(int(ys.min()), hip_y - h // 6), min(int(ys.max()), hip_y + h // 20)
        band = rows[lo:hi]
        split = lo + int(np.argmin(band)) if band.size else hip_y
    parts = [("top", m.copy()), ("bottom", np.zeros_like(m))] if split is None else \
        [("top", m & (np.arange(h)[:, None] < split)), ("bottom", m & (np.arange(h)[:, None] >= split))]
    if split is not None and (parts[1][1].sum() < 0.08 * m.sum() or parts[0][1].sum() < 0.08 * m.sum()):
        parts = [("full", m)]  # vestido/macacao: uma peca so
    lab = rgb_to_lab(original)
    for part, pm in parts:
        if pm.sum() < 200:
            continue
        core = erode(pm.astype(np.float32), 2) > 0.5
        sel = core if core.sum() >= 100 else pm
        med = np.median(lab[sel], axis=0)
        yy, xx = np.nonzero(pm)
        g = Garment(part, color_name(med), [round(float(v), 1) for v in med], round(float(pm.mean()), 4),
                    [float(xx.min()), float(yy.min()), float(xx.max()), float(yy.max())])
        if part in ("top", "full") and kp:
            straps = []
            for sho in ("rsho", "lsho"):
                s = point(kp, sho)
                if s:
                    r = max(4, int(h * 0.02))
                    y0 = max(0, int(s[1]) - r)
                    win = m[y0:max(y0 + 1, int(s[1]) + r), max(0, int(s[0]) - r):max(1, int(s[0]) + r)]
                    if win.size:  # ombro fora da imagem: sem medida
                        straps.append(bool(win.mean() > 0.15))
            g.straps = any(straps) if straps else None
        if part in ("top", "full") and hip_y is not None:
            g.length = "cropped above the navel" if yy.max() < hip_y - h * 0.03 else ""
        if part == "bottom" and kp:
            knees = [p for p in (point(kp, "rkne"), point(kp, "lkne")) if p]
            if knees:
                ky = sum(p[1] for p in knees) / len(knees)
                g.length = "short, above mid-thigh" if yy.max() < (hip_y or ky) + (ky - (hip_y or ky)) * 0.6 else \
                    "above the knee" if yy.max() < ky else "long, below the knee"
        cond.garments.append(g)
    return cond


def clothing_consistency(original: np.ndarray, final: np.ndarray, clothes_o: np.ndarray | None,
                         clothes_f: np.ndarray | None, person_o: np.ndarray, kp) -> dict[str, Any]:
    """Identidade VISUAL da roupa sem pixel igual: cor de cada peca, cobertura, forma (IoU com folga) e PECA NOVA na pele
    (porta 2026-10-08: alca inventada no tomara-que-caia)."""
    if clothes_o is None or clothes_f is None or not (clothes_o > 0.5).any():
        return {"status": "UNKNOWN", "reason": "roupa nao segmentada na foto ou no resultado"}
    co, cf = clothes_o > 0.5, clothes_f > 0.5
    lab_o, lab_f = rgb_to_lab(original), rgb_to_lab(final)
    out: dict[str, Any] = {}
    cond_o = clothing_condition(original, clothes_o, kp)
    cond_f = clothing_condition(final, clothes_f, kp)
    worst_color = 0.0
    per = []
    for go in cond_o.garments:
        gf = next((g for g in cond_f.garments if g.part == go.part), None)
        if gf is None:
            per.append({"part": go.part, "missing": True})
            worst_color = max(worst_color, 99.0)
            continue
        de = float(np.linalg.norm(np.array(go.lab) - np.array(gf.lab)))
        worst_color = max(worst_color, de)
        per.append({"part": go.part, "color_o": go.color, "color_f": gf.color, "dE": round(de, 2),
                    "straps_o": go.straps, "straps_f": gf.straps, "area_ratio": round(gf.area_frac / max(1e-6, go.area_frac), 3)})
    out["garments"] = per
    out["color_dE_max"] = round(worst_color, 2)
    g = max(3, int(min(co.shape) * 0.02))
    inter = (cf & (dilate(co.astype(np.float32), g) > 0.5)).sum()
    out["shape_iou_loose"] = round(float(inter) / max(1.0, float((cf | co).sum())), 3)
    # peca NOVA na pele: roupa no resultado onde a foto tinha PELE e longe da roupa original
    skin_o = (skin_pixels(original) > 0.5) & (person_o > 0.5) & ~(dilate(co.astype(np.float32), g) > 0.5)
    new = cf & skin_o
    out["new_clothing_on_skin"] = round(float(new.sum()) / max(1.0, float(co.sum())), 4)
    out["straps_invented"] = any(p.get("straps_o") is False and p.get("straps_f") is True for p in per)
    out["status"] = "MEASURED"
    return out


# --- silhueta --------------------------------------------------------------------------------------------

def silhouette(person: np.ndarray | None, kp) -> dict[str, Any]:
    """Larguras da pessoa na linha dos ombros, cintura (meio ombro-quadril, mais baixo) e quadril. Razoes so como
    medida (sem limite): o DWPose nao mede volume e a pose muda a largura 2D."""
    if person is None or not kp:
        return {}
    pts = {k: point(kp, k) for k in ("rsho", "lsho", "rhip", "lhip")}
    if not all(pts.values()):
        return {}
    m = person > 0.5
    h, w = m.shape

    def width(y: float) -> float | None:
        yi = int(np.clip(y, 0, h - 1))
        row = m[max(0, yi - 2):yi + 3].any(axis=0)
        xs = np.nonzero(row)[0]
        return float(xs.max() - xs.min()) if xs.size else None

    sy = (pts["rsho"][1] + pts["lsho"][1]) / 2 + 0.05 * abs(pts["rsho"][0] - pts["lsho"][0])
    hy = (pts["rhip"][1] + pts["lhip"][1]) / 2
    wy = sy + (hy - sy) * 0.7
    ws, ww, wh = width(sy), width(wy), width(hy)
    if not (ws and ww and wh):
        return {}
    return {"shoulder_px": ws, "waist_px": ww, "hip_px": wh, "waist_hip": round(ww / wh, 3),
            "waist_shoulder": round(ww / ws, 3), "hip_shoulder": round(wh / ws, 3)}


# --- borda / halo ----------------------------------------------------------------------------------------

def boundary_halo(original: np.ndarray, final: np.ndarray, person_o: np.ndarray, region: np.ndarray,
                  person_f: np.ndarray | None = None) -> dict[str, Any]:
    """Halo = fundo levemente repintado em volta da pessoa: na faixa reconstruida que e FUNDO no resultado,
    diferenca de luminancia/cor e de nitidez contra a foto. Fundo devolvido = 0."""
    reg = region > 0.5
    fg = (person_f > 0.5) if person_f is not None else (person_o > 0.5)
    band = reg & ~(dilate(fg.astype(np.float32), 2) > 0.5)
    if band.sum() < 100:
        return {"status": "UNKNOWN", "band_px": int(band.sum())}
    lo, lf = rgb_to_lab(original), rgb_to_lab(final)
    d = np.linalg.norm(lo - lf, axis=-1)
    bg_like = band & (d < 30)  # o que e claramente objeto novo (cabelo da Persona) nao e halo
    if bg_like.sum() < 50:
        return {"status": "UNKNOWN", "band_px": int(band.sum())}

    def sharp(img):
        y = rgb_to_lab(img)[..., 0]
        p = np.pad(y, 1, mode="edge")
        return np.abs(p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4 * y)

    so, sf = sharp(original)[bg_like], sharp(final)[bg_like]
    return {"status": "MEASURED", "halo_dE": round(float(d[bg_like].mean()), 2),
            "halo_dL": round(float((lf[..., 0] - lo[..., 0])[bg_like].mean()), 2),
            "sharpness_ratio": round(float(sf.mean() / max(1e-3, so.mean())), 3), "band_px": int(bg_like.sum())}


__all__ = ["ClothingCondition", "FaceGeometryCondition", "Garment", "GazeCondition", "boundary_halo", "clothing_condition",
           "clothing_consistency", "color_name", "compare_face_geometry", "face_geometry", "gaze", "silhouette"]
