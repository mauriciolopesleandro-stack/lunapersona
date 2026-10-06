"""Leitura da foto de referencia (V2, modo "replicar foto").

A foto vira uma FICHA: quem vira a Luna (a mulher de maior destaque), quem fica
como esta (outras pessoas), a pose (esqueleto), o tipo de camera (selfie,
espelho, foto tirada por outra pessoa), a luz MEDIDA nos pixels e a descricao
(roupa, ambiente, objetos). Nada aqui inventa: o que nao foi medido fica vazio.

O nucleo so decide com numeros e texto; quem le a imagem (LunaFaces, DWPose,
Florence) e o backend de validacao.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.core.validation.analysis import DetectedBody, DetectedFace, ImageAnalysis
from app.providers.base import ProviderImage, ReferenceImage

SELFIE, MIRROR, THIRD = "selfie", "mirror_selfie", "third_person"
CAMERA_TEXT = {
    SELFIE: "front camera selfie, phone held at arm's length",
    MIRROR: "mirror selfie, holding a smartphone in front of the mirror",
    THIRD: "photo taken by a friend with a smartphone",
}


class ReferenceError(ValueError):
    """A foto nao serve para replicar (ex.: nenhuma mulher encontrada)."""


@dataclass
class LightStats:
    brightness: float  # 0-255, media da luminancia
    contrast: float  # desvio da luminancia
    warmth: float  # media(R) - media(B): >0 quente, <0 fria
    saturation: float  # 0-1
    clipped_highlights: float  # fracao de pixels estourados
    face_to_scene: float | None = None  # brilho do rosto / brilho da cena (flash ~> 1.4)

    def label(self) -> str:
        parts = []
        parts.append("dim low light" if self.brightness < 80 else "bright light" if self.brightness > 170 else "ambient light")
        if self.warmth > 18:
            parts.append("warm tones")
        elif self.warmth < -10:
            parts.append("cool tones")
        if self.face_to_scene is not None and self.face_to_scene > 1.4 and self.brightness < 110:
            parts.append("direct phone flash on the face")
        if self.clipped_highlights > 0.04:
            parts.append("some blown highlights")
        if self.contrast > 70:
            parts.append("hard shadows")
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "label": self.label()}


@dataclass
class ReferenceSheet:
    image: str  # locator da foto no provider
    width: int
    height: int
    target_face: DetectedFace
    target_body: DetectedBody | None
    others: list[DetectedFace]
    caption: str
    fields: dict[str, str] = field(default_factory=dict)
    camera: str = THIRD
    light: LightStats | None = None
    warnings: list[str] = field(default_factory=list)

    def person_box(self) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.target_face.bbox
        if self.target_body is not None:
            bx1, by1, bx2, by2 = self.target_body.bbox
            x1, y1, x2, y2 = min(x1, bx1), min(y1, by1), max(x2, bx2), max(y2, by2)
        fh = self.target_face.bbox[3] - self.target_face.bbox[1]
        return (max(0.0, x1 - fh * 0.4), max(0.0, y1 - fh * 0.5), min(float(self.width), x2 + fh * 0.4),
                min(float(self.height), y2 + fh * 0.3))

    def description(self) -> str:
        """Texto da cena para o modelo: o que a foto mostra, sem estilo inventado."""
        f = self.fields
        bits = [CAMERA_TEXT[self.camera]]
        for key in ("pose", "clothing", "objects", "environment", "expression"):
            if f.get(key):
                bits.append(f[key])
        if not any(f.get(k) for k in ("pose", "clothing", "environment")):
            bits.append(self.caption)
        if self.light is not None:
            bits.append(self.light.label())
        return ", ".join(b.strip().rstrip(".") for b in bits if b and b.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image, "width": self.width, "height": self.height,
            "target_face": asdict(self.target_face), "target_body": asdict(self.target_body) if self.target_body else None,
            "others": [asdict(o) for o in self.others], "caption": self.caption, "fields": self.fields,
            "camera": self.camera, "light": self.light.to_dict() if self.light else None, "warnings": self.warnings,
            "person_box": self.person_box(), "description": self.description(),
        }


class ReferenceReader(Protocol):
    async def read(self, image: ProviderImage, master: ReferenceImage) -> ReferenceSheet:
        """Le a foto e devolve a ficha."""


# --- decisoes (puras) ------------------------------------------------------------


def _area(b: tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside(x: float, y: float, b: tuple[float, float, float, float]) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def choose_target(analysis: ImageAnalysis) -> tuple[DetectedFace, DetectedBody | None, list[DetectedFace], list[str]]:
    """A mulher de maior destaque (rosto maior x certeza) vira a Luna; as outras pessoas ficam."""
    warnings: list[str] = []
    women = [f for f in analysis.faces if (f.sex or "").upper() == "F"]
    if not women:
        if analysis.faces:
            raise ReferenceError("Nao achei uma mulher na foto (so rostos de homem): nao ha quem substituir pela Luna.")
        raise ReferenceError("Nao achei nenhum rosto na foto: para replicar, o rosto precisa aparecer.")
    target = max(women, key=lambda f: _area(f.bbox) * max(f.det_score, 0.1))
    if len(women) > 1:
        warnings.append(f"{len(women)} mulheres na foto: a Luna fica no lugar da de maior destaque")
    fx, fy = (target.bbox[0] + target.bbox[2]) / 2, (target.bbox[1] + target.bbox[3]) / 2
    holders = [b for b in analysis.bodies if _inside(fx, fy, (b.bbox[0] - 20, b.bbox[1] - 60, b.bbox[2] + 20, b.bbox[3]))]
    body = max(holders, key=lambda b: (b.visible_points, b.height_frac)) if holders else None
    if body is None:
        warnings.append("corpo da pessoa nao detectado: so cabeca e ombros serao trocados com seguranca")
    others = [f for f in analysis.faces if f is not target]
    return target, body, others, warnings


def camera_type(caption: str, face: DetectedFace, body: DetectedBody | None, width: int, height: int) -> str:
    text = caption.lower()
    if "mirror" in text or "reflection" in text:
        return MIRROR
    if "selfie" in text or "front camera" in text:
        return SELFIE
    face_frac = _area(face.bbox) / max(1.0, width * height)
    if body is not None and face_frac > 0.03:
        # braco esticado ate a borda: pulso fora do quadro ou colado na borda
        wrists = [body.keypoints[i] for i in (4, 7) if i < len(body.keypoints)]
        if any(c < 0.3 or x < width * 0.04 or x > width * 0.96 or y > height * 0.96 for x, y, c in wrists):
            return SELFIE
    return THIRD


def sam_points(sheet: ReferenceSheet) -> tuple[str, str]:
    """Pontos do SAM2: rosto, topo da cabeca (cabelo) e tronco dela; negativos nos outros rostos."""
    x1, y1, x2, y2 = sheet.target_face.bbox
    fx, fy, fh = (x1 + x2) / 2, (y1 + y2) / 2, y2 - y1
    pos = [{"x": int(fx), "y": int(fy)}, {"x": int(fx), "y": max(0, int(y1 - fh * 0.15))}]
    body = sheet.target_body
    if body is not None:
        neck = body.keypoints[1] if len(body.keypoints) > 1 else None
        hips = [body.keypoints[i] for i in (8, 11) if i < len(body.keypoints) and body.keypoints[i][2] > 0.3]
        if neck and neck[2] > 0.3 and hips:
            hx = sum(h[0] for h in hips) / len(hips)
            hy = sum(h[1] for h in hips) / len(hips)
            pos.append({"x": int((neck[0] + hx) / 2), "y": int((neck[1] + hy) / 2)})
        elif neck and neck[2] > 0.3:
            pos.append({"x": int(neck[0]), "y": int(min(sheet.height - 1, neck[1] + fh))})
    neg = [{"x": int((o.bbox[0] + o.bbox[2]) / 2), "y": int((o.bbox[1] + o.bbox[3]) / 2)} for o in sheet.others]
    if not neg:  # o SAM2 quebra com negativos vazios: canto mais longe dela
        corners = [(4, 4), (sheet.width - 5, 4), (4, sheet.height - 5), (sheet.width - 5, sheet.height - 5)]
        far = max(corners, key=lambda c: (c[0] - fx) ** 2 + (c[1] - fy) ** 2)
        neg = [{"x": far[0], "y": far[1]}]
    return json.dumps(pos), json.dumps(neg)


SUNGLASSES = re.compile(r"\bsun ?glasses\b|\bshades\b", re.I)


def accessory_regions(sheet: ReferenceSheet) -> list[dict[str, float]]:
    """Acessorios da foto que voltam no fim (pixels originais): hoje, oculos escuros.
    Elipse sobre os olhos, pelos 5 pontos do rosto (ou pela caixa do rosto)."""
    if not SUNGLASSES.search(sheet.caption or ""):
        return []
    f = sheet.target_face
    x1, y1, x2, y2 = f.bbox
    if len(f.kps) >= 2:
        (lx, ly), (rx, ry) = f.kps[0], f.kps[1]
        iod = max(1.0, ((rx - lx) ** 2 + (ry - ly) ** 2) ** 0.5)
        return [{"cx": (lx + rx) / 2, "cy": (ly + ry) / 2, "rx": iod * 1.3, "ry": iod * 0.55, "what": "sunglasses"}]
    w, h = x2 - x1, y2 - y1
    return [{"cx": (x1 + x2) / 2, "cy": y1 + h * 0.38, "rx": w * 0.6, "ry": h * 0.18, "what": "sunglasses"}]


FIELD_KEYS = ("pose", "clothing", "objects", "environment", "expression")
STRUCTURE_PROMPT = (
    "Read this description of a photo and answer ONLY with a JSON object with these keys, in English, short phrases, "
    "describing ONLY what is written (never invent): pose (body position and what the hands do), clothing (what the "
    "woman wears), objects (what she holds or uses), environment (the place and background), expression (face). "
    "Use an empty string when the description does not say.\n\nDescription: "
)


def parse_fields(text: str) -> dict[str, str]:
    """JSON do LLM -> campos; texto quebrado ou fora do formato = {} (cai na descricao crua)."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data[k]).strip() for k in FIELD_KEYS if isinstance(data.get(k), str) and data[k].strip()}


__all__ = ["accessory_regions", "CAMERA_TEXT", "MIRROR", "SELFIE", "STRUCTURE_PROMPT", "THIRD", "LightStats", "ReferenceError",
           "ReferenceReader", "ReferenceSheet", "camera_type", "choose_target", "parse_fields", "sam_points"]
