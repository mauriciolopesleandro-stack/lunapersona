"""O que se mede numa imagem, e as interfaces de quem mede.

InsightFace e DWPose sao MEDIDORES (ver app/validation_backends): nenhum deles
condiciona a geracao. Trocar o detector nao mexe nos validadores.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.validation.geometry import Keypoint
from app.providers.base import ProviderImage, ReferenceImage


@dataclass
class DetectedFace:
    bbox: tuple[float, float, float, float]
    similarity: float | None = None  # ArcFace com a master_face
    age: float | None = None
    sex: str | None = None
    det_score: float = 0.0
    yaw: float | None = None


@dataclass
class DetectedBody:
    keypoints: list[Keypoint]
    bbox: tuple[float, float, float, float]
    height_frac: float  # altura do corpo / altura da imagem
    visible_points: int


@dataclass
class ImageAnalysis:
    width: int
    height: int
    faces: list[DetectedFace] = field(default_factory=list)
    bodies: list[DetectedBody] = field(default_factory=list)
    seconds: float = 0.0

    def main_body(self) -> DetectedBody | None:
        return max(self.bodies, key=lambda b: (b.visible_points, b.height_frac)) if self.bodies else None

    def persona_face(self) -> DetectedFace | None:
        scored = [f for f in self.faces if f.similarity is not None]
        return max(scored, key=lambda f: f.similarity) if scored else None


class ImageAnalyzer(Protocol):
    async def check_ready(self) -> list[str]: ...

    async def analyze(self, image: ProviderImage, master_face: ReferenceImage) -> ImageAnalysis:
        """Todos os rostos (com semelhanca com a master) e todos os corpos."""

    async def analyze_reference(self, reference: ReferenceImage, master_face: ReferenceImage) -> ImageAnalysis:
        """O mesmo para uma imagem que ainda nao esta no provider (ex.: master_body)."""


class AnatomyDetector(Protocol):
    """Detector de anatomia (membros extras, corpo duplicado...). Nao existe um
    validado no projeto: sem ele o AnatomyValidator responde UNKNOWN."""

    async def inspect(self, image: ProviderImage, analysis: ImageAnalysis) -> dict[str, Any]: ...


class TextReader(Protocol):
    """OCR para o vazamento do gatilho da LoRA em placas (opcional)."""

    async def read(self, image: ProviderImage) -> str | None: ...
