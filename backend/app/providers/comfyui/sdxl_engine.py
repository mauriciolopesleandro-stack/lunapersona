"""ModelAdapter V2 no ComfyUI para checkpoints SDXL (RealVisXL, Lustify): o MESMO adapter com
checkpoint diferente, vindo do registro. Sem fallback: checkpoint indisponivel no provider = erro.

  inpaint(): workflow sdxl-inpaint-control (pose + profundidade + LoRA, so a mascara)
  inpaint() com referencia facial: passada de rosto com InstantID (ComfyRegionPassAdapter)
  generate(): workflow base do checkpoint (txt2img)
"""
from __future__ import annotations

import uuid
from typing import Any

import numpy as np

from app.clients.comfyui_client import ComfyUIError
from app.core.engines.adapter import AdapterResult, InpaintRequest, ModelAdapter
from app.core.engines.models import ModelInfo
from app.core.persona_replacement.blending import feather
from app.providers.base import ProviderError, ProviderImage, RegionPassRequest
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter
from app.providers.comfyui.replacement import crop_for, mask_png
from app.providers.comfyui.session import ComfySession, output_name
from app.workflow_manager.manager import WorkflowNotFoundError

WORKFLOW = "sdxl-inpaint-control"
WORKFLOW_DD = "sdxl-inpaint-control-dd"  # forca por pixel (Differential Diffusion)
NODES = ("DWPreprocessor", "DepthAnythingV2Preprocessor", "SetUnionControlNetType", "ControlNetApplyAdvanced",
         "DifferentialDiffusion")


def work_dims(w: int, h: int, side: int) -> tuple[int, int]:
    s = side / max(w, h)
    return max(512, round(w * s / 8) * 8), max(512, round(h * s / 8) * 8)


class SDXLComfyAdapter(ModelAdapter):
    def __init__(self, session: ComfySession, model: ModelInfo, lora: ModelInfo, controlnet: str,
                 region: ComfyRegionPassAdapter | None = None, depth_model: str = "depth_anything_v2_vits.pth",
                 lora_strength: float = 1.0, price_per_hour: float | None = None) -> None:
        self.session = session
        self.model = model
        self.model_id = model.id
        self.lora = lora
        self.controlnet = controlnet
        self.region = region
        self.depth_model = depth_model
        self.lora_strength = lora_strength  # do registro; so muda por pedido EXPLICITO do usuario
        self.price = price_per_hour

    async def load(self) -> list[str]:
        problems: list[str] = []
        for wf in (WORKFLOW, WORKFLOW_DD):
            try:
                self.session.workflows.get_workflow(wf)
            except WorkflowNotFoundError:
                problems.append(f"workflow {wf} nao encontrado")
        try:
            info = await self.session.client.get_object_info()
        except ComfyUIError as exc:
            return problems + [f"nao consegui conferir o ComfyUI: {exc}"]
        problems += [f"no {n} nao esta no ComfyUI" for n in NODES if n not in info]

        def options(node, fld):
            return info.get(node, {}).get("input", {}).get("required", {}).get(fld, [[]])[0] or []
        if self.model.file not in options("CheckpointLoaderSimple", "ckpt_name"):
            problems.append(f"checkpoint {self.model.file} ({self.model.title}) nao esta no provider")
        if self.lora.file not in options("LoraLoaderModelOnly", "lora_name"):
            problems.append(f"LoRA {self.lora.file} nao esta no provider")
        if self.controlnet not in options("ControlNetLoader", "control_net_name"):
            problems.append(f"ControlNet {self.controlnet} nao esta no provider")
        if self.region is not None:
            problems += await self.region.validate_configuration()
        return problems

    def supports_reference(self) -> bool:
        return self.region is not None and bool(self.region.identity_adapters)

    def supports_controlnet(self) -> bool:
        return True

    def supports_lora(self) -> bool:
        return True

    def estimate_vram(self) -> float | None:
        return self.model.vram_gb

    def metadata(self) -> dict[str, Any]:
        return {**self.model.metadata(), "lora": self.lora.file, "lora_hash": self.lora.sha256,
                "lora_strength": self.lora_strength, "controlnet": self.controlnet, "workflow": WORKFLOW}

    async def _upload_mask(self, mask: np.ndarray, crop: dict[str, int]) -> str:
        soft = feather(mask, max(2, int(min(crop["w"], crop["h"]) * 0.006)))
        try:
            return await self.session.client.upload_image(f"v2mask_{uuid.uuid4().hex[:10]}.png", mask_png(soft))
        except ComfyUIError as exc:
            raise ProviderError(f"nao consegui enviar a mascara: {exc}") from exc

    async def inpaint(self, req: InpaintRequest) -> AdapterResult:
        if req.identity.reference is not None and req.identity.reference_strength > 0:
            return await self._identity_refine(req)
        crop = crop_for(req.mask, margin=0.15)
        if crop is None:
            raise ProviderError(f"{req.stage}: mascara vazia")
        ww, wh = work_dims(crop["w"], crop["h"], req.work_side)
        s = self.model.sampling
        values = {
            "IMAGE": req.image, "STRUCTURE": req.controls.structure or req.image,
            "MASK": await self._upload_mask(req.mask if req.strength_map is None else req.mask * req.strength_map, crop),
            "CKPT": self.model.file, "PROMPT": req.prompt, "NEGATIVE": req.negative, "SEED": req.seed, "DENOISE": req.denoise,
            "LORA_NAME": self.lora.file, "LORA_STRENGTH": self.lora_strength if req.identity.use_lora else 0.0,
            "STEPS": req.steps or s.get("steps", 30), "CFG": req.cfg or s.get("cfg", 5.0),
            "SAMPLER": s.get("sampler", "dpmpp_2m_sde"), "SCHEDULER": s.get("scheduler", "karras"),
            "CONTROLNET": self.controlnet, "POSE_STRENGTH": req.controls.pose_strength, "DEPTH_STRENGTH": req.controls.depth_strength,
            "CN_END": req.controls.end_percent, "DEPTH_MODEL": self.depth_model,
            "CROP_X": crop["x"], "CROP_Y": crop["y"], "CROP_W": crop["w"], "CROP_H": crop["h"], "WORK_W": ww, "WORK_H": wh,
            "FILENAME_PREFIX": f"luna_v2_{req.stage}",
        }
        wf = WORKFLOW
        if req.strength_map is not None:
            wf = WORKFLOW_DD
            values["COMPOSITE_MASK"] = await self._upload_mask(req.mask, crop)  # onde o resultado entra (a regiao toda)
        self.session.mark(self.model.id)
        prompt_id, out, seconds = await self.session.run(wf, values)
        params = {k: values[k] for k in ("CKPT", "DENOISE", "STEPS", "CFG", "LORA_STRENGTH", "POSE_STRENGTH", "DEPTH_STRENGTH",
                                         "CN_END", "WORK_W", "WORK_H", "SEED")}
        if req.strength_map is not None:
            inside = req.mask > 0.5
            params["strength_map"] = {"min": round(float(req.strength_map[inside].min()), 2) if inside.any() else None,
                                      "low_frac": round(float((req.strength_map[inside] < 0.99).mean()), 3) if inside.any() else 0}
        return AdapterResult(output_name(out), round(seconds, 2), await self.session.gpu(),
                             {"workflow": wf, "prompt_id": prompt_id, "crop": crop, **params})

    async def _identity_refine(self, req: InpaintRequest) -> AdapterResult:
        """Rosto com referencia facial (InstantID): recorte da mascara, LoRA na forca fixa."""
        if self.region is None or "instantid" not in self.region.identity_adapters:
            raise ProviderError("referencia facial pedida mas o adaptador de identidade nao esta configurado (sem fallback)")
        crop = crop_for(req.mask)
        if crop is None:
            raise ProviderError(f"{req.stage}: mascara vazia")
        h, w = req.mask.shape
        clip = await self._upload_mask(req.mask, crop)
        region = {"crop": crop, "feather": 0.0, "shapes": [{"kind": "rect", "cx": crop["w"] / 2, "cy": crop["h"] / 2,
                                                            "rx": crop["w"], "ry": crop["h"], "include": True}]}
        name = req.stage if req.stage.startswith("face") else f"face_{req.stage}"
        out = await self.region.refine(ProviderImage("comfyui", req.image, "", w, h), RegionPassRequest(
            region=region, prompt=req.prompt, negative=req.negative, denoise=req.denoise, strength=req.strength,
            seed=req.seed, name=name, use_lora=req.identity.use_lora, identity_adapter="instantid",
            adapter_weight=req.identity.reference_strength, reference=req.identity.reference, clip_mask=clip))
        return AdapterResult(out.image.locator, out.seconds, out.gpu, {**out.effective_parameters, "clip_mask": clip})

    async def generate(self, prompt: str, negative: str, width: int, height: int, seed: int, **kw) -> AdapterResult:
        profile_wf = kw.get("workflow") or ("realvis-base" if self.model.id == "realvisxl" else "lustify-base")
        s = self.model.sampling
        values = {"PROMPT": prompt, "NEGATIVE": negative, "WIDTH": width, "HEIGHT": height, "SEED": seed, "CKPT": self.model.file,
                  "LORA_NAME": self.lora.file, "LORA_STRENGTH": self.lora_strength, "STEPS": s.get("steps", 30),
                  "CFG": s.get("cfg", 5.0), "SAMPLER": s.get("sampler"), "SCHEDULER": s.get("scheduler")}
        self.session.mark(self.model.id)
        prompt_id, out, seconds = await self.session.run(profile_wf, values)
        return AdapterResult(output_name(out), round(seconds, 2), await self.session.gpu(), {"workflow": profile_wf, **values})


class RealVisXLAdapter(SDXLComfyAdapter):
    """RealVisXL V5 (padrao da V2)."""


class LustifyAdapter(SDXLComfyAdapter):
    """Lustify (backend alternativo de benchmark; nunca substitui o RealVis sozinho)."""


__all__ = ["LustifyAdapter", "RealVisXLAdapter", "SDXLComfyAdapter", "WORKFLOW", "work_dims"]
