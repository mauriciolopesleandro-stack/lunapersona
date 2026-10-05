import pytest

from app.clients.comfyui_client import ComfyUIExecutionError, ConnectionStatus, GenerationOutputImage
from app.providers.base import (
    GenerationParameters,
    PromptSections,
    ProviderError,
    ProviderInput,
    ProviderRegistry,
    UnknownProviderError,
)
from app.providers.comfyui.adapter import ZIMAGE_UNET, ComfyUIAdapter, build_prompt, lora_strength
from app.workflow_manager.manager import WorkflowManager
from tests.conftest import REPO


class FakeComfy:
    def __init__(self, loras=("luna_zimage_v1.safetensors",), fail=False, online=True):
        self.loras = list(loras)
        self.fail = fail
        self.online = online
        self.graphs = []

    async def check_connection(self):
        return ConnectionStatus(ok=self.online, message="ok" if self.online else "recusou")

    async def list_loras(self):
        return self.loras

    async def list_diffusion_models(self):
        return [ZIMAGE_UNET]

    async def queue_prompt(self, graph):
        if self.fail:
            raise ComfyUIExecutionError("sem VRAM")
        self.graphs.append(graph)
        return f"p{len(self.graphs)}"

    async def wait_for_completion(self, prompt_id):
        return {"prompt_id": prompt_id}

    def extract_images(self, entry):
        return [GenerationOutputImage(f"{entry['prompt_id']}.png", "", "output", f"http://x/{entry['prompt_id']}.png")]


def sections(**kw) -> PromptSections:
    base = dict(identity_directive="", identity_traits="oval face, dark brown eyes", appearance="black dress",
                style="candid photo", scene="walking in a cafe", constraints=["do not change identity"], negative=[])
    base.update(kw)
    return PromptSections(**base)


def request(**params) -> ProviderInput:
    return ProviderInput(
        persona_id="luna", prompt=sections(), parameters=GenerationParameters(**params), sex="F",
        identity_assets={"lora": {"zimage_file": "luna_zimage_v1.safetensors", "trigger": "lunavox", "zimage_strength": 1.0}},
    )


def adapter(comfy: FakeComfy) -> ComfyUIAdapter:
    return ComfyUIAdapter(comfy, WorkflowManager(REPO / "workflows"))


def test_prompt_with_lora_starts_with_trigger_and_skips_text_identity():
    text = build_prompt(request(), "lunavox")
    assert text.startswith("lunavox, a woman")
    assert "oval face" not in text and "do not" not in text
    assert text.index("black dress") < text.index("walking in a cafe") < text.index("candid photo")


def test_prompt_without_lora_carries_identity_in_text():
    assert build_prompt(request(), None).startswith("a woman, oval face, dark brown eyes")


def test_lora_strength_mapping():
    assert lora_strength(1.0, 0.5) == 1.0
    assert lora_strength(1.0, 1.0) == 1.3
    assert lora_strength(1.2, 0.0) == pytest.approx(0.84)


async def test_generate_uses_lora_workflow():
    comfy = FakeComfy()
    out = await adapter(comfy).generate(request(seed=7, identity_strength=1.0))
    graph = comfy.graphs[0]
    assert graph["11"]["inputs"]["lora_name"] == "luna_zimage_v1.safetensors"
    assert graph["11"]["inputs"]["strength_model"] == 1.3
    assert graph["8"]["inputs"]["seed"] == 7
    assert out.images[0].locator == "p1.png [output]"
    assert out.effective_parameters["workflow_id"] == "zimage-txt2img-lora"


async def test_generate_without_lora_on_pod_falls_back_to_text():
    comfy = FakeComfy(loras=())
    out = await adapter(comfy).generate(request())
    assert out.effective_parameters["workflow_id"] == "zimage-txt2img"
    assert "lunavox" not in out.effective_parameters["prompt"]


async def test_comfy_errors_become_provider_errors():
    with pytest.raises(ProviderError):
        await adapter(FakeComfy(fail=True)).generate(request())


def test_normalize_parameters_fits_model():
    params = adapter(FakeComfy()).normalize_parameters(GenerationParameters(width=2000, height=2000, identity_strength=3))
    assert params.width * params.height <= 1_300_000 and params.width % 16 == 0
    assert params.identity_strength == 1.0


async def test_validate_configuration_reports_offline():
    assert await adapter(FakeComfy()).validate_configuration() == []
    problems = await adapter(FakeComfy(online=False)).validate_configuration()
    assert problems and "fora do ar" in problems[0]


def test_registry_unknown_provider_is_controlled_error():
    registry = ProviderRegistry()
    registry.register(adapter(FakeComfy()))
    assert registry.names() == ["comfyui"]
    with pytest.raises(UnknownProviderError):
        registry.get("midjourney")
