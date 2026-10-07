"""Adapters do ComfyUI para o Persona Replacement (contratos em
core/persona_replacement/contracts.py):

  ComfyImageStore           le/grava pixels (download/upload no ComfyUI)
  ComfySegmenter            pessoa (SAM2), cabelo (Florence "hair"), oculos (grounding)
  ComfyReplacementTransformer  transformacao localizada: reusa o ComfyRegionPassAdapter
                            (recorte ampliado + checkpoint + LoRA + InstantID) com a
                            MASCARA DA ETAPA como mascara de recorte - nada fora dela.
"""
from __future__ import annotations

import ast
import io
import json
import uuid
from typing import Any

import numpy as np
from PIL import Image

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError
from app.core.generation.reference import ReferenceSheet, sam_points
from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.contracts import RawSegments, TransformRequest, TransformResult
from app.providers.base import ProviderError, ProviderImage, RegionPassRequest
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter, work_size
from app.providers.comfyui.session import ComfySession, output_name
from app.validation_backends.skin import _split_locator
from app.workflow_manager.manager import WorkflowNotFoundError

SEGMENT_WORKFLOW = "replacement-segment"


def to_png(pixels: np.ndarray) -> bytes:
    buf = io.BytesIO()
    mode = "L" if pixels.ndim == 2 else "RGB"
    Image.fromarray(pixels.astype(np.uint8), mode).save(buf, "PNG")
    return buf.getvalue()


def mask_png(mask: np.ndarray) -> bytes:
    return to_png((np.clip(mask, 0, 1) * 255).round())


def parse_boxes(text: str) -> list[tuple[float, float, float, float]]:
    return [b for b, _ in parse_labeled_boxes(text)]


def parse_labeled_boxes(text: str) -> list[tuple[tuple[float, float, float, float], str]]:
    """Caixas do Florence COM o rotulo (oculos x brinco decidem PRESERVE x REMOVE na politica de atributos)."""
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return []
    if isinstance(data, list):
        data = data[0] if data else {}
    boxes = (data or {}).get("bboxes") or []
    labels = (data or {}).get("labels") or []
    if boxes and isinstance(boxes[0], list) and boxes[0] and isinstance(boxes[0][0], list):
        boxes = boxes[0]
    if labels and isinstance(labels[0], list):
        labels = labels[0]
    out = []
    for i, b in enumerate(boxes):
        if len(b) >= 4:
            label = str(labels[i]).strip().lower() if i < len(labels) else ""
            out.append((tuple(float(v) for v in b[:4]), label.replace("</s>", "").replace("<s>", "").strip()))
    return out  # type: ignore[return-value]


def crop_for(mask: np.ndarray, margin: float = 0.18) -> dict[str, int] | None:
    ys, xs = np.nonzero(mask > 0.02)
    if len(xs) == 0:
        return None
    h, w = mask.shape
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    mw, mh = (x2 - x1) * margin + 8, (y2 - y1) * margin + 8
    x1, y1 = max(0, int(x1 - mw)), max(0, int(y1 - mh))
    x2, y2 = min(w, int(x2 + mw)), min(h, int(y2 + mh))
    cw, ch = max(64, (x2 - x1) // 8 * 8), max(64, (y2 - y1) // 8 * 8)
    return {"x": min(x1, w - cw), "y": min(y1, h - ch), "w": min(cw, w), "h": min(ch, h)}


class ComfyImageStore:
    def __init__(self, client: ComfyUIClient) -> None:
        self.client = client

    async def load(self, image: str) -> np.ndarray:
        name, sub, folder = _split_locator(image)
        content = await self.client.download_file(name, sub, folder)
        return np.asarray(Image.open(io.BytesIO(content)).convert("RGB"), dtype=np.uint8)

    async def save(self, pixels: np.ndarray, name: str) -> str:
        return await self.client.upload_image(f"repl_{name}_{uuid.uuid4().hex[:8]}.png", to_png(pixels))


class ComfySegmenter:
    def __init__(self, session: ComfySession, store: ComfyImageStore) -> None:
        self.session = session
        self.store = store

    async def segment(self, image: str, sheet: ReferenceSheet) -> RawSegments:
        pos, neg = sam_points(sheet)
        client = self.session.client
        try:
            graph = self.session.workflows.render(SEGMENT_WORKFLOW, {"IMAGE": image, "POINTS_POS": pos, "POINTS_NEG": neg})
            entry = await client.wait_for_completion(await client.queue_prompt(graph))
        except ComfyUIError as exc:
            raise ProviderError(f"segmentacao falhou: {exc}") from exc
        images = client.extract_images(entry)
        texts = {k: str(v["text"][0]) for k, v in entry.get("outputs", {}).items() if v.get("text")}

        async def mask_of(tag: str) -> np.ndarray | None:
            item = next((i for i in images if f"_{tag}" in i.filename), None)
            if item is None:
                return None
            return (await self.store.load(output_name(item)))[..., 0].astype(np.float32) / 255.0

        async def union_of(tag: str) -> np.ndarray | None:
            found = [await self.store.load(output_name(i)) for i in images if f"_{tag}" in i.filename]
            if not found:
                return None
            return np.max(np.stack([f[..., 0] for f in found]), axis=0).astype(np.float32) / 255.0

        person = await mask_of("person")
        if person is None:
            raise ProviderError("a segmentacao nao devolveu a mascara da pessoa")
        labeled = parse_labeled_boxes(texts.get("f6", ""))
        return RawSegments(person=person, hair=await mask_of("hair"), protect_boxes=[b for b, _ in labeled],
                           tattoos=await mask_of("tattoo"), clothes=await union_of("clothes"),
                           protect_labels=[label for _, label in labeled])


class ComfyReplacementTransformer:
    def __init__(self, region: ComfyRegionPassAdapter, client: ComfyUIClient, feather_frac: float = 0.01) -> None:
        self.region = region
        self.client = client
        self.feather_frac = feather_frac

    async def transform(self, request: TransformRequest) -> TransformResult:
        crop = crop_for(request.mask)
        if crop is None:
            raise ProviderError(f"{request.name}: mascara vazia")
        h, w = request.mask.shape
        soft = feather(request.mask, max(2, int(min(crop["w"], crop["h"]) * self.feather_frac)))
        try:
            clip = await self.client.upload_image(f"replmask_{uuid.uuid4().hex[:10]}.png", mask_png(soft))
        except ComfyUIError as exc:
            raise ProviderError(f"nao consegui enviar a mascara: {exc}") from exc
        region = {"crop": crop, "feather": 0.0,
                  "shapes": [{"kind": "rect", "cx": crop["w"] / 2, "cy": crop["h"] / 2, "rx": crop["w"], "ry": crop["h"],
                              "include": True}]}
        out = await self.region.refine(ProviderImage("comfyui", request.image, "", w, h), RegionPassRequest(
            region=region, prompt=request.prompt, negative=request.negative, denoise=request.denoise,
            strength=request.strength, seed=request.seed, name=request.name, use_lora=request.use_lora,
            identity_adapter=request.identity_adapter, adapter_weight=request.adapter_weight,
            reference=request.reference, clip_mask=clip))
        return TransformResult(out.image.locator, out.seconds, out.gpu, {**out.effective_parameters, "clip_mask": clip})


TRANSFER_WORKFLOW = "realvis-persona-transfer"
SKIN_WORKFLOW = "realvis-skin-depth"
TRANSFER_NODES = ("DWPreprocessor", "SetUnionControlNetType", "ControlNetApplyAdvanced", "ControlNetLoader")
SKIN_NODES = ("DepthAnythingV2Preprocessor",)


class ComfyTransferTransformer:
    """Persona Transfer: "persona_transfer" vai para o workflow com pose (DWPose -> ControlNet
    Union openpose, rosto+cabelo gerados com a LoRA); "tattoo_removal" (pele de bracos/maos/colo)
    vai para o workflow com profundidade tirada da foto ORIGINAL (geometria preservada); as
    demais (refino de rosto, integracao) reusam o transformer do replacement."""

    def __init__(self, session: ComfySession, base: ComfyReplacementTransformer, profile, lora, transfer: dict[str, Any],
                 skin: dict[str, Any] | None = None, feather_frac: float = 0.006) -> None:
        self.session = session
        self.base = base
        self.profile = profile
        self.lora = lora
        self.transfer = transfer
        self.skin = skin
        self.feather_frac = feather_frac

    async def validate_configuration(self) -> list[str]:
        problems: list[str] = []
        workflows, nodes = [TRANSFER_WORKFLOW], list(TRANSFER_NODES)
        if self.skin and self.skin.get("controlnet"):
            workflows.append(SKIN_WORKFLOW)
            nodes += SKIN_NODES
        for wf in workflows:
            try:
                self.session.workflows.get_workflow(wf)
            except WorkflowNotFoundError:
                problems.append(f"Workflow {wf} nao encontrado.")
        try:
            info = await self.session.client.get_object_info()
        except ComfyUIError as exc:
            return problems + [f"Nao consegui conferir os nos do ComfyUI: {exc}"]
        problems += [f"No {n} nao esta no pod." for n in nodes if n not in info]
        options = info.get("ControlNetLoader", {}).get("input", {}).get("required", {}).get("control_net_name", [[]])[0] or []
        if self.transfer["controlnet"]["file"] not in options:
            problems.append(f"ControlNet {self.transfer['controlnet']['file']} nao esta no pod.")
        return problems

    async def transform(self, request: TransformRequest) -> TransformResult:
        from app.core.persona_replacement.transfer import TATTOO, TRANSFER

        if request.name == TRANSFER:
            workflow, cn, prefix, control = TRANSFER_WORKFLOW, self.transfer["controlnet"], "luna_transfer", None
        elif request.name == TATTOO and self.skin and self.skin.get("controlnet"):
            if not request.control:
                raise ProviderError(f"{request.name}: falta a imagem de estrutura (foto original)")
            workflow, cn, prefix, control = SKIN_WORKFLOW, self.skin["controlnet"], "luna_skin_depth", request.control
        else:
            return await self.base.transform(request)
        crop = crop_for(request.mask, margin=0.12)
        if crop is None:
            raise ProviderError(f"{request.name}: mascara vazia")
        ww, wh = work_size(crop["w"], crop["h"])
        soft = feather(request.mask, max(2, int(min(crop["w"], crop["h"]) * self.feather_frac)))
        try:
            mask = await self.session.client.upload_image(f"xfermask_{uuid.uuid4().hex[:10]}.png", mask_png(soft))
        except ComfyUIError as exc:
            raise ProviderError(f"nao consegui enviar a mascara: {exc}") from exc
        t, s = self.transfer, self.profile.sampling
        values = {
            "IMAGE": request.image, "MASK": mask, "PROMPT": request.prompt, "NEGATIVE": request.negative,
            "SEED": request.seed, "DENOISE": request.denoise, "CKPT": self.profile.checkpoint,
            "LORA_NAME": self.lora.file, "LORA_STRENGTH": self.lora.strength if request.use_lora else 0.0,
            "STEPS": int(t.get("steps", 30)), "CFG": float(t.get("cfg", s.cfg)), "SAMPLER": s.sampler,
            "SCHEDULER": s.scheduler, "CONTROLNET": t["controlnet"]["file"], "CN_TYPE": cn["type"],
            "CN_STRENGTH": float(cn["strength"]), "CN_END": float(cn["end_percent"]), "CROP_X": crop["x"],
            "CROP_Y": crop["y"], "CROP_W": crop["w"], "CROP_H": crop["h"], "WORK_W": ww, "WORK_H": wh,
            "FILENAME_PREFIX": prefix,
        }
        if control:
            values.update(CONTROL=control, DEPTH_MODEL=cn.get("depth_model", "depth_anything_v2_vits.pth"))
        self.session.mark(self.profile.id)
        prompt_id, out, seconds = await self.session.run(workflow, values)
        return TransformResult(output_name(out), round(seconds, 2), await self.session.gpu(),
                               {"workflow": workflow, "prompt_id": prompt_id, "crop": crop, "work_size": [ww, wh],
                                **{k: values[k] for k in ("DENOISE", "STEPS", "CFG", "CONTROLNET", "CN_TYPE",
                                                          "CN_STRENGTH", "CN_END", "LORA_STRENGTH", "PROMPT")}})


__all__ = ["ComfyImageStore", "ComfyReplacementTransformer", "ComfySegmenter", "ComfyTransferTransformer", "SKIN_WORKFLOW",
           "TRANSFER_WORKFLOW",
           "crop_for", "mask_png", "parse_boxes", "to_png"]
