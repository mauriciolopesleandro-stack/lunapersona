"""Estado compartilhado pelas etapas do ComfyUI: qual familia de modelo esta
na GPU (para registrar troca de modelo) e leitura de GPU/VRAM.

Z-Image (~13 GB) e Qwen 2511 (~24 GB) nao cabem juntos numa GPU de 24 GB: em
pedido avulso cada etapa recarrega o seu modelo (benchmark: 223 s por imagem
contra 103 s em lote). A telemetria registra isso em vez de esconder.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError, GenerationOutputImage
from app.providers.base import ProviderConfigurationError, ProviderError
from app.workflow_manager.manager import WorkflowManager, WorkflowNotFoundError, WorkflowParamError


def output_name(image: GenerationOutputImage) -> str:
    name = f"{image.subfolder}/{image.filename}" if image.subfolder else image.filename
    return f"{name} [output]"


def new_seed() -> int:
    return uuid.uuid4().int % (2**32)


class ComfySession:
    def __init__(self, client: ComfyUIClient, workflows: WorkflowManager) -> None:
        self.client = client
        self.workflows = workflows
        self.loaded_family: str | None = None

    def mark(self, family: str) -> bool | None:
        """Registra a familia que vai rodar; devolve True se havia outra na GPU
        (None = primeira etapa deste processo: nao se sabe o que estava carregado)."""
        previous, self.loaded_family = self.loaded_family, family
        return None if previous is None else previous != family

    async def gpu(self) -> dict[str, Any]:
        try:
            status = await self.client.check_connection()
        except ComfyUIError:
            return {}
        devices = (status.stats or {}).get("devices") or []
        if not devices:
            return {}
        dev = devices[0]
        total, free = dev.get("vram_total"), dev.get("vram_free")
        return {
            "name": dev.get("name"),
            "vram_total_mb": round(total / 2**20) if total else None,
            "vram_used_mb": round((total - free) / 2**20) if total and free is not None else None,
        }

    async def run(self, workflow_id: str, values: dict[str, Any], patch=None) -> tuple[str, GenerationOutputImage, float]:
        """Renderiza e executa um workflow; devolve (prompt_id, imagem, segundos)."""
        start = time.monotonic()
        try:
            graph = self.workflows.render(workflow_id, values)
            if patch is not None:
                patch(graph)
            prompt_id = await self.client.queue_prompt(graph)
            images = self.client.extract_images(await self.client.wait_for_completion(prompt_id))
        except (WorkflowNotFoundError, WorkflowParamError) as exc:
            raise ProviderConfigurationError(str(exc)) from exc
        except ComfyUIError as exc:
            raise ProviderError(str(exc)) from exc
        images = [i for i in images if "_pose_" not in i.filename]
        if not images:
            raise ProviderError("O ComfyUI terminou sem devolver imagem.")
        return prompt_id, images[0], time.monotonic() - start
