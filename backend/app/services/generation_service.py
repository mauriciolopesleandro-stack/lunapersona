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
from app.clients.llm_client import OllamaClient
from app.model_manager.manager import ModelManager
from app.persona_manager.manager import PersonaManager
from app.services.prompt_translator import to_english
from app.services.reference_caption import clean_reference_caption
from app.workflow_manager.manager import WorkflowManager, WorkflowParamError


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
    # Foto enviada pela pessoa (ja no input/ do ComfyUI): a cena e mantida e a
    # pessoa vira a persona. denoise = quanto a foto e redesenhada.
    reference_image: str | None = None
    denoise: float | None = None


# Vai no fim do prompt da persona. Sem palavras de enquadramento (close,
# poros visiveis): o corpo inteiro precisa continuar possivel.
REALISM_SUFFIX = "raw candid smartphone photo, natural skin texture, subtle skin imperfections, natural light, sharp focus"
# Segunda passada (workflows com LoRA): amplia a imagem e redesenha os detalhes.
HIRES_SCALE = 1.5
HIRES_MAX_PIXELS = 2_400_000

IMG2IMG_WORKFLOW = "chroma-img2img"
IMG2IMG_LORA_WORKFLOW = "chroma-img2img-lora"
DESCRIBE_WORKFLOW = "describe-image"


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
        llm_client: OllamaClient | None = None,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.workflow_manager = workflow_manager
        self.model_manager = model_manager
        self.persona_manager = persona_manager
        # Traduz o texto digitado (portugues) para ingles antes de gerar.
        self.llm_client = llm_client

    @staticmethod
    def _hires_size(width: int, height: int) -> tuple[int, int]:
        scale = min(HIRES_SCALE, (HIRES_MAX_PIXELS / (width * height)) ** 0.5)
        return round(width * scale / 16) * 16, round(height * scale / 16) * 16

    async def _lora_available(self, filename: str) -> bool:
        # Se o arquivo ainda nao chegou ao pod, a persona volta para o texto
        # de identidade em vez de a geracao falhar no ComfyUI.
        try:
            return filename in await self.comfyui_client.list_loras()
        except ComfyUIError:
            return False

    async def _describe_reference(self, image_name: str) -> str:
        """Descricao da foto de referencia (Florence-2). Sem o custom node no
        pod (ex.: o outro volume), a geracao segue so com o texto digitado."""
        try:
            graph = self.workflow_manager.render(DESCRIBE_WORKFLOW, {"IMAGE": image_name})
            entry = await self.comfyui_client.wait_for_completion(await self.comfyui_client.queue_prompt(graph))
        except ComfyUIError:
            return ""
        for output in entry.get("outputs", {}).values():
            text = output.get("text")
            if text:
                return str(text[0]).strip()
        return ""

    async def generate(self, req: GenerationRequest) -> GenerationResponse:
        model = self.model_manager.get_model(req.model_id)

        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)

        user_prompt = await to_english(self.llm_client, req.prompt)
        prompt = user_prompt
        workflow_id = req.workflow_id
        reference_image_name: str | None = None
        lora_params: dict[str, Any] = {}
        use_lora = False
        if req.persona_id:
            persona = self.persona_manager.get_persona(req.persona_id)
            lora = persona.lora
            if lora and lora.workflow_id in model.compatible_workflows and await self._lora_available(lora.file):
                # A LoRA ja carrega rosto, corpo e acessorios: o texto longo de
                # identidade so competiria com ela (e vira retrato/colagem).
                use_lora = True
                traits = persona.reference_prompt_fragment() if req.reference_image else persona.body_prompt_fragment()
                prompt = f"photo of {lora.trigger}, {traits + ', ' if traits else ''}{user_prompt}"
                workflow_id = lora.workflow_id
                lora_params = {"LORA_NAME": lora.file, "LORA_STRENGTH": lora.strength}
                # O site sempre manda o guidance das Configuracoes (4.0); o da
                # persona vence porque foi calibrado para a LoRA dela.
                if lora.guidance is not None:
                    lora_params["GUIDANCE"] = lora.guidance
            else:
                identity_fragment = persona.identity_prompt_fragment()
                if identity_fragment:
                    prompt = f"{identity_fragment}, {user_prompt}"

                # Se a persona tem uma foto de referencia, ancora a identidade
                # nela via FLUX Kontext (flux-kontext-reference) em vez de so
                # texto - forca esse workflow independente do que foi pedido,
                # pra toda geracao com essa persona ficar visualmente consistente.
                reference = self.persona_manager.get_primary_reference_bytes(req.persona_id)
                if not req.reference_image and reference and "flux-kontext-reference" in model.compatible_workflows:
                    filename, content = reference
                    reference_image_name = await self.comfyui_client.upload_image(filename, content)
                    workflow_id = "flux-kontext-reference"

        if req.reference_image:
            workflow_id = IMG2IMG_LORA_WORKFLOW if use_lora else IMG2IMG_WORKFLOW
            if workflow_id not in model.compatible_workflows:
                raise WorkflowParamError(f"O modelo '{req.model_id}' nao aceita imagem de referencia.")
            reference_image_name = req.reference_image
            # Com o "Quanto mudar" alto, so o que esta escrito sobrevive da
            # foto: descreve-la no prompt mantem roupa, pose e cenario. Com
            # persona, os tracos da pessoa original saem da descricao.
            description = await self._describe_reference(req.reference_image)
            if description and req.persona_id:
                description = clean_reference_caption(description)
            if description:
                prompt = f"{prompt}, {description}"

        if use_lora:
            prompt = f"{prompt}, {REALISM_SUFFIX}"

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
        if req.denoise is not None:
            params["DENOISE"] = req.denoise
        if use_lora:
            params["HIRES_WIDTH"], params["HIRES_HEIGHT"] = self._hires_size(params["WIDTH"], params["HEIGHT"])
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
