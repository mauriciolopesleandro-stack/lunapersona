"""GenerationOrchestrator: o laco do Persona Engine.

    GERAR -> VALIDAR -> passou? -> ACEITA
                          nao -> ANALISAR A FALHA -> AJUSTAR -> GERAR de novo

No maximo max_attempts tentativas (e nunca mais que MAX_ATTEMPTS_LIMIT):
nao existe laco infinito. Se todas reprovarem, o job fica FAILED com todas
as tentativas gravadas e a melhor marcada.

So conhece interfaces: ModelAdapter (o modelo), o validador de identidade,
o QualityGate, o FailureAnalyzer e o RetryManager.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable, Protocol

from app.core.generation.history import ACCEPTED, ERROR, FAILED, RUNNING, GenerationHistory
from app.core.generation.prompt_builder import PromptBuilder
from app.core.generation.request import MAX_ATTEMPTS_LIMIT, GenerationRequest, InvalidGenerationRequestError
from app.core.observability import log_event
from app.core.persona import PersonaProfile, PersonaRepository
from app.core.persona.references import ReferenceManager
from app.core.storage import new_id, utcnow
from app.core.validation.types import ACCEPT, Failure, IdentityValidationResult
from app.providers.base import (
    GenerationParameters,
    ProviderCapabilities,
    ProviderError,
    ProviderImage,
    ProviderInput,
    ProviderRegistry,
    ReferenceImage,
)


class IdentityChecker(Protocol):
    async def check_ready(self) -> list[str]: ...

    async def validate(
        self, references: list[ReferenceImage], image: ProviderImage, persona: PersonaProfile
    ) -> IdentityValidationResult: ...


class Gate(Protocol):
    def decide(self, result: IdentityValidationResult, threshold: float) -> IdentityValidationResult: ...


class Analyzer(Protocol):
    def analyze(self, result: IdentityValidationResult, threshold: float) -> list[Failure]: ...


class Retrier(Protocol):
    def plan(
        self, attempt: int, parameters: GenerationParameters, emphasis: list[str],
        result: IdentityValidationResult | None, failures: list[Failure],
        capabilities: ProviderCapabilities, persona: PersonaProfile,
    ) -> Any: ...


class Thresholds(Protocol):
    def resolve(self, job: float | None, persona: float | None, provider: str) -> float: ...


class GenerationJobError(Exception):
    """O job nao pode nem comecar (persona sem foto, modelo fora do ar)."""


Translator = Callable[[str], Awaitable[str]]


class GenerationOrchestrator:
    def __init__(
        self,
        *,
        personas: PersonaRepository,
        references: ReferenceManager,
        providers: ProviderRegistry,
        validator: IdentityChecker,
        gate: Gate,
        analyzer: Analyzer,
        retry: Retrier,
        thresholds: Thresholds,
        history: GenerationHistory,
        builder: PromptBuilder | None = None,
        translator: Translator | None = None,
    ) -> None:
        self.personas = personas
        self.references = references
        self.providers = providers
        self.validator = validator
        self.gate = gate
        self.analyzer = analyzer
        self.retry = retry
        self.thresholds = thresholds
        self.history = history
        self.builder = builder or PromptBuilder()
        # Cena digitada em portugues -> ingles (os modelos entendem melhor).
        self.translator = translator

    # --- preparo (sincrono: erros viram 4xx na rota) ----------------------

    def prepare(self, req: GenerationRequest, retry_of: str | None = None) -> dict[str, Any]:
        req.validate()
        persona = self.personas.get(req.persona_id)
        provider = req.provider or self.providers.default()
        adapter = self.providers.get(provider)
        threshold = self.thresholds.resolve(req.validation_threshold, persona.validation.threshold, provider)
        params = adapter.normalize_parameters(req.generation_parameters)
        now = utcnow()
        job = {
            "id": new_id(),
            "persona_id": persona.id,
            "persona_version": persona.version,
            "provider": provider,
            "scene_prompt": req.scene_prompt.strip(),
            "scene_prompt_en": None,
            "style_overrides": dict(req.style_overrides),
            "parameters": params.to_dict(),
            "prompt": None,
            "threshold": threshold,
            "max_attempts": min(req.max_attempts, MAX_ATTEMPTS_LIMIT),
            "status": RUNNING,
            "attempt": 0,
            "best_result_id": None,
            "accepted_result_id": None,
            "error": None,
            "retry_of": retry_of,
            "results": [],
            "failures": [],
            "retries": [],
            "created_at": now,
            "updated_at": now,
        }
        return self.history.save(job)

    def prepare_retry(self, job_id: str, max_attempts: int | None = None) -> dict[str, Any]:
        """Novo job com o mesmo pedido de um anterior (POST /generation/:id/retry)."""
        old = self.history.get(job_id)
        params = GenerationParameters(**{**old["parameters"], "seed": None})
        req = GenerationRequest(
            persona_id=old["persona_id"], scene_prompt=old["scene_prompt"], provider=old["provider"],
            style_overrides=old.get("style_overrides", {}), generation_parameters=params,
            validation_threshold=old["threshold"], max_attempts=max_attempts or old["max_attempts"],
        )
        return self.prepare(req, retry_of=job_id)

    # --- execucao --------------------------------------------------------

    async def run(self, job_id: str) -> dict[str, Any]:
        job = self.history.get(job_id)
        try:
            await self._loop(job)
        except GenerationJobError as exc:
            job["status"], job["error"] = ERROR, str(exc)
            log_event("generation_error", job_id=job["id"], error=str(exc))
        except Exception as exc:  # nunca deixa o job preso em RUNNING
            job["status"], job["error"] = ERROR, f"{exc.__class__.__name__}: {exc}"
            log_event("generation_error", job_id=job["id"], error=job["error"])
            self.history.save(job)
            raise
        return self.history.save(job)

    async def submit(self, req: GenerationRequest) -> dict[str, Any]:
        return await self.run(self.prepare(req)["id"])

    async def _loop(self, job: dict[str, Any]) -> None:
        persona = self.personas.get(job["persona_id"])
        adapter = self.providers.get(job["provider"])
        capabilities = adapter.get_capabilities()
        refs = self.references.identity_references(persona.id)
        if not refs:
            raise GenerationJobError("A persona nao tem foto de rosto ativa: sem ela nao ha como conferir a identidade.")
        references = [
            ReferenceImage(r.id, r.filename, self.references.read_bytes(r), r.type, r.weight) for r in refs
        ]
        # Sem o modelo OU sem como medir o rosto, nem comeca: gerar 4 vezes
        # sem poder validar so gastaria GPU.
        problems = [*await adapter.validate_configuration(), *await self.validator.check_ready()]
        if problems:
            raise GenerationJobError("; ".join(problems))

        scene = job["scene_prompt"]
        if self.translator is not None:
            scene = (await self.translator(scene)) or scene
        job["scene_prompt_en"] = scene

        params = GenerationParameters(**job["parameters"])
        emphasis: list[str] = []
        threshold = float(job["threshold"])
        for attempt in range(1, job["max_attempts"] + 1):
            job["attempt"] = attempt
            if attempt > 1:
                log_event("regeneration_started", job_id=job["id"], attempt=attempt, parameters=params.to_dict(),
                          emphasis=emphasis)
            sections = self.builder.build(persona, scene, job["style_overrides"], emphasis)
            if job["prompt"] is None:
                job["prompt"] = sections.to_dict()
            result: dict[str, Any] = {
                "id": new_id(), "job_id": job["id"], "attempt": attempt, "image_url": None, "image_locator": None,
                "identity_score": None, "face_score": None, "appearance_score": None, "status": "ERROR",
                "validation": None, "prompt_text": sections.text, "parameters": params.to_dict(),
                "effective_parameters": {}, "duration_seconds": None, "created_at": utcnow(),
            }
            job["results"].append(result)
            log_event("generation_started", job_id=job["id"], persona_id=persona.id, provider=job["provider"],
                      attempt=attempt)
            validation: IdentityValidationResult | None = None
            try:
                output = await adapter.generate(ProviderInput(
                    persona_id=persona.id, prompt=sections, parameters=params, references=references,
                    identity_assets=persona.identity_assets, sex=persona.identity.sex,
                ))
            except ProviderError as exc:
                failures = [Failure("provider_error", "high", str(exc))]
                log_event("generation_error", job_id=job["id"], attempt=attempt, error=str(exc))
            else:
                image = output.images[0]
                result.update(image_url=image.url, image_locator=image.locator,
                              effective_parameters=output.effective_parameters,
                              duration_seconds=round(output.duration_seconds, 2))
                log_event("generation_completed", job_id=job["id"], attempt=attempt,
                          duration_seconds=result["duration_seconds"])
                log_event("identity_validation_started", job_id=job["id"], attempt=attempt)
                validation = self.gate.decide(await self.validator.validate(references, image, persona), threshold)
                result.update(identity_score=validation.identity_score, face_score=validation.face_similarity,
                              appearance_score=validation.appearance_score, status=validation.status,
                              validation=validation.to_dict())
                log_event("identity_validation_completed", job_id=job["id"], attempt=attempt,
                          identity_score=validation.identity_score, status=validation.status)
                if validation.status == ACCEPT:
                    job["status"], job["accepted_result_id"] = ACCEPTED, result["id"]
                    job["best_result_id"] = result["id"]
                    log_event("generation_accepted", job_id=job["id"], attempt=attempt,
                              identity_score=validation.identity_score)
                    return
                failures = self.analyzer.analyze(validation, threshold)
                log_event("generation_rejected", job_id=job["id"], attempt=attempt,
                          identity_score=validation.identity_score, failures=[f.failure_type for f in failures])
            for failure in failures:
                job["failures"].append({"id": new_id(), "result_id": result["id"], **failure.to_dict(),
                                        "created_at": utcnow()})
            self.history.save(job)
            if attempt == job["max_attempts"]:
                break
            plan = self.retry.plan(attempt, params, emphasis, validation, failures, capabilities, persona)
            job["retries"].append({
                "attempt_number": attempt + 1,
                "previous_score": validation.identity_score if validation else None,
                "failure_reason": failures[0].failure_type if failures else None,
                "new_parameters": plan.parameters.to_dict(),
                "emphasis": plan.emphasis,
                "changes": plan.changes,
                "created_at": utcnow(),
            })
            params, emphasis = replace(plan.parameters), list(plan.emphasis)

        scored = [r for r in job["results"] if r["identity_score"] is not None]
        job["best_result_id"] = max(scored, key=lambda r: r["identity_score"])["id"] if scored else None
        job["status"] = FAILED
        log_event("generation_failed", job_id=job["id"], attempts=job["attempt"],
                  best_score=max((r["identity_score"] for r in scored), default=None))


__all__ = ["GenerationJobError", "GenerationOrchestrator", "InvalidGenerationRequestError"]
