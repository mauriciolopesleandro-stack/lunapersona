"""Preparo do pack com a persona (trocar so a pessoa de uma foto).

Antes do redesenho (workflows/chroma-person-swap-lora.json), duas passadas
rapidas do Florence-2 na foto ja no tamanho de trabalho:

1. acha a mulher ("woman") e os rostos ("face"). O rosto dela e o que tem o
   centro dentro da caixa dela - o do outro (marido, amigo) vira ponto
   negativo do SAM2 e nunca entra na area redesenhada com forca;
2. descreve SO ela (recorte da caixa dela no PromptGen): a descricao da foto
   inteira falava do homem e o modelo desenhava um homem no lugar dela.

Tudo vira parametro do workflow (pontos do SAM2 e a caixa da cabeca, ja
alargada para caber o cabelo da persona). Na falha de qualquer deteccao,
volta para estimativas pela caixa da pessoa ou o centro da foto.
"""
from __future__ import annotations

import ast
import json
import re
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError

log = logging.getLogger(__name__)

FLORENCE = "microsoft/Florence-2-large"
PROMPTGEN = "MiaoshouAI/Florence-2-large-PromptGen-v2.0"
# Cabeca: caixa do rosto alargada para caber o cabelo longo da persona.
# Maior que isso pegava peito e maos (forca cheia: o gesto mudava).
HEAD_SIDE = 0.6  # largura do rosto a mais de cada lado
HEAD_TOP = 0.5  # altura do rosto acima dele
HEAD_BOTTOM = 0.8  # abaixo (pescoco; o cabelo longo desce pelo corpo, que e redesenhado de leve)
FEMALE = re.compile(r"\b(?:woman|women|girl|lady|female|she|her)\b", re.I)

Box = tuple[float, float, float, float]


@dataclass
class PersonSwapPlan:
    points_pos: str
    points_neg: str
    head: tuple[int, int, int, int]  # x, y, largura, altura (no tamanho de trabalho)
    caption: str
    woman: Box | None
    face: Box | None


def _florence(node_image: list, text: str, task: str = "caption_to_phrase_grounding", model: str = "2") -> dict[str, Any]:
    return {
        "class_type": "Florence2Run",
        "inputs": {
            "image": node_image, "florence2_model": [model, 0], "text_input": text, "task": task,
            "fill_mask": False, "keep_model_loaded": False, "max_new_tokens": 512, "num_beams": 3,
            "do_sample": False, "output_mask_select": "", "seed": 1,
        },
    }


def _boxes(text: str) -> list[Box]:
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
    if boxes and isinstance(boxes[0], list) and boxes[0] and isinstance(boxes[0][0], list):
        boxes = boxes[0]
    return [tuple(float(v) for v in b[:4]) for b in boxes if len(b) >= 4]  # type: ignore[misc]


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside(point: tuple[float, float], b: Box) -> bool:
    return b[0] <= point[0] <= b[2] and b[1] <= point[1] <= b[3]


def _center(b: Box) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def plan_regions(
    woman_boxes: list[Box], face_boxes: list[Box], female_faces: set[int] | None = None
) -> tuple[Box | None, Box | None, list[Box]]:
    """(caixa dela, rosto dela, rostos dos outros). female_faces = indices dos
    rostos que a descricao do recorte diz ser de mulher (abraco: a caixa dela
    tambem pega o rosto dele)."""
    woman = max(woman_boxes, key=_area) if woman_boxes else None
    if woman is None:
        return None, None, face_boxes
    mine = [i for i, f in enumerate(face_boxes) if _inside(_center(f), woman)]
    if female_faces is not None and len(mine) > 1:
        mine = [i for i in mine if i in female_faces] or mine
    face_i = max(mine, key=lambda i: _area(face_boxes[i])) if mine else None
    face = face_boxes[face_i] if face_i is not None else None
    others = [f for i, f in enumerate(face_boxes) if i != face_i]
    return woman, face, others


def head_box(woman: Box | None, face: Box | None, width: int, height: int) -> tuple[int, int, int, int]:
    if face is None:
        if woman is None:
            x1, y1, x2, y2 = width * 0.3, 0.0, width * 0.7, height * 0.35
        else:
            # sem rosto: o quarto de cima da pessoa
            wx1, wy1, wx2, wy2 = woman
            x1, y1, x2, y2 = wx1, wy1, wx2, wy1 + (wy2 - wy1) * 0.3
    else:
        fx1, fy1, fx2, fy2 = face
        fw, fh = fx2 - fx1, fy2 - fy1
        x1, x2 = fx1 - fw * HEAD_SIDE, fx2 + fw * HEAD_SIDE
        y1, y2 = fy1 - fh * HEAD_TOP, fy2 + fh * HEAD_BOTTOM
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(width, int(x2)), min(height, int(y2))
    return x1, y1, max(16, x2 - x1), max(16, y2 - y1)


def sam_points(woman: Box | None, face: Box | None, others: list[Box], width: int, height: int) -> tuple[str, str]:
    pos: list[dict[str, int]] = []
    if woman is not None:
        cx = (woman[0] + woman[2]) / 2
        pos.append({"x": int(cx), "y": int(woman[1] + (woman[3] - woman[1]) * 0.45)})
    if face is not None:
        fx, fy = _center(face)
        pos.append({"x": int(fx), "y": int(fy)})
    if not pos:
        pos.append({"x": width // 2, "y": height // 2})
    neg = [{"x": int(_center(f)[0]), "y": int(_center(f)[1])} for f in others]
    return json.dumps(pos), json.dumps(neg)


async def _run_texts(comfyui: ComfyUIClient, graph: dict[str, Any]) -> dict[str, str]:
    entry = await comfyui.wait_for_completion(await comfyui.queue_prompt(graph))
    out: dict[str, str] = {}
    for node_id, output in entry.get("outputs", {}).items():
        if output.get("text"):
            out[node_id] = str(output["text"][0])
    return out


async def plan_person_swap(comfyui: ComfyUIClient, image: str, width: int, height: int) -> PersonSwapPlan:
    base = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "10": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
    }
    woman_boxes: list[Box] = []
    face_boxes: list[Box] = []
    try:
        texts = await _run_texts(comfyui, {
            **base,
            "2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": FLORENCE, "precision": "fp16"}},
            "3": _florence(["10", 0], "woman"),
            "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 3]}},
            "5": _florence(["10", 0], "face"),
            "6": {"class_type": "PreviewAny", "inputs": {"source": ["5", 3]}},
        })
        woman_boxes, face_boxes = _boxes(texts.get("4", "")), _boxes(texts.get("6", ""))
    except ComfyUIError as exc:
        log.warning("Deteccao do pack falhou: %s", exc)

    # Mais de um rosto na caixa dela: o Florence descreve cada um e fica o de mulher.
    female_faces: set[int] | None = None
    woman_box = max(woman_boxes, key=_area) if woman_boxes else None
    candidates = [i for i, f in enumerate(face_boxes) if woman_box and _inside(_center(f), woman_box)]
    if len(candidates) > 1:
        graph: dict[str, Any] = {**base, "2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": FLORENCE, "precision": "fp16"}}}
        for i in candidates:
            fx1, fy1, fx2, fy2 = face_boxes[i]
            pw, ph = (fx2 - fx1) * 0.3, (fy2 - fy1) * 0.3
            x1, y1 = max(0, int(fx1 - pw)), max(0, int(fy1 - ph))
            x2, y2 = min(width, int(fx2 + pw)), min(height, int(fy2 + ph))
            graph[f"c{i}"] = {"class_type": "ImageCrop", "inputs": {"image": ["10", 0], "width": max(16, x2 - x1), "height": max(16, y2 - y1), "x": x1, "y": y1}}
            graph[f"f{i}"] = _florence([f"c{i}", 0], "", task="caption")
            graph[f"p{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [f"f{i}", 2]}}
        try:
            texts = await _run_texts(comfyui, graph)
            female_faces = {i for i in candidates if FEMALE.search(texts.get(f"p{i}", ""))}
        except ComfyUIError as exc:
            log.warning("Descricao dos rostos do pack falhou: %s", exc)

    woman, face, others = plan_regions(woman_boxes, face_boxes, female_faces)
    pos, neg = sam_points(woman, face, others, width, height)
    head = head_box(woman, face, width, height)

    caption = ""
    if woman is not None:
        pad_x, pad_y = (woman[2] - woman[0]) * 0.06, (woman[3] - woman[1]) * 0.04
        x1, y1 = max(0, int(woman[0] - pad_x)), max(0, int(woman[1] - pad_y))
        x2, y2 = min(width, int(woman[2] + pad_x)), min(height, int(woman[3] + pad_y))
        try:
            texts = await _run_texts(comfyui, {
                **base,
                "11": {"class_type": "ImageCrop", "inputs": {"image": ["10", 0], "width": x2 - x1, "height": y2 - y1, "x": x1, "y": y1}},
                "2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": PROMPTGEN, "precision": "fp16"}},
                "3": _florence(["11", 0], "", task="more_detailed_caption"),
                "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 2]}},
            })
            caption = texts.get("4", "").strip()
        except ComfyUIError as exc:
            log.warning("Descricao do recorte do pack falhou: %s", exc)

    plan = PersonSwapPlan(points_pos=pos, points_neg=neg, head=head, caption=caption, woman=woman, face=face)
    _debug({"image": image, "size": [width, height], "others": others, **asdict(plan)})
    return plan


# Ultimas deteccoes no volume: da para desenhar as caixas e conferir por que
# uma foto saiu errada.
DEBUG_LOG = Path("/workspace/luna-pack-debug.jsonl")


def _debug(entry: dict[str, Any]) -> None:
    try:
        if DEBUG_LOG.parent.exists():
            with DEBUG_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
