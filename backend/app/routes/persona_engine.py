"""Rotas do Persona Engine (/api/engine/...). Nenhuma logica de identidade
aqui: so validam a entrada e chamam os servicos (core/persona,
core/generation). Todas exigem o X-Luna-Token (app/security.py).

Ficam em /api/engine para nao mudar as rotas /api/personas que as telas
atuais usam.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.core.generation.history import JobNotFoundError
from app.core.generation.request import (
    DEFAULT_MAX_ATTEMPTS,
    MAX_ATTEMPTS_LIMIT,
    GenerationRequest,
    InvalidGenerationRequestError,
)
from app.core.persona import PersonaValidationError
from app.core.persona.references import InvalidReferenceError
from app.jobs import register_tasks
from app.persona_manager.manager import InvalidReferenceFileError, PersonaNotFoundError, ReferenceNotFoundError
from app.providers.base import GenerationParameters, UnknownProviderError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/engine")


_tasks: set[asyncio.Task] = register_tasks(set())


@contextmanager
def _errors():
    try:
        yield
    except (PersonaNotFoundError, ReferenceNotFoundError, JobNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        PersonaValidationError, InvalidReferenceError, InvalidReferenceFileError,
        InvalidGenerationRequestError, UnknownProviderError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class IdentityBody(BaseModel):
    traits: dict[str, str] | None = None
    apparent_age: int | None = Field(default=None, ge=1, le=120)
    sex: Literal["", "F", "M"] | None = None
    distinctive_keywords: list[str] | None = None


class ConstraintsBody(BaseModel):
    rules: list[str] | None = None
    negative: list[str] | None = None


class ValidationBody(BaseModel):
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class PersonaPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    identity: IdentityBody | None = None
    appearance: dict[str, str] | None = None
    style: dict[str, str] | None = None
    constraints: ConstraintsBody | None = None
    validation: ValidationBody | None = None


class PersonaCreateBody(PersonaPatchBody):
    name: str = Field(..., min_length=1, max_length=80)


class ReferencePatchBody(BaseModel):
    type: Literal["PRIMARY", "FACE", "FULL_BODY", "PROFILE", "STYLE", "OTHER"] | None = None
    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    active: bool | None = None
    label: str | None = Field(default=None, max_length=200)


def _repo(request: Request):
    return request.app.state.persona_repository


def _refs(request: Request):
    return request.app.state.reference_manager


def _persona_payload(request: Request, persona_id: str) -> dict[str, Any]:
    profile = _repo(request).get(persona_id)
    references = _refs(request).list(persona_id)
    primary = next((r.id for r in references if r.is_primary), None)
    return {**profile.to_dict(), "primary_reference_id": primary, "references": [r.to_dict() for r in references]}


# --- personas ------------------------------------------------------------


@router.post("/personas", status_code=201)
async def create_persona(body: PersonaCreateBody, request: Request):
    with _errors():
        profile = _repo(request).create(body.model_dump(exclude_unset=True))
        return _persona_payload(request, profile.id)


@router.get("/personas")
async def list_personas(request: Request, include_inactive: bool = False):
    return {"personas": [p.to_dict() for p in _repo(request).list(include_inactive=include_inactive)]}


@router.get("/personas/{persona_id}")
async def get_persona(persona_id: str, request: Request):
    with _errors():
        return _persona_payload(request, persona_id)


@router.patch("/personas/{persona_id}")
async def update_persona(persona_id: str, body: PersonaPatchBody, request: Request):
    with _errors():
        _repo(request).update(persona_id, body.model_dump(exclude_unset=True))
        return _persona_payload(request, persona_id)


@router.delete("/personas/{persona_id}")
async def delete_persona(persona_id: str, request: Request):
    """Desativa (nao apaga LoRA, voz, fotos nem historico)."""
    with _errors():
        profile = _repo(request).deactivate(persona_id)
    return {"id": profile.id, "active": profile.active}


@router.get("/personas/{persona_id}/versions")
async def persona_versions(persona_id: str, request: Request):
    with _errors():
        return {"versions": _repo(request).versions(persona_id)}


# --- referencias ---------------------------------------------------------


@router.get("/personas/{persona_id}/references")
async def list_references(persona_id: str, request: Request):
    with _errors():
        _repo(request).get(persona_id)
        return {"references": [r.to_dict() for r in _refs(request).list(persona_id)]}


@router.get("/personas/{persona_id}/references/history")
async def references_history(persona_id: str, request: Request):
    with _errors():
        _repo(request).get(persona_id)
        return {"history": _refs(request).history(persona_id)}


@router.post("/personas/{persona_id}/references", status_code=201)
async def add_reference(
    persona_id: str,
    request: Request,
    file: UploadFile = File(...),
    type: str = Form("OTHER"),
    weight: float = Form(1.0),
    label: str = Form(""),
):
    content = await file.read()
    with _errors():
        _repo(request).get(persona_id)
        return _refs(request).add(persona_id, file.filename or "", content, type, weight, label).to_dict()


@router.patch("/personas/{persona_id}/references/{reference_id}")
async def update_reference(persona_id: str, reference_id: str, body: ReferencePatchBody, request: Request):
    with _errors():
        _repo(request).get(persona_id)
        return _refs(request).update(persona_id, reference_id, body.model_dump(exclude_none=True)).to_dict()


@router.put("/personas/{persona_id}/references/{reference_id}/file")
async def replace_reference(persona_id: str, reference_id: str, request: Request, file: UploadFile = File(...)):
    content = await file.read()
    with _errors():
        _repo(request).get(persona_id)
        return _refs(request).replace(persona_id, reference_id, file.filename or "", content).to_dict()


@router.delete("/personas/{persona_id}/references/{reference_id}")
async def delete_reference(persona_id: str, reference_id: str, request: Request):
    with _errors():
        _repo(request).get(persona_id)
        _refs(request).remove(persona_id, reference_id)
    return {"deleted": reference_id}


# --- geracao -------------------------------------------------------------


class GenerationBody(BaseModel):
    persona_id: str
    scene_prompt: str = Field(..., min_length=1, max_length=2000)
    provider: str | None = None
    style_overrides: dict[str, str] = Field(default_factory=dict)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    seed: int | None = Field(default=None, ge=0, lt=2**32)
    identity_strength: float = Field(default=0.5, ge=0.0, le=1.0)
    face_restore: bool = False
    validation_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=MAX_ATTEMPTS_LIMIT)


class RetryBody(BaseModel):
    max_attempts: int | None = Field(default=None, ge=1, le=MAX_ATTEMPTS_LIMIT)


def _touch(request: Request) -> None:
    # Gerar e consultar contam como uso: o pod nao se desliga no meio.
    tracker = getattr(request.app.state, "idle_shutdown", None)
    if tracker is not None:
        tracker.touch()


def _start(request: Request, job: dict[str, Any]) -> dict[str, Any]:
    orchestrator = request.app.state.generation_orchestrator
    notify = getattr(request.app.state, "engine_notify", None)

    async def work() -> None:
        try:
            done = await orchestrator.run(job["id"])
        except Exception:
            log.exception("Persona Engine: geracao %s quebrou", job["id"])
            return
        if notify is not None:
            notify(done)

    task = asyncio.create_task(work())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _summary(job)


def _summary(job: dict[str, Any]) -> dict[str, Any]:
    """O job sem as listas (para listagens)."""
    best = next((r for r in job["results"] if r["id"] == job.get("best_result_id")), None)
    return {
        **{k: v for k, v in job.items() if k not in ("results", "failures", "retries", "prompt")},
        "best_result": best,
    }


@router.get("/providers")
async def list_providers(request: Request):
    registry = request.app.state.provider_registry
    return {"providers": [registry.get(n).get_capabilities().to_dict() for n in registry.names()],
            "default": registry.default()}


@router.post("/generation", status_code=202)
async def start_generation(body: GenerationBody, request: Request):
    _touch(request)
    req = GenerationRequest(
        persona_id=body.persona_id,
        scene_prompt=body.scene_prompt,
        provider=body.provider,
        style_overrides=body.style_overrides,
        generation_parameters=GenerationParameters(
            width=body.width, height=body.height, seed=body.seed,
            identity_strength=body.identity_strength, face_restore=body.face_restore,
        ),
        validation_threshold=body.validation_threshold,
        max_attempts=body.max_attempts,
    )
    with _errors():
        job = request.app.state.generation_orchestrator.prepare(req)
    return _start(request, job)


@router.get("/generation/{job_id}")
async def get_generation(job_id: str, request: Request):
    _touch(request)
    with _errors():
        job = request.app.state.generation_history.get(job_id)
    return {**job, "best_result": _summary(job)["best_result"]}


@router.get("/generation/{job_id}/results")
async def get_generation_results(job_id: str, request: Request):
    with _errors():
        job = request.app.state.generation_history.get(job_id)
    return {"job_id": job["id"], "status": job["status"], "results": job["results"],
            "failures": job["failures"], "retries": job["retries"]}


@router.post("/generation/{job_id}/retry", status_code=202)
async def retry_generation(job_id: str, request: Request, body: RetryBody | None = None):
    _touch(request)
    with _errors():
        job = request.app.state.generation_orchestrator.prepare_retry(job_id, body.max_attempts if body else None)
    return _start(request, job)


@router.get("/personas/{persona_id}/generations")
async def persona_generations(persona_id: str, request: Request, limit: int = 30):
    with _errors():
        _repo(request).get(persona_id, include_inactive=True)
    jobs = request.app.state.generation_history.list(persona_id, max(1, min(limit, 100)))
    return {"generations": [_summary(j) for j in jobs]}
