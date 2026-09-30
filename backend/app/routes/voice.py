from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.jobs import JobRegistry
from app.persona_manager.manager import PersonaNotFoundError

router = APIRouter()
_jobs = JobRegistry()


class DesignBody(BaseModel):
    description: str = Field(..., min_length=3)
    text: str = ""


class SpeakBody(BaseModel):
    persona_id: str
    text: str = Field(..., min_length=1, max_length=2000)


class SaveVoiceBody(BaseModel):
    filename: str
    subfolder: str = ""
    text: str
    description: str = ""


def _service(request: Request):
    request.app.state.idle_shutdown.touch()
    return request.app.state.voice_service


@router.post("/voice/design/jobs")
async def start_design(body: DesignBody, request: Request):
    service = _service(request)
    return _jobs.start(lambda: service.design(body.description, body.text))


@router.post("/voice/speak/jobs")
async def start_speak(body: SpeakBody, request: Request):
    service = _service(request)
    return _jobs.start(lambda: service.speak(body.persona_id, body.text))


@router.get("/voice/jobs/{job_id}")
async def get_voice_job(job_id: str, request: Request):
    _service(request)
    return _jobs.get(job_id)


@router.get("/personas/{persona_id}/voice")
async def get_persona_voice(persona_id: str, request: Request):
    try:
        persona = request.app.state.persona_manager.get_persona(persona_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"voice": asdict(persona.voice) if persona.voice else None}


@router.get("/personas/{persona_id}/voice/file")
async def get_persona_voice_file(persona_id: str, request: Request):
    try:
        path = request.app.state.persona_manager.get_voice_path(persona_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path:
        raise HTTPException(status_code=404, detail="Essa persona ainda nao tem voz.")
    return FileResponse(path)


@router.put("/personas/{persona_id}/voice")
async def save_persona_voice(persona_id: str, body: SaveVoiceBody, request: Request):
    service = _service(request)
    try:
        voice = await service.save_voice(persona_id, body.filename, body.subfolder, body.text, body.description)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"voice": voice}
