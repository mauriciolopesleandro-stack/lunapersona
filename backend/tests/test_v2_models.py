"""Persona Engine V2 (experimental): configuracao, ModelAdapter (RealVisXL e
Lustify pelo mesmo codigo), telemetria por passada e trava de custo."""
import copy
import json

import pytest

from app.core.generation.budget import BudgetExceeded, BudgetGuard, ExperimentPlan
from app.core.generation.pass_telemetry import base_record, cost_of, text_hash
from app.core.generation.v2_config import V2ConfigError, load_v2_config, parse_v2_config
from app.providers.base import (
    GenerationParameters,
    ModelAdapter,
    NegativeSet,
    PoseControl,
    PromptSections,
    ProviderConfigurationError,
    SceneAdapter,
    SceneRequest,
)
from app.providers.comfyui.checkpoint import v2_model_adapters
from app.providers.comfyui.session import ComfySession
from app.workflow_manager.manager import WorkflowManager
from tests.conftest import REPO
from tests.test_adapters_comfy import FakeComfy

CONFIG = REPO / "config" / "persona_engine_v2.json"
RAW = json.loads(CONFIG.read_text(encoding="utf-8"))


class FakeComfyV2(FakeComfy):
    def __init__(self, checkpoints=("RealVisXL_V5.0_fp16.safetensors", "lustifyNSFWCheckpoint_zenithV9.safetensors"),
                 loras=("lunavox_sdxl_v1.safetensors",)):
        super().__init__(loras=loras)
        self.checkpoints = list(checkpoints)

    async def list_checkpoints(self):
        return self.checkpoints


def adapters(comfy):
    return v2_model_adapters(ComfySession(comfy, WorkflowManager(REPO / "workflows")), load_v2_config(CONFIG))


def request(pose=None, seed=7101):
    prompt = PromptSections(subject="lunavox, a woman", appearance="subtle smile",
                            scene="sitting by a window, white tank top", style="", directives=[],
                            negative=NegativeSet(global_terms=["extra limbs"], persona_terms=["tattoo"]))
    return SceneRequest(prompt=prompt, parameters=GenerationParameters(seed=seed), lora={}, pose=pose)


# --- configuracao ---------------------------------------------------------------


def test_real_config_has_both_candidates_and_sdxl_lora():
    cfg = load_v2_config(CONFIG)
    assert set(cfg.models) == {"realvisxl", "lustify"} and cfg.generation_model == "realvisxl"
    assert cfg.lora.file == "lunavox_sdxl_v1.safetensors" and cfg.lora.strength == 1.0
    assert cfg.age_target == 27 and cfg.benchmark_limit_usd == 0.10 and cfg.status == "EXPERIMENTAL"
    # mesmos parametros nos dois candidatos (comparacao justa)
    assert cfg.models["realvisxl"].sampling == cfg.models["lustify"].sampling
    # passadas configuradas mas desligadas ate o teste 2
    assert [p.name for p in cfg.face_passes] == ["identity_structure", "facial_refinement", "microdetail"]
    assert [p.mask for p in cfg.body_passes] == ["body_full", "body_regions"]
    assert not any(p.enabled for p in (*cfg.face_passes, *cfg.body_passes))
    assert [p.denoise for p in cfg.face_passes] == sorted([p.denoise for p in cfg.face_passes], reverse=True)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(generation_model="flux"), "nao existe"),
    (lambda d: d["face_passes"][0].update(strength=1.4), "fora de 0-1"),
    (lambda d: d["face_passes"][1].update(denoise="x"), "numero"),
    (lambda d: d["body_passes"][0].update(mask="face_full"), "Mascara"),
    (lambda d: d["face_passes"].append(dict(d["face_passes"][0])), "No maximo 3"),
    (lambda d: d["lora"].update(file=""), "LoRA"),
    (lambda d: d["lora"].update(strength=0), "lora.strength"),
    (lambda d: d.pop("benchmark"), "ausente"),
])
def test_invalid_config_is_explicit_error(mutate, message):
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(V2ConfigError, match=message):
        parse_v2_config(data)


# --- ModelAdapter ----------------------------------------------------------------


def test_one_adapter_per_model_same_contract():
    found = adapters(FakeComfyV2())
    assert set(found) == {"realvisxl", "lustify"}
    for a in found.values():
        assert isinstance(a, ModelAdapter) and isinstance(a, SceneAdapter)
        assert a.get_capabilities().supports_negative_prompt is True


@pytest.mark.parametrize("model, ckpt, workflow", [
    ("realvisxl", "RealVisXL_V5.0_fp16.safetensors", "realvis-base"),
    ("lustify", "lustifyNSFWCheckpoint_zenithV9.safetensors", "lustify-base"),
])
async def test_base_graph_uses_profile_lora_and_negative(model, ckpt, workflow):
    comfy = FakeComfyV2()
    out = await adapters(comfy)[model].generate(request())
    g = comfy.graphs[0]
    assert g["1"]["inputs"]["ckpt_name"] == ckpt
    assert g["2"]["inputs"]["lora_name"] == "lunavox_sdxl_v1.safetensors" and g["2"]["inputs"]["strength_model"] == 1.0
    k = g["6"]["inputs"]
    assert (k["steps"], k["cfg"], k["sampler_name"], k["scheduler"], k["seed"]) == (30, 5.0, "dpmpp_2m_sde", "karras", 7101)
    assert (g["5"]["inputs"]["width"], g["5"]["inputs"]["height"]) == (832, 1216)
    assert g["3"]["inputs"]["text"].startswith("lunavox, a woman, subtle smile, sitting by a window")
    negative = g["4"]["inputs"]["text"]
    for term in ("extra limbs", "tattoo", "plastic skin", "porcelain skin", "nude"):
        assert term in negative
    p = out.effective_parameters
    assert out.stage == "base" and p["negative_applied"] is True and p["workflow"] == workflow and p["model"] == model
    assert out.model_ids == [ckpt, "lunavox_sdxl_v1.safetensors"]


async def test_both_models_get_identical_prompt_and_settings():
    comfy = FakeComfyV2()
    found = adapters(comfy)
    await found["realvisxl"].generate(request())
    await found["lustify"].generate(request())
    a, b = comfy.graphs
    for node in ("2", "3", "4", "5", "6"):
        assert a[node] == b[node]
    assert a["1"] != b["1"]  # so o checkpoint muda


async def test_missing_checkpoint_or_lora_is_reported_without_fallback():
    problems = await adapters(FakeComfyV2(checkpoints=[], loras=[]))["lustify"].validate_configuration()
    assert any("lustifyNSFWCheckpoint_zenithV9" in p for p in problems)
    assert any("lunavox_sdxl_v1" in p and "sem fallback" in p for p in problems)
    assert await adapters(FakeComfyV2())["realvisxl"].validate_configuration() == []


async def test_pose_is_not_supported_yet():
    a = adapters(FakeComfyV2())["realvisxl"]
    assert any("Pose" in p for p in await a.validate_configuration(pose=True))
    with pytest.raises(ProviderConfigurationError):
        await a.generate(request(pose=PoseControl("pose.png", 0.8)))


def test_model_versions_are_reproducible():
    v = adapters(FakeComfyV2())["lustify"].model_versions()
    assert v["checkpoint_sha256"].startswith("1a3abf0b") and v["lora_sha256"].startswith("0a58a72e")


# --- telemetria por passada ---------------------------------------------------------


def test_base_record_has_hashes_and_unmeasured_as_none():
    r = base_record(model="lustify", model_version="zenith-v9", lora="lunavox_sdxl_v1.safetensors", lora_strength=1.0,
                    seed=7101, prompt="a", negative="b")
    d = r.to_dict()
    assert d["prompt_hash"] == text_hash("a") != d["negative_hash"]
    assert d["pass_type"] == "base" and d["pass_number"] == 0 and d["denoise"] == 1.0
    assert d["face_identity_score"] is None and d["anatomy_score"] is None and d["rollback"] is False
    assert cost_of(60, 0.57) == pytest.approx(0.0095, abs=1e-4) and cost_of(None, 0.57) is None


# --- trava de custo -------------------------------------------------------------------


def plan(images=6, per_image=20, overhead=360):
    return ExperimentPlan("teste 1", "comparar base", "lustify tem pele mais natural", images, per_image, overhead, 0.57)


def test_budget_allows_small_experiment():
    report = BudgetGuard(0.10).check(plan(overhead=300))
    assert report["estimated_cost_usd"] == pytest.approx(0.57 * 420 / 3600, abs=1e-4)


def test_budget_stops_above_limit_and_says_what_to_authorize():
    with pytest.raises(BudgetExceeded) as err:
        BudgetGuard(0.10).check(plan(images=30))
    msg = str(err.value)
    assert "PARADO" in msg and "30 imagens" in msg and "Hipotese" in msg


def test_budget_respects_explicit_authorization():
    assert BudgetGuard(0.10).check(plan(images=30), authorized_usd=0.20)["allowed_usd"] == 0.20
    with pytest.raises(BudgetExceeded):
        BudgetGuard(0.10).check(plan(images=0))
