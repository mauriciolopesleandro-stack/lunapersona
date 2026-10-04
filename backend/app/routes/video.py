import asyncio
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app import notify
from app.clients.comfyui_client import ComfyUIError
from app.jobs import JobRegistry, register_tasks
from app.persona_manager.manager import PersonaNotFoundError
from app.services.swap_service import COMFY_ROOT, MAX_SECONDS as SWAP_MAX_SECONDS, SwapRequest
from app.services.talk_service import TalkRequest
from app.services.video_service import MAX_SECONDS, SEGMENT_SECONDS, VideoRequest
from app.workflow_manager.manager import WorkflowParamError

router = APIRouter()

# Mesmo esquema de /generate/jobs: um video leva minutos, bem mais que o
# limite de ~100 s do proxy da RunPod, entao o site inicia e consulta.
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = register_tasks(set())
_MAX_JOBS = 20
_reference_jobs = JobRegistry()


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


async def _run_job(job_id: str, service, req, label: str = "🎬 Vídeo pronto") -> None:
    job = _jobs[job_id]
    started = time.monotonic()
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
        video = result.videos[0].url if result.videos else None
        notify.fire(f"{label}: {result.seconds} s de vídeo", started, video=video)
    except Exception as exc:
        job["status"] = "error"
        job["error_status"] = 502 if isinstance(exc, ComfyUIError) else 500
        job["detail"] = str(exc) or exc.__class__.__name__
        notify.fire(f"⚠️ O vídeo falhou: {job['detail'][:300]}", started)


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
    task = asyncio.create_task(_run_job(job_id, request.app.state.talk_service, req, "🗣️ Vídeo falando pronto"))
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
    task = asyncio.create_task(_run_job(job_id, request.app.state.swap_service, req, "🎬 Troca no vídeo pronta"))
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


class KeyframesBody(BaseModel):
    video: str = Field(..., min_length=1)
    max_seconds: int = Field(SWAP_MAX_SECONDS, ge=2, le=SWAP_MAX_SECONDS)


class FramePersonaBody(BaseModel):
    persona_id: str
    frame: str = Field(..., min_length=1)
    width: int = Field(..., ge=64)
    height: int = Field(..., ge=64)
    extra: str = ""
    seed: int | None = None


@router.post("/video/swap/keyframes")
async def swap_keyframes(body: KeyframesBody, request: Request):
    """Quadros principais do video (inicio, meio, fim) - leva segundos."""
    request.app.state.idle_shutdown.touch()
    try:
        result = await request.app.state.swap_service.keyframes(body.video, body.max_seconds)
    except WorkflowParamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    comfy = request.app.state.comfyui_client
    result["frames"] = [
        {"filename": name, "subfolder": "", "type": "input", "url": comfy.build_image_url(name, "", "input")}
        for name in result["frames"]
    ]
    return result


@router.post("/video/swap/reference/jobs")
async def start_frame_persona(body: FramePersonaBody, request: Request):
    """A persona num quadro do video, para aprovar antes da troca."""
    request.app.state.idle_shutdown.touch()
    service = request.app.state.swap_service
    try:
        request.app.state.persona_manager.get_persona(body.persona_id)
    except PersonaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def work():
        started = time.monotonic()
        image, check = await service.persona_in_frame(
            body.persona_id, body.frame, body.width, body.height, body.extra, body.seed
        )
        notify.fire("🖼️ Persona no quadro do vídeo pronta", started, photo=notify.photo_link(image.url))
        return {"image": asdict(image), "check": check}

    return _reference_jobs.start(work)


@router.get("/video/swap/reference/jobs/{job_id}")
async def get_frame_persona(job_id: str, request: Request):
    request.app.state.idle_shutdown.touch()
    return _reference_jobs.get(job_id)
