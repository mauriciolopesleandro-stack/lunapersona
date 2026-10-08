"""Analise da foto ANTES da geracao (spec Master 5/6/7): dados ESTRUTURADOS, nunca imagem.

ReplacementSceneAnalysis junta o que os medidores ja leram (Florence-2: legenda/rotulos; DWPose: esqueleto e maos;
InsightFace: rostos; luz medida nos pixels) e o que a segmentacao separou (marcas, acessorios), e diz o que e cada
coisa: quantas pessoas, pose de cada membro, roupa, acessorios com politica, e os atributos INDESEJADOS da pessoa
original com o lugar no corpo ("tattoo_right_forearm"). Nenhum modelo gera nada aqui.

semantic_masks(): o conjunto de mascaras com nome (pessoa, rosto, pele, cabelo, mao, roupa, acessorio, tatuagem,
fundo, pescoco, braco, perna, identidade original, marcas originais). A de tatuagem tem prioridade sobre a de pele:
pele = pele - tatuagem (tatuagem = REMOVE, pele = RECONSTRUCT).

render_pose_map(): o esqueleto do DWPose desenhado (modo debug), para conferir no olho o que guiou a pose.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from app.core.validation.geometry import KP, point

# membros do DWPose (lado = lado DA PESSOA: "right" e o braco direito dela, a esquerda da foto se ela olha a camera)
LIMBS = {
    "right_upper_arm": ("rsho", "relb"), "right_forearm": ("relb", "rwri"),
    "left_upper_arm": ("lsho", "lelb"), "left_forearm": ("lelb", "lwri"),
    "right_thigh": ("rhip", "rkne"), "right_shin": ("rkne", "rank"),
    "left_thigh": ("lhip", "lkne"), "left_shin": ("lkne", "lank"),
}
_ARMS = ("right_upper_arm", "right_forearm", "left_upper_arm", "left_forearm")
_LEGS = ("right_thigh", "right_shin", "left_thigh", "left_shin")
_CLOTHING_WORDS = {
    "top": ("top", "shirt", "t-shirt", "blouse", "corset", "bra", "tank", "crop", "sweater", "hoodie", "jacket", "coat",
            "bikini top", "camisole"),
    "bottom": ("jeans", "shorts", "pants", "trousers", "skirt", "leggings", "bikini bottom"),
    "full": ("dress", "jumpsuit", "swimsuit", "romper", "gown"),
}
_PLACES = ("balcony", "beach", "bedroom", "bathroom", "kitchen", "living room", "street", "pool", "garden", "park", "gym",
           "office", "car", "restaurant", "bar", "mirror", "door", "hallway", "room", "studio", "terrace")


@dataclass
class ReplacementSceneAnalysis:
    person_count: int
    main_person: dict[str, Any]
    scene: dict[str, Any]
    pose: dict[str, Any]
    clothing: dict[str, Any]
    hair: dict[str, Any]
    accessories: list[dict[str, Any]]
    undesired_attributes: list[str]
    markings: list[dict[str, Any]] = field(default_factory=list)
    makeup: dict[str, Any] = field(default_factory=dict)
    occlusions: list[str] = field(default_factory=list)
    depth: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- pose ----------------------------------------------------------------------------------------

def _limb_state(kp, sho: str, elb: str, wri: str) -> str:
    s, e, w = point(kp, sho), point(kp, elb), point(kp, wri)
    if not (s and e):
        return "nao visivel"
    if not w:
        return "parcial (sem pulso)"
    if w[1] < s[1] - 5:
        return "levantado (mao acima do ombro)"
    v1, v2 = np.array(e) - np.array(s), np.array(w) - np.array(e)
    cos = float(v1 @ v2 / max(1e-6, np.linalg.norm(v1) * np.linalg.norm(v2)))
    return "esticado" if cos > 0.8 else "dobrado"


def _orientation(kp) -> str:
    a, b = point(kp, "rsho"), point(kp, "lsho")
    hip_a, hip_b = point(kp, "rhip"), point(kp, "lhip")
    if not (a and b):
        return "desconhecida"
    width = abs(a[0] - b[0])
    ref = None
    if hip_a and hip_b:
        ref = abs(((a[1] + b[1]) / 2) - ((hip_a[1] + hip_b[1]) / 2))
    if ref and width < ref * 0.35:
        return "de lado"
    # DWPose: ombro direito da pessoa a esquerda na foto = de frente; invertido = de costas
    return "de frente" if a[0] < b[0] else "de costas ou espelhada"


def _head(face) -> str:
    yaw = getattr(face, "yaw", None)
    if yaw is None and len(getattr(face, "kps", []) or []) >= 3:
        (lx, _), (rx, _), (nx, _) = face.kps[0], face.kps[1], face.kps[2]
        mid, half = (lx + rx) / 2, max(1.0, abs(rx - lx) / 2)
        yaw = float(np.clip((nx - mid) / half, -1, 1) * 60)
    if yaw is None:
        return "desconhecida"
    return "frontal" if abs(yaw) < 15 else ("virada para a direita da foto" if yaw > 0 else "virada para a esquerda da foto")


def pose_description(kp, face, hands: list | None) -> dict[str, Any]:
    if not kp:
        return {"body_orientation": "desconhecida", "head_rotation": _head(face), "right_arm": "nao visivel",
                "left_arm": "nao visivel", "hands": "sem esqueleto", "keypoints_visible": 0}
    vis = sum(1 for p in kp if p and len(p) > 2 and p[2] and p[2] > 0.3)
    hand_txt = "sem maos no detector"
    if hands:
        good = [sum(1 for p in h if p[2] and p[2] > 0.3) for h in hands]
        hand_txt = ", ".join(f"mao {i + 1}: {g}/21 pontos" for i, g in enumerate(good))
    return {"body_orientation": _orientation(kp), "head_rotation": _head(face),
            "right_arm": _limb_state(kp, "rsho", "relb", "rwri"), "left_arm": _limb_state(kp, "lsho", "lelb", "lwri"),
            "hands": hand_txt, "keypoints_visible": vis}


# --- marcas por parte do corpo ----------------------------------------------------------------------

def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    t = float(np.clip((p - a) @ ab / max(1e-6, ab @ ab), 0, 1))
    return float(np.linalg.norm(p - (a + t * ab)))


def body_part(kp, xy: tuple[float, float], face_bbox) -> str:
    """Parte do corpo mais perto de um ponto (esqueleto do DWPose). Sem esqueleto: 'body'."""
    p = np.array(xy, np.float32)
    if face_bbox is not None:
        x1, y1, x2, y2 = face_bbox
        if x1 <= xy[0] <= x2 and y1 <= xy[1] <= y2:
            return "face"
        fh = y2 - y1
        if x1 - fh * 0.2 <= xy[0] <= x2 + fh * 0.2 and y2 <= xy[1] <= y2 + fh * 0.6:
            return "neck"
    if not kp:
        return "body"
    best, name = 1e9, "body"
    for limb, (a, b) in LIMBS.items():
        pa, pb = point(kp, a), point(kp, b)
        if pa and pb:
            d = _seg_dist(p, np.array(pa, np.float32), np.array(pb, np.float32))
            if d < best:
                best, name = d, limb
    for side, wri, elb in (("right", "rwri", "relb"), ("left", "lwri", "lelb")):
        w, e = point(kp, wri), point(kp, elb)
        if w and e:
            ew = np.array(w, np.float32) - np.array(e, np.float32)
            reach = float(np.linalg.norm(ew)) * 0.6
            t = float((p - np.array(e, np.float32)) @ ew / max(1e-6, float(ew @ ew)))  # >1 = alem do pulso
            if t > 0.9 and np.linalg.norm(p - np.array(w)) < reach:
                d = float(np.linalg.norm(p - np.array(w)))
                if d < best:
                    best, name = d, f"{side}_hand"
    rs, ls, rh, lh = (point(kp, k) for k in ("rsho", "lsho", "rhip", "lhip"))
    if rs and ls and rh and lh:
        xs, ys = [rs[0], ls[0], rh[0], lh[0]], [rs[1], ls[1], rh[1], lh[1]]
        # dentro do tronco e longe dos membros (braco encostado no corpo fica com o braco)
        if min(xs) <= xy[0] <= max(xs) and min(ys) <= xy[1] <= max(ys) and best > abs(rs[0] - ls[0]) * 0.12:
            return "chest" if xy[1] < (min(ys) + max(ys)) / 2 else "belly"
    for side, sho in (("right", "rsho"), ("left", "lsho")):
        s = point(kp, sho)
        if s and np.linalg.norm(p - np.array(s)) < best:
            best, name = float(np.linalg.norm(p - np.array(s))), f"{side}_shoulder"
    return name


def components(mask: np.ndarray, max_side: int = 256, min_px: int = 4) -> list[dict[str, Any]]:
    """Componentes conexos (8-vizinhos) numa versao reduzida da mascara: centro e area de cada mancha."""
    m = mask > 0.5
    if not m.any():
        return []
    h, w = m.shape
    step = max(1, int(np.ceil(max(h, w) / max_side)))
    small = m[::step, ::step]
    seen = np.zeros_like(small, bool)
    out = []
    sh, sw = small.shape
    for y0, x0 in zip(*np.nonzero(small)):
        if seen[y0, x0]:
            continue
        q, pts = deque([(y0, x0)]), []
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            pts.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < sh and 0 <= nx < sw and small[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
        if len(pts) < min_px:
            continue
        ys, xs = np.array(pts).T
        out.append({"center": (float(xs.mean() * step), float(ys.mean() * step)), "area_px": int(len(pts) * step * step),
                    "bbox": [float(xs.min() * step), float(ys.min() * step), float((xs.max() + 1) * step),
                             float((ys.max() + 1) * step)]})
    return out


# --- analise --------------------------------------------------------------------------------------

def _clothing(text: str) -> dict[str, Any]:
    low = (text or "").lower()
    out: dict[str, Any] = {}
    for part, words in _CLOTHING_WORDS.items():
        hits = [w for w in words if w in low]
        if hits:
            out[part] = ", ".join(dict.fromkeys(hits))
    return out


def _location(text: str) -> str:
    low = (text or "").lower()
    return next((p for p in _PLACES if p in low), "nao identificado")


def analyze_scene(scene, attrs, faces_in_photo: int | None = None) -> ReplacementSceneAnalysis:
    """`scene`: o _Scene da engine (foto, leitura, mascaras, camadas, marcas). `attrs`: AttributePolicy resolvida."""
    sheet = scene.sheet
    f = getattr(sheet, "fields", {}) or {}
    caption = getattr(sheet, "caption", "") or ""
    face = sheet.target_face
    body = sheet.target_body
    kp = scene.base_pose
    others = list(getattr(sheet, "others", []) or [])
    count = faces_in_photo if faces_in_photo is not None else 1 + len(others)
    light = getattr(sheet, "light", None)
    scene_d = {"location": _location(" ".join([f.get("environment", ""), caption])),
               "environment_text": f.get("environment", ""),
               "lighting": light.label() if light is not None else "nao medida",
               "light_stats": None if light is None else {k: round(float(v), 3) for k, v in asdict(light).items()
                                                          if v is not None},
               "camera": getattr(sheet, "camera", "desconhecida"),
               "camera_angle": "nivel dos olhos" if getattr(sheet, "camera", "") != "selfie" else "selfie (braco esticado)"}
    pose = pose_description(kp, face, getattr(body, "hands", None) if body is not None else None)
    pose["text"] = f.get("pose", "")
    clothing = _clothing(" ".join([f.get("clothing", ""), caption]))
    clothing["text"] = f.get("clothing", "")
    clothing["policy"] = attrs.get("clothing")
    hair_words = [w for w in ("blonde", "blond", "brunette", "black", "brown", "red", "curly", "straight", "wavy", "bun",
                              "ponytail", "braid", "short", "long") if w in caption.lower()]
    hair = {"source": ", ".join(hair_words) or "nao descrito", "policy": attrs.get("hair"),
            "segmented": bool(scene.masks is not None and (scene.masks.hair > 0.5).any())}
    accessories = []
    for b in scene.box_policy:
        accessories.append({"item": b.get("item") or b.get("label") or "objeto", "label": b.get("label", ""),
                            "attribute": b.get("attribute"), "policy": b.get("policy"), "box": b.get("box"),
                            "note": b.get("note", "")})
    layer_kind = {(x.item or x.label): x.kind for x in scene.layers}
    for a in accessories:
        if a["item"] in layer_kind:
            a["layer"] = layer_kind[a["item"]]
    marks, undesired = [], []
    src = scene.markings if scene.markings is not None else scene.ink_zone
    if src is not None:
        fb = face.bbox if face is not None else None
        for c in components(src):
            part = body_part(kp, c["center"], fb)
            name = f"tattoo_{part}"
            marks.append({"name": name, "part": part, **c, "policy": attrs.get("tattoos")})
            if attrs.get("tattoos") == "REMOVE" and name not in undesired:
                undesired.append(name)
    for a in accessories:
        if a["policy"] == "REMOVE":
            undesired.append(f"{a['item']}_remove")
    occl = [f"{x.item or x.label} sobre o rosto ({x.kind})" for x in scene.layers if (x.item or "") in ("glasses", "hat")]
    notes = []
    if count > 1:
        notes.append(f"{count} rostos na foto: so a pessoa principal e trocada")
    return ReplacementSceneAnalysis(
        person_count=count,
        main_person={"face_bbox": None if face is None else [round(float(v), 1) for v in face.bbox],
                     "body_bbox": None if body is None else [round(float(v), 1) for v in body.bbox],
                     "face_detector_score": None if face is None else round(float(getattr(face, "det_score", 0.0)), 3)},
        scene=scene_d, pose=pose, clothing=clothing, hair=hair, accessories=accessories,
        undesired_attributes=undesired, markings=marks,
        makeup={"policy": attrs.get("makeup"), "detected": "sem detector: o rosto inteiro e reconstruido"},
        occlusions=occl,
        depth={"source": "Depth Anything V2 dentro do ComfyUI (so corpo/pele)", "exported": False},
        notes=notes,
    )


# --- mascaras com nome ------------------------------------------------------------------------------

def _limb_mask(h: int, w: int, kp, limbs, radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    out = np.zeros((h, w), np.float32)
    for limb in limbs:
        a, b = LIMBS[limb]
        pa, pb = point(kp, a), point(kp, b)
        if not (pa and pb):
            continue
        ax, ay, bx, by = pa[0], pa[1], pb[0], pb[1]
        dx, dy = bx - ax, by - ay
        t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / max(1e-6, dx * dx + dy * dy), 0, 1)
        d = np.hypot(xx - (ax + t * dx), yy - (ay + t * dy))
        out = np.maximum(out, (d <= radius).astype(np.float32))
    return out


def semantic_masks(scene, hand_mask: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Mascaras com nome (0-1, tamanho da foto). Hierarquia: pessoa > pele > tatuagem; tatuagem sai da pele."""
    h, w = scene.original.shape[:2]
    z = np.zeros((h, w), np.float32)
    m = scene.masks
    if m is None:
        return {"person_mask": scene.identity.copy(), "face_mask": scene.face_full, "source_identity_mask": scene.identity}
    person = (m.person > 0.5).astype(np.float32)
    tattoo = z if scene.markings is None else (scene.markings > 0.5).astype(np.float32)
    skin = (m.skin > 0.5).astype(np.float32) * person * (1 - tattoo)  # tatuagem tem prioridade sobre a pele
    accessory = z if scene.accessory is None else (scene.accessory > 0.5).astype(np.float32)
    fx1, fy1, fx2, fy2 = scene.sheet.target_face.bbox
    fh = max(1.0, fy2 - fy1)
    out = {
        "person_mask": person,
        "face_mask": (scene.face_full > 0.5).astype(np.float32),
        "skin_mask": skin,
        "hair_mask": (m.hair > 0.5).astype(np.float32),
        "hand_mask": z if hand_mask is None else (hand_mask > 0.5).astype(np.float32) * person,
        "clothing_mask": (m.clothing > 0.5).astype(np.float32),
        "accessory_mask": accessory,
        "tattoo_mask": tattoo,
        "background_mask": 1 - person,
        "source_identity_mask": (scene.identity > 0.5).astype(np.float32),
        "source_marking_mask": tattoo,
    }
    kp = scene.base_pose
    yy, xx = np.mgrid[0:h, 0:w]
    neck = ((xx >= fx1 - fh * 0.1) & (xx <= fx2 + fh * 0.1) & (yy >= fy2 - fh * 0.15) & (yy <= fy2 + fh * 0.55))
    out["neck_mask"] = neck.astype(np.float32) * person * (1 - out["face_mask"]) * (1 - out["clothing_mask"])
    if kp:
        r = fh * 0.32
        out["arm_mask"] = _limb_mask(h, w, kp, _ARMS, r) * person
        out["leg_mask"] = _limb_mask(h, w, kp, _LEGS, r * 1.3) * person
    return out


def check_hierarchy(masks: dict[str, np.ndarray]) -> list[str]:
    """Regras da spec: tatuagem nunca conta como pele; pele, roupa e tatuagem ficam dentro da pessoa."""
    bad = []
    t, s, p = masks.get("tattoo_mask"), masks.get("skin_mask"), masks.get("person_mask")
    if t is not None and s is not None and ((t > 0.5) & (s > 0.5)).any():
        bad.append("tatuagem dentro da mascara de pele")
    if p is not None:
        for k in ("skin_mask", "tattoo_mask", "clothing_mask"):
            if k in masks and ((masks[k] > 0.5) & ~(p > 0.5)).any():
                bad.append(f"{k} fora da pessoa")
    return bad


# --- mapa de pose (debug) -------------------------------------------------------------------------

_BONES = [("neck", "rsho"), ("neck", "lsho"), ("rsho", "relb"), ("relb", "rwri"), ("lsho", "lelb"), ("lelb", "lwri"),
          ("neck", "rhip"), ("neck", "lhip"), ("rhip", "rkne"), ("rkne", "rank"), ("lhip", "lkne"), ("lkne", "lank"),
          ("neck", "nose")]
_HAND_BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (0, 9), (9, 10), (10, 11), (11, 12),
               (0, 13), (13, 14), (14, 15), (15, 16), (0, 17), (17, 18), (18, 19), (19, 20)]


def _line(img: np.ndarray, a, b, color, width: int) -> None:
    h, w = img.shape[:2]
    n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
    for t in np.linspace(0, 1, n):
        x, y = int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t)
        img[max(0, y - width):min(h, y + width + 1), max(0, x - width):min(w, x + width + 1)] = color


def render_pose_map(h: int, w: int, kp, hands: list | None = None) -> np.ndarray:
    """Esqueleto do DWPose sobre fundo preto (o mesmo tipo de imagem que guia o ControlNet)."""
    img = np.zeros((h, w, 3), np.uint8)
    if not kp:
        return img
    lw = max(1, min(h, w) // 300)
    for i, (a, b) in enumerate(_BONES):
        pa, pb = point(kp, a), point(kp, b)
        if pa and pb:
            hue = i / len(_BONES)
            color = (int(255 * abs(np.sin(hue * 3.1))), int(255 * abs(np.sin(hue * 3.1 + 2))), int(255 * abs(np.sin(hue * 3.1 + 4))))
            _line(img, pa, pb, color, lw + 1)
    for name in KP:
        p = point(kp, name)
        if p:
            _line(img, p, p, (255, 255, 255), lw + 2)
    for hand in hands or []:
        for a, b in _HAND_BONES:
            if a < len(hand) and b < len(hand) and hand[a][2] and hand[b][2] and hand[a][2] > 0.3 and hand[b][2] > 0.3:
                _line(img, hand[a][:2], hand[b][:2], (0, 200, 255), lw)
    return img


__all__ = ["ReplacementSceneAnalysis", "analyze_scene", "body_part", "check_hierarchy", "components", "pose_description",
           "render_pose_map", "semantic_masks"]
