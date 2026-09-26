import asyncio
import uuid
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.clients.comfyui_client import ComfyUIError
from app.model_manager.manager import ModelNotFoundError
from app.persona_manager.manager import PersonaNotFoundError
from app.services.generation_service import GenerationRequest, GenerationResponse
from app.workflow_manager.manager import WorkflowNotFoundError, WorkflowParamError

router = APIRouter()

# Geracoes em andamento/terminadas, so em memoria (um processo por pod).
# O proxy da RunPod corta respostas com mais de ~100 s, e uma geracao numa
# L4 passa disso: o site inicia o job e consulta ate terminar.
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
_MAX_JOBS = 50


class GenerateBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    prompt: str = Field(..., min_length=1)
    model_id: str | None = None
    workflow_id: str | None = None
    persona_id: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    sampler_name: str | None = None
    scheduler: str | None = None


def _build_request(body: GenerateBody, request: Request) -> GenerationRequest:
    default_config = request.app.state.default_config
    return GenerationRequest(
        prompt=body.prompt,
        model_id=body.model_id or default_config["default_model_id"],
        workflow_id=body.workflow_id or default_config["default_workflow_id"],
        persona_id=body.persona_id,
        width=body.width,
        height=body.height,
        steps=body.steps,
        guidance=body.guidance,
        seed=body.seed,
        sampler_name=body.sampler_name,
        scheduler=body.scheduler,
    )


def _error_status(exc: Exception) -> int | None:
    if isinstance(exc, (ModelNotFoundError, WorkflowNotFoundError, PersonaNotFoundError)):
        return 404
    if isinstance(exc, WorkflowParamError):
        return 400
    if isinstance(exc, ComfyUIError):
        return 502
    return None


def _payload(result: GenerationResponse) -> dict:
    return {
        "prompt_id": result.prompt_id,
        "model_id": result.model_id,
        "workflow_id": result.workflow_id,
        "persona_id": result.persona_id,
        "duration_seconds": result.duration_seconds,
        "images": [asdict(img) for img in result.images],
    }


@router.post("/generate")
async def generate(body: GenerateBody, request: Request):
    request.app.state.idle_shutdown.touch()
    try:
        result = await request.app.state.generation_service.generate(_build_request(body, request))
    except Exception as exc:
        status = _error_status(exc)
        if status is None:
            raise
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _payload(result)


async def _run_job(job_id: str, service, req: GenerationRequest) -> None:
    job = _jobs[job_id]
    try:
        job["result"] = _payload(await service.generate(req))
        job["status"] = "done"
    except Exception as exc:
        job["status"] = "error"
        job["error_status"] = _error_status(exc) or 500
        job["detail"] = str(exc) or exc.__class__.__name__


@router.post("/generate/jobs")
async def start_generate_job(body: GenerateBody, request: Request):
    request.app.state.idle_shutdown.touch()
    req = _build_request(body, request)

    while len(_jobs) >= _MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"status": "running"}
    task = asyncio.create_task(_run_job(job_id, request.app.state.generation_service, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "status": "running"}


@router.get("/generate/jobs/{job_id}")
async def get_generate_job(job_id: str, request: Request):
    # Consultar conta como uso: sem isso o pod podia se desligar no meio de
    # uma geracao longa.
    request.app.state.idle_shutdown.touch()
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Geracao nao encontrada (o backend pode ter reiniciado).")
    if job["status"] == "error":
        return {"status": "error", "error_status": job["error_status"], "detail": job["detail"]}
    if job["status"] == "done":
        return {"status": "done", "result": job["result"]}
    return {"status": "running"}
