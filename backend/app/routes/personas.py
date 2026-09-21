from dataclasses import asdict

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.persona_manager.manager import (
    InvalidReferenceFileError,
    PersonaNotFoundError,
    ReferenceNotFoundError,
)

router = APIRouter()


class IdentityUpdateBody(BaseModel):
    fixed: dict[str, str] | None = None
    variable_defaults: dict[str, str] | None = None


class GenerationDefaultsBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: str | None = None
    workflow_id: str | None = None


def _persona_manager(request: Request):
    return request.app.state.persona_manager


@router.get("/personas")
async def list_personas(request: Request):
    manager = _persona_manager(request)
    personas = manager.list_personas()
    return {
        "personas": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "reference_count": len(p.references),
            }
            for p in personas
        ]
    }


@router.get("/personas/{persona_id}")
async def get_persona(persona_id: str, request: Request):
    manager = _persona_manager(request)
    try:
        persona = manager.get_persona(persona_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return asdict(persona)


@router.put("/personas/{persona_id}/identity")
async def update_identity(persona_id: str, body: IdentityUpdateBody, request: Request):
    manager = _persona_manager(request)
    try:
        persona = manager.update_identity(persona_id, body.fixed, body.variable_defaults)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return asdict(persona)


@router.put("/personas/{persona_id}/generation")
async def update_generation_defaults(persona_id: str, body: GenerationDefaultsBody, request: Request):
    manager = _persona_manager(request)
    try:
        persona = manager.update_generation_defaults(persona_id, body.model_id, body.workflow_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return asdict(persona)


@router.get("/personas/{persona_id}/references")
async def list_references(persona_id: str, request: Request):
    manager = _persona_manager(request)
    try:
        manager.get_persona(persona_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"references": [asdict(r) for r in manager.list_references(persona_id)]}


@router.post("/personas/{persona_id}/references")
async def upload_reference(persona_id: str, request: Request, file: UploadFile = File(...)):
    manager = _persona_manager(request)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")
    try:
        reference = manager.add_reference(persona_id, file.filename or "referencia", content)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidReferenceFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asdict(reference)


@router.get("/personas/{persona_id}/references/{reference_id}/file")
async def get_reference_file(persona_id: str, reference_id: str, request: Request):
    manager = _persona_manager(request)
    try:
        path = manager.get_reference_path(persona_id, reference_id)
    except ReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo de referencia nao encontrado no disco.")
    return FileResponse(path)


@router.delete("/personas/{persona_id}/references/{reference_id}")
async def delete_reference(persona_id: str, reference_id: str, request: Request):
    manager = _persona_manager(request)
    try:
        manager.delete_reference(persona_id, reference_id)
    except ReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": reference_id}
