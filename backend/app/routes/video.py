import asyncio
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.clients.comfyui_client import ComfyUIError
from app.services.swap_service import COMFY_ROOT, MAX_SECONDS as SWAP_MAX_SECONDS, SwapRequest
from app.services.talk_service import TalkRequest
from app.services.video_service import MAX_SECONDS, SEGMENT_SECONDS, VideoRequest

router = APIRouter()

# Mesmo esquema de /generate/jobs: um video leva minutos, bem mais que o
# limite de ~100 s do proxy da RunPod, entao o site inicia e consulta.
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
_MAX_JOBS = 20


class VideoBody(BaseModel):
    image: str = Field(..., min_length=1)
    image_type: Literal["output", "input"] = "output"
    image_subfolder: str = ""
    prompt: str = ""
    seconds: int = Field(SEGMENT_SECONDS, ge=SEGMENT_SECONDS, le=MAX_SECONDS)
    quality: Literal["480p", "720p"] = "480p"
    source_width: int | None = None
    source_height: int | None = None
    seed: int | None = None
    # Continuar um video anterior (historia em partes).
    continue_video: str = ""
    continue_last_frame: str = ""
    continue_width: int | None = None
    continue_height: int | None = None
    continue_seconds: int = 0


class TalkBody(BaseModel):
    persona_id: str
    image: str = Field(..., min_length=1)
    image_type: Literal["output", "input"] = "output"
    image_subfolder: str = ""
    text: str = Field(..., min_length=1, max_length=600)
    extra_prompt: str = ""
    quality: Literal["480p", "720p"] = "480p"
    source_width: int | None = None
    source_height: int | None = None
    seed: int | None = None


class SwapBody(BaseModel):
    video: str = Field(..., min_length=1)
    image: str = ""
    image_type: Literal["output", "input"] = "output"
    image_subfolder: str = ""
    prompt: str = ""
    quality: Literal["480p", "720p"] = "480p"
    max_seconds: int = Field(10, ge=2, le=SWAP_MAX_SECONDS)
    seed: int | None = None
    persona_id: str | None = None


_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
_MAX_VIDEO_BYTES = 500 * 1024 * 1024


async def _run_job(job_id: str, service, req) -> None:
    job = _jobs[job_id]
    try:
        result = await service.generate(req)
        job["result"] = {
            "prompt_id": result.prompt_id,
            "seconds": result.seconds,
            "width": result.width,
            "height": result.height,
            "duration_seconds": result.duration_seconds,
            "videos": [asdict(v) for v in result.videos],
            "last_frame": asdict(result.last_frame) if result.last_frame else None,
            "motion": result.motion,
            "reference": asdict(result.reference) if result.reference else None,
        }
        job["status"] = "done"
    except Exception as exc:
        job["status"] = "error"
        job["error_status"] = 502 if isinstance(exc, ComfyUIError) else 500
        job["detail"] = str(exc) or exc.__class__.__name__


@router.post("/video/jobs")
async def start_video_job(body: VideoBody, request: Request):
    request.app.state.idle_shutdown.touch()
    req = VideoRequest(**body.model_dump())
    while len(_jobs) >= _MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"status": "running"}
    task = asyncio.create_task(_run_job(job_id, request.app.state.video_service, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "status": "running"}


@router.post("/video/talk/jobs")
async def start_talk_job(body: TalkBody, request: Request):
    """Persona falando: texto -> voz dela -> video com a boca sincronizada.
    Consulta pelo mesmo /video/jobs/{job_id}."""
    request.app.state.idle_shutdown.touch()
    req = TalkRequest(**body.model_dump())
    while len(_jobs) >= _MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"status": "running"}
    task = asyncio.create_task(_run_job(job_id, request.app.state.talk_service, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "status": "running"}


@router.post("/video/swap/upload")
async def upload_swap_video(request: Request, file: UploadFile = File(...)):
    """Recebe o video da troca de personagem e grava direto no input/ do
    ComfyUI (mesma maquina) - o upload do ComfyUI recusa mais de 100 MB."""
    request.app.state.idle_shutdown.touch()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Envie um video MP4, MOV ou WEBM.")
    name = f"troca_src_{uuid.uuid4().hex}{ext}"
    dest = COMFY_ROOT / "input" / name
    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            size += len(chunk)
            if size > _MAX_VIDEO_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Video grande demais (maximo 500 MB).")
            out.write(chunk)
    return {"name": name}


@router.post("/video/swap/jobs")
async def start_swap_job(body: SwapBody, request: Request):
    """Troca a pessoa do video pela persona. Consulta em /video/jobs/{job_id}."""
    request.app.state.idle_shutdown.touch()
    req = SwapRequest(**body.model_dump())
    while len(_jobs) >= _MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"status": "running"}
    task = asyncio.create_task(_run_job(job_id, request.app.state.swap_service, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "status": "running"}


@router.get("/video/jobs/{job_id}")
async def get_video_job(job_id: str, request: Request):
    # Consultar conta como uso: o pod nao desliga no meio de um video.
    request.app.state.idle_shutdown.touch()
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Video nao encontrado (o backend pode ter reiniciado).")
    if job["status"] == "error":
        return {"status": "error", "error_status": job["error_status"], "detail": job["detail"]}
    if job["status"] == "done":
        return {"status": "done", "result": job["result"]}
    return {"status": "running"}
