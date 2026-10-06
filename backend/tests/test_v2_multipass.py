"""Multi-pass da V2: cada passada parte do checkpoint anterior, e medida e so
fica se nao piorou (senao rollback); regioes, mascara, telemetria e o adapter."""
import io

import pytest
from PIL import Image

from app.core.generation.multipass import (
    ACCEPTED,
    ERROR,
    ROLLBACK,
    SKIPPED,
    MultiPassRunner,
    body_region,
    face_region,
)
from app.core.generation.v2_config import load_v2_config
from app.providers.base import (
    AdapterCapabilities,
    GenerationParameters,
    PromptSections,
    ProviderError,
    ProviderImage,
    ReferenceImage,
    RegionPassAdapter,
    SceneAdapter,
    SceneRequest,
    StageOutput,
)
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter, render_mask
from app.providers.comfyui.session import ComfySession
from app.workflow_manager.manager import WorkflowManager
from tests.conftest import REPO
from tests.fakes import STANDING, WALKING, analysis, body, face
from tests.test_v2_models import FakeComfyV2

CFG = load_v2_config(REPO / "config" / "persona_engine_v2.json")
MASTER = ReferenceImage("m", "m.png", b"x", "a" * 64)
GPU = {"name": "Fake GPU", "vram_used_mb": 7600}


class FakeBase(SceneAdapter):
    name = "fake-base"

    async def generate(self, request):
        return StageOutput(ProviderImage("fake", "base", "http://fake/base.png", 832, 1216), "base", self.name, 14.0,
                           request.parameters.seed, gpu=GPU, effective_parameters={"prompt": "lunavox, a woman"})

    async def validate_configuration(self, lora, pose):
        return []

    def get_capabilities(self):
        return AdapterCapabilities(name=self.name, title="Fake")


class FakeRegion(RegionPassAdapter):
    name = "fake-region"

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    async def validate_configuration(self):
        return []

    async def refine(self, image, request):
        self.calls.append((image.locator, request))
        if request.name in self.fail_on:
            raise ProviderError("caiu")
        return StageOutput(ProviderImage("fake", request.name, f"http://fake/{request.name}.png", 832, 1216),
                           request.name, self.name, 7.0, request.seed, gpu=GPU)


class Script:
    """Analise por imagem: dicionario locator -> ImageAnalysis (o resto = base)."""

    def __init__(self, by_name):
        self.by_name = by_name

    async def analyze(self, image, master):
        return self.by_name.get(image.locator, self.by_name["base"])


class Skin:
    def __init__(self, scores):
        self.scores = scores

    async def analyze(self, image, face_, config):
        from app.core.validation.skin import grade
        s = self.scores.get(image.locator, 0.5)
        return grade(1 + s * 2.5, 0.0, config, {})


def look(sim, age=27.0, kps=STANDING, extra_faces=()):
    return analysis([face(sim, age=age), *extra_faces], [body(kps)])


def runner(by_name, skin=None, region=None):
    region = region or FakeRegion()
    r = MultiPassRunner(base=FakeBase(), region=region, analyzer=Script(by_name), skin_analyzer=Skin(skin or {}),
                        rules=CFG.acceptance, age_target=27, duplicate_similarity=0.4,
                        skin_config={"calibration": {"lo": 1.0, "hi": 3.5}, "bands": {"fail_below": 0.3, "warn_below": 0.5,
                                     "good_from": 0.7, "excellent_from": 0.85}}, price_per_hour=0.57)
    return r, region


async def run(r):
    req = SceneRequest(prompt=PromptSections("lunavox, a woman", "", "gym", ""), parameters=GenerationParameters(seed=7201), lora={})
    prompts = {**CFG.pass_prompts, "body": "gym, natural anatomy"}
    return await r.run(req, MASTER, list(CFG.face_passes), list(CFG.body_passes), prompts, "plastic skin",
                       (CFG.lora.file, CFG.lora.strength), ("realvisxl", "RealVisXL V5.0"))


async def test_all_passes_chain_from_previous_checkpoint():
    r, region = runner({"base": look(0.37), "face_1": look(0.50), "face_2": look(0.55), "face_3": look(0.56),
                        "body_1": look(0.56), "body_2": look(0.555)})
    res = await run(r)
    assert [d["status"] for d in res.decisions] == [ACCEPTED] * 5
    # cada passada recebeu a imagem da anterior, nunca a base de novo
    assert [c[0] for c in region.calls] == ["base", "face_1", "face_2", "face_3", "body_1"]
    assert res.final.name == "body_2" and [c.name for c in res.checkpoints][0] == "base"


async def test_identity_drop_rolls_back_to_best_checkpoint():
    r, region = runner({"base": look(0.37), "face_1": look(0.50), "face_2": look(0.55), "face_3": look(0.48),
                        "body_1": look(0.55), "body_2": look(0.55)})
    res = await run(r)
    d3 = res.decisions[2]
    assert d3["status"] == ROLLBACK and d3["kept"] == "face_2" and "identidade caiu" in d3["reasons"][0]
    assert region.calls[3][0] == "face_2"  # corpo 1 partiu do face_2, nao do face_3 que piorou
    rec = next(x for x in res.records if x.pass_type == "face" and x.pass_number == 3)
    assert rec.rollback and "identidade caiu" in rec.rollback_reason and rec.face_identity_score == 0.48


@pytest.mark.parametrize("bad, reason", [
    (look(0.52, age=17), "idade se afastou"),
    (look(0.52, kps=WALKING), "pose mudou"),
    (look(0.52, extra_faces=[face(0.5, bbox=(600.0, 80.0, 680.0, 180.0))]), "mais de uma Luna"),
    (look(0.52, extra_faces=[face(0.05, bbox=(600.0, 80.0, 680.0, 180.0))]), "rosto novo"),
    (analysis([], [body()]), "rosto da persona sumiu"),
])
async def test_pass_that_changes_the_person_is_rolled_back(bad, reason):
    r, _ = runner({"base": look(0.50), "face_1": bad})
    res = await run(r)
    assert res.decisions[0]["status"] == ROLLBACK and any(reason in x for x in res.decisions[0]["reasons"])


async def test_face_pass_1_must_not_lower_identity_and_skin_is_not_a_gate():
    r, _ = runner({"base": look(0.50), "face_1": look(0.49), "face_2": look(0.51)},
                  skin={"base": 0.9, "face_2": 0.1})
    res = await run(r)
    assert res.decisions[0]["status"] == ROLLBACK and "nao subiu" in res.decisions[0]["reasons"][0]
    assert res.decisions[1]["status"] == ACCEPTED  # pele pior (0.9 -> 0.1) nao reprova
    assert res.records[2].skin_score == pytest.approx(0.1)


async def test_without_face_face_passes_are_skipped_but_body_runs():
    no_face = analysis([], [body()])
    r, region = runner({"base": no_face, "body_1": no_face, "body_2": no_face})
    res = await run(r)
    assert [d["status"] for d in res.decisions[:3]] == [SKIPPED] * 3
    assert [c[1].name for c in region.calls] == ["body_1", "body_2"]


async def test_provider_error_keeps_previous_state_and_continues():
    r, region = runner({"base": look(0.40), "face_2": look(0.45)}, region=FakeRegion(fail_on={"face_1"}))
    res = await run(r)
    assert res.decisions[0]["status"] == ERROR and region.calls[1][0] == "base"
    assert res.decisions[1]["status"] == ACCEPTED


async def test_telemetry_has_one_record_per_pass_with_fixed_lora():
    r, _ = runner({"base": look(0.37)})
    res = await run(r)
    recs = [x.to_dict() for x in res.records]
    assert [(x["pass_type"], x["pass_number"]) for x in recs] == [("base", 0), ("face", 1), ("face", 2), ("face", 3),
                                                                  ("body", 1), ("body", 2)]
    assert len({x["seed"] for x in recs}) == 6 and recs[0]["seed"] == 7201
    # peso da LoRA nunca muda; a passada de microdetalhe roda SEM a LoRA (lora=false na config), registrado
    assert [x["lora_strength"] for x in recs] == [1.0, 1.0, 1.0, 0.0, 1.0, 1.0]
    assert recs[3]["lora"] == "nenhuma" and recs[1]["lora"] == "lunavox_sdxl_v1.safetensors"
    assert [x["denoise"] for x in recs[1:4]] == [0.42, 0.22, 0.18] and recs[1]["mask_type"] == "face_full"
    assert recs[0]["cost"] == pytest.approx(0.57 * 14 / 3600, abs=1e-5) and recs[0]["vram"] == 7600


# --- regioes e mascara ----------------------------------------------------------------


def test_face_regions_shrink_and_stay_inside_the_image():
    f = face(0.5, bbox=(380.0, 60.0, 460.0, 170.0))
    full, inner = face_region(f, "face_full", 832, 1216), face_region(f, "face_inner", 832, 1216)
    assert full.crop.w > inner.crop.w and full.shapes[0].rx > inner.shapes[0].rx
    for reg in (full, inner):
        c = reg.crop
        assert c.x >= 0 and c.y >= 0 and c.x + c.w <= 832 and c.y + c.h <= 1216 and c.w % 8 == 0


def test_body_regions_never_include_the_face():
    b, f = body(), face(0.5)
    for mask in ("body_full", "body_regions"):
        reg = body_region(b, f, mask, 832, 1216)
        excluded = [s for s in reg.shapes if not s.include]
        assert len(excluded) == 1 and excluded[0].cx + reg.crop.x == pytest.approx(420.0)
    limbs = body_region(b, f, "body_regions", 832, 1216)
    assert len([s for s in limbs.shapes if s.include]) == 8


def test_mask_has_strength_inside_zero_outside_and_face_cut_out():
    reg = body_region(body(), face(0.5), "body_full", 832, 1216).to_dict()
    img = Image.open(io.BytesIO(render_mask(reg, 0.6)))
    c = reg["crop"]
    assert img.size == (c["w"], c["h"])
    fx, fy = 420 - c["x"], 115 - c["y"]
    assert img.getpixel((fx, fy)) < 10  # rosto fora
    assert abs(img.getpixel((416 - c["x"], 700 - c["y"])) - 153) <= 3  # 255 * 0.6 no corpo
    assert img.getpixel((2, 2)) < 10


async def test_region_adapter_graph():
    comfy = FakeComfyV2()
    a = ComfyRegionPassAdapter(ComfySession(comfy, WorkflowManager(REPO / "workflows")), CFG.model("realvisxl"), CFG.lora)
    reg = face_region(face(0.5), "face_full", 832, 1216).to_dict()
    from app.providers.base import RegionPassRequest
    out = await a.refine(ProviderImage("comfyui", "base.png [output]", "u", 832, 1216),
                         RegionPassRequest(reg, "lunavox, a woman, face", "plastic skin", 0.30, 0.75, 9, "face_1"))
    g = comfy.graphs[0]
    assert g["10"]["inputs"]["image"] == "base.png [output]" and g["17"]["inputs"]["image"] == comfy.uploads[0]
    crop = g["11"]["inputs"]
    assert (crop["x"], crop["y"], crop["width"], crop["height"]) == (reg["crop"]["x"], reg["crop"]["y"], reg["crop"]["w"], reg["crop"]["h"])
    k = g["14"]["inputs"]
    assert (k["denoise"], k["steps"], k["cfg"], k["seed"]) == (0.30, 20, 5.0, 9)
    assert g["2"]["inputs"]["strength_model"] == 1.0 and g["1"]["inputs"]["ckpt_name"] == "RealVisXL_V5.0_fp16.safetensors"
    assert out.effective_parameters["workflow"] == "realvis-face-pass" and out.effective_parameters["strength"] == 0.75


async def test_microdetail_pass_runs_without_lora():
    r, region = runner({"base": look(0.40)})
    await run(r)
    by_name = {c[1].name: c[1] for c in region.calls}
    assert by_name["face_1"].use_lora and not by_name["face_3"].use_lora


async def test_region_adapter_without_lora_sets_weight_zero():
    comfy = FakeComfyV2()
    a = ComfyRegionPassAdapter(ComfySession(comfy, WorkflowManager(REPO / "workflows")), CFG.model("realvisxl"), CFG.lora)
    from app.providers.base import RegionPassRequest
    reg = face_region(face(0.5), "face_skin", 832, 1216).to_dict()
    out = await a.refine(ProviderImage("comfyui", "x.png [output]", "u", 832, 1216),
                         RegionPassRequest(reg, "skin", "neg", 0.18, 0.45, 3, "face_3", use_lora=False))
    assert comfy.graphs[0]["2"]["inputs"]["strength_model"] == 0.0 and out.effective_parameters["lora"] is None


def test_style_profile_goes_into_prompts_and_negative():
    assert CFG.style.id == "smartphone_raw_v2" and len(CFG.style.reference_sha256) == 64
    styled = CFG.styled(CFG.pass_prompts["microdetail"])
    assert "{style}" not in styled and "light freckles" in styled and "visible pores" in styled
    assert CFG.styled("in a gym").startswith("in a gym, amateur smartphone photo")
    neg = CFG.negative_for(CFG.model("realvisxl"))
    assert "heavy makeup" in neg and "plastic skin" in neg and "nude" in neg
    assert "bokeh" in neg and "studio lighting" in neg and "DSLR" in neg  # sem cara de foto profissional


async def test_analyzer_keep_alive_nodes_join_the_graph():
    from app.validation_backends.comfyui import ComfyImageAnalyzer

    class C(FakeComfyV2):
        async def wait_for_completion(self, prompt_id):
            return {"prompt_id": prompt_id, "outputs": {"lf": {"text": ["[]"]}}}

    comfy = C()
    keep = {"ka1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "RealVisXL_V5.0_fp16.safetensors"}}}
    await ComfyImageAnalyzer(comfy, keep_alive=keep).analyze(ProviderImage("comfyui", "a.png [output]", "", 832, 1216), MASTER)
    await ComfyImageAnalyzer(comfy).analyze(ProviderImage("comfyui", "a.png [output]", "", 832, 1216), MASTER)
    assert "ka1" in comfy.graphs[0] and "ka1" not in comfy.graphs[1]  # V1 continua igual
