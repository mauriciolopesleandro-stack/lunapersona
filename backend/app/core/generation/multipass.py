"""Multi-pass do Persona Engine V2: BASE -> ROSTO 1-3 -> CORPO 1-2 -> validacao.

Cada passada parte do MELHOR estado aceito ate ali (checkpoint), nunca do zero.
Depois de cada passada a imagem e medida (rosto, idade, pele, pose, pessoas,
corpo) e a passada so fica se nao piorou o que importa (limites da config);
senao, ROLLBACK para o checkpoint anterior, com o motivo registrado.

O nucleo calcula ONDE mexer (regioes e mascaras a partir dos pontos do rosto e
do corpo) e decide se aceita; quem desenha e o RegionPassAdapter do provider.

Significado dos parametros de cada passada:
  denoise  = quanto o recorte e redesenhado pelo modelo (+ LoRA da persona);
  strength = opacidade da mascara ao colar o resultado (quanto da passada fica).
A LoRA roda sempre com o peso configurado; nenhuma passada mexe nesse peso.

A nota da pele e telemetria: nunca decide sozinha (confunde brilho com textura).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.generation.pass_telemetry import PassRecord, base_record, cost_of, text_hash
from app.core.generation.v2_config import PassSpec
from app.core.validation.analysis import DetectedBody, DetectedFace, ImageAnalysis
from app.core.validation.geometry import Keypoint, pose_distance
from app.providers.base import (
    ProviderError,
    ProviderImage,
    ReferenceImage,
    RegionPassAdapter,
    RegionPassRequest,
    SceneAdapter,
    SceneRequest,
)

ACCEPTED, ROLLBACK, SKIPPED, ERROR = "ACCEPTED", "ROLLBACK", "SKIPPED", "ERROR"
LIMB_POINTS = (3, 4, 6, 7, 9, 10, 12, 13)  # cotovelos, pulsos, joelhos, tornozelos (OpenPose 18)


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class Shape:
    """Area da mascara em coordenadas do RECORTE: elipse ou retangulo; include/exclude."""

    kind: str  # "ellipse" | "rect"
    cx: float
    cy: float
    rx: float
    ry: float
    include: bool = True


@dataclass(frozen=True)
class PassRegion:
    crop: Box
    shapes: tuple[Shape, ...]
    feather: float  # fracao do menor lado do recorte

    def to_dict(self) -> dict[str, Any]:
        return {"crop": self.crop.to_dict(), "shapes": [asdict(s) for s in self.shapes], "feather": self.feather}


@dataclass
class Measure:
    face: float | None
    age: float | None
    skin: float | None
    pose: float | None  # distancia da pose contra a BASE (0 = igual)
    persona_instances: int
    faces: int
    body: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Checkpoint:
    name: str
    image: ProviderImage
    analysis: ImageAnalysis
    measure: Measure


@dataclass
class MultiPassResult:
    final: Checkpoint
    checkpoints: list[Checkpoint] = field(default_factory=list)
    records: list[PassRecord] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "final": {"name": self.final.name, "image": self.final.image.url, "measure": self.final.measure.to_dict()},
            "checkpoints": [{"name": c.name, "image": c.image.url, "locator": c.image.locator,
                             "measure": c.measure.to_dict()} for c in self.checkpoints],
            "records": [r.to_dict() for r in self.records],
            "decisions": self.decisions,
        }


# --- regioes ------------------------------------------------------------------


def _clamp_box(cx: float, cy: float, w: float, h: float, width: int, height: int) -> Box:
    w, h = min(w, width), min(h, height)
    x = int(max(0, min(width - w, cx - w / 2)))
    y = int(max(0, min(height - h, cy - h / 2)))
    return Box(x, y, int(w) // 8 * 8, int(h) // 8 * 8)


def face_region(face: DetectedFace, mask: str, width: int, height: int) -> PassRegion:
    x1, y1, x2, y2 = face.bbox
    fw, fh = x2 - x1, y2 - y1
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    # recorte quadrado com contexto (cabelo, pescoco): o modelo precisa ver a cabeca inteira
    side = max(fw, fh) * (2.2 if mask == "face_full" else 1.8)
    crop = _clamp_box(cx, cy, side, side, width, height)
    lx, ly = cx - crop.x, cy - crop.y
    scale = {"face_full": 0.62, "face_inner": 0.42, "face_skin": 0.55}[mask]
    shapes = (Shape("ellipse", lx, ly, fw * scale, fh * scale),)
    return PassRegion(crop, shapes, 0.10 if mask == "face_full" else 0.08)


def body_region(body: DetectedBody, face: DetectedFace | None, mask: str, width: int, height: int) -> PassRegion:
    bx1, by1, bx2, by2 = body.bbox
    bw, bh = bx2 - bx1, by2 - by1
    crop = _clamp_box((bx1 + bx2) / 2, (by1 + by2) / 2, bw * 1.25 + 32, bh * 1.12 + 32, width, height)
    shapes: list[Shape] = []
    if mask == "body_full":
        shapes.append(Shape("rect", (bx1 + bx2) / 2 - crop.x, (by1 + by2) / 2 - crop.y, bw * 0.56, bh * 0.53))
    else:  # body_regions: maos, bracos, pernas
        r = max(18.0, bh * 0.07)
        for i in LIMB_POINTS:
            if i < len(body.keypoints) and body.keypoints[i][2] > 0.3:
                x, y, _ = body.keypoints[i]
                shapes.append(Shape("ellipse", x - crop.x, y - crop.y, r, r))
    if face is not None:  # passada de corpo NUNCA mexe no rosto
        fx1, fy1, fx2, fy2 = face.bbox
        shapes.append(Shape("ellipse", (fx1 + fx2) / 2 - crop.x, (fy1 + fy2) / 2 - crop.y,
                            (fx2 - fx1) * 0.75, (fy2 - fy1) * 0.8, include=False))
    return PassRegion(crop, tuple(shapes), 0.04)


# --- medida e decisao ------------------------------------------------------------


def measure(analysis: ImageAnalysis, skin: float | None, base_pose: list[Keypoint] | None,
            duplicate_similarity: float) -> Measure:
    face = analysis.persona_face()
    body = analysis.main_body()
    pose = pose_distance(base_pose, body.keypoints) if base_pose and body else None
    persona = [f for f in analysis.faces if f.similarity is not None and f.similarity >= duplicate_similarity]
    return Measure(face.similarity if face else None, face.age if face else None, skin, pose, len(persona),
                   len(analysis.faces))


def judge(spec: PassSpec, before: Measure, after: Measure, rules: dict[str, Any], age_target: int) -> list[str]:
    """Motivos para recusar a passada (lista vazia = aceita)."""
    reasons = []
    if after.face is None:
        reasons.append("rosto da persona sumiu")
    elif before.face is not None and before.face - after.face > float(rules["max_identity_drop"]):
        reasons.append(f"identidade caiu {before.face - after.face:.3f} (> {rules['max_identity_drop']})")
    if spec.kind == "face" and before.face is not None and after.face is not None:
        gain = float(rules.get("min_face_gain", {}).get(spec.name, -1.0))
        if after.face - before.face < gain:
            reasons.append(f"identidade nao subiu o minimo ({after.face - before.face:+.3f} < {gain:+.3f})")
    if before.age is not None and after.age is not None:
        worse = abs(after.age - age_target) - abs(before.age - age_target)
        if worse > float(rules["max_age_worsening"]):
            reasons.append(f"idade se afastou do alvo {age_target} ({before.age:.0f} -> {after.age:.0f})")
    if after.pose is not None and after.pose > float(rules["max_pose_distance"]):
        reasons.append(f"pose mudou ({after.pose:.3f} > {rules['max_pose_distance']})")
    if after.persona_instances > max(1, before.persona_instances):
        reasons.append(f"mais de uma Luna ({after.persona_instances})")
    if after.faces > before.faces:
        reasons.append(f"rosto novo apareceu ({before.faces} -> {after.faces})")
    return reasons


# --- execucao --------------------------------------------------------------------


class MultiPassRunner:
    def __init__(self, *, base: SceneAdapter, region: RegionPassAdapter, analyzer, skin_analyzer=None,
                 rules: dict[str, Any], age_target: int, duplicate_similarity: float, skin_config: dict[str, Any] | None,
                 price_per_hour: float | None = None) -> None:
        self.base = base
        self.region = region
        self.analyzer = analyzer
        self.skin_analyzer = skin_analyzer
        self.rules = rules
        self.age_target = age_target
        self.duplicate_similarity = duplicate_similarity
        self.skin_config = skin_config
        self.price = price_per_hour

    async def _check(self, name: str, image: ProviderImage, master: ReferenceImage,
                     base_pose: list[Keypoint] | None) -> Checkpoint:
        analysis = await self.analyzer.analyze(image, master)
        skin = None
        if self.skin_analyzer is not None and self.skin_config:
            try:
                skin = (await self.skin_analyzer.analyze(image, analysis.persona_face(), self.skin_config)).score
            except Exception:  # pele e telemetria: falha vira None, nunca derruba a geracao
                skin = None
        return Checkpoint(name, image, analysis, measure(analysis, skin, base_pose, self.duplicate_similarity))

    def _fill(self, rec: PassRecord, m: Measure, seconds: float, gpu: dict[str, Any]) -> None:
        rec.face_identity_score, rec.skin_score, rec.pose_score = m.face, m.skin, m.pose
        rec.age_score = round(max(0.0, 1 - abs(m.age - self.age_target) / 10), 3) if m.age is not None else None
        rec.subject_count, rec.body_score = m.persona_instances, m.body
        rec.duration, rec.gpu, rec.vram = round(seconds, 2), gpu.get("name"), gpu.get("vram_used_mb")
        rec.cost = cost_of(seconds, self.price)

    async def run(self, request: SceneRequest, master: ReferenceImage, face_passes: list[PassSpec],
                  body_passes: list[PassSpec], prompts: dict[str, str], negative: str, lora: tuple[str, float],
                  model: tuple[str, str]) -> MultiPassResult:
        seed = request.parameters.seed or 0
        base_out = await self.base.generate(request)
        base = await self._check("base", base_out.image, master, None)
        body0 = base.analysis.main_body()
        base_pose = body0.keypoints if body0 else None
        base.measure.pose = 0.0 if base_pose else None
        rec = base_record(model=model[0], model_version=model[1], lora=lora[0], lora_strength=lora[1], seed=seed,
                          prompt=base_out.effective_parameters.get("prompt", ""), negative=negative)
        self._fill(rec, base.measure, base_out.seconds, base_out.gpu)
        rec.image = base_out.image.url
        result = MultiPassResult(final=base, checkpoints=[base], records=[rec])
        current = base
        for spec in [*face_passes, *body_passes]:
            if not spec.enabled:
                continue
            name = f"{spec.kind}_{spec.number}"
            prompt = prompts[spec.kind if spec.kind == "body" else spec.name]
            rec = PassRecord(model=model[0], model_version=model[1], lora=lora[0], lora_strength=lora[1],
                             seed=(seed + 100 * len(result.records)) % 2**32, prompt_hash=text_hash(prompt),
                             negative_hash=text_hash(negative), pass_type=spec.kind, pass_number=spec.number,
                             mask_type=spec.mask, denoise=spec.denoise, strength=spec.strength)
            result.records.append(rec)
            face = current.analysis.persona_face()
            body = current.analysis.main_body()
            if spec.kind == "face" and face is None:
                rec.rollback, rec.rollback_reason = True, "sem rosto da persona para refinar"
                result.decisions.append({"pass": name, "status": SKIPPED, "reasons": [rec.rollback_reason]})
                continue
            if spec.kind == "body" and body is None:
                rec.rollback, rec.rollback_reason = True, "sem corpo detectado para refinar"
                result.decisions.append({"pass": name, "status": SKIPPED, "reasons": [rec.rollback_reason]})
                continue
            w, h = current.analysis.width, current.analysis.height
            region = (face_region(face, spec.mask, w, h) if spec.kind == "face"
                      else body_region(body, face, spec.mask, w, h))
            start = time.monotonic()
            try:
                out = await self.region.refine(current.image, RegionPassRequest(
                    region=region.to_dict(), prompt=prompt, negative=negative, denoise=spec.denoise,
                    strength=spec.strength, seed=rec.seed, name=name))
            except ProviderError as exc:
                rec.rollback, rec.rollback_reason = True, f"erro do provider: {exc}"
                result.decisions.append({"pass": name, "status": ERROR, "reasons": [str(exc)]})
                continue
            after = await self._check(name, out.image, master, base_pose)
            self._fill(rec, after.measure, out.seconds, out.gpu)
            rec.duration = round(time.monotonic() - start, 2)
            rec.image = out.image.url
            reasons = judge(spec, current.measure, after.measure, self.rules, self.age_target)
            result.checkpoints.append(after)
            decision = {"pass": name, "region": region.to_dict(), "before": current.measure.to_dict(),
                        "after": after.measure.to_dict(), "reasons": reasons, "pass_seconds": out.seconds}
            if reasons:
                rec.rollback, rec.rollback_reason = True, "; ".join(reasons)
                decision["status"], decision["kept"] = ROLLBACK, current.name
            else:
                decision["status"], decision["kept"] = ACCEPTED, name
                current = after
            result.decisions.append(decision)
        result.final = current
        return result


__all__ = ["ACCEPTED", "ERROR", "ROLLBACK", "SKIPPED", "Box", "Checkpoint", "Measure", "MultiPassResult",
           "MultiPassRunner", "PassRegion", "Shape", "body_region", "face_region", "judge", "measure"]
