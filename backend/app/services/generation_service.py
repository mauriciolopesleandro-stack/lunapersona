"""Orquestra uma geracao de imagem: resolve modelo + workflow, monta o
grafo, envia ao ComfyUI, aguarda e retorna o resultado. E o unico lugar
onde ComfyUIClient, WorkflowManager e ModelManager se encontram.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError, GenerationOutputImage
from app.model_manager.manager import ModelManager
from app.persona_manager.manager import PersonaManager
from app.workflow_manager.manager import WorkflowManager


@dataclass
class GenerationRequest:
    prompt: str
    model_id: str
    workflow_id: str
    persona_id: str | None = None
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
    persona_id: str | None
    images: list[GenerationOutputImage]
    duration_seconds: float


class GenerationService:
    def __init__(
        self,
        comfyui_client: ComfyUIClient,
        workflow_manager: WorkflowManager,
        model_manager: ModelManager,
        persona_manager: PersonaManager,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.workflow_manager = workflow_manager
        self.model_manager = model_manager
        self.persona_manager = persona_manager

    async def _lora_available(self, filename: str) -> bool:
        # Se o arquivo ainda nao chegou ao pod, a persona volta para o texto
        # de identidade em vez de a geracao falhar no ComfyUI.
        try:
            return filename in await self.comfyui_client.list_loras()
        except ComfyUIError:
            return False

    async def generate(self, req: GenerationRequest) -> GenerationResponse:
        model = self.model_manager.get_model(req.model_id)

        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)

        prompt = req.prompt
        workflow_id = req.workflow_id
        reference_image_name: str | None = None
        lora_params: dict[str, Any] = {}
        if req.persona_id:
            persona = self.persona_manager.get_persona(req.persona_id)
            lora = persona.lora
            if lora and lora.workflow_id in model.compatible_workflows and await self._lora_available(lora.file):
                # A LoRA ja carrega rosto, corpo e acessorios: o texto longo de
                # identidade so competiria com ela (e vira retrato/colagem).
                prompt = f"photo of {lora.trigger}, {req.prompt}"
                workflow_id = lora.workflow_id
                lora_params = {"LORA_NAME": lora.file, "LORA_STRENGTH": lora.strength}
            else:
                identity_fragment = persona.identity_prompt_fragment()
                if identity_fragment:
                    prompt = f"{identity_fragment}, {req.prompt}"

                # Se a persona tem uma foto de referencia, ancora a identidade
                # nela via FLUX Kontext (flux-kontext-reference) em vez de so
                # texto - forca esse workflow independente do que foi pedido,
                # pra toda geracao com essa persona ficar visualmente consistente.
                reference = self.persona_manager.get_primary_reference_bytes(req.persona_id)
                if reference and "flux-kontext-reference" in model.compatible_workflows:
                    filename, content = reference
                    reference_image_name = await self.comfyui_client.upload_image(filename, content)
                    workflow_id = "flux-kontext-reference"

        params: dict[str, Any] = {
            "PROMPT": prompt,
            "WIDTH": req.width or model.defaults.get("width", 1024),
            "HEIGHT": req.height or model.defaults.get("height", 1024),
            "STEPS": req.steps or model.defaults.get("steps", 20),
            "GUIDANCE": req.guidance or model.defaults.get("guidance", 2.5),
            "SEED": seed,
            "SAMPLER_NAME": req.sampler_name or model.defaults.get("sampler_name", "euler"),
            "SCHEDULER": req.scheduler or model.defaults.get("scheduler", "simple"),
            "FILENAME_PREFIX": "luna_studio",
        }
        if reference_image_name:
            params["REFERENCE_IMAGE"] = reference_image_name
        params.update(lora_params)
        params.update(self.model_manager.loader_params(req.model_id))

        graph = self.workflow_manager.render(workflow_id, params)

        start = time.monotonic()
        prompt_id = await self.comfyui_client.queue_prompt(graph)
        history_entry = await self.comfyui_client.wait_for_completion(prompt_id)
        duration = time.monotonic() - start

        images = self.comfyui_client.extract_images(history_entry)

        return GenerationResponse(
            prompt_id=prompt_id,
            model_id=req.model_id,
            workflow_id=workflow_id,
            persona_id=req.persona_id,
            images=images,
            duration_seconds=duration,
        )
