"""Contrato dos adapters do ComfyUI com um ComfyUI falso e os workflows reais do repo."""
import pytest

from app.clients.comfyui_client import ComfyUIExecutionError, ConnectionStatus, GenerationOutputImage
from app.providers.base import (
    FaceIdentityAdapter,
    GenerationParameters,
    PoseControl,
    PoseControlAdapter,
    PromptSections,
    ProviderConfigurationError,
    ProviderError,
    ProviderImage,
    ReferenceImage,
    SceneAdapter,
    SceneRequest,
)
from app.providers.comfyui import comfyui_provider_set
from app.providers.comfyui.face import BFS, LIGHTNING
from app.providers.comfyui.face import UNET as QWEN_UNET
from app.providers.comfyui.scene import CONTROL_PATCH, UNET, build_prompt
from app.workflow_manager.manager import WorkflowManager
from tests.conftest import REPO

LORA = {"file": "luna_zimage_v1.safetensors", "trigger": "lunavox", "strength": 1.0}


class FakeComfy:
    def __init__(self, loras=("luna_zimage_v1.safetensors", LIGHTNING, BFS), fail=False):
        self.loras = list(loras)
        self.fail = fail
        self.graphs = []
        self.uploads = []

    async def check_connection(self):
        return ConnectionStatus(ok=True, message="ok", stats={"devices": [
            {"name": "RTX PRO 4000", "vram_total": 24467 * 2**20, "vram_free": 11000 * 2**20}]})

    async def list_loras(self):
        return self.loras

    async def list_diffusion_models(self):
        return [UNET, QWEN_UNET]

    async def list_model_patches(self):
        return [CONTROL_PATCH]

    async def get_object_info(self):
        return {"DWPreprocessor": {}, "ZImageFunControlnet": {}}

    async def upload_image(self, name, content):
        self.uploads.append(name)
        return name

    async def queue_prompt(self, graph):
        if self.fail:
            raise ComfyUIExecutionError("sem VRAM")
        self.graphs.append(graph)
        return f"p{len(self.graphs)}"

    async def wait_for_completion(self, prompt_id):
        return {"prompt_id": prompt_id}

    def extract_images(self, entry):
        n = entry["prompt_id"]
        return [GenerationOutputImage(f"{n}_pose_00001_.png", "", "output", "x"),
                GenerationOutputImage(f"{n}.png", "", "output", f"http://x/{n}.png")]


def provider(comfy):
    return comfyui_provider_set(comfy, WorkflowManager(REPO / "workflows"))


def scene_request(pose=None, seed=7):
    prompt = PromptSections(subject="lunavox, a woman", appearance="black dress", scene="in a cafe", style="",
                            directives=["only one woman in the photo"])
    return SceneRequest(prompt=prompt, parameters=GenerationParameters(seed=seed), lora=LORA, pose=pose)


def test_contracts():
    p = provider(FakeComfy())
    assert isinstance(p.scene, SceneAdapter) and isinstance(p.face, FaceIdentityAdapter)
    assert isinstance(p.pose, PoseControlAdapter)
    assert p.scene.get_capabilities().supports_negative_prompt is False


async def test_scene_graph_matches_benchmark():
    comfy = FakeComfy()
    out = await provider(comfy).scene.generate(scene_request())
    g = comfy.graphs[0]
    assert g["11"]["inputs"]["lora_name"] == "luna_zimage_v1.safetensors" and g["11"]["inputs"]["strength_model"] == 1.0
    k = g["8"]["inputs"]
    assert (k["steps"], k["cfg"], k["sampler_name"], k["scheduler"], k["seed"]) == (8, 1.0, "res_multistep", "simple", 7)
    assert g["6"]["class_type"] == "ConditioningZeroOut"  # negativo zerado, como no benchmark
    assert g["4"]["inputs"]["model"] == ["11", 0]  # sem ControlNet no modo livre
    assert out.image.locator == "p1.png [output]" and out.effective_parameters["negative_applied"] is False
    assert out.gpu == {"name": "RTX PRO 4000", "vram_total_mb": 24467, "vram_used_mb": 13467}
    assert build_prompt(scene_request()).startswith("lunavox, a woman, black dress, in a cafe")


async def test_scene_with_pose_inserts_controlnet():
    comfy = FakeComfy()
    await provider(comfy).scene.generate(scene_request(pose=PoseControl("pose.png", 0.8)))
    g = comfy.graphs[0]
    assert g["4"]["inputs"]["model"] == ["p30", 0]
    assert g["p30"]["inputs"]["strength"] == 0.8 and g["p20"]["inputs"]["image"] == "pose.png"
    assert g["p30"]["inputs"]["model"] == ["11", 0]


async def test_scene_without_lora_fails_explicitly():
    p = provider(FakeComfy(loras=(LIGHTNING, BFS)))
    problems = await p.scene.validate_configuration(LORA, pose=False)
    assert problems and "sem fallback" in problems[0]
    with pytest.raises(ProviderConfigurationError):
        await p.scene.generate(SceneRequest(prompt=scene_request().prompt, parameters=GenerationParameters(), lora={}))


async def test_face_lock_uses_only_master_face_on_whole_image():
    comfy = FakeComfy()
    p = provider(comfy)
    master = ReferenceImage("f7e8", "references/f7e8.png", b"face", "abcdef" * 10 + "abcd")
    out = await p.face.lock_face(ProviderImage("comfyui", "base.png [output]", "u", 832, 1216), master, 9)
    g = comfy.graphs[0]
    assert g["10"]["inputs"]["image"] == "base.png [output]"
    assert g["11"]["inputs"]["image"] == comfy.uploads[0] and comfy.uploads[0].startswith("master_")
    assert (g["14"]["inputs"]["x"], g["14"]["inputs"]["y"], g["14"]["inputs"]["width"], g["14"]["inputs"]["height"]) == (0, 0, 832, 1216)
    assert (g["12"]["inputs"]["width"], g["12"]["inputs"]["height"]) == (832, 1216)  # tamanho do benchmark
    assert g["7"]["inputs"]["lora_name"] == BFS and g["31"]["inputs"]["seed"] == 9
    assert out.stage == "face_lock" and out.effective_parameters["master_face"] == "f7e8"
    # a mesma master nao sobe duas vezes
    await p.face.lock_face(ProviderImage("comfyui", "b2.png [output]", "u", 832, 1216), master, 10)
    assert len(comfy.uploads) == 1


async def test_model_switch_is_recorded():
    comfy = FakeComfy()
    p = provider(comfy)
    first = await p.scene.generate(scene_request())
    face = await p.face.lock_face(first.image, ReferenceImage("m", "m.png", b"x", "a" * 64), 1)
    again = await p.scene.generate(scene_request())
    assert first.model_switch is None and face.model_switch is True and again.model_switch is True


async def test_comfy_errors_become_provider_errors():
    with pytest.raises(ProviderError):
        await provider(FakeComfy(fail=True)).scene.generate(scene_request())


async def test_face_lock_reports_missing_models():
    problems = await provider(FakeComfy(loras=("luna_zimage_v1.safetensors",))).face.validate_configuration()
    assert any(BFS in p for p in problems) and any(LIGHTNING in p for p in problems)
