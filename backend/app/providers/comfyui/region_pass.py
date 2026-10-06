"""ComfyRegionPassAdapter: passadas de rosto/corpo da V2 (realvis-face-pass,
realvis-body-pass).

Recorta a regiao da imagem ANTERIOR, amplia para o tamanho de trabalho,
redesenha com o checkpoint + LoRA da persona (denoise da passada), volta ao
tamanho e cola com uma mascara desenhada aqui (elipses/retangulos do nucleo,
borda suave, opacidade = strength). Fora da mascara a imagem fica identica.
"""
from __future__ import annotations

import io
import uuid
from typing import Any

from app.clients.comfyui_client import ComfyUIError
from app.core.generation.v2_config import LoraSpec, ModelProfile
from app.providers.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderImage,
    RegionPassAdapter,
    RegionPassRequest,
    StageOutput,
)
from app.providers.comfyui.session import ComfySession, output_name
from app.workflow_manager.manager import WorkflowNotFoundError

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageFilter = None  # type: ignore[assignment]

WORK_SIDE = 1024  # lado maior do recorte durante a passada (SDXL)


def work_size(w: int, h: int) -> tuple[int, int]:
    scale = WORK_SIDE / max(w, h)
    return max(512, round(w * scale / 8) * 8), max(512, round(h * scale / 8) * 8)


def render_mask(region: dict[str, Any], strength: float) -> bytes:
    """PNG em tons de cinza do tamanho do recorte: 255*strength dentro, 0 fora, borda suave."""
    if Image is None:
        raise ProviderConfigurationError("Pillow ausente: a mascara da passada nao pode ser desenhada.")
    crop = region["crop"]
    w, h = int(crop["w"]), int(crop["h"])
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    level = max(0, min(255, round(255 * strength)))
    for include in (True, False):  # inclusoes primeiro, exclusoes (rosto no corpo) por cima
        for s in region["shapes"]:
            if bool(s.get("include", True)) != include:
                continue
            box = [s["cx"] - s["rx"], s["cy"] - s["ry"], s["cx"] + s["rx"], s["cy"] + s["ry"]]
            fill = level if include else 0
            (draw.ellipse if s["kind"] == "ellipse" else draw.rectangle)(box, fill=fill)
    radius = max(1.0, float(region.get("feather", 0.05)) * min(w, h))
    mask = mask.filter(ImageFilter.GaussianBlur(radius))
    buf = io.BytesIO()
    mask.save(buf, "PNG")
    return buf.getvalue()


def clip_to_person(graph: dict[str, Any], clip_mask: str, crop: dict[str, Any]) -> None:
    """Modo replicar: a mascara da passada vezes a mascara da pessoa (recortada no
    mesmo lugar) - a passada nunca mexe no fundo da foto."""
    graph["c1"] = {"class_type": "LoadImage", "inputs": {"image": clip_mask}}
    graph["c2"] = {"class_type": "ImageToMask", "inputs": {"image": ["c1", 0], "channel": "red"}}
    graph["c3"] = {"class_type": "CropMask", "inputs": {"mask": ["c2", 0], "x": int(crop["x"]), "y": int(crop["y"]),
                                                       "width": int(crop["w"]), "height": int(crop["h"])}}
    graph["c4"] = {"class_type": "MaskComposite", "inputs": {"destination": ["17", 0], "source": ["c3", 0], "x": 0, "y": 0,
                                                            "operation": "multiply"}}
    graph["18"]["inputs"]["mask"] = ["c4", 0]


class ComfyRegionPassAdapter(RegionPassAdapter):
    def __init__(self, session: ComfySession, profile: ModelProfile, lora: LoraSpec, steps: int = 20,
                 face_workflow: str = "realvis-face-pass", body_workflow: str = "realvis-body-pass",
                 identity_adapters: dict[str, Any] | None = None) -> None:
        self.session = session
        self.profile = profile
        self.lora = lora
        self.steps = steps
        self.workflows = {"face": face_workflow, "body": body_workflow}
        self.identity_adapters = dict(identity_adapters or {})
        self.name = f"{profile.id}-region-pass"
        self._references: dict[str, str] = {}

    async def _reference_name(self, reference) -> str:
        key = reference.sha256 or reference.reference_id
        if key not in self._references:
            self._references[key] = await self.session.client.upload_image(f"v2ref_{key[:16]}.png", reference.content)
        return self._references[key]

    async def validate_configuration(self) -> list[str]:
        problems = []
        for wf in [*self.workflows.values(), *(a["workflow"] for a in self.identity_adapters.values())]:
            try:
                self.session.workflows.get_workflow(wf)
            except WorkflowNotFoundError:
                problems.append(f"Workflow {wf} nao encontrado.")
        if Image is None:
            problems.append("Pillow ausente no backend (mascara das passadas).")
        if "instantid" in self.identity_adapters:
            problems += await self._check_instantid(self.identity_adapters["instantid"])
        return problems

    async def _check_instantid(self, iid: dict[str, Any]) -> list[str]:
        try:
            info = await self.session.client.get_object_info()
        except ComfyUIError as exc:
            return [f"Nao consegui conferir o InstantID: {exc}"]
        missing = [f"No {n} (InstantID) nao esta no pod." for n in ("ApplyInstantID", "InstantIDModelLoader", "InstantIDFaceAnalysis")
                   if n not in info]
        if missing:
            return missing
        options = lambda node, field: info.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])[0] or []  # noqa: E731
        problems = []
        if iid["model"] not in options("InstantIDModelLoader", "instantid_file"):
            problems.append(f"Modelo {iid['model']} do InstantID nao esta no pod.")
        if iid["controlnet"] not in options("ControlNetLoader", "control_net_name"):
            problems.append(f"ControlNet {iid['controlnet']} do InstantID nao esta no pod.")
        return problems

    async def refine(self, image: ProviderImage, request: RegionPassRequest) -> StageOutput:
        crop = request.region["crop"]
        ww, wh = work_size(int(crop["w"]), int(crop["h"]))
        kind = "face" if request.name.startswith("face") else "body"
        try:
            mask_name = await self.session.client.upload_image(f"v2mask_{uuid.uuid4().hex[:12]}.png",
                                                               render_mask(request.region, request.strength))
        except ComfyUIError as exc:
            raise ProviderError(f"nao consegui enviar a mascara: {exc}") from exc
        s = self.profile.sampling
        values = {
            "IMAGE": image.locator, "MASK": mask_name, "PROMPT": request.prompt, "NEGATIVE": request.negative,
            "SEED": request.seed, "DENOISE": request.denoise, "CKPT": self.profile.checkpoint,
            "LORA_NAME": self.lora.file, "LORA_STRENGTH": self.lora.strength if request.use_lora else 0.0,
            "STEPS": self.steps, "CFG": s.cfg,
            "SAMPLER": s.sampler, "SCHEDULER": s.scheduler, "CROP_X": int(crop["x"]), "CROP_Y": int(crop["y"]),
            "CROP_W": int(crop["w"]), "CROP_H": int(crop["h"]), "WORK_W": ww, "WORK_H": wh,
            "FILENAME_PREFIX": f"luna_v2_{request.name}",
        }
        workflow = self.workflows[kind]
        if request.identity_adapter:
            adapter = self.identity_adapters.get(request.identity_adapter)
            if adapter is None or request.reference is None:
                raise ProviderConfigurationError(
                    f"Adaptador de identidade '{request.identity_adapter}' sem configuracao ou sem a master.")
            workflow = adapter["workflow"]
            values.update(REFERENCE=await self._reference_name(request.reference), ID_WEIGHT=request.adapter_weight,
                          INSTANTID=adapter["model"], INSTANTID_CONTROLNET=adapter["controlnet"])
        switch = self.session.mark(self.profile.id)
        patch = (lambda g: clip_to_person(g, request.clip_mask, crop)) if request.clip_mask else None
        prompt_id, out, seconds = await self.session.run(workflow, values, patch)
        return StageOutput(
            image=ProviderImage("comfyui", output_name(out), out.url, image.width, image.height),
            stage=request.name, adapter=self.name, seconds=round(seconds, 2), seed=request.seed,
            model_ids=[self.profile.checkpoint, self.lora.file], provider_job_id=prompt_id, model_switch=switch,
            gpu=await self.session.gpu(),
            effective_parameters={"workflow": workflow, "identity_adapter": request.identity_adapter,
                                  "adapter_weight": request.adapter_weight, "denoise": request.denoise,
                                  "strength": request.strength, "crop": crop, "work_size": [ww, wh],
                                  "steps": self.steps, "cfg": s.cfg, "prompt": request.prompt, "mask": mask_name,
                                  "lora": self.lora.file if request.use_lora else None,
                                  "lora_strength": values["LORA_STRENGTH"]},
        )


__all__ = ["ComfyRegionPassAdapter", "clip_to_person", "render_mask", "work_size"]
