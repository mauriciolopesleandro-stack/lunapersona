from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.clients.comfyui_client import ComfyUIError
from app.model_manager.manager import ModelNotFoundError
from app.persona_manager.manager import PersonaNotFoundError
from app.services.generation_service import GenerationRequest
from app.workflow_manager.manager import WorkflowNotFoundError, WorkflowParamError

router = APIRouter()


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


@router.post("/generate")
async def generate(body: GenerateBody, request: Request):
    default_config = request.app.state.default_config
    generation_service = request.app.state.generation_service

    model_id = body.model_id or default_config["default_model_id"]
    workflow_id = body.workflow_id or default_config["default_workflow_id"]

    req = GenerationRequest(
        prompt=body.prompt,
        model_id=model_id,
        workflow_id=workflow_id,
        persona_id=body.persona_id,
        width=body.width,
        height=body.height,
        steps=body.steps,
        guidance=body.guidance,
        seed=body.seed,
        sampler_name=body.sampler_name,
        scheduler=body.scheduler,
    )

    try:
        result = await generation_service.generate(req)
    except (ModelNotFoundError, WorkflowNotFoundError, PersonaNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowParamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ComfyUIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "prompt_id": result.prompt_id,
        "model_id": result.model_id,
        "workflow_id": result.workflow_id,
        "persona_id": result.persona_id,
        "duration_seconds": result.duration_seconds,
        "images": [asdict(img) for img in result.images],
    }
