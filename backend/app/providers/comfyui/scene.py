"""ZImageAdapter: etapa de cena do Persona Engine V1 (workflows/zimage-txt2img-lora.json).

Z-Image Turbo int8 + LoRA da persona, 8 passos, CFG 1, res_multistep/simple,
shift 3 - os parametros do benchmark. Com pose pedida, o mesmo grafo ganha
DWPose + ControlNet Union (forca da ficha, 0.8 no benchmark C2).

- Sem a LoRA no pod a etapa FALHA (ProviderConfigurationError). Nao existe
  fallback para Z-Image puro nem para o Chroma: mudaria a persona e as metricas.
- O negativo NAO e aplicado: o Z-Image Turbo roda com CFG 1 e o grafo zera o
  condicionamento negativo (ConditioningZeroOut). O adapter declara isso
  (supports_negative_prompt=False) e o nucleo usa o negativo so como regra
  de validacao.
"""
from __future__ import annotations

from typing import Any

from app.clients.comfyui_client import ComfyUIError
from app.providers.base import (
    AdapterCapabilities,
    ProviderConfigurationError,
    ProviderImage,
    SceneAdapter,
    SceneRequest,
    StageOutput,
)
from app.providers.comfyui.session import ComfySession, new_seed, output_name
from app.workflow_manager.manager import WorkflowNotFoundError

WORKFLOW = "zimage-txt2img-lora"
UNET = "z_image_turbo_int8_convrot.safetensors"
TEXT_ENCODER = "qwen_3_4b_fp4_mixed.safetensors"
VAE = "ae.safetensors"
CONTROL_PATCH = "Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors"
SAMPLING = {"STEPS": 8, "CFG": 1.0, "SHIFT": 3, "SAMPLER": "res_multistep", "SCHEDULER": "simple"}
DWPOSE = {"detect_hand": "enable", "detect_body": "enable", "detect_face": "enable", "resolution": 1024,
          "bbox_detector": "yolox_l.onnx", "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
          "scale_stick_for_xinsr_cn": "disable"}


def build_prompt(request: SceneRequest) -> str:
    """Gatilho + quem primeiro (formato das legendas do treino), depois roupa,
    cena, estilo e as diretivas. O gatilho so existe aqui, nunca vindo do usuario."""
    p = request.prompt
    parts = [p.subject, p.appearance, p.scene, p.style, *p.directives]
    return ", ".join(s.strip() for s in parts if s and s.strip())


def add_pose(graph: dict[str, Any], source: str, strength: float, width: int, height: int) -> None:
    """DWPose da imagem de pose + ControlNet Union entre a LoRA (no 11) e o sampling (no 4)."""
    graph["p20"] = {"class_type": "LoadImage", "inputs": {"image": source}}
    graph["p21"] = {"class_type": "ImageScale", "inputs": {"image": ["p20", 0], "upscale_method": "lanczos",
                                                            "width": width, "height": height, "crop": "center"}}
    graph["p22"] = {"class_type": "DWPreprocessor", "inputs": {"image": ["p21", 0], **DWPOSE}}
    graph["p23"] = {"class_type": "ImageScale", "inputs": {"image": ["p22", 0], "upscale_method": "bilinear",
                                                            "width": width, "height": height, "crop": "disabled"}}
    graph["p50"] = {"class_type": "ModelPatchLoader", "inputs": {"name": CONTROL_PATCH}}
    graph["p30"] = {"class_type": "ZImageFunControlnet", "inputs": {"model": ["11", 0], "model_patch": ["p50", 0],
                                                                     "vae": ["3", 0], "image": ["p23", 0], "strength": strength}}
    graph["4"]["inputs"]["model"] = ["p30", 0]


class ZImageAdapter(SceneAdapter):
    name = "zimage"

    def __init__(self, session: ComfySession) -> None:
        self.session = session

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name, title="Z-Image Turbo + LoRA da persona", supports_negative_prompt=False,
            supports_pose_control=True, default_size=(832, 1216), max_pixels=1_300_000,
        )

    def model_versions(self, lora: dict[str, Any]) -> dict[str, str]:
        return {"unet": UNET, "text_encoder": TEXT_ENCODER, "vae": VAE, "lora": lora.get("file", ""),
                "workflow": WORKFLOW, "control_patch": CONTROL_PATCH}

    async def validate_configuration(self, lora: dict[str, Any], pose: bool) -> list[str]:
        client = self.session.client
        status = await client.check_connection()
        if not status.ok:
            return [f"ComfyUI fora do ar: {status.message}"]
        problems = []
        try:
            self.session.workflows.get_workflow(WORKFLOW)
        except WorkflowNotFoundError:
            problems.append(f"Workflow {WORKFLOW} nao encontrado.")
        try:
            if UNET not in await client.list_diffusion_models():
                problems.append(f"Modelo {UNET} nao esta no pod.")
            if not lora.get("file") or lora["file"] not in await client.list_loras():
                problems.append(f"LoRA da persona ({lora.get('file') or 'nao configurada'}) nao esta no pod. "
                                "Sem ela o Persona Engine V1 nao gera (sem fallback).")
            if pose and CONTROL_PATCH not in await client.list_model_patches():
                problems.append(f"ControlNet {CONTROL_PATCH} nao esta no pod.")
        except ComfyUIError as exc:
            problems.append(f"Nao consegui listar os modelos do ComfyUI: {exc}")
        return problems

    async def generate(self, request: SceneRequest) -> StageOutput:
        params = self.normalize_parameters(request.parameters)
        seed = params.seed if params.seed is not None else new_seed()
        lora = request.lora
        if not lora.get("file") or not lora.get("trigger"):
            raise ProviderConfigurationError("A persona nao tem LoRA do Z-Image configurada.")
        values = {
            "PROMPT": build_prompt(request), "WIDTH": params.width, "HEIGHT": params.height, "SEED": seed,
            "LORA_NAME": lora["file"], "LORA_STRENGTH": float(lora.get("strength", 1.0)),
            "FILENAME_PREFIX": "luna_engine", **SAMPLING,
        }
        pose = request.pose
        switch = self.session.mark("zimage")
        prompt_id, image, seconds = await self.session.run(
            WORKFLOW, values,
            (lambda g: add_pose(g, pose.source, pose.strength, params.width, params.height)) if pose else None,
        )
        return StageOutput(
            image=ProviderImage("comfyui", output_name(image), image.url, params.width, params.height),
            stage="scene", adapter=self.name, seconds=round(seconds, 2), seed=seed,
            model_ids=[UNET, lora["file"]] + ([CONTROL_PATCH] if pose else []),
            provider_job_id=prompt_id, model_switch=switch, gpu=await self.session.gpu(),
            effective_parameters={
                "workflow": WORKFLOW, "prompt": values["PROMPT"], "lora_strength": values["LORA_STRENGTH"],
                "width": params.width, "height": params.height, "seed": seed, **SAMPLING,
                "negative_applied": False,
                "pose": {"source": pose.source, "strength": pose.strength} if pose else None,
            },
        )
