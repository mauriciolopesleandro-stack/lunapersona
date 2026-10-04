"""Historia em fotos: planeja a serie de prompts a partir de uma historia
(services/story_service.py). As fotos sao geradas pela rota de geracao normal."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.jobs import JobRegistry
from app.persona_manager.manager import PersonaNotFoundError

router = APIRouter()
_jobs = JobRegistry()


class PlanBody(BaseModel):
    story: str = Field(..., min_length=20, max_length=12000)
    count: int = Field(default=8, ge=1, le=20)
    # formato das fotos (9:16, 4:5, 1:1, 16:9) - o enquadramento e escolhido para caber nele
    shape: str = "9:16"


class PhotosBody(BaseModel):
    # nomes devolvidos por /generate/reference (fotos ja no input/ do ComfyUI)
    images: list[str] = Field(..., min_length=1, max_length=20)
    shape: str = "9:16"


@router.post("/personas/{persona_id}/story/from-photos/jobs")
async def start_plan_from_photos(persona_id: str, body: PhotosBody, request: Request):
    request.app.state.idle_shutdown.touch()
    service = request.app.state.story_service
    try:
        request.app.state.persona_manager.get_persona(persona_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _jobs.start(lambda: service.plan_from_photos(persona_id, body.images, body.shape))


@router.post("/personas/{persona_id}/story/jobs")
async def start_plan(persona_id: str, body: PlanBody, request: Request):
    request.app.state.idle_shutdown.touch()
    service = request.app.state.story_service
    try:
        request.app.state.persona_manager.get_persona(persona_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _jobs.start(lambda: service.plan(persona_id, body.story, body.count, body.shape))


@router.get("/story/jobs/{job_id}")
async def get_plan(job_id: str, request: Request):
    request.app.state.idle_shutdown.touch()
    return _jobs.get(job_id)
