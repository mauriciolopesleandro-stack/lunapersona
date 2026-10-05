"""Adapter do ComfyUI do estudio: Z-Image Turbo com a LoRA da persona
(workflows/zimage-txt2img-lora.json, ~18 s por foto). Sem a LoRA no pod, o
Z-Image puro com a identidade so em texto.

Traducao do pedido generico:
- identity_strength (0-1) -> forca da LoRA (0.5 = a forca salva na persona;
  1.0 = 30% acima) e peso do InstantID no retoque do rosto.
- face_restore -> passada do InstantID no rosto mais parecido com a foto
  principal (workflows/sdxl-instantid-face.json).
- O prompt sai no formato das legendas do treino da LoRA: gatilho, quem,
  reforcos, aparencia, cena, estilo. As restricoes ("nao mudar a
  identidade") nao entram: o Z-Image roda sem negativo (CFG 1) e frase com
  "nao" num modelo de difusao so atrapalha.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError, GenerationOutputImage
from app.providers.base import (
    GenerationParameters,
    ModelAdapter,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderError,
    ProviderImage,
    ProviderInput,
    ProviderOutput,
)
from app.services.person_swap import luna_faces
from app.workflow_manager.manager import WorkflowManager, WorkflowNotFoundError, WorkflowParamError

ZIMAGE_WORKFLOW = "zimage-txt2img"
ZIMAGE_LORA_WORKFLOW = "zimage-txt2img-lora"
FACE_WORKFLOW = "sdxl-instantid-face"
ZIMAGE_UNET = "z_image_turbo_int8_convrot.safetensors"
SAMPLING = {"STEPS": 8, "CFG": 1.0, "SAMPLER": "res_multistep", "SCHEDULER": "simple"}
FACE_PAD = 0.15
WHO = {"F": "a woman", "M": "a man"}


def output_name(image: GenerationOutputImage) -> str:
    name = f"{image.subfolder}/{image.filename}" if image.subfolder else image.filename
    return f"{name} [output]"


def lora_strength(base: float, identity_strength: float) -> float:
    return round(base * (0.7 + 0.6 * identity_strength), 3)


def build_prompt(request: ProviderInput, trigger: str | None) -> str:
    p = request.prompt
    who = WHO.get(request.sex, "a person")
    # Com a LoRA, o rosto vem dela: o texto longo de rosto competia com ela
    # (virava retrato/colagem). Sem LoRA, a identidade so existe no texto.
    lead = [f"{trigger}, {who}"] if trigger else [who, p.identity_traits]
    parts = [*lead, *p.emphasis, p.appearance, p.scene, p.style]
    return ", ".join(s.strip() for s in parts if s and s.strip())


class ComfyUIAdapter(ModelAdapter):
    name = "comfyui"

    def __init__(self, comfyui_client: ComfyUIClient, workflow_manager: WorkflowManager) -> None:
        self.client = comfyui_client
        self.workflows = workflow_manager

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            title="ComfyUI - Z-Image Turbo + LoRA da persona",
            identity_mechanisms=["lora", "text", "face_restore"],
            supports_face_restore=True,
            supports_negative_prompt=False,
            default_size=(832, 1216),
            max_pixels=1_300_000,
        )

    async def validate_configuration(self) -> list[str]:
        problems = []
        status = await self.client.check_connection()
        if not status.ok:
            return [f"ComfyUI fora do ar: {status.message}"]
        try:
            if ZIMAGE_UNET not in await self.client.list_diffusion_models():
                problems.append(f"Modelo {ZIMAGE_UNET} nao esta no pod.")
        except ComfyUIError as exc:
            problems.append(f"Nao consegui listar os modelos do ComfyUI: {exc}")
        for workflow_id in (ZIMAGE_WORKFLOW, ZIMAGE_LORA_WORKFLOW):
            try:
                self.workflows.get_workflow(workflow_id)
            except WorkflowNotFoundError:
                problems.append(f"Workflow {workflow_id} nao encontrado.")
        return problems

    async def _lora_ready(self, filename: str) -> bool:
        try:
            return filename in await self.client.list_loras()
        except ComfyUIError:
            return False

    async def generate(self, request: ProviderInput) -> ProviderOutput:
        params = self.normalize_parameters(request.parameters)
        seed = params.seed if params.seed is not None else uuid.uuid4().int % (2**32)
        lora = request.identity_assets.get("lora") or {}
        lora_file, trigger = lora.get("zimage_file", ""), lora.get("trigger", "")
        use_lora = bool(lora_file and trigger) and await self._lora_ready(lora_file)
        values = {
            "PROMPT": build_prompt(request, trigger if use_lora else None),
            "WIDTH": params.width, "HEIGHT": params.height, "SEED": seed,
            "FILENAME_PREFIX": "luna_engine", **SAMPLING,
        }
        if params.steps:
            values["STEPS"] = params.steps
        workflow_id = ZIMAGE_WORKFLOW
        if use_lora:
            workflow_id = ZIMAGE_LORA_WORKFLOW
            values["LORA_NAME"] = lora_file
            values["LORA_STRENGTH"] = lora_strength(float(lora.get("zimage_strength", 1.0)), params.identity_strength)
        start = time.monotonic()
        try:
            graph = self.workflows.render(workflow_id, values)
            prompt_id = await self.client.queue_prompt(graph)
            images = self.client.extract_images(await self.client.wait_for_completion(prompt_id))
        except (WorkflowNotFoundError, WorkflowParamError) as exc:
            raise ProviderConfigurationError(str(exc)) from exc
        except ComfyUIError as exc:
            raise ProviderError(str(exc)) from exc
        if not images:
            raise ProviderError("O ComfyUI terminou sem devolver imagem.")
        effective = {"workflow_id": workflow_id, "seed": seed, "prompt": values["PROMPT"],
                     "lora_strength": values.get("LORA_STRENGTH"), "face_restore": "off"}
        image = images[0]
        if params.face_restore and request.references:
            restored = await self._restore_face(image, request, params, seed)
            effective["face_restore"] = "applied" if restored else "skipped"
            image = restored or image
        return ProviderOutput(
            images=[ProviderImage(self.name, output_name(image), image.url, params.width, params.height)],
            provider_job_id=prompt_id,
            duration_seconds=time.monotonic() - start,
            effective_parameters=effective,
        )

    async def _restore_face(
        self, image: GenerationOutputImage, request: ProviderInput, params: GenerationParameters, seed: int
    ) -> GenerationOutputImage | None:
        """InstantID no rosto mais parecido com a foto principal. None se o
        pod nao tiver os modelos/nos ou nao achar rosto (fica a imagem)."""
        ref = request.references[0]
        try:
            persona_image = await self.client.upload_image(
                f"engine_{ref.reference_id}{Path(ref.filename).suffix}", ref.content
            )
            name = output_name(image)
            faces = await luna_faces(
                self.client,
                {"1": {"class_type": "LoadImage", "inputs": {"image": name}},
                 "2": {"class_type": "LoadImage", "inputs": {"image": persona_image}}},
                ["1", 0], ["2", 0],
            )
            if not faces:
                return None
            x1, y1, x2, y2 = max(faces, key=lambda f: float(f.get("sim", 0.0)))["bbox"]
            fw, fh = x2 - x1, y2 - y1
            x, y = max(0, int(x1 - fw * FACE_PAD)), max(0, int(y1 - fh * FACE_PAD))
            w = max(16, min(params.width, int(x2 + fw * FACE_PAD)) - x)
            h = max(16, min(params.height, int(y2 + fh * FACE_PAD)) - y)
            graph = self.workflows.render(FACE_WORKFLOW, {
                "IMAGE": name, "PERSONA_IMAGE": persona_image, "ORIGINAL": name,
                "FACE_X": x, "FACE_Y": y, "FACE_W": w, "FACE_H": h,
                "WIDTH": params.width, "HEIGHT": params.height, "SEED": seed,
                "FACE_PROMPT": ", ".join(p for p in (f"photo of {WHO.get(request.sex, 'a person')}",
                                                     request.prompt.identity_traits, "natural skin texture") if p),
                "ID_WEIGHT": round(0.6 + 0.4 * params.identity_strength, 3),
                "FILENAME_PREFIX": "luna_engine",
            })
            out = self.client.extract_images(await self.client.wait_for_completion(await self.client.queue_prompt(graph)))
        except (ComfyUIError, WorkflowNotFoundError, WorkflowParamError):
            return None
        return out[0] if out else None
