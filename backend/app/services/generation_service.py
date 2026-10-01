"""Orquestra uma geracao de imagem: resolve modelo + workflow, monta o
grafo, envia ao ComfyUI, aguarda e retorna o resultado. E o unico lugar
onde ComfyUIClient, WorkflowManager e ModelManager se encontram.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError, GenerationOutputImage
from app.clients.llm_client import OllamaClient
from app.model_manager.manager import ModelManager
from app.persona_manager.manager import PersonaManager
from app.services.person_swap import (
    PERSONA_HEAD_FRACTION,
    PersonSwapPlan,
    image_size,
    plan_person_swap,
    qwen_available,
    qwen_prompt,
    ring_params,
)
from app.services.prompt_translator import to_english
from app.services.reference_caption import clean_reference_caption
from app.services.scene_describer import describe_image
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
    # Pack com a persona: com reference_image, troca SO a pessoa da foto
    # (contorno recortado) e o resto volta identico. Sem isso, img2img da foto
    # inteira (o cenario tambem muda um pouco).
    person_swap: bool = False


# Vai no fim do prompt da persona. Sem palavras de enquadramento (close,
# poros visiveis): o corpo inteiro precisa continuar possivel.
REALISM_SUFFIX = (
    "candid amateur smartphone photo, unposed, real everyday place, background in focus, "
    "natural skin texture, subtle skin imperfections, natural light"
)
# Segunda passada (workflows com LoRA): amplia a imagem e redesenha os detalhes.
HIRES_SCALE = 1.5
HIRES_MAX_PIXELS = 2_400_000

# Se o pedido ja fala de expressao/olhar, a atitude padrao da persona nao entra.
_EXPRESSION_WORDS = re.compile(
    r"\b(?:smil\w*|laugh\w*|grin\w*|expression|surpris\w*|shock\w*|mouth|wink\w*|pout\w*|serious|sad|angry|"
    r"cry\w*|tongue|scream\w*|gaze|frown\w*|kiss\w*|looking)\b",
    re.I,
)
# Frases da descricao sobre outra pessoa (no pack so a mulher e redesenhada).
# ...mas frase que tambem fala dela fica (sem isso sumia a descricao dela e o
# modelo pintava fundo no lugar da cabeca).
_HER = re.compile(r"\b(?:she|her|woman|women|girl|lady|wife|girlfriend)\b", re.I)
_OTHER_PERSON = re.compile(r"\b(?:man|men|he|his|him|husband|boyfriend|guy|male|beard)\b", re.I)

# Correcao de rosto depois da geracao com LoRA (workflows/chroma-face-refine.json).
FACE_REFINE_WORKFLOW = "chroma-face-refine"

PERSON_SWAP_WORKFLOW = "chroma-person-swap-lora"
QWEN_SWAP_WORKFLOW = "qwen-person-swap"

IMG2IMG_WORKFLOW = "chroma-img2img"
IMG2IMG_LORA_WORKFLOW = "chroma-img2img-lora"


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
        return await describe_image(self.comfyui_client, self.workflow_manager, image_name)

    async def _refine_faces(
        self, images: list[GenerationOutputImage], params: dict[str, Any]
    ) -> list[GenerationOutputImage]:
        """Redesenha o rosto em alta resolucao com a LoRA e cola de volta. Sem
        rosto na imagem (ou sem os nos no pod), fica a imagem original."""
        refined: list[GenerationOutputImage] = []
        for image in images:
            name = f"{image.subfolder}/{image.filename}" if image.subfolder else image.filename
            try:
                graph = self.workflow_manager.render(FACE_REFINE_WORKFLOW, {**params, "IMAGE": f"{name} [output]"})
                prompt_id = await self.comfyui_client.queue_prompt(graph)
                entry = await self.comfyui_client.wait_for_completion(prompt_id)
                refined.extend(self.comfyui_client.extract_images(entry) or [image])
            except (ComfyUIError, WorkflowParamError):
                refined.append(image)
        return refined

    async def generate(self, req: GenerationRequest) -> GenerationResponse:
        model = self.model_manager.get_model(req.model_id)

        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)

        user_prompt = await to_english(self.llm_client, req.prompt)
        prompt = user_prompt
        workflow_id = req.workflow_id
        reference_image_name: str | None = None
        lora_params: dict[str, Any] = {}
        use_lora = False
        attitude = ""
        face_params: dict[str, Any] = {}
        swap_plan: PersonSwapPlan | None = None
        if req.persona_id:
            persona = self.persona_manager.get_persona(req.persona_id)
            lora = persona.lora
            if lora and lora.workflow_id in model.compatible_workflows and await self._lora_available(lora.file):
                # A LoRA ja carrega rosto, corpo e acessorios: o texto longo de
                # identidade so competiria com ela (e vira retrato/colagem).
                use_lora = True
                traits = persona.reference_prompt_fragment() if req.reference_image else persona.body_prompt_fragment()
                if req.person_swap:
                    # Cabelo da pessoa da foto (curto, cacheado) vencia: o
                    # formato do cabelo da persona entra junto.
                    hair = persona.identity.fixed.get("formato_cabelo", "").strip()
                    traits = ", ".join(p for p in (hair, traits) if p)
                # No pack a expressao e a da foto (a historia continua coerente).
                if not req.person_swap and not _EXPRESSION_WORDS.search(user_prompt):
                    attitude = persona.attitude_prompt_fragment()
                lead = ", ".join(p for p in (attitude, traits) if p)
                who = "a young woman, " if req.person_swap else ""
                prompt = f"photo of {lora.trigger}, {who}{lead + ', ' if lead else ''}{user_prompt}"
                face_params = {
                    "FACE_PROMPT": ", ".join(
                        p for p in (f"close-up portrait photo of {lora.trigger}", attitude, REALISM_SUFFIX) if p
                    ),
                    "LORA_NAME": lora.file,
                }
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
            if req.person_swap:
                if not use_lora:
                    raise WorkflowParamError("Trocar a pessoa da foto precisa de uma persona com LoRA.")
                workflow_id = PERSON_SWAP_WORKFLOW
            if workflow_id not in model.compatible_workflows:
                raise WorkflowParamError(f"O modelo '{req.model_id}' nao aceita imagem de referencia.")
            reference_image_name = req.reference_image
            if req.person_swap:
                # Onde ela e o rosto dela estao e a descricao SO dela (recorte):
                # a da foto inteira falava do homem e ele aparecia no lugar dela.
                swap_plan = await plan_person_swap(
                    self.comfyui_client,
                    req.reference_image,
                    req.width or model.defaults.get("width", 1024),
                    req.height or model.defaults.get("height", 1024),
                )
                description = swap_plan.caption
            else:
                # Com o "Quanto mudar" alto, so o que esta escrito sobrevive
                # da foto: descreve-la no prompt mantem roupa, pose e cenario.
                description = await self._describe_reference(req.reference_image)
            # Com persona, os tracos da pessoa original saem da descricao.
            if description and req.persona_id:
                description = clean_reference_caption(description, keep_expression=not attitude)
            if description and req.person_swap:
                # O recorte as vezes pega um pedaco do outro: frase so sobre
                # ele sai (a que fala dela tambem fica).
                description = " ".join(
                    s
                    for s in re.split(r"(?<=[.!?])\s+", description)
                    if not _OTHER_PERSON.search(s) or _HER.search(s)
                )
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
        if swap_plan is not None:
            params["POINTS_POS"], params["POINTS_NEG"] = swap_plan.points_pos, swap_plan.points_neg
            params["HEAD_X"], params["HEAD_Y"], params["HEAD_W"], params["HEAD_H"] = swap_plan.head
            # Qwen-Image-Edit instalado: ele faz a troca (cena + foto da
            # persona + instrucao) - o Chroma redesenhando a regiao dela
            # perdia abraco, rosto de perfil e gestos. Sem ele, fica o Chroma.
            persona_ref = self.persona_manager.get_primary_reference_bytes(req.persona_id) if req.persona_id else None
            size = image_size(persona_ref[1]) if persona_ref else None
            if persona_ref and size and await qwen_available(self.comfyui_client):
                workflow_id = QWEN_SWAP_WORKFLOW
                params["PERSONA_IMAGE"] = await self.comfyui_client.upload_image(*persona_ref)
                params["PERSONA_W"], params["PERSONA_H"] = size[0], int(size[1] * PERSONA_HEAD_FRACTION)
                params["PROMPT"] = qwen_prompt(swap_plan, params["WIDTH"])
                params.update(ring_params(swap_plan, params["WIDTH"], params["HEIGHT"]))
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

        images = self.comfyui_client.extract_images(history_entry)
        if face_params and images and FACE_REFINE_WORKFLOW in model.compatible_workflows:
            images = await self._refine_faces(images, {**params, **face_params})
        duration = time.monotonic() - start

        return GenerationResponse(
            prompt_id=prompt_id,
            model_id=req.model_id,
            workflow_id=workflow_id,
            persona_id=req.persona_id,
            images=images,
            duration_seconds=duration,
        )
