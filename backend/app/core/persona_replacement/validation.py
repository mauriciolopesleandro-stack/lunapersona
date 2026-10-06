"""Validacao especifica do replacement: metricas INDEPENDENTES, sem nota composta
obrigatoria. ReplacementIntegrationScore e informativo (o pior de luz, textura e
borda) ate haver dados para calibrar uma agregacao.

  identity / age / subject_count / pose   (LunaFaces + DWPose, entrada x saida)
  background_changed   fracao de pixels FORA da pessoa que mudaram (> limite = FAIL)
  clothing_changed     fracao de pixels da ROUPA que mudaram
  lighting             luminancia/cor da regiao alterada x a pessoa original no mesmo lugar
  texture              grao da regiao alterada x a foto em volta
  edge                 halo na faixa externa e nitidez da faixa interna
  body / anatomy       UNKNOWN (sem medidor confiavel)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from app.core.persona_replacement.blending import edge_metrics
from app.core.persona_replacement.lighting import noise_level, to_ycc
from app.core.persona_replacement.segmentation import MaskSet, dilate

PASS, FAIL, UNKNOWN, INFO = "PASS", "FAIL", "UNKNOWN", "INFORMATIONAL"


def changed_fraction(a: np.ndarray, b: np.ndarray, region: np.ndarray, threshold: int = 6) -> float | None:
    sel = region > 0.5
    if sel.sum() == 0:
        return None
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > threshold
    return round(float(diff[sel].mean()), 5)


def background_change(original: np.ndarray, result: np.ndarray, person: np.ndarray) -> float | None:
    """Fora da pessoa (com margem de 2 px para a borda suave)."""
    return changed_fraction(original, result, 1 - dilate((person > 0.02).astype(np.float32), 2))


def clothing_change(original: np.ndarray, result: np.ndarray, clothing: np.ndarray) -> float | None:
    return changed_fraction(original, result, clothing, threshold=10)


def lighting_consistency(original: np.ndarray, result: np.ndarray, region: np.ndarray) -> dict[str, float | None]:
    sel = region > 0.5
    if sel.sum() < 30:
        return {"delta_luma": None, "delta_chroma": None, "score": None}
    a, b = to_ycc(original)[sel], to_ycc(result)[sel]
    dy = float(b[:, 0].mean() - a[:, 0].mean())
    dc = float(np.hypot(b[:, 1].mean() - a[:, 1].mean(), b[:, 2].mean() - a[:, 2].mean()))
    score = max(0.0, 1.0 - abs(dy) / 40.0 - dc / 30.0)
    return {"delta_luma": round(dy, 2), "delta_chroma": round(dc, 2), "score": round(score, 3)}


def surroundings(person: np.ndarray, near: int = 4, far: int = 24) -> np.ndarray:
    """Faixa de FUNDO em volta da pessoa (sem os px colados nela, onde a borda viraria "grao")."""
    p = (person > 0.5).astype(np.float32)
    return np.clip(dilate(p, far) - dilate(p, near), 0, 1)


def texture_consistency(original: np.ndarray, result: np.ndarray, region: np.ndarray, person: np.ndarray) -> dict[str, float | None]:
    ref, cur = noise_level(original, surroundings(person)), noise_level(result, erode_region(region))
    if not ref or cur is None:
        return {"reference_noise": ref, "region_noise": cur, "score": None}
    ratio = cur / ref
    score = max(0.0, 1.0 - abs(np.log(max(ratio, 1e-3))) / np.log(3.0))
    return {"reference_noise": round(ref, 3), "region_noise": round(cur, 3), "ratio": round(ratio, 3),
            "score": round(float(score), 3)}


def erode_region(region: np.ndarray, r: int = 2) -> np.ndarray:
    return 1.0 - dilate(1.0 - (region > 0.5).astype(np.float32), r)


def edge_quality(original: np.ndarray, result: np.ndarray, person: np.ndarray, width: int) -> dict[str, float | None]:
    m = edge_metrics(original, result, person, width)
    if m["halo"] is None or m["sharpness_ratio"] is None:
        return {**m, "score": None}
    ratio = m["sharpness_ratio"]
    score = max(0.0, 1.0 - m["halo"] / 15.0 - abs(np.log(max(ratio, 1e-3))) / np.log(4.0))
    return {**m, "score": round(float(score), 3)}


@dataclass
class ReplacementReport:
    status: str
    identity: float | None
    age: float | None
    pose: float | None
    subject_count: int | None
    background_changed: float | None
    clothing_changed: float | None
    lighting: dict[str, Any] = field(default_factory=dict)
    texture: dict[str, Any] = field(default_factory=dict)
    edge: dict[str, Any] = field(default_factory=dict)
    integration_score: float | None = None  # INFORMATIVO
    body: str = UNKNOWN
    anatomy: str = UNKNOWN
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate(original: np.ndarray, result: np.ndarray, masks: MaskSet, modified: np.ndarray, measure: Any,
             checks: dict[str, Any], edge_width: int) -> ReplacementReport:
    bg = background_change(original, result, masks.person)
    cloth = clothing_change(original, result, masks.clothing)
    light = lighting_consistency(original, result, modified)
    tex = texture_consistency(original, result, modified, masks.person)
    edge = edge_quality(original, result, masks.person, edge_width)
    parts = [x["score"] for x in (light, tex, edge) if x.get("score") is not None]
    failures = []
    if measure.face is None or measure.face < float(checks["min_final_face"]):
        failures.append("identity_low")
    if bg is not None and bg > float(checks["max_background_changed"]):
        failures.append("background_changed")
    if cloth is not None and cloth > float(checks["max_clothing_changed"]):
        failures.append("clothing_changed")
    if measure.pose is not None and measure.pose > float(checks["max_pose_distance"]):
        failures.append("pose_changed")
    if measure.persona_instances > 1:
        failures.append("persona_duplicated")
    return ReplacementReport(
        status=FAIL if failures else PASS, identity=measure.face, age=measure.age, pose=measure.pose,
        subject_count=measure.persona_instances, background_changed=bg, clothing_changed=cloth, lighting=light,
        texture=tex, edge=edge, integration_score=min(parts) if parts else None, failures=failures)


__all__ = ["FAIL", "PASS", "UNKNOWN", "ReplacementReport", "erode_region", "surroundings", "background_change", "changed_fraction",
           "clothing_change", "edge_quality", "lighting_consistency", "texture_consistency", "validate"]
