"""Validadores do Persona Engine V1. Cada um responde sozinho, com status,
nota (quando existe), limiar, evidencias, confianca e motivo.

Status:
  PASS / FAIL       medido e decidido
  UNKNOWN           nao ha como medir com confianca (NUNCA vira PASS)
  NOT_COMPARABLE    medir seria comparar coisas diferentes (ex.: poses diferentes)
  INFORMATIONAL     registro, nao decide nada

Limiares vem da Persona Sheet (validation_profile), nunca do codigo.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.persona.sheet import PersonaSheet
from app.core.validation.analysis import AnatomyDetector, ImageAnalysis, TextReader
from app.core.validation.geometry import Keypoint, body_ratios, pose_distance, ratio_deviation
from app.providers.base import ProviderImage

PASS, FAIL, UNKNOWN, NOT_COMPARABLE, INFORMATIONAL = "PASS", "FAIL", "UNKNOWN", "NOT_COMPARABLE", "INFORMATIONAL"
# Corpo grande o bastante para ser "sujeito" e nao figurante distante.
PROMINENT_BODY = 0.25


@dataclass
class CheckResult:
    name: str
    status: str
    blocking: bool
    reason: str
    score: float | None = None
    threshold: float | None = None
    confidence: str = "MEDIUM"
    failure_type: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationContext:
    sheet: PersonaSheet
    image: ProviderImage
    analysis: ImageAnalysis
    # Esqueleto pedido (modo POSE_CONTROLLED); None = pose livre.
    requested_pose: list[Keypoint] | None = None
    # Esqueleto da master_body (medido uma vez, mesma imagem da ficha).
    master_body_pose: list[Keypoint] | None = None
    threshold_override: float | None = None


class FaceIdentityValidator:
    name = "face_identity"

    async def check(self, ctx: ValidationContext) -> CheckResult:
        cfg = ctx.sheet.validation["face_identity"]
        threshold = ctx.threshold_override if ctx.threshold_override is not None else float(cfg["threshold"])
        face = ctx.analysis.persona_face()
        if face is None:
            return CheckResult(self.name, FAIL, True, "Nenhum rosto na imagem: a identidade nao pode ser verificada.",
                               threshold=threshold, failure_type="face_not_found",
                               evidence={"faces": len(ctx.analysis.faces)})
        ok = round(face.similarity, 4) >= round(threshold, 4)
        return CheckResult(
            self.name, PASS if ok else FAIL, True,
            f"ArcFace {face.similarity:.3f} {'>=' if ok else '<'} {threshold:.2f} contra a master_face.",
            score=round(face.similarity, 4), threshold=threshold, confidence=cfg.get("confidence", "MEDIUM"),
            failure_type=None if ok else "face_identity_low",
            evidence={"metric": cfg.get("metric"), "faces": len(ctx.analysis.faces), "bbox": face.bbox, "age": face.age},
        )


class AgeValidator:
    """Informativo na V1: registra o drift de idade conhecido (Qwen BFS rejuvenesce)."""

    name = "age"

    async def check(self, ctx: ValidationContext) -> CheckResult:
        age = ctx.sheet.age
        face = ctx.analysis.persona_face()
        if face is None or face.age is None or not age.get("accepted_range"):
            return CheckResult(self.name, UNKNOWN, False, "Idade nao estimada.", confidence="LOW")
        low, high = age["accepted_range"]
        inside = low <= face.age <= high
        return CheckResult(
            self.name, INFORMATIONAL, False,
            f"Idade estimada {face.age:.0f} ({'dentro' if inside else 'fora'} de {low}-{high}; alvo {age.get('target')}).",
            score=face.age, confidence="LOW",
            evidence={"target": age.get("target"), "range": [low, high], "inside": inside,
                      "known_drift": age.get("known_drift")},
        )


class SubjectCountValidator:
    """Responde "existe mais de uma instancia da persona?", nao "existem duas pessoas?".

    - Mais de um rosto com semelhanca >= persona_duplicate_similarity: duplicata -> FAIL.
    - Rostos que nao sao ela (garcom, pedestres): permitidos.
    - Corpo grande sem rosto da persona: nao da para dizer se e ela (de costas,
      corpo dentro de corpo). Fica como evidencia e confianca baixa, sem
      heuristica que reprove a imagem.
    """

    name = "subject_count"

    async def check(self, ctx: ValidationContext) -> CheckResult:
        cfg = ctx.sheet.validation["subject_count"]
        dup = float(cfg["persona_duplicate_similarity"])
        expected = int(cfg.get("persona_instances_expected", 1))
        faces = ctx.analysis.faces
        persona_like = [f for f in faces if f.similarity is not None and f.similarity >= dup]
        others = [f for f in faces if f not in persona_like]
        prominent = [b for b in ctx.analysis.bodies if b.height_frac >= PROMINENT_BODY]
        evidence = {
            "persona_instances": len(persona_like), "persona_similarities": [round(f.similarity, 3) for f in persona_like],
            "other_faces": len(others), "other_similarities": [round(f.similarity or 0, 3) for f in others],
            "bodies": len(ctx.analysis.bodies), "prominent_bodies": len(prominent),
            "background_bodies": len(ctx.analysis.bodies) - len(prominent),
        }
        if len(persona_like) > expected:
            return CheckResult(self.name, FAIL, True,
                               f"{len(persona_like)} rostos com cara da persona (>= {dup}): duplicacao da persona.",
                               score=float(len(persona_like)), threshold=float(expected),
                               confidence=cfg.get("confidence", "LOW"), failure_type="persona_duplicated", evidence=evidence)
        # Corpos grandes alem dos rostos achados: alguem sem rosto visivel.
        unexplained = max(0, len(prominent) - len(faces))
        note = ""
        if unexplained:
            note = f" {unexplained} corpo(s) grande(s) sem rosto identificavel: duplicata sem rosto nao e verificavel."
        return CheckResult(self.name, PASS, True,
                           f"{len(persona_like)} instancia da persona; {len(others)} outra(s) pessoa(s) com rosto, permitidas.{note}",
                           score=float(len(persona_like)), threshold=float(expected),
                           confidence="LOW" if unexplained else cfg.get("confidence", "LOW"), evidence=evidence)


class AnatomyValidator:
    name = "anatomy"

    def __init__(self, detector: AnatomyDetector | None = None) -> None:
        self.detector = detector

    async def check(self, ctx: ValidationContext) -> CheckResult:
        if self.detector is None:
            return CheckResult(self.name, UNKNOWN, True,
                               "Sem detector de anatomia validado: nao verificado (nao e PASS).", confidence="LOW",
                               evidence={"detector": None, "main_body_points": (ctx.analysis.main_body().visible_points
                                                                                  if ctx.analysis.main_body() else 0)})
        result = await self.detector.inspect(ctx.image, ctx.analysis)
        failures = result.get("hard_failures") or []
        if failures:
            return CheckResult(self.name, FAIL, True, f"Falha anatomica: {', '.join(failures)}.",
                               confidence=result.get("confidence", "MEDIUM"), failure_type="anatomy", evidence=result)
        if result.get("verified"):
            return CheckResult(self.name, PASS, True, "Detector nao achou falha grave.",
                               confidence=result.get("confidence", "MEDIUM"), evidence=result)
        return CheckResult(self.name, UNKNOWN, True, "Detector sem conclusao.", confidence="LOW", evidence=result)


class PoseValidator:
    name = "pose"

    async def check(self, ctx: ValidationContext) -> CheckResult:
        cfg = ctx.sheet.validation["pose"]
        body = ctx.analysis.main_body()
        if ctx.requested_pose is None:
            return CheckResult(self.name, INFORMATIONAL, False, "Pose livre (sem pose pedida).",
                               evidence={"main_body_points": body.visible_points if body else 0})
        limit = float(cfg["max_distance"])
        if body is None:
            return CheckResult(self.name, FAIL, True, "Nenhum corpo detectado para comparar com a pose pedida.",
                               threshold=limit, failure_type="pose_mismatch")
        distance = pose_distance(ctx.requested_pose, body.keypoints)
        if distance is None:
            return CheckResult(self.name, UNKNOWN, False, "Pontos insuficientes para comparar as poses.",
                               threshold=limit, confidence="LOW")
        ok = distance <= limit
        return CheckResult(self.name, PASS if ok else FAIL, True,
                           f"Distancia do esqueleto {distance:.3f} {'<=' if ok else '>'} {limit}.",
                           score=distance, threshold=limit, confidence=cfg.get("confidence", "MEDIUM"),
                           failure_type=None if ok else "pose_mismatch", evidence={"metric": cfg.get("metric")})


class BodyConsistencyValidator:
    """Proporcoes contra a master_body SO quando a pose bate com a dela;
    senao NOT_COMPARABLE (nao inventa nota)."""

    name = "body_consistency"

    async def check(self, ctx: ValidationContext) -> CheckResult:
        cfg = ctx.sheet.validation["body_consistency"]
        master = ctx.sheet.data["master_references"].get("master_body", {})
        reference = master.get("ratios") or {}
        body = ctx.analysis.main_body()
        if body is None or not ctx.master_body_pose or not reference:
            return CheckResult(self.name, NOT_COMPARABLE, False, "Sem corpo ou sem esqueleto da master_body.",
                               confidence="LOW", evidence={"pose_match": False})
        match_limit = float(cfg["pose_match_max_distance"])
        distance = pose_distance(ctx.master_body_pose, body.keypoints)
        if distance is None or distance > match_limit:
            return CheckResult(self.name, NOT_COMPARABLE, False,
                               f"Pose diferente da master_body ({master.get('pose_class')}): proporcoes 2D nao comparaveis.",
                               confidence="LOW", evidence={"pose_match": False, "pose_distance": distance})
        deviation = ratio_deviation(body_ratios(body.keypoints), reference)
        if deviation is None:
            return CheckResult(self.name, UNKNOWN, False, "Segmentos insuficientes.", confidence="LOW",
                               evidence={"pose_match": True, "pose_distance": distance})
        limit = float(cfg["max_deviation"])
        ok = deviation <= limit
        return CheckResult(self.name, PASS if ok else FAIL, True,
                           f"Desvio medio das proporcoes {deviation:.1%} {'<=' if ok else '>'} {limit:.0%} (mesma pose da master).",
                           score=deviation, threshold=limit, confidence=cfg.get("confidence", "LOW"),
                           failure_type=None if ok else "body_mismatch",
                           evidence={"pose_match": True, "pose_distance": distance, "ratios": body_ratios(body.keypoints)})


class TriggerLeakValidator:
    """O gatilho da LoRA escrito na cena. Com OCR: FAIL se aparecer; sem OCR: UNKNOWN."""

    name = "trigger_leak"

    def __init__(self, reader: TextReader | None = None) -> None:
        self.reader = reader

    async def check(self, ctx: ValidationContext) -> CheckResult:
        trigger = ctx.sheet.generation["scene"]["trigger"]
        if self.reader is None:
            return CheckResult(self.name, UNKNOWN, False, "OCR desligado: vazamento do gatilho nao verificado.",
                               confidence="LOW", evidence={"trigger": "***"})
        text = (await self.reader.read(ctx.image) or "").lower()
        # Parecido o bastante: "lunavox", "luna vox", "lunav0x".
        pattern = re.compile(r"l\W*u\W*n\W*a\W*v\W*[o0]\W*x")
        leaked = bool(pattern.search(text)) or trigger.lower() in text
        return CheckResult(self.name, FAIL if leaked else PASS, True,
                           "Gatilho da LoRA escrito na imagem." if leaked else "Gatilho nao encontrado no texto da imagem.",
                           confidence="MEDIUM", failure_type="trigger_leak" if leaked else None,
                           evidence={"ocr_chars": len(text)})
