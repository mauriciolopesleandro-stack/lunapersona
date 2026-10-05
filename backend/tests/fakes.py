"""Pecas falsas para testar o laco sem GPU: um modelo que "gera" imagens
numeradas e um validador que devolve notas pre-definidas."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.generation.history import GenerationHistory
from app.core.generation.orchestrator import GenerationOrchestrator
from app.core.persona import PersonaRepository
from app.core.persona.references import ReferenceManager
from app.core.validation.types import ACCEPT, REJECT, Failure, IdentityValidationResult, MetricScore
from app.persona_manager.manager import PersonaManager
from app.providers.base import (
    GenerationParameters,
    ModelAdapter,
    ProviderCapabilities,
    ProviderError,
    ProviderImage,
    ProviderInput,
    ProviderOutput,
    ProviderRegistry,
)
from tests.helpers import png


class FakeAdapter(ModelAdapter):
    name = "fake"

    def __init__(self, fail_on: set[int] | None = None, problems: list[str] | None = None) -> None:
        self.calls: list[ProviderInput] = []
        self.fail_on = fail_on or set()
        self.problems = problems or []

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(name="fake", title="Fake", identity_mechanisms=["text"],
                                    supports_face_restore=True, default_size=(512, 512))

    async def validate_configuration(self) -> list[str]:
        return self.problems

    async def generate(self, request: ProviderInput) -> ProviderOutput:
        self.calls.append(request)
        n = len(self.calls)
        if n in self.fail_on:
            raise ProviderError("modelo caiu")
        return ProviderOutput(images=[ProviderImage("fake", f"img{n}", f"http://fake/img{n}.png", 512, 512)],
                              provider_job_id=f"j{n}", duration_seconds=0.1,
                              effective_parameters={"identity_strength": request.parameters.identity_strength})


def result_with(score: float | None, **metrics: float | None) -> IdentityValidationResult:
    names = {"face_similarity": 0.4, "facial_structure": 0.2, **{k: 0.1 for k in metrics}}
    built = {k: MetricScore(k, w, metrics.get(k, score)) for k, w in names.items()}
    return IdentityValidationResult(
        identity_score=score, face_similarity=metrics.get("face_similarity", score),
        facial_structure_score=metrics.get("facial_structure", score), appearance_score=None,
        distinctive_features_score=None, coverage=1.0 if score is not None else 0.0,
        face_found=score is not None, metrics=built,
    )


@dataclass
class ScriptedValidator:
    """Devolve as notas na ordem; repete a ultima."""

    scores: list[float | None]
    seen: list[str] = field(default_factory=list)

    async def validate(self, references, image, persona):
        self.seen.append(image.locator)
        score = self.scores[min(len(self.seen) - 1, len(self.scores) - 1)]
        return result_with(score)


class SimpleGate:
    def decide(self, result, threshold):
        ok = result.identity_score is not None and result.identity_score >= threshold
        return replace(result, status=ACCEPT if ok else REJECT, reasons=[] if ok else ["abaixo do limiar"])


class SimpleAnalyzer:
    def analyze(self, result, threshold):
        return [Failure("low_identity_confidence", "medium", "teste")]


@dataclass
class Plan:
    parameters: GenerationParameters
    emphasis: list[str]
    changes: dict


class SimpleRetry:
    def plan(self, attempt, parameters, emphasis, result, failures, capabilities, persona):
        new = replace(parameters, identity_strength=min(1.0, parameters.identity_strength + 0.1))
        return Plan(new, [*emphasis, f"tentativa {attempt + 1}"], {"identity_strength": new.identity_strength})


class FixedThresholds:
    def __init__(self, default: float = 0.9) -> None:
        self.default = default

    def resolve(self, job, persona, provider):
        return job if job is not None else (persona if persona is not None else self.default)


def build_orchestrator(personas_dir, adapter=None, validator=None, gate=None, analyzer=None, retry=None,
                       thresholds=None, with_reference=True):
    manager = PersonaManager(personas_dir)
    references = ReferenceManager(manager)
    if with_reference:
        references.add("luna", "rosto.png", png(tag=b"luna"), "FACE")
    registry = ProviderRegistry()
    registry.register(adapter or FakeAdapter())
    return GenerationOrchestrator(
        personas=PersonaRepository(personas_dir), references=references, providers=registry,
        validator=validator or ScriptedValidator([0.95]), gate=gate or SimpleGate(),
        analyzer=analyzer or SimpleAnalyzer(), retry=retry or SimpleRetry(),
        thresholds=thresholds or FixedThresholds(), history=GenerationHistory(personas_dir),
    )
