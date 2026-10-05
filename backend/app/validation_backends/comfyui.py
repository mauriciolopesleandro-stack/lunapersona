"""Medidas da validacao pelo ComfyUI do pod.

LunaFacesAnalyzer: rostos pelo InsightFace (no LunaFaces,
comfyui_nodes/luna_faces) - semelhanca ArcFace, sexo, idade, para onde o
rosto esta virado e os 5 pontos do rosto. Roda na CPU, segundos por imagem.

FlorenceFeatureChecker: procura os tracos marcantes da persona (ex.:
"necklace") na descricao do Florence-2. Medida fraca: so diz se a descricao
menciona o traco.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError
from app.core.validation.analysis import DetectedFace
from app.providers.base import ProviderImage, ReferenceImage
from app.services.person_swap import luna_faces
from app.services.scene_describer import describe_image
from app.workflow_manager.manager import WorkflowManager

NODE = "LunaFaces"


def to_face(data: dict[str, Any]) -> DetectedFace:
    return DetectedFace(
        bbox=tuple(float(v) for v in data["bbox"]),  # type: ignore[arg-type]
        det_score=float(data.get("score", 0.0)),
        sex=data.get("sex"),
        age=float(data["age"]) if data.get("age") else None,
        yaw=data.get("yaw"),
        similarity=float(data["sim"]) if data.get("sim") is not None else None,
        kps=[(float(x), float(y)) for x, y in data.get("kps", [])],
    )


class LunaFacesAnalyzer:
    def __init__(self, comfyui_client: ComfyUIClient) -> None:
        self.client = comfyui_client
        self._uploaded: dict[str, str] = {}
        self._reference_faces: dict[str, DetectedFace | None] = {}

    async def check_ready(self) -> list[str]:
        try:
            info = await self.client.get_object_info()
        except ComfyUIError as exc:
            return [f"ComfyUI fora do ar: {exc}"]
        if NODE not in info:
            return ["O no LunaFaces nao esta no pod (scripts/setup_instantid.sh): sem ele nao ha como conferir o rosto."]
        return []

    async def _upload(self, filename: str, content: bytes) -> str:
        key = hashlib.sha1(content).hexdigest()
        if key not in self._uploaded:
            self._uploaded[key] = await self.client.upload_image(
                f"engine_{key[:16]}{Path(filename).suffix or '.png'}", content
            )
        return self._uploaded[key]

    async def _image_name(self, image: ProviderImage) -> str:
        if image.provider == "comfyui":
            return image.locator
        # Imagem de outro provider: baixa e manda para o ComfyUI medir.
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.get(image.url)
            resp.raise_for_status()
        return await self._upload(Path(image.locator).name or "gerada.png", resp.content)

    async def _faces(self, image_name: str, reference_name: str | None) -> list[DetectedFace]:
        graph = {"1": {"class_type": "LoadImage", "inputs": {"image": image_name}}}
        if reference_name:
            graph["2"] = {"class_type": "LoadImage", "inputs": {"image": reference_name}}
        found = await luna_faces(self.client, graph, ["1", 0], ["2", 0] if reference_name else None)
        return [to_face(f) for f in found or []]

    async def reference_face(self, reference: ReferenceImage) -> DetectedFace | None:
        key = hashlib.sha1(reference.content).hexdigest()
        if key not in self._reference_faces:
            faces = await self._faces(await self._upload(reference.filename, reference.content), None)
            self._reference_faces[key] = faces[0] if faces else None
        return self._reference_faces[key]

    async def generated_faces(self, image: ProviderImage, reference: ReferenceImage) -> list[DetectedFace]:
        reference_name = await self._upload(reference.filename, reference.content)
        return await self._faces(await self._image_name(image), reference_name)


class FlorenceFeatureChecker:
    def __init__(self, comfyui_client: ComfyUIClient, workflow_manager: WorkflowManager) -> None:
        self.client = comfyui_client
        self.workflows = workflow_manager

    async def check(self, image: ProviderImage, keywords: list[str]) -> tuple[list[str], list[str]] | None:
        if image.provider != "comfyui":
            return None
        caption = (await describe_image(self.client, self.workflows, image.locator)).lower()
        if not caption:
            return None
        found = [k for k in keywords if k.lower() in caption]
        return found, [k for k in keywords if k not in found]
