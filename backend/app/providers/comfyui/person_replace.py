"""ComfyPersonReplaceAdapter: BASE do modo "replicar foto" da V2.

Faz o papel da geracao base (SceneAdapter) no MultiPassRunner, mas em vez de
criar uma cena do zero troca a pessoa da foto de referencia pela persona
(workflows/realvis-person-replace.json): SAM2 com os pontos da ficha da foto,
recorte ampliado, RealVisXL + LoRA a partir dos pixels da foto (denoise da
config) e so a area da pessoa volta. Devolve tambem a mascara da pessoa
(`clip_mask`) para as passadas nao saírem dela.
"""
from __future__ import annotations

import time
from typing import Any

from app.clients.comfyui_client import ComfyUIError
from app.core.generation.reference import ReferenceSheet, sam_points
from app.core.generation.v2_config import LoraSpec, ModelProfile
from app.providers.base import (
    AdapterCapabilities,
    ProviderConfigurationError,
    ProviderError,
    ProviderImage,
    SceneAdapter,
    SceneRequest,
    StageOutput,
)
from app.providers.comfyui.scene import build_prompt
from app.providers.comfyui.session import ComfySession, new_seed, output_name
from app.workflow_manager.manager import WorkflowNotFoundError, WorkflowParamError

WORK_SIDE = 1024


def replace_crop(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    x, y = max(0, int(x1)), max(0, int(y1))
    w = (min(width, int(x2) + 1) - x) // 8 * 8
    h = (min(height, int(y2) + 1) - y) // 8 * 8
    return x, y, max(64, w), max(64, h)


def work_size(w: int, h: int) -> tuple[int, int]:
    scale = WORK_SIDE / max(w, h)
    return max(512, round(w * scale / 8) * 8), max(512, round(h * scale / 8) * 8)


class ComfyPersonReplaceAdapter(SceneAdapter):
    def __init__(self, session: ComfySession, profile: ModelProfile, lora: LoraSpec, negative: list[str],
                 settings: dict[str, Any], sheet: ReferenceSheet | None = None) -> None:
        self.session = session
        self.profile = profile
        self.lora = lora
        self.negative = list(negative)
        self.settings = dict(settings)
        self.sheet = sheet
        self.name = f"{profile.id}-person-replace"

    def bind(self, sheet: ReferenceSheet) -> ComfyPersonReplaceAdapter:
        """Um adapter por foto de referencia (a ficha da foto vai junto)."""
        clone = ComfyPersonReplaceAdapter(self.session, self.profile, self.lora, self.negative, self.settings, sheet)
        return clone

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(name=self.name, title="Replicar foto (troca a pessoa)", supports_negative_prompt=True,
                                   supports_pose_control=False, default_size=(832, 1216), max_pixels=4_000_000)

    def normalize_parameters(self, params):
        return params  # o tamanho e o da propria foto

    def model_versions(self, lora: dict[str, Any] | None = None) -> dict[str, str]:
        return {"checkpoint": self.profile.checkpoint, "lora": self.lora.file, "workflow": self.settings["workflow"],
                "sam": self.settings.get("sam_model", "")}

    async def validate_configuration(self, lora: dict[str, Any] | None = None, pose: bool = False) -> list[str]:
        problems = []
        try:
            self.session.workflows.get_workflow(self.settings["workflow"])
        except WorkflowNotFoundError:
            problems.append(f"Workflow {self.settings['workflow']} nao encontrado.")
        try:
            info = await self.session.client.get_object_info()
            for node in ("Sam2Segmentation", "DownloadAndLoadSAM2Model", "GrowMaskWithBlur"):
                if node not in info:
                    problems.append(f"No {node} nao esta no pod.")
        except ComfyUIError as exc:
            problems.append(f"Nao consegui conferir os nos do ComfyUI: {exc}")
        return problems

    async def generate(self, request: SceneRequest) -> StageOutput:
        sheet = self.sheet
        if sheet is None:
            raise ProviderConfigurationError("Replicar foto sem a ficha da foto de referencia.")
        seed = request.parameters.seed if request.parameters.seed is not None else new_seed()
        x, y, w, h = replace_crop(sheet.person_box(), sheet.width, sheet.height)
        ww, wh = work_size(w, h)
        pos, neg = sam_points(sheet)
        s = self.profile.sampling
        values = {
            "REFERENCE": sheet.image, "PROMPT": build_prompt(request),
            "NEGATIVE": ", ".join(dict.fromkeys([*request.prompt.negative.all_terms(), *self.negative])),
            "SEED": seed, "DENOISE": float(self.settings["denoise"]), "CKPT": self.profile.checkpoint,
            "LORA_NAME": self.lora.file, "LORA_STRENGTH": self.lora.strength, "STEPS": s.steps, "CFG": s.cfg,
            "SAMPLER": s.sampler, "SCHEDULER": s.scheduler, "POINTS_POS": pos, "POINTS_NEG": neg,
            "CROP_X": x, "CROP_Y": y, "CROP_W": w, "CROP_H": h, "WORK_W": ww, "WORK_H": wh,
            "SAM_MODEL": self.settings.get("sam_model", "sam2_hiera_base_plus.safetensors"),
            "MASK_GROW": int(self.settings.get("mask_grow", 16)), "EDGE_BLUR": int(self.settings.get("edge_blur", 10)),
            "FILENAME_PREFIX": "luna_v2_replace",
        }
        start = time.monotonic()
        switch = self.session.mark(self.profile.id)
        client = self.session.client
        try:
            graph = self.session.workflows.render(self.settings["workflow"], values)
            prompt_id = await client.queue_prompt(graph)
            images = client.extract_images(await client.wait_for_completion(prompt_id))
        except (WorkflowNotFoundError, WorkflowParamError) as exc:
            raise ProviderConfigurationError(str(exc)) from exc
        except ComfyUIError as exc:
            raise ProviderError(str(exc)) from exc
        result = next((i for i in images if "_mask" not in i.filename), None)
        mask = next((i for i in images if "_mask" in i.filename), None)
        if result is None:
            raise ProviderError("A troca de pessoa terminou sem imagem.")
        return StageOutput(
            image=ProviderImage("comfyui", output_name(result), result.url, sheet.width, sheet.height),
            stage="base", adapter=self.name, seconds=round(time.monotonic() - start, 2), seed=seed,
            model_ids=[self.profile.checkpoint, self.lora.file], provider_job_id=prompt_id, model_switch=switch,
            gpu=await self.session.gpu(),
            effective_parameters={"workflow": self.settings["workflow"], "mode": "replicate", "prompt": values["PROMPT"],
                                  "negative": values["NEGATIVE"], "denoise": values["DENOISE"], "crop": [x, y, w, h],
                                  "work_size": [ww, wh], "sam_points": {"positive": pos, "negative": neg},
                                  "clip_mask": output_name(mask) if mask else None, "reference": sheet.image},
        )


__all__ = ["ComfyPersonReplaceAdapter", "replace_crop", "work_size"]
