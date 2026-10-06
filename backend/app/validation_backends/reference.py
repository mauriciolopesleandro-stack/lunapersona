"""ComfyReferenceReader: le a foto de referencia num grafo so do ComfyUI.

- LunaFaces (InsightFace): rostos, homem/mulher, idade, 5 pontos, semelhanca com a master;
- DWPose: esqueleto de cada pessoa;
- Florence-2 PromptGen ("more_detailed_caption"): descricao da foto (o mesmo modelo
  que a V1 ja usa em describe-image; nenhum modelo novo);
- pixels (Pillow/NumPy): luz medida (brilho, contraste, quente/fria, estouro, flash).

Opcional: um LLM de texto que JA esta no pod organiza a descricao em campos
(pose, roupa, objetos, ambiente, expressao). Sem ele ou com resposta quebrada,
a ficha usa a descricao crua - registrado em `warnings`.
"""
from __future__ import annotations

import io
import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError
from app.core.generation.reference import (
    STRUCTURE_PROMPT,
    LightStats,
    ReferenceError,
    ReferenceSheet,
    camera_type,
    choose_target,
    parse_fields,
)
from app.core.validation.analysis import ImageAnalysis
from app.providers.base import ProviderImage, ReferenceImage
from app.validation_backends.comfyui import DWPOSE, parse_pose, to_body, to_face
from app.validation_backends.skin import _split_locator

try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]

FLORENCE = "MiaoshouAI/Florence-2-large-PromptGen-v2.0"
Ask = Callable[[str], Awaitable[str]]


def light_stats(img: Any, face_bbox: tuple[float, float, float, float] | None = None) -> LightStats | None:
    if np is None:
        return None
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    face_ratio = None
    if face_bbox is not None:
        x1, y1, x2, y2 = (int(v) for v in face_bbox)
        patch = lum[max(0, y1):max(y1 + 1, y2), max(0, x1):max(x1 + 1, x2)]
        if patch.size:
            face_ratio = round(float(patch.mean()) / max(1.0, float(lum.mean())), 3)
    return LightStats(round(float(lum.mean()), 1), round(float(lum.std()), 1),
                      round(float(rgb[..., 0].mean() - rgb[..., 2].mean()), 1), round(float(sat.mean()), 3),
                      round(float((lum > 250).mean()), 4), face_ratio)


def background_change(reference: Any, final: Any, person_mask: Any, threshold: int = 6) -> float | None:
    """Fracao dos pixels FORA da pessoa (mascara > 2%) que mudaram mais que `threshold`
    niveis entre a foto original e o resultado. 0 = fundo intacto."""
    if np is None:
        return None
    a = np.asarray(reference.convert("RGB"), dtype=np.int16)
    b = np.asarray(final.convert("RGB").resize(reference.size), dtype=np.int16)
    m = np.asarray(person_mask.convert("L").resize(reference.size), dtype=np.float32) / 255.0
    outside = m < 0.02
    if not outside.any():
        return None
    changed = np.abs(a - b).max(axis=2) > threshold
    return round(float(changed[outside].mean()), 5)


class ComfyReferenceReader:
    def __init__(self, client: ComfyUIClient, ask: Ask | None = None) -> None:
        self.client = client
        self.ask = ask
        self._masters: dict[str, str] = {}

    async def _master(self, master: ReferenceImage) -> str:
        key = master.sha256 or master.reference_id
        if key not in self._masters:
            self._masters[key] = await self.client.upload_image(f"refmaster_{key[:16]}.png", master.content)
        return self._masters[key]

    async def read(self, image: ProviderImage, master: ReferenceImage) -> ReferenceSheet:
        graph = {
            "1": {"class_type": "LoadImage", "inputs": {"image": image.locator}},
            "2": {"class_type": "LoadImage", "inputs": {"image": await self._master(master)}},
            "lf": {"class_type": "LunaFaces", "inputs": {"image": ["1", 0], "det_size": 1024, "reference": ["2", 0]}},
            "dw": {"class_type": "DWPreprocessor", "inputs": {"image": ["1", 0], **DWPOSE}},
            "dwp": {"class_type": "PreviewAny", "inputs": {"source": ["dw", 1]}},
            "fm": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": FLORENCE, "precision": "fp16"}},
            "fr": {"class_type": "Florence2Run", "inputs": {
                "image": ["1", 0], "florence2_model": ["fm", 0], "text_input": "", "task": "more_detailed_caption",
                "fill_mask": False, "keep_model_loaded": False, "max_new_tokens": 512, "num_beams": 3,
                "do_sample": False, "output_mask_select": "", "seed": 1}},
            "frp": {"class_type": "PreviewAny", "inputs": {"source": ["fr", 2]}},
        }
        try:
            entry = await self.client.wait_for_completion(await self.client.queue_prompt(graph))
        except ComfyUIError as exc:
            raise ReferenceError(f"Nao consegui ler a foto no ComfyUI: {exc}") from exc
        texts = {k: str(v["text"][0]) for k, v in entry.get("outputs", {}).items() if v.get("text")}

        filename, sub, folder = _split_locator(image.locator)
        content = await self.client.download_file(filename, sub, folder)
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
            faces = [to_face(f) for f in json.loads(texts.get("lf", "[]"))]
            bodies = [b for b in (to_body(kp, height) for kp in parse_pose(texts.get("dwp", "[]"))) if b]
            analysis = ImageAnalysis(width, height, faces, bodies)
            face, body, others, warnings = choose_target(analysis)
            light = light_stats(img, face.bbox)
        if min(width, height) < 700:
            warnings.append(f"foto pequena ({width}x{height}): o resultado herda a falta de detalhe")
        caption = texts.get("frp", "").strip()
        fields: dict[str, str] = {}
        if self.ask is not None and caption:
            try:
                fields = parse_fields(await self.ask(STRUCTURE_PROMPT + caption))
            except Exception as exc:  # LLM fora do ar: segue com a descricao crua, registrado
                warnings.append(f"descricao nao organizada em campos ({exc.__class__.__name__}): usando o texto cru")
            if not fields:
                warnings.append("LLM nao devolveu campos validos: usando a descricao crua")
        return ReferenceSheet(image=image.locator, width=width, height=height, target_face=face, target_body=body,
                              others=others, caption=caption, fields=fields,
                              camera=camera_type(caption, face, body, width, height), light=light, warnings=warnings)


__all__ = ["ComfyReferenceReader", "background_change", "light_stats"]
