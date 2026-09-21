"""Orquestra uma geracao de imagem: resolve modelo + workflow, monta o
grafo, envia ao ComfyUI, aguarda e retorna o resultado. E o unico lugar
onde ComfyUIClient, WorkflowManager e ModelManager se encontram.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, GenerationOutputImage
from app.model_manager.manager import ModelManager
from app.workflow_manager.manager import WorkflowManager


@dataclass
class GenerationRequest:
    prompt: str
    model_id: str
    workflow_id: str
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    sampler_name: str | None = None
    scheduler: str | None = None


@dataclass
class GenerationResponse:
    prompt_id: str
    model_id: str
    workflow_id: str
    images: list[GenerationOutputImage]
    duration_seconds: float


class GenerationService:
    def __init__(
        self,
        comfyui_client: ComfyUIClient,
        workflow_manager: WorkflowManager,
        model_manager: ModelManager,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.workflow_manager = workflow_manager
        self.model_manager = model_manager

    async def generate(self, req: GenerationRequest) -> GenerationResponse:
        model = self.model_manager.get_model(req.model_id)

        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)

        params: dict[str, Any] = {
            "PROMPT": req.prompt,
            "WIDTH": req.width or model.defaults.get("width", 1024),
            "HEIGHT": req.height or model.defaults.get("height", 1024),
            "STEPS": req.steps or model.defaults.get("steps", 20),
            "GUIDANCE": req.guidance or model.defaults.get("guidance", 2.5),
            "SEED": seed,
            "SAMPLER_NAME": req.sampler_name or model.defaults.get("sampler_name", "euler"),
            "SCHEDULER": req.scheduler or model.defaults.get("scheduler", "simple"),
            "FILENAME_PREFIX": "luna_studio",
        }
        params.update(self.model_manager.loader_params(req.model_id))

        graph = self.workflow_manager.render(req.workflow_id, params)

        start = time.monotonic()
        prompt_id = await self.comfyui_client.queue_prompt(graph)
        history_entry = await self.comfyui_client.wait_for_completion(prompt_id)
        duration = time.monotonic() - start

        images = self.comfyui_client.extract_images(history_entry)

        return GenerationResponse(
            prompt_id=prompt_id,
            model_id=req.model_id,
            workflow_id=req.workflow_id,
            images=images,
            duration_seconds=duration,
        )
