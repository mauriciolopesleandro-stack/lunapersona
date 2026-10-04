"""Troca de cabeca (rosto + cabelo) numa passada: Qwen-Image-Edit 2511 com a
LoRA BFS Best Face Swap head V5 (workflows/qwen-bfs-head-swap.json; modelos em
scripts/setup_qwen_edit.sh + scripts/setup_fast.sh).

E o que os criadores usam hoje (pesquisa de 2026-10): o modelo refaz a cabeca
inteira com a luz da cena, sem mascaras nem colagem de pixels - o que deixava
borda e mao cortada no pack antigo (person_swap.py). Roupa, pose, maos e
objetos ficam os da imagem de origem.

Com duas pessoas na imagem o modelo nao sabe qual trocar: so um recorte em
volta do rosto escolhido vai para ele e volta colado com borda suave.
"""
from __future__ import annotations

import logging
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError, GenerationOutputImage
from app.services.person_swap import luna_faces
from app.workflow_manager.manager import WorkflowManager

WORKFLOW = "qwen-bfs-head-swap"
BFS_LORA = "bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
MODEL_PIXELS = 1024 * 1024
# Recorte em volta do rosto: lado = 3.6x o rosto, centro um pouco abaixo dele
# (pescoco e ombros entram - o modelo precisa ver onde a cabeca encaixa).
CROP_SIDE = 3.6
CROP_DROP = 0.7
FEATHER = 0.08  # borda suave da colagem, fracao do lado menor do recorte

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]


async def available(comfyui: ComfyUIClient) -> bool:
    """A LoRA BFS esta no pod? (sem ela, quem chama usa o caminho antigo)."""
    try:
        return BFS_LORA in await comfyui.list_loras()
    except ComfyUIError:
        return False


def model_size(width: int, height: int, pixels: int = MODEL_PIXELS) -> tuple[int, int]:
    scale = (pixels / (width * height)) ** 0.5
    return max(256, round(width * scale / 16) * 16), max(256, round(height * scale / 16) * 16)


def crop_box(face: Box, width: int, height: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) do recorte em volta do rosto, dentro da imagem."""
    x1, y1, x2, y2 = face
    side = max(x2 - x1, y2 - y1) * CROP_SIDE
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + (y2 - y1) * CROP_DROP
    left, top = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    right, bottom = int(min(width, cx + side / 2)), int(min(height, cy + side / 2))
    return left, top, right - left, bottom - top


async def faces_in(comfyui: ComfyUIClient, image: str, reference: str | None = None) -> list[dict[str, Any]]:
    """Rostos da imagem (LunaFaces, do maior para o menor); com reference, cada
    um traz "sim" = semelhanca com ela."""
    graph: dict[str, Any] = {"1": {"class_type": "LoadImage", "inputs": {"image": image}}}
    ref = None
    if reference:
        graph["2"] = {"class_type": "LoadImage", "inputs": {"image": reference}}
        ref = ["2", 0]
    return await luna_faces(comfyui, graph, ["1", 0], ref) or []


async def head_swap(
    comfyui: ComfyUIClient,
    workflow_manager: WorkflowManager,
    body: str,
    head: str,
    width: int,
    height: int,
    seed: int,
    face: Box | None = None,
    prefix: str = "luna_headswap",
) -> GenerationOutputImage | None:
    """Poe a cabeca de `head` (foto da persona ou retrato do personagem) em
    `body` ("nome [output]" ou nome do input/), que tem width x height. face =
    rosto a trocar quando ha mais de uma pessoa; sem face, a imagem inteira."""
    if face is not None:
        x, y, w, h = crop_box(face, width, height)
        feather = int(min(w, h) * FEATHER)
    else:
        x, y, w, h, feather = 0, 0, width, height, 0
    mw, mh = model_size(w, h)
    graph = workflow_manager.render(WORKFLOW, {
        "BODY_IMAGE": body, "HEAD_IMAGE": head, "WIDTH": mw, "HEIGHT": mh,
        "CROP_X": x, "CROP_Y": y, "CROP_W": w, "CROP_H": h, "FEATHER": feather,
        "SEED": seed, "FILENAME_PREFIX": prefix,
    })
    try:
        entry = await comfyui.wait_for_completion(await comfyui.queue_prompt(graph))
    except ComfyUIError as exc:
        log.warning("Troca de cabeca falhou: %s", exc)
        return None
    images = comfyui.extract_images(entry)
    return images[0] if images else None
