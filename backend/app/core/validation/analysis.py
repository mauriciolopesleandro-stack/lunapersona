"""Interfaces do que mede a imagem. O validador nao sabe que por tras esta
o InsightFace dentro do ComfyUI (app/validation_backends/) - trocar o
detector de rosto nao mexe na nota."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.providers.base import ProviderImage, ReferenceImage


@dataclass
class DetectedFace:
    bbox: tuple[float, float, float, float]
    det_score: float = 0.0
    sex: str | None = None
    age: float | None = None
    # Para onde a cabeca esta virada (0 = de frente; ~0.35+ = perfil).
    yaw: float | None = None
    # Cosseno do ArcFace com o rosto da referencia (so na imagem gerada).
    similarity: float | None = None
    # 5 pontos: olho esq., olho dir., nariz, boca esq., boca dir.
    kps: list[tuple[float, float]] = field(default_factory=list)


class FaceAnalyzer(Protocol):
    async def check_ready(self) -> list[str]:
        """Problemas que impedem medir (lista vazia = pronto)."""

    async def reference_face(self, reference: ReferenceImage) -> DetectedFace | None:
        """O maior rosto da foto de referencia."""

    async def generated_faces(self, image: ProviderImage, reference: ReferenceImage) -> list[DetectedFace]:
        """Rostos da imagem gerada, cada um com a semelhanca com a referencia."""


class FeatureChecker(Protocol):
    async def check(self, image: ProviderImage, keywords: list[str]) -> tuple[list[str], list[str]] | None:
        """(achados, faltando) entre os tracos marcantes; None = nao deu para medir."""
