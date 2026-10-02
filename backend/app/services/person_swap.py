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

Com o Qwen (workflows/qwen-person-swap.json) a mesma passada acha tambem as
maos dela (ficam as da foto original, com o que ela segura) e o rosto na
foto da persona (o image 2 vai recortado nele).
"""
from __future__ import annotations

import ast
import json
import re
import logging
from dataclasses import asdict, dataclass, field
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
MALE = re.compile(r"\b(?:man|men|boy|guy|male|he|his|him|beard|bearded)\b", re.I)

Box = tuple[float, float, float, float]


@dataclass
class PersonSwapPlan:
    points_pos: str
    points_neg: str
    head: tuple[int, int, int, int]  # x, y, largura, altura (no tamanho de trabalho)
    caption: str
    woman: Box | None
    face: Box | None
    # Maos na foto (com o que ela segura): ficam da foto original no Qwen.
    hands: list[Box] = field(default_factory=list)
    # Rosto na foto da persona (no tamanho dela): o Qwen recebe so esse recorte.
    persona_face: Box | None = None
    # Rostos dos outros (o retangulo vermelho nunca os envolve).
    others: list[Box] = field(default_factory=list)


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


def face_genders(man_boxes: list[Box], face_boxes: list[Box], captions: dict[int, str]) -> tuple[set[int], set[int]]:
    """(rostos de mulher, rostos de homem). Descricao so de mulher/so de homem
    conta 2; caixa "man" com um rosto so dentro marca esse rosto como dele.
    Descricao com os dois ("a man and a woman") nao decide nada."""
    score = {i: 0 for i in range(len(face_boxes))}
    for i, text in captions.items():
        female, male = bool(FEMALE.search(text)), bool(MALE.search(text))
        if female and not male:
            score[i] += 2
        elif male and not female:
            score[i] -= 2
    for m in man_boxes:
        inside = [i for i, f in enumerate(face_boxes) if _inside(_center(f), m)]
        if len(inside) == 1:
            score[inside[0]] -= 2
    return {i for i, s in score.items() if s > 0}, {i for i, s in score.items() if s < 0}


def _box_from_face(face: Box, width: int, height: int) -> Box:
    """Caixa dela estimada pelo rosto (a caixa "woman" estava no homem)."""
    fw, fh = face[2] - face[0], face[3] - face[1]
    return (
        max(0.0, face[0] - fw * 1.5), max(0.0, face[1] - fh * 0.6),
        min(float(width), face[2] + fw * 1.5), min(float(height), face[3] + fh * 6),
    )


def plan_regions(
    woman_boxes: list[Box],
    face_boxes: list[Box],
    female_faces: set[int] | None = None,
    male_faces: set[int] | None = None,
    width: int = 0,
    height: int = 0,
) -> tuple[Box | None, Box | None, list[Box]]:
    """(caixa dela, rosto dela, rostos dos outros). female_faces/male_faces =
    indices dos rostos que sao dela/dele (ver face_genders)."""
    female = female_faces or set()
    male = male_faces or set()
    woman = max(woman_boxes, key=_area) if woman_boxes else None
    if woman is not None:
        mine = [i for i, f in enumerate(face_boxes) if _inside(_center(f), woman) and i not in male]
        if female and not any(i in female for i in mine):
            # o rosto dela esta fora da caixa "woman": a caixa era a dele
            her = max(female, key=lambda i: _area(face_boxes[i]))
            woman, mine = _box_from_face(face_boxes[her], width or 10**6, height or 10**6), [her]
    elif female:
        her = max(female, key=lambda i: _area(face_boxes[i]))
        woman, mine = _box_from_face(face_boxes[her], width or 10**6, height or 10**6), [her]
    else:
        return None, None, face_boxes
    if len(mine) > 1:
        mine = [i for i in mine if i in female] or mine
    face_i = max(mine, key=lambda i: _area(face_boxes[i])) if mine else None
    face = face_boxes[face_i] if face_i is not None else None
    others = [f for i, f in enumerate(face_boxes) if i != face_i]
    return trim_to_her(woman, face, others), face, others


def trim_to_her(woman: Box, face: Box | None, others: list[Box]) -> Box:
    """O Florence muitas vezes da UMA caixa "woman" para o casal inteiro: o
    retangulo vermelho envolvia os dois e o Qwen trocava o homem. A caixa
    para antes do rosto de quem esta ao lado dela."""
    x1, y1, x2, y2 = woman
    # sem o rosto dela (olhando para baixo): o lado dela e o centro da caixa
    fcx = _center(face)[0] if face is not None else (x1 + x2) / 2
    for o in others:
        ocx = _center(o)[0]
        # rosto dele um pouco acima da caixa dela tambem conta (ele e mais alto)
        if not (x1 <= ocx <= x2 and o[3] > y1 and o[1] < y2):
            continue
        if ocx > fcx:
            x2 = min(x2, max(face[2], o[0]) if face is not None else o[0])
        else:
            x1 = max(x1, min(face[0], o[2]) if face is not None else o[2])
    return x1, y1, x2, y2


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
        # sob o rosto dela (o centro da caixa caia entre os dois no abraco)
        cx = _center(face)[0] if face is not None else (woman[0] + woman[2]) / 2
        pos.append({"x": int(cx), "y": int(woman[1] + (woman[3] - woman[1]) * 0.45)})
    if face is not None:
        fx, fy = _center(face)
        pos.append({"x": int(fx), "y": int(fy)})
        # topo da cabeca (cabelo): sem ele o SAM deixava o cabelo solto de
        # fora e sobrava mecha loira do lado da persona
        pos.append({"x": int(fx), "y": max(0, int(face[1] - (face[3] - face[1]) * 0.15))})
    if not pos:
        pos.append({"x": width // 2, "y": height // 2})
    neg = [{"x": int(_center(f)[0]), "y": int(_center(f)[1])} for f in others]
    if not neg:
        # O SAM2 quebra com a lista de negativos vazia: o canto mais longe dela
        # (fundo) serve de negativo inofensivo.
        cx, cy = (pos[0]["x"], pos[0]["y"])
        corners = [(4, 4), (width - 5, 4), (4, height - 5), (width - 5, height - 5)]
        far = max(corners, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
        neg = [{"x": far[0], "y": far[1]}]
    return json.dumps(pos), json.dumps(neg)


async def _run_texts(comfyui: ComfyUIClient, graph: dict[str, Any]) -> dict[str, str]:
    entry = await comfyui.wait_for_completion(await comfyui.queue_prompt(graph))
    out: dict[str, str] = {}
    for node_id, output in entry.get("outputs", {}).items():
        if output.get("text"):
            out[node_id] = str(output["text"][0])
    return out


async def plan_person_swap(
    comfyui: ComfyUIClient, image: str, width: int, height: int, persona_image: str | None = None
) -> PersonSwapPlan:
    base = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "10": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
    }
    woman_boxes: list[Box] = []
    face_boxes: list[Box] = []
    hand_boxes: list[Box] = []
    man_boxes: list[Box] = []
    persona_faces: list[Box] = []
    detect: dict[str, Any] = {
        **base,
        "2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": FLORENCE, "precision": "fp16"}},
        "3": _florence(["10", 0], "woman"),
        "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 3]}},
        "5": _florence(["10", 0], "face"),
        "6": {"class_type": "PreviewAny", "inputs": {"source": ["5", 3]}},
        "7": _florence(["10", 0], "hand"),
        "8": {"class_type": "PreviewAny", "inputs": {"source": ["7", 3]}},
        "11": _florence(["10", 0], "man"),
        "12": {"class_type": "PreviewAny", "inputs": {"source": ["11", 3]}},
    }
    if persona_image:
        detect["20"] = {"class_type": "LoadImage", "inputs": {"image": persona_image}}
        detect["21"] = _florence(["20", 0], "face")
        detect["22"] = {"class_type": "PreviewAny", "inputs": {"source": ["21", 3]}}
    try:
        texts = await _run_texts(comfyui, detect)
        woman_boxes, face_boxes = _boxes(texts.get("4", "")), _boxes(texts.get("6", ""))
        hand_boxes, persona_faces = _boxes(texts.get("8", "")), _boxes(texts.get("22", ""))
        man_boxes = _boxes(texts.get("12", ""))
    except ComfyUIError as exc:
        log.warning("Deteccao do pack falhou: %s", exc)

    # De quem e cada rosto. Casal colado (beijo, rosto com rosto): o Florence
    # dava a caixa "woman" no homem e o rosto dele virava o dela (o Qwen
    # trocava o homem). Dois sinais: a descricao de um recorte JUSTO no rosto
    # (com folga o recorte pegava os dois e dizia "a man and a woman") e a
    # caixa "man" quando ela tem um rosto so.
    female_faces, male_faces = face_genders(man_boxes, face_boxes, {})
    if face_boxes:
        graph: dict[str, Any] = {**base, "2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": FLORENCE, "precision": "fp16"}}}
        for i, (fx1, fy1, fx2, fy2) in enumerate(face_boxes):
            pw, ph = (fx2 - fx1) * 0.05, (fy2 - fy1) * 0.05
            x1, y1 = max(0, int(fx1 - pw)), max(0, int(fy1 - ph))
            x2, y2 = min(width, int(fx2 + pw)), min(height, int(fy2 + ph))
            graph[f"c{i}"] = {"class_type": "ImageCrop", "inputs": {"image": ["10", 0], "width": max(16, x2 - x1), "height": max(16, y2 - y1), "x": x1, "y": y1}}
            graph[f"f{i}"] = _florence([f"c{i}", 0], "", task="caption")
            graph[f"p{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [f"f{i}", 2]}}
        try:
            texts = await _run_texts(comfyui, graph)
            female_faces, male_faces = face_genders(
                man_boxes, face_boxes, {i: texts.get(f"p{i}", "") for i in range(len(face_boxes))}
            )
        except ComfyUIError as exc:
            log.warning("Descricao dos rostos do pack falhou: %s", exc)

    woman, face, others = plan_regions(woman_boxes, face_boxes, female_faces, male_faces, width, height)
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

    plan = PersonSwapPlan(
        points_pos=pos, points_neg=neg, head=head, caption=caption, woman=woman, face=face,
        hands=her_hands(hand_boxes, woman, face),
        persona_face=max(persona_faces, key=_area) if persona_faces else None,
        others=others,
    )
    _debug({"image": image, "size": [width, height], "others": others, **asdict(plan)})
    return plan


# Da descricao do recorte, so o trecho sobre ELA ("woman with short blonde
# hair, wearing a beige sweater") - a frase "about ..." descrevia a cena
# ("a happy couple", "a smiling waiter") e o Qwen trocava a pessoa errada.
_HER_PHRASE = re.compile(
    r"\b(?:a|an|the)\s+((?:[a-z-]+\s+){0,2}?woman\b[^.]*?)"
    r"(?=,?\s+(?:and|while|as|who\s+is\s+looking\s+at)\s+(?:a|an|the|his|her)\s+man\b|[.;]|$)",
    re.I,
)
_WEARING = re.compile(
    r"\bwearing\s+([^.;]+?)"
    r"(?=,?\s+(?:and\s+)?(?:she|her|is|has|with)\b|,?\s+and\s+(?:a|an|the|his)\b|\s+\w+ing\b|[.;,]|$)",
    re.I,
)
_AGE_WORDS = re.compile(r"\b(?:young|elderly|older|middle-aged|pregnant)\s+", re.I)
# Onde a frase dela deixa de descrever ela e passa a falar do que ela faz.
_HER_CUT = re.compile(
    r",?\s+(?:wearing|sitting|standing|holding|appears|looking|leaning|who|is|smiling|laughing|and|while|on|in|at)\b",
    re.I,
)


def describe_her(caption: str) -> tuple[str, str]:
    """(como ela e, a roupa) a partir da descricao do recorte. Da frase dela
    so a parte que descreve ela ("woman with long, wavy blonde hair"): o
    resto ("appears to be looking...") falava da cena."""
    phrases = [m.group(1) for m in _HER_PHRASE.finditer(caption or "")]
    # a frase com o cabelo dela e a que mais distingue ("woman with long, wavy blonde hair")
    phrase = next((p for p in phrases if "hair" in p.lower()), phrases[0] if phrases else "")
    desc = _AGE_WORDS.sub("", _HER_CUT.split(phrase)[0]).strip(" ,")[:140] if phrase else "woman"
    wearing = _WEARING.search(caption or "")
    clothes = wearing.group(1).strip(" ,")[:120] if wearing else ""
    return desc, clothes


def qwen_prompt(plan: PersonSwapPlan, crop: tuple[int, int, int, int]) -> str:
    """Instrucao do Qwen-Image-Edit: diz QUEM trocar (lado do recorte + como
    ela e) - so com "the woman" ele trocava o homem - e que do image 2 so vem
    rosto, cabelo e pele; a roupa e a dela no image 1 (vinha a da foto 2)."""
    side = ""
    if plan.woman is not None:
        cx = ((plan.woman[0] + plan.woman[2]) / 2 - crop[0]) / max(1, crop[2])
        side = " on the left side of image 1" if cx < 0.4 else " on the right side of image 1" if cx > 0.6 else " in the middle of image 1"
    # Sem a descricao do cabelo dela ("woman with blonde curly hair"): com ela
    # o Qwen mantinha o cabelo loiro. Quem trocar fica pelo retangulo + lado.
    _, clothes = describe_her(plan.caption)
    keep_clothes = f" She keeps the same clothes ({clothes}) from image 1." if clothes else " She keeps the same clothes from image 1."
    mood = _EXPRESSION.findall(plan.caption or "")
    gaze = _GAZE.findall(plan.caption or "")
    expression = " She is " + " and ".join(p.lower() for p in (mood[:1] + gaze[:1])) + "." if mood or gaze else ""
    return (
        f"Replace the woman inside the red rectangle{side} with the woman from image 2, and remove the red "
        "rectangle. Her head and hair in image 1 are covered by a gray blur: paint there the head of the woman "
        "from image 2, with her face, her long dark brown hair and her tanned skin - clearly the same person as "
        "image 2. Not her clothes. Same pose, arms, hands, gesture, head position and head angle as the woman in "
        f"image 1, holding the same objects.{expression}{keep_clothes} Do not change anyone outside the red "
        "rectangle. Keep the background, objects, lighting and framing exactly the same. Sharp, detailed, "
        "photorealistic photo."
    )


# Expressao dela na descricao: com a cabeca escondida o Qwen nao ve o sorriso.
_EXPRESSION = re.compile(
    r"\b(smiling broadly|smiling|laughing|grinning|serious|surprised)\b", re.I
)
# Para onde ela olha (com a cabeca escondida o Qwen virava o rosto para a camera).
_GAZE = re.compile(
    r"\b(looking (?:down|up|away|to the (?:left|right|side)|at (?:him|the man|each other|her phone|the phone|the "
    r"laptop|the screen|the camera|something)))\b",
    re.I,
)


QWEN_MODEL = "qwen_image_edit_2511_fp8mixed.safetensors"
# Foto da persona para o Qwen: so a parte de cima (rosto e cabelo) - com a
# roupa ele vestia a pessoa com ela. Reserva quando o rosto dela nao e achado.
PERSONA_HEAD_FRACTION = 0.47
# Recorte pelo rosto da persona (larguras/alturas do rosto): cabelo dos lados
# e em cima, ate o pescoco embaixo - rosto grande no image 2 = identidade.
PERSONA_SIDE, PERSONA_TOP, PERSONA_BOTTOM = 1.0, 0.6, 1.0

# A troca roda num recorte em volta dela, nao na foto inteira: o Qwen trabalha
# em ~1 MP, e com a foto inteira o rosto dela chegava pequeno (identidade
# fraca) e voltava ampliado (borrado no meio de uma foto nitida).
CROP_PAD = 0.3  # da caixa dela, de cada lado
QWEN_PIXELS = 1024 * 1024
FULL_CROP = 0.8  # recorte maior que isso da foto: usa a foto inteira

# Do que o Qwen devolve so a cabeca e o cabelo voltam (zona em larguras e
# alturas do rosto); corpo, maos e objetos ficam os da foto original - colar
# o corpo inteiro trazia maos redesenhadas sem o que ela segurava.
ZONE_SIDE = 0.9
ZONE_TOP = 0.5
ZONE_DROP = 2.6  # abaixo do queixo: cabelo longo da persona ate o peito
# Caixa da mao alargada para levar junto o que ela segura (celular, copo).
HAND_PAD = 0.35
MAX_HANDS = 4


def her_hands(hands: list[Box], woman: Box | None, face: Box | None) -> list[Box]:
    """Maos dentro da caixa dela (com folga: a mao na boca sai da caixa no
    close). A mao no rosto tambem fica a original - comendo, a comida sumia
    e o Qwen desenhava outro objeto - mas sem folga (ver qwen_swap_params)."""
    if woman is not None:
        mx, my = (woman[2] - woman[0]) * 0.25, (woman[3] - woman[1]) * 0.1
        woman = (woman[0] - mx, woman[1] - my, woman[2] + mx, woman[3] + my)
    kept = [h for h in sorted(hands, key=_area, reverse=True) if woman is None or _inside(_center(h), woman)]
    return kept[:MAX_HANDS]


def _near_face(h: Box, face: Box | None) -> bool:
    if face is None:
        return False
    fw, fh = face[2] - face[0], face[3] - face[1]
    return _inside(_center(h), (face[0] - fw * 0.3, face[1] - fh * 0.3, face[2] + fw * 0.3, face[3] + fh * 0.3))


def _clamp_box(x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(width, int(x2)), min(height, int(y2))
    return x1, y1, max(16, x2 - x1), max(16, y2 - y1)


def zone_box(plan: PersonSwapPlan, width: int, height: int) -> tuple[int, int, int, int]:
    if plan.face is not None:
        fx1, fy1, fx2, fy2 = plan.face
        fw, fh = fx2 - fx1, fy2 - fy1
        x1, x2 = fx1 - fw * ZONE_SIDE, fx2 + fw * ZONE_SIDE
        if plan.woman is not None:
            # cabelo volumoso passa da largura do rosto: vai ate a caixa dela
            # (ja cortada antes do rosto do outro) - sobrava cabelo loiro do lado
            x1, x2 = min(x1, plan.woman[0]), max(x2, plan.woman[2])
        return _clamp_box(x1, fy1 - fh * ZONE_TOP, x2, fy2 + fh * ZONE_DROP, width, height)
    if plan.woman is not None:
        wx1, wy1, wx2, wy2 = plan.woman
        return _clamp_box(wx1, wy1, wx2, wy1 + (wy2 - wy1) * 0.45, width, height)
    return _clamp_box(width * 0.25, 0, width * 0.75, height * 0.5, width, height)


def crop_box(plan: PersonSwapPlan, width: int, height: int) -> tuple[int, int, int, int]:
    """Recorte que vai para o Qwen: ela, o retangulo vermelho e um pouco de
    cena em volta (ele precisa ver o abraco para manter a pose)."""
    zx, zy, zw, zh = zone_box(plan, width, height)
    if plan.woman is None:
        return 0, 0, width, height
    x1, y1 = min(plan.woman[0], zx), min(plan.woman[1], zy)
    x2, y2 = max(plan.woman[2], zx + zw), max(plan.woman[3], zy + zh)
    pad_x = max(RING_MARGIN + 32, (x2 - x1) * CROP_PAD)
    pad_y = max(RING_MARGIN + 32, (y2 - y1) * CROP_PAD)
    crop = _clamp_box(x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y, width, height)
    if crop[2] * crop[3] > FULL_CROP * width * height:
        return 0, 0, width, height
    return crop


def qwen_size(w: int, h: int) -> tuple[int, int]:
    """Tamanho de trabalho do Qwen com a mesma proporcao do recorte (o
    FluxKontextImageScale arredondava para outra proporcao e a colagem
    voltava esticada)."""
    scale = (QWEN_PIXELS / (w * h)) ** 0.5
    return max(256, round(w * scale / 16) * 16), max(256, round(h * scale / 16) * 16)


def persona_crop(plan: PersonSwapPlan, size: tuple[int, int]) -> tuple[int, int, int, int]:
    pw, ph = size
    if plan.persona_face is None:
        return 0, 0, pw, int(ph * PERSONA_HEAD_FRACTION)
    fx1, fy1, fx2, fy2 = plan.persona_face
    fw, fh = fx2 - fx1, fy2 - fy1
    return _clamp_box(fx1 - fw * PERSONA_SIDE, fy1 - fh * PERSONA_TOP, fx2 + fw * PERSONA_SIDE, fy2 + fh * PERSONA_BOTTOM, pw, ph)


def swap_face_box(plan: PersonSwapPlan, width: int, height: int) -> tuple[int, int, int, int] | None:
    """Rosto dela (alargado) para a correcao de rosto depois da troca."""
    if plan.face is None:
        return None
    fx1, fy1, fx2, fy2 = plan.face
    fw, fh = fx2 - fx1, fy2 - fy1
    return _clamp_box(fx1 - fw * 0.15, fy1 - fh * 0.15, fx2 + fw * 0.15, fy2 + fh * 0.1, width, height)


def qwen_swap_params(plan: PersonSwapPlan, width: int, height: int, persona_size: tuple[int, int]) -> dict[str, Any]:
    """Parametros do workflows/qwen-person-swap.json alem da cena e do prompt."""
    crop = crop_box(plan, width, height)
    cx, cy, cw, ch = crop
    qw, qh = qwen_size(cw, ch)
    zx, zy, zw, zh = zone_box(plan, width, height)
    px, py, pw, ph = persona_crop(plan, persona_size)
    params: dict[str, Any] = {
        "CROP_X": cx, "CROP_Y": cy, "CROP_W": cw, "CROP_H": ch,
        "QWEN_W": qw, "QWEN_H": qh,
        "ZONE_X": zx, "ZONE_Y": zy, "ZONE_W": zw, "ZONE_H": zh,
        "PERSONA_X": px, "PERSONA_Y": py, "PERSONA_W": pw, "PERSONA_H": ph,
        "PROMPT": qwen_prompt(plan, crop),
        **ring_params(plan, crop),
    }
    for i in range(MAX_HANDS):
        n = i + 1
        if i < len(plan.hands):
            hx1, hy1, hx2, hy2 = plan.hands[i]
            # perto do rosto a folga pegaria um pedaco do rosto antigo
            pad = 0.05 if _near_face(plan.hands[i], plan.face) else HAND_PAD
            pad_w, pad_h = (hx2 - hx1) * pad, (hy2 - hy1) * pad
            hx, hy, hw, hh = _clamp_box(hx1 - pad_w, hy1 - pad_h, hx2 + pad_w, hy2 + pad_h, width, height)
            params.update({f"HAND{n}_X": hx, f"HAND{n}_Y": hy, f"HAND{n}_W": hw, f"HAND{n}_H": hh, f"HAND{n}_V": 1.0})
        else:
            params.update({f"HAND{n}_X": 0, f"HAND{n}_Y": 0, f"HAND{n}_W": 16, f"HAND{n}_H": 16, f"HAND{n}_V": 0.0})
    return params


def image_size(content: bytes) -> tuple[int, int] | None:
    """(largura, altura) de um PNG ou JPEG sem abrir a imagem (o backend nao tem Pillow)."""
    if content[:8] == b"\x89PNG\r\n\x1a\n" and len(content) >= 24:
        return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    if content[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(content):
            if content[i] != 0xFF:
                i += 1
                continue
            marker = content[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(content[i + 7:i + 9], "big"), int.from_bytes(content[i + 5:i + 7], "big")
            i += 2 + int.from_bytes(content[i + 2:i + 4], "big")
    return None


async def qwen_available(comfyui: ComfyUIClient) -> bool:
    try:
        return QWEN_MODEL in await comfyui.list_diffusion_models()
    except ComfyUIError:
        return False


RING_MARGIN = 96  # maior que o HAIR_ROOM da colagem: a linha nunca entra no que volta
RING_THICKNESS = 8


def ring_params(plan: PersonSwapPlan, crop: tuple[int, int, int, int]) -> dict[str, int]:
    """Retangulo vermelho em volta dela no image 1 do Qwen (coordenadas do
    recorte): em abraco, so a descricao nao bastava e ele trocava o homem.
    Fica fora da area colada de volta, entao nao aparece no resultado."""
    cx, cy, width, height = crop
    if plan.woman is None:
        x1, y1, x2, y2 = 0, 0, width, height
    else:
        wx1, wy1, wx2, wy2 = trim_to_her(
            (plan.woman[0] - RING_MARGIN, plan.woman[1] - RING_MARGIN, plan.woman[2] + RING_MARGIN, plan.woman[3] + RING_MARGIN),
            plan.face, plan.others,
        )
        x1, y1 = max(0, int(wx1) - cx), max(0, int(wy1) - cy)
        x2, y2 = min(width, int(wx2) - cx), min(height, int(wy2) - cy)
    t = RING_THICKNESS
    w, h = max(2 * t + 2, x2 - x1), max(2 * t + 2, y2 - y1)
    return {
        "RING_X": x1, "RING_Y": y1, "RING_W": w, "RING_H": h,
        "RING_IX": x1 + t, "RING_IY": y1 + t, "RING_IW": w - 2 * t, "RING_IH": h - 2 * t,
    }


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
