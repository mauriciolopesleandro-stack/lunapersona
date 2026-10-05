"""ScoreEngine: transforma medidas cruas em notas 0-1 e na nota composta.

Nota composta = media ponderada so do que foi medido; a parte do peso que
ficou sem medir vira `coverage`. Cabelo e corpo, por exemplo, ainda nao tem
modelo no pod: aparecem como "nao medido" em vez de um numero inventado.
"""
from __future__ import annotations

import math

from app.core.validation.analysis import DetectedFace
from app.core.validation.config import APPEARANCE_METRICS, IdentityValidationConfig
from app.core.validation.types import MetricScore


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _mid(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def face_ratios(face: DetectedFace) -> dict[str, float] | None:
    """Proporcoes do rosto pelos 5 pontos, divididas pela distancia entre os
    olhos (nao depende do tamanho do rosto na foto)."""
    if len(face.kps) < 5:
        return None
    left_eye, right_eye, nose, mouth_l, mouth_r = face.kps[:5]
    iod = _dist(left_eye, right_eye)
    width = face.bbox[2] - face.bbox[0]
    if iod < 1 or width < 1:
        return None
    eyes = _mid(left_eye, right_eye)
    mouth = _mid(mouth_l, mouth_r)
    return {
        "eyes": iod / width,                      # olhos mais juntos/separados no rosto
        "nose": _dist(eyes, nose) / iod,          # comprimento do nariz
        "mouth": _dist(mouth_l, mouth_r) / iod,   # largura da boca
        "proportions": _dist(eyes, mouth) / iod,  # altura do terco medio/inferior
    }


class ScoreEngine:
    def __init__(self, config: IdentityValidationConfig) -> None:
        self.config = config

    def metric(self, name: str, score: float | None = None, note: str = "", **raw) -> MetricScore:
        return MetricScore(name=name, weight=self.config.weights.get(name, 0.0),
                           score=None if score is None else round(_clamp(score), 4), raw=raw, note=note)

    def face_similarity(self, similarity: float | None) -> MetricScore:
        if similarity is None:
            return self.metric("face_similarity", note="sem rosto para comparar")
        c = self.config
        score = (similarity - c.similarity_floor) / (c.similarity_ceiling - c.similarity_floor)
        return self.metric("face_similarity", score, cosine=round(similarity, 4))

    def facial_structure(self, reference: DetectedFace | None, generated: DetectedFace | None) -> MetricScore:
        c = self.config
        if reference is None or generated is None:
            return self.metric("facial_structure", note="sem rosto")
        if any(f.yaw is None or abs(f.yaw) > c.structure_max_yaw for f in (reference, generated)):
            return self.metric("facial_structure", note="rosto virado: proporcoes nao comparaveis")
        ref, gen = face_ratios(reference), face_ratios(generated)
        if ref is None or gen is None:
            return self.metric("facial_structure", note="sem os pontos do rosto (LunaFaces antigo no pod)")
        parts = {k: round(_clamp(1 - abs(gen[k] - ref[k]) / ref[k] / c.structure_tolerance), 4) for k in ref}
        return self.metric("facial_structure", sum(parts.values()) / len(parts), parts=parts)

    def age(self, generated_age: float | None, target_age: float | None) -> MetricScore:
        if generated_age is None or not target_age:
            return self.metric("age", note="idade nao estimada")
        c = self.config
        diff = abs(generated_age - target_age)
        return self.metric("age", 1 - max(0.0, diff - c.age_tolerance) / c.age_span,
                           generated=generated_age, target=target_age)

    def distinctive(self, found: list[str] | None, missing: list[str] | None) -> MetricScore:
        if found is None or missing is None or not (found or missing):
            return self.metric("distinctive_features", note="sem tracos marcantes configurados ou sem descricao")
        return self.metric("distinctive_features", len(found) / (len(found) + len(missing)),
                           found=found, missing=missing)

    def not_measured(self, name: str, note: str) -> MetricScore:
        return self.metric(name, note=note)

    def composite(self, metrics: dict[str, MetricScore]) -> tuple[float | None, float]:
        total = sum(m.weight for m in metrics.values())
        measured = [m for m in metrics.values() if m.measured and m.weight > 0]
        weight = sum(m.weight for m in measured)
        if not measured or total <= 0:
            return None, 0.0
        score = sum(m.weight * m.score for m in measured) / weight  # type: ignore[operator]
        return round(score, 4), round(weight / total, 4)

    def appearance(self, metrics: dict[str, MetricScore]) -> float | None:
        parts = [metrics[n] for n in APPEARANCE_METRICS if n in metrics and metrics[n].measured]
        weight = sum(m.weight for m in parts)
        if not parts or weight <= 0:
            return None
        return round(sum(m.weight * m.score for m in parts) / weight, 4)  # type: ignore[operator]
