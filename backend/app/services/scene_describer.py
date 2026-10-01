"""Entende a foto antes de animar: descreve a cena (Florence-2 PromptGen, o
mesmo workflow describe-image da foto de referencia) e, se a pessoa nao
escreveu o movimento, pede ao modelo de chat do pod um movimento natural para
essa cena.

Sem isso o video so tinha o texto digitado: o modelo nao "sabia" quem
segurava o bafometro e o objeto deformava. Qualquer falha (node ou Ollama
fora) devolve vazio e o video segue so com o texto da pessoa.
"""
from __future__ import annotations

import logging

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError
from app.clients.llm_client import ChatMessage, LLMError, OllamaClient
from app.services.reference_caption import clean_reference_caption
from app.workflow_manager.manager import WorkflowManager

log = logging.getLogger(__name__)

DESCRIBE_WORKFLOW = "describe-image"
_MOTION_SYSTEM = (
    "You write the motion prompt for an image-to-video model that animates a photo for 5 seconds. "
    "Read the scene description and reply with ONE short English sentence describing natural, simple motion "
    "that continues this exact scene: who moves, what they do with the objects already in the scene, and their "
    "facial expression. Keep every object in the same hands. Do not add new people, objects or places. "
    "Reply with the sentence only."
)


async def describe_image(comfyui_client: ComfyUIClient, workflow_manager: WorkflowManager, image: str) -> str:
    """Descricao crua do Florence-2. image aceita "nome [output]" (imagem gerada)."""
    try:
        graph = workflow_manager.render(DESCRIBE_WORKFLOW, {"IMAGE": image})
        entry = await comfyui_client.wait_for_completion(await comfyui_client.queue_prompt(graph))
    except ComfyUIError as exc:
        log.warning("Descricao da imagem falhou: %s", exc)
        return ""
    for output in entry.get("outputs", {}).values():
        text = output.get("text")
        if text:
            return str(text[0]).strip()
    return ""


class SceneDescriber:
    def __init__(
        self, comfyui_client: ComfyUIClient, workflow_manager: WorkflowManager, llm_client: OllamaClient | None = None
    ) -> None:
        self.comfyui_client = comfyui_client
        self.workflow_manager = workflow_manager
        self.llm_client = llm_client

    async def describe(self, image: str) -> str:
        """Cena da foto, sem os tracos da pessoa (a identidade vem da propria
        imagem) e sem texto/marcas (viram letras embaralhadas no video)."""
        caption = await describe_image(self.comfyui_client, self.workflow_manager, image)
        return clean_reference_caption(caption) if caption else ""

    async def suggest_motion(self, scene: str) -> str:
        if not self.llm_client or not scene:
            return ""
        try:
            answer, _ = await self.llm_client.chat(
                [ChatMessage("system", _MOTION_SYSTEM), ChatMessage("user", scene)],
                keep_alive="0",
                timeout=120.0,
            )
        except LLMError as exc:
            log.warning("Sugestao de movimento falhou: %s", exc)
            return ""
        answer = " ".join(answer.split()).strip("\"' ")
        return answer if 0 < len(answer) < 400 else ""
