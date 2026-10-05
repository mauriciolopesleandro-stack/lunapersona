"""Realismo da pele (Persona Engine V1.1): contratos, notas, politica de correcao
e a guarda que impede a correcao de mexer na identidade.

O que e medido de verdade (por quem implementa SkinRealismAnalyzer):
  - microtextura: detalhe fino que sobra na pele depois de um desfoque leve;
  - uniformidade: fracao de blocos de pele quase sem detalhe (excesso de suavizacao).
O que NAO e medido (sem detector confiavel): aparencia CGI, porcelana, simetria
artificial -> ficam como UNKNOWN em `unknown_aspects`, nunca viram nota.

A escala (calibration.lo/hi) e PROVISORIA ate o benchmark com imagens reais.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.core.validation.analysis import DetectedFace, ImageAnalysis
from app.core.validation.geometry import pose_distance
from app.providers.base import ProviderImage

PASS, WARN, FAIL, UNKNOWN = "PASS", "WARN", "FAIL", "UNKNOWN"
SKIN_REALISM_LOW = "SKIN_REALISM_LOW"
SKIN_TEXTURE_LOW = "SKIN_TEXTURE_LOW"
OVERSMOOTHING_DETECTED = "OVERSMOOTHING_DETECTED"
UNMEASURED_ASPECTS = ["cgi_appearance", "porcelain_appearance", "artificial_symmetry"]


@dataclass
class SkinRealismResult:
    score: float | None
    status: str
    grade: str | None
    reasons: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    unknown_aspects: list[str] = field(default_factory=lambda: list(UNMEASURED_ASPECTS))
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkinRealismAnalyzer(Protocol):
    async def analyze(self, image: ProviderImage, face: DetectedFace | None, config: dict[str, Any]) -> SkinRealismResult:
        """Mede a pele do rosto `face` em `image`."""


def unknown(note: str, raw: dict[str, Any] | None = None) -> SkinRealismResult:
    return SkinRealismResult(None, UNKNOWN, None, [], raw or {}, note=note)


def grade(texture: float, oversmoothed: float, config: dict[str, Any], raw: dict[str, Any]) -> SkinRealismResult:
    """Microtextura -> nota 0-1 pela calibracao; nota -> status pelas faixas.
    Faixas (iniciais, a calibrar): <0.30 FAIL, <0.50 WARN, <0.70 ACCEPTABLE,
    <0.85 GOOD, senao EXCELLENT."""
    cal = config["calibration"]
    lo, hi = float(cal["lo"]), float(cal["hi"])
    # Dois sinais medidos; vale o pior. Pele "plastica" aparece como pouca
    # microtextura OU como muitos blocos sem nenhum detalhe (bochecha lisa).
    texture_part = max(0.0, min(1.0, (texture - lo) / (hi - lo)))
    smooth_part = max(0.0, min(1.0, 1.0 - oversmoothed))
    score = round(min(texture_part, smooth_part), 3)
    raw = {**raw, "texture_component": round(texture_part, 3), "smoothness_component": round(smooth_part, 3)}
    bands = config["bands"]
    if score < bands["fail_below"]:
        status, label = FAIL, "FAIL"
    elif score < bands["warn_below"]:
        status, label = WARN, "WARN"
    else:
        status = PASS
        label = "ACCEPTABLE" if score < bands["good_from"] else "GOOD" if score < bands["excellent_from"] else "EXCELLENT"
    reasons = []
    if status != PASS:
        reasons += [SKIN_REALISM_LOW, SKIN_TEXTURE_LOW]
    if oversmoothed >= float(config.get("oversmoothing_fraction", 0.6)):
        reasons.append(OVERSMOOTHING_DETECTED)
    return SkinRealismResult(score, status, label, reasons,
                             {**raw, "texture": round(texture, 3), "oversmoothed_fraction": round(oversmoothed, 3),
                              "calibration_status": cal.get("status", "PROVISIONAL")})


@dataclass
class CorrectionDecision:
    apply: bool
    reason: str | None


class SkinCorrectionPolicy:
    """Corrige so quando ha medida e ela esta ruim. UNKNOWN nunca dispara correcao."""

    def decide(self, result: SkinRealismResult, config: dict[str, Any], attempts_done: int) -> CorrectionDecision:
        if not config.get("enabled"):
            return CorrectionDecision(False, None)
        if attempts_done >= int(config.get("max_attempts", 1)):
            return CorrectionDecision(False, "limite de correcoes atingido")
        if result.status == UNKNOWN or result.score is None:
            return CorrectionDecision(False, "pele nao medida (UNKNOWN): sem correcao")
        if result.score < float(config["correct_below"]):
            return CorrectionDecision(True, (result.reasons or [SKIN_REALISM_LOW])[0])
        return CorrectionDecision(False, None)


@dataclass
class GuardVerdict:
    accepted: bool
    reasons: list[str]
    deltas: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IdentityPreservationGuard:
    """Realismo nao pode destruir identidade: a correcao so fica se a pele
    melhorou E rosto, idade, pose e pessoas nao mudaram alem do limite."""

    def evaluate(self, before: ImageAnalysis, after: ImageAnalysis, skin_before: SkinRealismResult,
                 skin_after: SkinRealismResult, config: dict[str, Any]) -> GuardVerdict:
        reasons: list[str] = []
        fb, fa = before.persona_face(), after.persona_face()
        face_before = fb.similarity if fb else None
        face_after = fa.similarity if fa else None
        deltas: dict[str, Any] = {"face_identity_before": face_before, "face_identity_after": face_after}
        if face_after is None or face_before is None:
            reasons.append("rosto nao encontrado depois da correcao")
        elif face_before - face_after > float(config["max_identity_drop"]):
            reasons.append(f"identidade caiu {face_before - face_after:.3f} (> {config['max_identity_drop']})")
        if fb and fa and fb.age is not None and fa.age is not None:
            deltas["age_before"], deltas["age_after"] = fb.age, fa.age
            if abs(fa.age - fb.age) > float(config["max_age_shift"]):
                reasons.append(f"idade mudou {fa.age - fb.age:+.0f} anos")
        mb, ma = before.main_body(), after.main_body()
        if mb and ma:
            distance = pose_distance(mb.keypoints, ma.keypoints)
            deltas["pose_distance"] = distance
            if distance is not None and distance > float(config["max_pose_distance"]):
                reasons.append(f"pose mudou ({distance:.3f})")
        if len(after.faces) != len(before.faces):
            reasons.append(f"numero de rostos mudou ({len(before.faces)} -> {len(after.faces)})")
        if skin_after.score is None or skin_before.score is None or skin_after.score <= skin_before.score:
            reasons.append("a pele nao melhorou")
        deltas["skin_before"], deltas["skin_after"] = skin_before.score, skin_after.score
        return GuardVerdict(not reasons, reasons, deltas)
