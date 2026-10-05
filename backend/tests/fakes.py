"""Pecas falsas para testar o Persona Engine V1 sem GPU: etapas de cena,
Face Lock e pose que "geram" imagens numeradas, e um analisador que devolve
rostos e corpos combinados por imagem."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from app.core.generation.history import GenerationHistory
from app.core.generation.negative import NegativePromptBuilder
from app.core.generation.orchestrator import GenerationOrchestrator
from app.core.generation.prompt_builder import PromptBuilder
from app.core.generation.retry_policy import RetryPolicy
from app.core.persona import PersonaRepository
from app.core.persona.sheet import PersonaSheetRepository
from app.core.telemetry import CostEstimator
from app.core.validation.analysis import DetectedBody, DetectedFace, ImageAnalysis
from app.core.validation.checks import (
    AgeValidator,
    AnatomyValidator,
    BodyConsistencyValidator,
    FaceIdentityValidator,
    PoseValidator,
    SubjectCountValidator,
    TriggerLeakValidator,
)
from app.core.validation.engine import ValidationEngine
from app.providers.base import (
    AdapterCapabilities,
    FaceIdentityAdapter,
    PoseControl,
    PoseControlAdapter,
    ProviderError,
    ProviderImage,
    ProviderRegistry,
    ProviderSet,
    SceneAdapter,
    StageOutput,
)
from tests.conftest import REPO

GLOBAL_NEGATIVE = json.loads((REPO / "config" / "persona_engine.json").read_text(encoding="utf-8"))["global_negative"]

# Corpo em pe, de frente (18 pontos OpenPose: x, y, confianca).
STANDING = [
    (416, 120, 0.9), (416, 200, 0.9), (356, 205, 0.9), (340, 330, 0.9), (335, 450, 0.9),
    (476, 205, 0.9), (492, 330, 0.9), (497, 450, 0.9), (380, 480, 0.9), (378, 690, 0.9),
    (376, 900, 0.9), (452, 480, 0.9), (454, 690, 0.9), (456, 900, 0.9),
    (405, 110, 0.9), (427, 110, 0.9), (395, 115, 0.9), (437, 115, 0.9),
]
# Passada larga com os bracos erguidos (pose bem diferente da STANDING).
WALKING = [
    (430, 120, 0.9), (420, 200, 0.9), (370, 210, 0.9), (300, 130, 0.9), (260, 40, 0.9),
    (470, 200, 0.9), (550, 130, 0.9), (600, 40, 0.9), (390, 480, 0.9), (260, 640, 0.9),
    (170, 820, 0.9), (450, 480, 0.9), (580, 650, 0.9), (690, 820, 0.9),
    (420, 110, 0.9), (440, 110, 0.9), (410, 115, 0.9), (450, 115, 0.9),
]


def body(kps=STANDING, height_frac=0.7) -> DetectedBody:
    xs, ys = [k[0] for k in kps], [k[1] for k in kps]
    return DetectedBody(list(kps), (min(xs), min(ys), max(xs), max(ys)), height_frac, 14)


def face(sim: float | None, age: float = 27.0, bbox=(380.0, 60.0, 460.0, 170.0)) -> DetectedFace:
    return DetectedFace(bbox=bbox, similarity=sim, age=age, sex="F", det_score=0.9)


def analysis(faces=None, bodies=None) -> ImageAnalysis:
    return ImageAnalysis(832, 1216, list(faces or []), list(bodies if bodies is not None else [body()]))


GOOD = analysis([face(0.68)])
LOW_FACE = analysis([face(0.42)])
DUPLICATE = analysis([face(0.66), face(0.61, bbox=(600.0, 80.0, 680.0, 180.0))], [body(), body()])
WITH_EXTRA = analysis([face(0.67), face(-0.04, bbox=(700.0, 300.0, 720.0, 330.0))], [body(), body(height_frac=0.1)])


class FakeScene(SceneAdapter):
    name = "fake-scene"

    def __init__(self, fail_on: set[int] | None = None, problems: list[str] | None = None) -> None:
        self.calls = []
        self.fail_on = fail_on or set()
        self.problems = problems or []
        self.log: list[str] | None = None

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(name=self.name, title="Fake", supports_pose_control=True)

    def model_versions(self, lora):
        return {"unet": "fake-unet", "lora": lora.get("file", "")}

    async def validate_configuration(self, lora, pose):
        return list(self.problems) + ([] if lora.get("file") else ["LoRA da persona nao configurada"])

    async def generate(self, request) -> StageOutput:
        self.calls.append(request)
        n = len(self.calls)
        if self.log is not None:
            self.log.append(f"scene{n}")
        if n in self.fail_on:
            raise ProviderError("cena caiu")
        image = ProviderImage("fake", f"scene{n}", f"http://fake/scene{n}.png", 832, 1216)
        return StageOutput(image, "scene", self.name, 13.0, request.parameters.seed,
                           ["fake-unet", request.lora["file"]], f"p{n}", model_switch=n > 1 and self.log is not None,
                           gpu={"name": "Fake GPU", "vram_used_mb": 12800, "vram_total_mb": 24467},
                           effective_parameters={"negative_applied": False, "pose": request.pose.source if request.pose else None})


class FakeFace(FaceIdentityAdapter):
    name = "fake-face"

    def __init__(self, problems: list[str] | None = None) -> None:
        self.calls = []
        self.problems = problems or []
        self.log: list[str] | None = None

    def model_versions(self):
        return {"unet": "fake-qwen", "bfs": "fake-bfs"}

    async def validate_configuration(self):
        return list(self.problems)

    async def lock_face(self, image, master_face, seed) -> StageOutput:
        self.calls.append((image.locator, master_face, seed))
        n = len(self.calls)
        if self.log is not None:
            self.log.append(f"face{n}")
        out = ProviderImage("fake", f"face{n}<{image.locator}", f"http://fake/face{n}.png", image.width, image.height)
        return StageOutput(out, "face_lock", self.name, 82.0, seed, ["fake-qwen", "fake-bfs"], f"q{n}",
                           gpu={"name": "Fake GPU", "vram_used_mb": 23700, "vram_total_mb": 24467})


class FakePose(PoseControlAdapter):
    name = "fake-pose"

    async def validate_configuration(self):
        return []

    async def prepare(self, pose_reference, strength):
        return PoseControl(pose_reference, strength)


class ScriptedAnalyzer:
    """analysis_for(locator, count) decide o que cada imagem "mostra"."""

    def __init__(self, analysis_for: Callable[[str, int], ImageAnalysis]) -> None:
        self.analysis_for = analysis_for
        self.calls: list[str] = []

    async def check_ready(self):
        return []

    async def analyze(self, image, master_face):
        self.calls.append(image.locator)
        return self.analysis_for(image.locator, len([c for c in self.calls if c.startswith("face")]))

    async def analyze_reference(self, reference, master_face):
        # master_body: andando de tres quartos (como a pose_01 real)
        return analysis([face(0.9)], [body(WALKING)])


def sequence(*results: ImageAnalysis) -> Callable[[str, int], ImageAnalysis]:
    """Imagens finais (face*) recebem os resultados na ordem; o resto (pose de referencia), um corpo em pe."""
    def pick(locator: str, n: int) -> ImageAnalysis:
        if not locator.startswith("face"):
            return analysis([], [body()])
        return results[min(n - 1, len(results) - 1)]
    return pick


class FixedPrice:
    async def gpu_price(self):
        return {"price_per_hour": 0.57, "gpu": "Fake GPU", "source": "teste"}


def build(engine_dir: Path, analyzer: ScriptedAnalyzer, scene=None, face_adapter=None, price=True):
    registry = ProviderRegistry()
    scene = scene or FakeScene()
    face_adapter = face_adapter or FakeFace()
    registry.register(ProviderSet("fake", "Fake", scene, face_adapter, FakePose()))
    orch = GenerationOrchestrator(
        personas=PersonaRepository(engine_dir), sheets=PersonaSheetRepository(engine_dir), providers=registry,
        analyzer=analyzer,
        validation=ValidationEngine([FaceIdentityValidator(), SubjectCountValidator(), AnatomyValidator(), PoseValidator(),
                                     BodyConsistencyValidator(), AgeValidator(), TriggerLeakValidator()]),
        retry=RetryPolicy(1), history=GenerationHistory(engine_dir),
        prompt_builder=PromptBuilder(NegativePromptBuilder(GLOBAL_NEGATIVE)),
        cost=CostEstimator(FixedPrice() if price else None),
    )
    return orch, scene, face_adapter
