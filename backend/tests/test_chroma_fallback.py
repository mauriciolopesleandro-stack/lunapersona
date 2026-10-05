"""A aba Gerar (GenerationService) nao cai mais no Chroma em silencio quando a
LoRA do Z-Image da persona nao esta no pod."""
import pytest

from app.model_manager.manager import ModelManager
from app.persona_manager.manager import PersonaManager
from app.services.generation_service import GenerationRequest, GenerationService
from app.workflow_manager.manager import WorkflowManager, WorkflowParamError
from tests.conftest import REPO


class NoLoraComfy:
    def __init__(self):
        self.graphs = []

    async def list_loras(self):
        return ["luna_chroma_v1.safetensors"]  # so a LoRA do Chroma

    async def queue_prompt(self, graph):
        self.graphs.append(graph)
        return "p1"


async def _service(personas_dir, comfy, **kw):
    return GenerationService(comfy, WorkflowManager(REPO / "workflows"), ModelManager(REPO / "models" / "registry.json"),
                             PersonaManager(personas_dir), llm_client=None, **kw)


async def test_persona_without_zimage_lora_fails_instead_of_chroma(personas_dir):
    comfy = NoLoraComfy()
    service = await _service(personas_dir, comfy)
    assert service.allow_chroma_fallback is False
    with pytest.raises(WorkflowParamError, match="Chroma nao e usado"):
        await service.generate(GenerationRequest(prompt="cafe", model_id="chroma1-hd-fp8",
                                                 workflow_id="chroma-txt2img", persona_id="luna"))
    assert comfy.graphs == []


async def test_fallback_only_when_explicitly_enabled(personas_dir):
    service = await _service(personas_dir, NoLoraComfy(), allow_chroma_fallback=True)
    assert service.allow_chroma_fallback is True
