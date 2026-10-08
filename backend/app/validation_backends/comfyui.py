"""Medidores do Persona Engine V1 no ComfyUI do pod (os mesmos do benchmark).

ComfyImageAnalyzer: num grafo so, todos os rostos (no LunaFaces: InsightFace
antelopev2, semelhanca ArcFace com a master_face, idade, sexo) e todos os
corpos (DWPose, pontos de 18 juntas). ~8 s por imagem no benchmark.

FlorenceTextReader: OCR do Florence-2 para achar o gatilho da LoRA escrito
na cena. Desligado por padrao (config/persona_engine.json) - nao foi medido
no benchmark e carrega mais um modelo na GPU.
"""
from __future__ import annotations

import ast
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError
from app.core.validation.analysis import DetectedBody, DetectedFace, ImageAnalysis
from app.providers.base import ProviderImage, ReferenceImage

DWPOSE = {"detect_hand": "enable", "detect_body": "enable", "detect_face": "disable", "resolution": 1024,
          "bbox_detector": "yolox_l.onnx", "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
          "scale_stick_for_xinsr_cn": "disable"}
NODES = ("LunaFaces", "DWPreprocessor", "PreviewAny")


def parse_pose(text: str) -> list[list[tuple[float, float, float]]]:
    try:
        data = json.loads(text)
    except ValueError:
        data = ast.literal_eval(text)
    frame = data[0] if isinstance(data, list) else data
    cw, ch = frame.get("canvas_width", 1), frame.get("canvas_height", 1)
    people = []
    for person in frame.get("people", []):
        pts = person.get("pose_keypoints_2d") or []
        triples = [tuple(float(v) for v in pts[i:i + 3]) for i in range(0, len(pts), 3)]
        if triples and max(max(t[0], t[1]) for t in triples) <= 1.0:
            triples = [(x * cw, y * ch, c) for x, y, c in triples]
        people.append(triples)
    return people


def parse_hands(text: str) -> list[list[list[tuple[float, float, float]]]]:
    """Maos de cada pessoa do DWPose (esquerda e direita, 21 pontos cada) - spec 46.4/46.11."""
    try:
        data = json.loads(text)
    except ValueError:
        data = ast.literal_eval(text)
    frame = data[0] if isinstance(data, list) else data
    cw, ch = frame.get("canvas_width", 1), frame.get("canvas_height", 1)
    out = []
    for person in frame.get("people", []):
        hands = []
        for key in ("hand_left_keypoints_2d", "hand_right_keypoints_2d"):
            pts = person.get(key) or []
            triples = [tuple(float(v) for v in pts[i:i + 3]) for i in range(0, len(pts), 3)]
            if triples and max(max(t[0], t[1]) for t in triples) <= 1.0:
                triples = [(x * cw, y * ch, c) for x, y, c in triples]
            if triples:
                hands.append(triples)
        out.append(hands)
    return out


def to_body(kp: list[tuple[float, float, float]], height: int) -> DetectedBody | None:
    pts = [(x, y) for x, y, c in kp[:18] if c and c > 0.3]
    visible = sum(1 for x in kp[:14] if x[2] > 0.3)
    if visible < 4:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return DetectedBody(kp, (min(xs), min(ys), max(xs), max(ys)), round((max(ys) - min(ys)) / height, 3), visible)


def to_face(data: dict[str, Any]) -> DetectedFace:
    return DetectedFace(
        bbox=tuple(float(v) for v in data["bbox"]),  # type: ignore[arg-type]
        similarity=float(data["sim"]) if data.get("sim") is not None else None,
        age=float(data["age"]) if data.get("age") else None,
        sex=data.get("sex"), det_score=float(data.get("score", 0.0)), yaw=data.get("yaw"),
        kps=[(float(x), float(y)) for x, y in data.get("kps", [])],
    )


class ComfyImageAnalyzer:
    def __init__(self, client: ComfyUIClient, keep_alive: dict[str, Any] | None = None) -> None:
        self.client = client
        self._uploaded: dict[str, str] = {}
        # V2: nos extras (ex.: o checkpoint das passadas) que entram no grafo da
        # analise so para o cache do ComfyUI nao descartar o modelo entre passadas.
        self.keep_alive = dict(keep_alive or {})

    async def check_ready(self) -> list[str]:
        try:
            info = await self.client.get_object_info()
        except ComfyUIError as exc:
            return [f"ComfyUI fora do ar: {exc}"]
        return [f"No {n} nao esta no pod (sem ele nao ha validacao)." for n in NODES if n not in info]

    async def _upload(self, name: str, content: bytes) -> str:
        key = hashlib.sha1(content).hexdigest()
        if key not in self._uploaded:
            self._uploaded[key] = await self.client.upload_image(f"val_{key[:16]}{Path(name).suffix or '.png'}", content)
        return self._uploaded[key]

    async def _image_name(self, image: ProviderImage) -> str:
        if image.provider == "comfyui":
            return image.locator
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.get(image.url)
            resp.raise_for_status()
        return await self._upload(Path(image.locator).name or "imagem.png", resp.content)

    async def analyze_reference(self, reference: ReferenceImage, master_face: ReferenceImage) -> ImageAnalysis:
        name = await self._upload(reference.filename, reference.content)
        return await self.analyze(ProviderImage("comfyui", name, ""), master_face)

    async def analyze(self, image: ProviderImage, master_face: ReferenceImage) -> ImageAnalysis:
        start = time.monotonic()
        name = await self._image_name(image)
        master = await self._upload(master_face.filename, master_face.content)
        graph = {
            "1": {"class_type": "LoadImage", "inputs": {"image": name}},
            "2": {"class_type": "LoadImage", "inputs": {"image": master}},
            "lf": {"class_type": "LunaFaces", "inputs": {"image": ["1", 0], "det_size": 1024, "reference": ["2", 0]}},
            "dw": {"class_type": "DWPreprocessor", "inputs": {"image": ["1", 0], **DWPOSE}},
            "dwp": {"class_type": "PreviewAny", "inputs": {"source": ["dw", 1]}},
            **self.keep_alive,
        }
        entry = await self.client.wait_for_completion(await self.client.queue_prompt(graph))
        texts = {k: str(v["text"][0]) for k, v in entry.get("outputs", {}).items() if v.get("text")}
        width, height = image.width or 0, image.height or 0
        people = parse_pose(texts["dwp"]) if texts.get("dwp") else []
        if not height and people:
            height = 1216  # sem tamanho informado: so afeta a fracao de altura
        hands = parse_hands(texts["dwp"]) if texts.get("dwp") else []
        bodies = []
        for i, kp in enumerate(people):
            b = to_body(kp, height or 1)
            if b:
                b.hands = hands[i] if i < len(hands) else []
                bodies.append(b)
        faces = [to_face(f) for f in json.loads(texts.get("lf", "[]"))]
        return ImageAnalysis(width, height, faces, bodies, round(time.monotonic() - start, 2))


class FlorenceTextReader:
    """OCR do Florence-2 large (task 'ocr')."""

    def __init__(self, client: ComfyUIClient) -> None:
        self.client = client

    async def read(self, image: ProviderImage) -> str | None:
        graph = {
            "1": {"class_type": "LoadImage", "inputs": {"image": image.locator}},
            "2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": "microsoft/Florence-2-large", "precision": "fp16"}},
            "3": {"class_type": "Florence2Run", "inputs": {
                "image": ["1", 0], "florence2_model": ["2", 0], "text_input": "", "task": "ocr", "fill_mask": False,
                "keep_model_loaded": False, "max_new_tokens": 256, "num_beams": 3, "do_sample": False,
                "output_mask_select": "", "seed": 1}},
            "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 2]}},
        }
        try:
            entry = await self.client.wait_for_completion(await self.client.queue_prompt(graph))
        except ComfyUIError:
            return None
        for out in entry.get("outputs", {}).values():
            if out.get("text"):
                return str(out["text"][0])
        return None
