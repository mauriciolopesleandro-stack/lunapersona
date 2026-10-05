"""Rotas do Persona Engine (/api/engine/...). Nenhuma logica de identidade
aqui: so validam a entrada e chamam os servicos (core/persona,
core/generation). Todas exigem o X-Luna-Token (app/security.py).

Ficam em /api/engine para nao mudar as rotas /api/personas que as telas
atuais usam.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.core.persona import PersonaValidationError
from app.core.persona.references import InvalidReferenceError
from app.persona_manager.manager import InvalidReferenceFileError, PersonaNotFoundError, ReferenceNotFoundError

router = APIRouter(prefix="/engine")


@contextmanager
def _errors():
    try:
        yield
    except (PersonaNotFoundError, ReferenceNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PersonaValidationError, InvalidReferenceError, InvalidReferenceFileError) as exc:
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
