"""GenerationOrchestrator do Persona Engine V1 (pipeline vencedor do benchmark).

    Persona Sheet ─► PromptBuilder ─► [PoseControl] ─► SceneAdapter (Z-Image + LoRA)
        ─► FaceIdentityAdapter (Qwen 2511 BFS, master_face) ─► ValidationEngine
        ─► ACEITA  |  RetryPolicy (por tipo de falha) ─► ...  |  FAILED

- Fonte de verdade: a Persona Sheet. Sem ficha, sem geracao.
- Masters conferidas pelo sha256 a cada job; nada aqui grava em master.
- Sem fallback: se a cena ou o Face Lock nao estao disponiveis, o job e ERROR.
- Teto de tentativas: max_attempts do pedido (nunca acima de MAX_ATTEMPTS_LIMIT).
- Cada tentativa guarda sementes, prompt, parametros efetivos, versoes, validacao,
  telemetria e custo: da para reproduzir a geracao.
- run() = um pedido (SINGLE_REQUEST_MODE). run_batch() = varios pedidos com as
  cenas primeiro e os Face Locks depois (BATCH_MODE, metade do tempo por imagem
  na GPU de 24 GB).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.generation.history import ACCEPTED, ERROR, FAILED, RUNNING, GenerationHistory
from app.core.generation.prompt_builder import PromptBuilder
from app.core.generation.request import MAX_ATTEMPTS_LIMIT, MODE_POSE, GenerationRequest
from app.core.generation.retry_policy import RELOCK_FACE, RetryPolicy
from app.core.observability import log_event
from app.core.persona import PersonaProfile, PersonaRepository
from app.core.persona.sheet import PersonaSheet, PersonaSheetError, PersonaSheetRepository
from app.core.storage import new_id, utcnow
from app.core.telemetry import BATCH_MODE, SINGLE_REQUEST_MODE, CostEstimator
from app.core.validation.analysis import ImageAnalyzer
from app.core.validation.checks import ValidationContext
from app.core.validation.engine import ValidationEngine, ValidationReport
from app.core.validation.geometry import Keypoint
from app.providers.base import (
    GenerationParameters,
    ProviderError,
    ProviderImage,
    ProviderRegistry,
    ProviderSet,
    ReferenceImage,
    SceneRequest,
    StageOutput,
)

Translator = Callable[[str], Awaitable[str]]
SEED_SPACE = 2**32


class GenerationJobError(Exception):
    """O job nao pode nem comecar (ficha, master, modelo fora do ar)."""


@dataclass
class JobContext:
    profile: PersonaProfile
    sheet: PersonaSheet
    provider: ProviderSet
    master_face: ReferenceImage
    lora: dict[str, Any]
    scene_text: str
    requested_pose: list[Keypoint] | None = None
    master_body_pose: list[Keypoint] | None = None


@dataclass
class AttemptState:
    attempt: int
    scene_seed: int
    face_seed: int
    strategy: str = "INITIAL"
    directives: list[str] = field(default_factory=list)
    relocks: int = 0
    base: StageOutput | None = None
    scene_stage: StageOutput | None = None  # cena gerada NESTA tentativa
    face_stage: StageOutput | None = None
    error: str | None = None
    prompt: Any = None
    last_failures: list[str] = field(default_factory=list)


class GenerationOrchestrator:
    def __init__(
        self,
        *,
        personas: PersonaRepository,
        sheets: PersonaSheetRepository,
        providers: ProviderRegistry,
        analyzer: ImageAnalyzer,
        validation: ValidationEngine,
        retry: RetryPolicy,
        history: GenerationHistory,
        prompt_builder: PromptBuilder,
        cost: CostEstimator | None = None,
        translator: Translator | None = None,
    ) -> None:
        self.personas = personas
        self.sheets = sheets
        self.providers = providers
        self.analyzer = analyzer
        self.validation = validation
        self.retry = retry
        self.history = history
        self.prompts = prompt_builder
        self.cost = cost or CostEstimator()
        self.translator = translator
        self._master_poses: dict[str, list[Keypoint] | None] = {}

    # --- preparo (sincrono: erros viram 4xx na rota) ----------------------

    def prepare(self, req: GenerationRequest, retry_of: str | None = None, batch_id: str | None = None) -> dict[str, Any]:
        req.validate()
        profile = self.personas.get(req.persona_id)
        sheet = self.sheets.get(req.persona_id)
        provider_name = req.provider or self.providers.default()
        provider = self.providers.get(provider_name)
        if req.mode == MODE_POSE and provider.pose is None:
            raise PersonaSheetError(f"O provider '{provider_name}' nao tem controle de pose.")
        lora = self._lora(profile, sheet)
        params = provider.scene.normalize_parameters(req.generation_parameters)
        now = utcnow()
        job = {
            "id": new_id(), "batch_id": batch_id, "retry_of": retry_of,
            "persona_id": profile.id, "persona_version": sheet.persona_version,
            "pipeline_version": sheet.pipeline_version, "provider": provider_name,
            "mode": req.mode, "pose_reference": req.pose_reference, "single_subject": req.single_subject,
            "scene_prompt": req.scene_prompt.strip(), "scene_prompt_en": None,
            "style_overrides": dict(req.style_overrides), "parameters": params.to_dict(),
            "threshold": req.validation_threshold if req.validation_threshold is not None
            else float(sheet.validation["face_identity"]["threshold"]),
            "threshold_source": "pedido" if req.validation_threshold is not None else "persona_sheet",
            "max_attempts": min(req.max_attempts, MAX_ATTEMPTS_LIMIT),
            "model_versions": {"scene": provider.scene.model_versions(lora), "face_lock": provider.face.model_versions(),
                               "pose": provider.pose.name if provider.pose and req.mode == MODE_POSE else None},
            "master_reference_versions": sheet.master_reference_versions,
            "prompt_version": None, "negative_version": None,
            "status": RUNNING, "attempt": 0, "execution_mode": None,
            "best_result_id": None, "accepted_result_id": None, "error": None,
            "results": [], "failures": [], "retries": [], "created_at": now, "updated_at": now,
        }
        return self.history.save(job)

    def prepare_retry(self, job_id: str, max_attempts: int | None = None) -> dict[str, Any]:
        old = self.history.get(job_id)
        return self.prepare(self._request_of(old, max_attempts), retry_of=job_id)

    @staticmethod
    def _request_of(job: dict[str, Any], max_attempts: int | None = None) -> GenerationRequest:
        params = GenerationParameters(**{**job["parameters"], "seed": None})
        return GenerationRequest(
            persona_id=job["persona_id"], scene_prompt=job["scene_prompt"], provider=job["provider"],
            style_overrides=job.get("style_overrides", {}), generation_parameters=params, mode=job["mode"],
            pose_reference=job.get("pose_reference"),
            validation_threshold=job["threshold"] if job.get("threshold_source") == "pedido" else None,
            max_attempts=max_attempts or job["max_attempts"], single_subject=job.get("single_subject", True),
        )

    @staticmethod
    def _lora(profile: PersonaProfile, sheet: PersonaSheet) -> dict[str, Any]:
        scene = sheet.generation["scene"]
        assets = profile.identity_assets.get("lora") or {}
        if assets.get("trigger") and assets["trigger"] != scene["trigger"]:
            raise PersonaSheetError("O gatilho da LoRA no persona.json difere do da Persona Sheet.")
        return {"file": assets.get("zimage_file", ""), "trigger": scene["trigger"],
                "strength": float(scene.get("lora_strength", 1.0))}

    # --- execucao --------------------------------------------------------

    async def run(self, job_id: str) -> dict[str, Any]:
        job = self.history.get(job_id)
        job["execution_mode"] = SINGLE_REQUEST_MODE
        try:
            ctx = await self._context(job)
            state = self._initial_state(job)
            await self._loop(job, ctx, state, SINGLE_REQUEST_MODE)
        except (GenerationJobError, PersonaSheetError) as exc:
            self._error(job, str(exc))
        except Exception as exc:  # nunca deixa o job preso em RUNNING
            self._error(job, f"{exc.__class__.__name__}: {exc}")
            self.history.save(job)
            raise
        return self.history.save(job)

    async def run_batch(self, job_ids: list[str]) -> list[dict[str, Any]]:
        """Fase 1: todas as cenas. Fase 2: todos os Face Locks. Fase 3: validacao.
        Quem falha segue sozinho nas proximas tentativas (troca de modelo por imagem)."""
        items = []
        for job_id in job_ids:
            job = self.history.get(job_id)
            job["execution_mode"] = BATCH_MODE
            try:
                items.append((job, await self._context(job), self._initial_state(job)))
            except (GenerationJobError, PersonaSheetError) as exc:
                self._error(job, str(exc))
                self.history.save(job)
        for job, ctx, state in items:
            state.attempt = job["attempt"] = 1
            await self._scene_step(job, ctx, state)
        for job, ctx, state in items:
            await self._face_step(job, ctx, state)
        for job, ctx, state in items:
            done = await self._finish_attempt(job, ctx, state, BATCH_MODE)
            if not done and state.attempt < job["max_attempts"]:
                self._apply_retry(job, state)
                await self._loop(job, ctx, state, SINGLE_REQUEST_MODE, start=state.attempt + 1)
            elif not done:
                self._fail(job)
            self.history.save(job)
        return [self.history.get(j) for j in job_ids]

    async def _context(self, job: dict[str, Any]) -> JobContext:
        profile = self.personas.get(job["persona_id"])
        sheet = self.sheets.get(job["persona_id"])
        if sheet.persona_version != job["persona_version"]:
            raise GenerationJobError(
                f"A Persona Sheet mudou de versao ({job['persona_version']} -> {sheet.persona_version}) depois do pedido."
            )
        provider = self.providers.get(job["provider"])
        lora = self._lora(profile, sheet)
        master = sheet.master("master_face")
        master_face = ReferenceImage(master.reference_id, master.file, sheet.read_master("master_face"), master.sha256)
        pose = job["mode"] == MODE_POSE
        problems = [
            *await provider.scene.validate_configuration(lora, pose),
            *await provider.face.validate_configuration(),
            *(await provider.pose.validate_configuration() if pose and provider.pose else []),
            *await self.analyzer.check_ready(),
        ]
        if problems:
            raise GenerationJobError("; ".join(problems))
        scene = job["scene_prompt"]
        if self.translator is not None:
            scene = (await self.translator(scene)) or scene
        job["scene_prompt_en"] = scene
        ctx = JobContext(profile, sheet, provider, master_face, lora, scene)
        if pose:
            requested = await self.analyzer.analyze(ProviderImage(provider.name, job["pose_reference"], ""), master_face)
            body = requested.main_body()
            ctx.requested_pose = body.keypoints if body else None
        ctx.master_body_pose = await self._master_body_pose(sheet, master_face)
        return ctx

    async def _master_body_pose(self, sheet: PersonaSheet, master_face: ReferenceImage) -> list[Keypoint] | None:
        if "master_body" not in sheet.masters:
            return None
        ref = sheet.master("master_body")
        if ref.sha256 not in self._master_poses:
            image = ReferenceImage(ref.reference_id, ref.file, sheet.read_master("master_body"), ref.sha256)
            analysis = await self.analyzer.analyze_reference(image, master_face)
            body = analysis.main_body()
            self._master_poses[ref.sha256] = body.keypoints if body else None
        return self._master_poses[ref.sha256]

    def _initial_state(self, job: dict[str, Any]) -> AttemptState:
        seed = job["parameters"].get("seed")
        seed = seed if seed is not None else uuid.uuid4().int % SEED_SPACE
        return AttemptState(attempt=0, scene_seed=seed, face_seed=(seed + 1) % SEED_SPACE)

    async def _loop(self, job, ctx: JobContext, state: AttemptState, mode: str, start: int = 1) -> None:
        for attempt in range(start, job["max_attempts"] + 1):
            state.attempt = job["attempt"] = attempt
            if attempt > 1:
                log_event("regeneration_started", job_id=job["id"], attempt=attempt, strategy=state.strategy,
                          directives=state.directives)
            if state.base is None:
                await self._scene_step(job, ctx, state)
            await self._face_step(job, ctx, state)
            if await self._finish_attempt(job, ctx, state, mode):
                return
            if attempt < job["max_attempts"]:
                self._apply_retry(job, state)
        self._fail(job)

    async def _scene_step(self, job, ctx: JobContext, state: AttemptState) -> None:
        state.scene_stage = state.face_stage = None
        state.error = None
        prompt = self.prompts.build(ctx.sheet, ctx.profile, ctx.scene_text, job["style_overrides"],
                                    state.directives, single_subject=job.get("single_subject", True))
        job["prompt_version"], job["negative_version"] = prompt.version, prompt.negative.version
        state.prompt = prompt
        pose = None
        if job["mode"] == MODE_POSE and ctx.provider.pose:
            strength = float(ctx.sheet.generation["pose_control"]["strength"])
            pose = await ctx.provider.pose.prepare(job["pose_reference"], strength)
        params = GenerationParameters(**{**job["parameters"], "seed": state.scene_seed})
        log_event("scene_started", job_id=job["id"], attempt=state.attempt, seed=state.scene_seed)
        try:
            state.base = state.scene_stage = await ctx.provider.scene.generate(
                SceneRequest(prompt=prompt, parameters=params, lora=ctx.lora, pose=pose))
        except ProviderError as exc:
            state.base, state.error = None, f"cena: {exc}"
            log_event("generation_error", job_id=job["id"], attempt=state.attempt, stage="scene", error=str(exc))
            return
        log_event("scene_completed", job_id=job["id"], attempt=state.attempt, seconds=state.base.seconds,
                  model_switch=state.base.model_switch)

    async def _face_step(self, job, ctx: JobContext, state: AttemptState) -> None:
        state.face_stage = None
        if state.base is None:
            return
        log_event("face_lock_started", job_id=job["id"], attempt=state.attempt, seed=state.face_seed)
        try:
            state.face_stage = await ctx.provider.face.lock_face(state.base.image, ctx.master_face, state.face_seed)
        except ProviderError as exc:
            state.error = f"face lock: {exc}"
            log_event("generation_error", job_id=job["id"], attempt=state.attempt, stage="face_lock", error=str(exc))
            return
        log_event("face_lock_completed", job_id=job["id"], attempt=state.attempt, seconds=state.face_stage.seconds,
                  model_switch=state.face_stage.model_switch)

    async def _finish_attempt(self, job, ctx: JobContext, state: AttemptState, mode: str) -> bool:
        """Valida, grava a tentativa e devolve True se aceitou."""
        report: ValidationReport | None = None
        validation_seconds = 0.0
        if state.face_stage is not None:
            log_event("validation_started", job_id=job["id"], attempt=state.attempt)
            start = time.monotonic()
            analysis = await self.analyzer.analyze(state.face_stage.image, ctx.master_face)
            report = await self.validation.run(ValidationContext(
                sheet=ctx.sheet, image=state.face_stage.image, analysis=analysis,
                requested_pose=ctx.requested_pose, master_body_pose=ctx.master_body_pose,
                threshold_override=job["threshold"] if job.get("threshold_source") == "pedido" else None,
            ), seconds=time.monotonic() - start)
            validation_seconds = report.seconds
            log_event("validation_completed", job_id=job["id"], attempt=state.attempt, status=report.status,
                      face=report.face_score, failures=report.failures)
        stages = [s.to_dict() for s in (state.scene_stage, state.face_stage) if s is not None]
        prompt = state.prompt
        result = {
            "id": new_id(), "job_id": job["id"], "attempt": state.attempt, "strategy": state.strategy,
            "image_url": state.face_stage.image.url if state.face_stage else None,
            "image_locator": state.face_stage.image.locator if state.face_stage else None,
            "base_image_url": state.base.image.url if state.base else None,
            "status": "ERROR" if report is None else ("ACCEPT" if report.accepted else "REJECT"),
            "face_score": report.face_score if report else None,
            "validation": report.to_dict() if report else None,
            "seeds": {"scene": state.scene_seed, "face_lock": state.face_seed},
            "prompt": prompt.to_dict() if prompt else None,
            "stages": stages,
            "metrics": await self.cost.metrics(stages, validation_seconds, mode),
            "error": state.error,
            "created_at": utcnow(),
        }
        job["results"].append(result)
        failures = report.failures if report else ["provider_error"]
        for f in failures:
            check = (report.checks.get(_check_of(f)) if report else None)
            job["failures"].append({"id": new_id(), "result_id": result["id"], "failure_type": f,
                                    "reason": check.reason if check else state.error, "created_at": utcnow()})
        state.last_failures = failures
        if report is not None and report.accepted:
            job["status"], job["accepted_result_id"], job["best_result_id"] = ACCEPTED, result["id"], result["id"]
            log_event("generation_accepted", job_id=job["id"], attempt=state.attempt, status=report.status,
                      face=report.face_score, unverified=report.unverified)
            self.history.save(job)
            return True
        log_event("generation_rejected", job_id=job["id"], attempt=state.attempt, failures=failures)
        self.history.save(job)
        return False

    def _apply_retry(self, job, state: AttemptState) -> None:
        failures = state.last_failures or ["provider_error"]
        decision = self.retry.decide(state.attempt, failures, state.scene_seed, state.face_seed,
                                     state.relocks, state.directives)
        job["retries"].append({**decision.to_dict(), "created_at": utcnow()})
        state.strategy = decision.strategy
        state.face_seed, state.directives = decision.face_seed, decision.directives
        if decision.strategy == RELOCK_FACE and state.base is not None:
            state.relocks += 1
            state.scene_stage = None  # a cena nao foi refeita nesta tentativa
        else:
            state.base, state.relocks, state.scene_seed = None, 0, decision.scene_seed

    def _fail(self, job) -> None:
        scored = [r for r in job["results"] if r.get("face_score") is not None]
        job["best_result_id"] = max(scored, key=lambda r: r["face_score"])["id"] if scored else None
        job["status"] = FAILED
        log_event("generation_failed", job_id=job["id"], attempts=job["attempt"])

    def _error(self, job, message: str) -> None:
        job["status"], job["error"] = ERROR, message
        log_event("generation_error", job_id=job["id"], error=message)


def _check_of(failure_type: str) -> str:
    return {
        "face_identity_low": "face_identity", "face_not_found": "face_identity",
        "persona_duplicated": "subject_count", "pose_mismatch": "pose", "anatomy": "anatomy",
        "body_mismatch": "body_consistency", "trigger_leak": "trigger_leak",
    }.get(failure_type, failure_type)


__all__ = ["GenerationJobError", "GenerationOrchestrator"]
