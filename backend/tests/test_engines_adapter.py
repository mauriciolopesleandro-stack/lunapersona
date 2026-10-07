"""V2: adapter SDXL do ComfyUI (RealVisXL/Lustify) - grafo, checkpoint do registro, LoRA fixa, controles,
referencia facial e checagem sem fallback."""
import numpy as np
import pytest

from app.core.engines.adapter import ControlSpec, IdentitySpec, InpaintRequest
from app.core.engines.models import ModelRegistry
from app.core.generation.v2_config import load_v2_config
from app.providers.base import ProviderError
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter
from app.providers.comfyui.sdxl_engine import LustifyAdapter, RealVisXLAdapter, work_dims
from app.providers.comfyui.session import ComfySession
from app.workflow_manager.manager import WorkflowManager
from tests.conftest import REPO
from tests.test_v2_models import FakeComfyV2
from tests.test_v2_multipass import MASTER

REG = ModelRegistry.load(REPO / "config" / "model_registry_v2.json")
CN = "controlnet-union-sdxl-1.0-promax.safetensors"


class InfoComfy(FakeComfyV2):
    def __init__(self, ckpts=("RealVisXL_V5.0_fp16.safetensors",), cns=(CN,)):
        super().__init__(checkpoints=ckpts)
        self.cns = list(cns)

    async def get_object_info(self):
        return {"DWPreprocessor": {}, "DepthAnythingV2Preprocessor": {}, "SetUnionControlNetType": {},
                "ControlNetApplyAdvanced": {},
                "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [self.checkpoints]}}},
                "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [self.loras]}}},
                "ControlNetLoader": {"input": {"required": {"control_net_name": [self.cns]}}}}


def adapter(comfy, cls=RealVisXLAdapter, model="realvisxl", region=False):
    session = ComfySession(comfy, WorkflowManager(REPO / "workflows"))
    reg = None
    if region:
        v2 = load_v2_config(REPO / "config" / "persona_engine_v2.json")
        reg = ComfyRegionPassAdapter(session, v2.model(), v2.lora, identity_adapters=v2.identity_adapters)
    m = REG.get(model)
    return cls(session, m, REG.get("lunavox_sdxl_v1"), CN, region=reg)


def mask():
    m = np.zeros((240, 160), np.float32)
    m[40:120, 50:110] = 1
    return m


async def test_inpaint_uses_registry_checkpoint_fixed_lora_and_both_controls():
    comfy = InfoComfy()
    a = adapter(comfy)
    req = InpaintRequest("foto.png", mask(), "lunavox, a woman", "tattoo", 0.9, 11, "identity",
                         controls=ControlSpec(pose_strength=0.8, depth_strength=0.5, end_percent=0.8, structure="estrutura.png"))
    out = await a.inpaint(req)
    g = comfy.graphs[0]
    assert g["1"]["inputs"]["ckpt_name"] == "RealVisXL_V5.0_fp16.safetensors"
    assert g["2"]["inputs"]["lora_name"] == "lunavox_sdxl_v1.safetensors" and g["2"]["inputs"]["strength_model"] == 1.0
    assert g["23"]["inputs"]["strength"] == 0.8 and g["26"]["inputs"]["strength"] == 0.5
    assert g["40"]["inputs"]["image"] == "estrutura.png" and g["10"]["inputs"]["image"] == "foto.png"
    assert g["14"]["inputs"]["denoise"] == 0.9 and out.parameters["CKPT"] == "RealVisXL_V5.0_fp16.safetensors"


async def test_lora_off_and_hires_work_size():
    comfy = InfoComfy()
    req = InpaintRequest("foto.png", mask(), "skin", "n", 0.3, 1, "tattoo", identity=IdentitySpec(use_lora=False), work_side=1536)
    await adapter(comfy).inpaint(req)
    g = comfy.graphs[0]
    assert g["2"]["inputs"]["strength_model"] == 0.0 and max(g["12"]["inputs"]["width"], g["12"]["inputs"]["height"]) == 1536
    assert work_dims(100, 200, 1024) == (512, 1024)


async def test_load_reports_missing_lustify_checkpoint_without_fallback():
    comfy = InfoComfy(ckpts=("RealVisXL_V5.0_fp16.safetensors",))
    problems = await adapter(comfy, LustifyAdapter, "lustify").load()
    assert any("lustifyNSFWCheckpoint_zenithV9" in p for p in problems)
    assert await adapter(comfy).load() == []


async def test_face_reference_goes_to_instantid_and_requires_the_adapter():
    comfy = InfoComfy()
    req = InpaintRequest("foto.png", mask(), "lunavox face", "n", 0.4, 3, "face_refine",
                         identity=IdentitySpec(use_lora=True, reference=MASTER, reference_strength=0.5))
    with pytest.raises(ProviderError, match="sem fallback"):
        await adapter(comfy).inpaint(req)
    await adapter(comfy, region=True).inpaint(req)
    g = comfy.graphs[-1]
    assert any(n["class_type"] == "ApplyInstantID" for n in g.values())


def test_metadata_has_license_and_hashes():
    md = adapter(InfoComfy()).metadata()
    assert md["hash"].startswith("6a35a785") and md["lora_hash"].startswith("0a58a72e") and "RAIL" in md["license"]
