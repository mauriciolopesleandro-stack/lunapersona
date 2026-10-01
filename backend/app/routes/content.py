from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.jobs import JobRegistry
from app.persona_manager.manager import PersonaNotFoundError
from app.services.content_service import ContentProfile

router = APIRouter()
_jobs = JobRegistry()


class ProfileBody(BaseModel):
    bio: str = ""
    personalidade: str = ""
    jeito_de_falar: str = ""
    publico: str = ""
    redes: str = ""
    nicho: str = ""
    limites: str = ""


class SendBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


def _service(request: Request):
    request.app.state.idle_shutdown.touch()
    return request.app.state.content_service


def _call(fn, *args):
    try:
        return fn(*args)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/personas/{persona_id}/content/profile")
async def get_profile(persona_id: str, request: Request):
    return {"profile": asdict(_call(_service(request).get_profile, persona_id))}


@router.put("/personas/{persona_id}/content/profile")
async def save_profile(persona_id: str, body: ProfileBody, request: Request):
    profile = _call(_service(request).save_profile, persona_id, ContentProfile(**body.model_dump()))
    return {"profile": asdict(profile)}


@router.get("/personas/{persona_id}/content/memory")
async def get_memory(persona_id: str, request: Request):
    return {"memory": _call(_service(request).get_memory, persona_id)}


@router.delete("/personas/{persona_id}/content/memory/{index}")
async def delete_memory(persona_id: str, index: int, request: Request):
    return {"memory": _call(_service(request).delete_memory, persona_id, index)}


@router.get("/personas/{persona_id}/content/conversation")
async def get_conversation(persona_id: str, request: Request):
    return {"messages": _call(_service(request).get_conversation, persona_id)}


@router.delete("/personas/{persona_id}/content/conversation")
async def clear_conversation(persona_id: str, request: Request):
    _call(_service(request).clear_conversation, persona_id)
    return {"messages": []}


@router.post("/personas/{persona_id}/content/jobs")
async def start_send(persona_id: str, body: SendBody, request: Request):
    service = _service(request)
    _call(service.get_profile, persona_id)  # 404 antes de abrir o job
    return _jobs.start(lambda: service.send(persona_id, body.text))


@router.get("/content/jobs/{job_id}")
async def get_send_job(job_id: str, request: Request):
    _service(request)
    return _jobs.get(job_id)
