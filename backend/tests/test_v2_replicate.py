"""V2, modo replicar foto: leitura da referencia (quem vira a Luna, camera, luz,
campos), troca da pessoa mantendo a foto e passadas presas a pessoa."""
import io
import json

import numpy as np
import pytest
from PIL import Image

from app.clients.comfyui_client import GenerationOutputImage
from app.core.generation.reference import (
    MIRROR,
    SELFIE,
    THIRD,
    LightStats,
    ReferenceError,
    ReferenceSheet,
    camera_type,
    choose_target,
    parse_fields,
    sam_points,
)
from app.core.validation.analysis import DetectedFace
from app.providers.base import (
    GenerationParameters,
    NegativeSet,
    PromptSections,
    ProviderImage,
    RegionPassRequest,
    SceneRequest,
)
from app.providers.comfyui.person_replace import ComfyPersonReplaceAdapter, replace_crop
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter
from app.providers.comfyui.session import ComfySession
from app.validation_backends.reference import ComfyReferenceReader, background_change, light_stats
from app.workflow_manager.manager import WorkflowManager
from tests.conftest import REPO
from tests.fakes import STANDING, analysis, body
from tests.test_v2_models import FakeComfyV2
from tests.test_v2_multipass import CFG, MASTER, look, run, runner


def woman(bbox=(380.0, 60.0, 460.0, 170.0), score=0.9, sex="F", age=26.0):
    return DetectedFace(bbox=bbox, similarity=0.2, age=age, sex=sex, det_score=score,
                        kps=[(400, 100), (440, 100), (420, 120), (405, 145), (435, 145)])


def sheet(**kw):
    a = analysis([woman(), woman((600.0, 80.0, 660.0, 150.0), sex="M")], [body()])
    face, b, others, warnings = choose_target(a)
    data = dict(image="ref.png", width=832, height=1216, target_face=face, target_body=b, others=others,
                caption="a woman taking a mirror selfie in a bathroom", warnings=warnings,
                light=LightStats(90.0, 50.0, 20.0, 0.3, 0.0, 1.1))
    data.update(kw)
    return ReferenceSheet(**data)


# --- quem vira a Luna -----------------------------------------------------------


def test_target_is_the_most_prominent_woman_and_others_stay():
    small = woman((100.0, 100.0, 130.0, 140.0))
    a = analysis([small, woman(), woman((600.0, 80.0, 660.0, 150.0), sex="M")], [body()])
    face, b, others, warnings = choose_target(a)
    assert face.bbox == (380.0, 60.0, 460.0, 170.0) and b is not None
    assert len(others) == 2 and any("2 mulheres" in w for w in warnings)


def test_no_woman_or_no_face_is_explicit_error():
    with pytest.raises(ReferenceError, match="mulher"):
        choose_target(analysis([woman(sex="M")], [body()]))
    with pytest.raises(ReferenceError, match="rosto"):
        choose_target(analysis([], [body()]))


def test_body_not_found_is_a_warning():
    _, b, _, warnings = choose_target(analysis([woman()], []))
    assert b is None and any("corpo" in w for w in warnings)


@pytest.mark.parametrize("caption, wrist, expected", [
    ("a woman taking a mirror selfie", None, MIRROR),
    ("selfie of a woman in a car", None, SELFIE),
    ("a woman in a park", (2.0, 600.0, 0.9), SELFIE),  # braco ate a borda
    ("a woman in a park", (300.0, 600.0, 0.9), THIRD),
])
def test_camera_type(caption, wrist, expected):
    kps = list(STANDING)
    if wrist:
        kps[4] = wrist
        kps[7] = wrist
    big_face = woman((300.0, 60.0, 600.0, 400.0))
    assert camera_type(caption, big_face, body(kps), 832, 1216) == expected


def test_sam_points_cover_face_hair_and_torso_and_avoid_others():
    pos, neg = (json.loads(x) for x in sam_points(sheet()))
    assert {"x": 420, "y": 115} in pos and len(pos) == 3  # rosto, topo da cabeca, tronco
    assert neg == [{"x": 630, "y": 115}]  # rosto do homem fica de fora
    pos2, neg2 = (json.loads(x) for x in sam_points(sheet(others=[])))
    assert len(neg2) == 1 and neg2[0]["x"] in (4, 827)  # canto inofensivo


def test_fields_from_llm_are_parsed_safely():
    assert parse_fields('ok: {"pose": "sitting", "clothing": "red top", "extra": "x", "objects": 3}') == {
        "pose": "sitting", "clothing": "red top"}
    assert parse_fields("sem json") == {} and parse_fields("{quebrado") == {}


def test_description_uses_fields_camera_and_measured_light():
    s = sheet(fields={"pose": "standing, phone in right hand", "clothing": "black sports top", "environment": "small bathroom"},
              camera=MIRROR)
    d = s.description()
    assert d.startswith("mirror selfie") and "black sports top" in d and "small bathroom" in d and "warm tones" in d
    assert "bathroom" in sheet(fields={}).description()  # sem campos: descricao crua


def test_light_label_detects_flash_and_dim():
    assert "direct phone flash" in LightStats(70.0, 60.0, 0.0, 0.2, 0.0, 1.8).label()
    assert LightStats(60.0, 20.0, -15.0, 0.1, 0.0, 1.0).label().startswith("dim low light, cool tones")


def test_light_stats_and_background_change_on_pixels():
    ref = Image.new("RGB", (100, 100), (120, 100, 80))
    stats = light_stats(ref, (10, 10, 30, 30))
    assert stats.brightness == pytest.approx(0.299 * 120 + 0.587 * 100 + 0.114 * 80, abs=0.5) and stats.warmth == 40.0
    mask = Image.new("L", (100, 100), 0)
    mask.paste(255, (40, 40, 60, 60))
    final = ref.copy()
    final.paste((10, 200, 10), (40, 40, 60, 60))  # so a pessoa mudou
    assert background_change(ref, final, mask) == 0.0
    final.paste((10, 200, 10), (0, 0, 10, 10))  # fundo mexido
    assert background_change(ref, final, mask) == pytest.approx(100 / (100 * 100 - 400), abs=1e-4)


# --- leitor no ComfyUI --------------------------------------------------------------


class ReaderComfy(FakeComfyV2):
    def __init__(self, faces, caption="a woman taking a selfie in a cafe"):
        super().__init__()
        self.faces, self.caption = faces, caption

    async def wait_for_completion(self, prompt_id):
        people = [{"pose_keypoints_2d": [v for kp in STANDING for v in kp]}]
        return {"outputs": {"lf": {"text": [json.dumps(self.faces)]}, "dwp": {"text": [json.dumps(
            [{"canvas_width": 832, "canvas_height": 1216, "people": people}])]}, "frp": {"text": [self.caption]}}}

    async def download_file(self, filename, sub="", folder="output"):
        buf = io.BytesIO()
        Image.new("RGB", (832, 1216), (90, 90, 90)).save(buf, "PNG")
        return buf.getvalue()


async def test_reader_builds_the_sheet_and_uses_the_llm_when_present():
    faces = [{"bbox": [380, 60, 460, 170], "score": 0.9, "sex": "F", "age": 25, "sim": 0.3, "kps": [[400, 100], [440, 100]]}]
    comfy = ReaderComfy(faces)

    async def ask(prompt):
        assert "a woman taking a selfie in a cafe" in prompt
        return '{"pose": "holding the phone", "clothing": "white tank top", "environment": "cafe"}'

    s = await ComfyReferenceReader(comfy, ask=ask).read(ProviderImage("comfyui", "ref.png", ""), MASTER)
    assert s.camera == SELFIE and s.fields["clothing"] == "white tank top" and s.target_body is not None
    assert s.light.brightness == pytest.approx(90.0) and s.width == 832
    g = comfy.graphs[0]
    assert g["fm"]["inputs"]["model"] == "MiaoshouAI/Florence-2-large-PromptGen-v2.0"  # mesmo modelo da V1
    assert comfy.uploads[0].startswith("refmaster_")


async def test_reader_without_llm_falls_back_to_caption_with_warning():
    faces = [{"bbox": [380, 60, 460, 170], "score": 0.9, "sex": "F", "age": 25}]

    async def broken(prompt):
        raise RuntimeError("ollama fora")

    s = await ComfyReferenceReader(ReaderComfy(faces), ask=broken).read(ProviderImage("comfyui", "ref.png", ""), MASTER)
    assert s.fields == {} and any("texto cru" in w for w in s.warnings)


async def test_reader_refuses_photo_without_a_woman():
    with pytest.raises(ReferenceError):
        await ComfyReferenceReader(ReaderComfy([{"bbox": [1, 1, 50, 50], "score": 0.9, "sex": "M"}])).read(
            ProviderImage("comfyui", "ref.png", ""), MASTER)


# --- troca da pessoa (base do modo replicar) ------------------------------------------


class ReplaceComfy(FakeComfyV2):
    async def get_object_info(self):
        return {"Sam2Segmentation": {}, "DownloadAndLoadSAM2Model": {}, "GrowMaskWithBlur": {}}

    def extract_images(self, entry):
        n = entry["prompt_id"]
        return [GenerationOutputImage(f"{n}_mask_00001_.png", "", "output", "m"),
                GenerationOutputImage(f"{n}_00001_.png", "", "output", f"http://x/{n}.png")]


def replace_adapter(comfy):
    model = CFG.model("realvisxl")
    return ComfyPersonReplaceAdapter(ComfySession(comfy, WorkflowManager(REPO / "workflows")), model, CFG.lora,
                                     CFG.negative_for(model), CFG.replicate)


async def test_person_replace_keeps_the_photo_and_returns_the_person_mask():
    comfy = ReplaceComfy()
    adapter = replace_adapter(comfy)
    assert await adapter.validate_configuration() == []
    s = sheet()
    req = SceneRequest(prompt=PromptSections("lunavox, a woman", "", s.description(), "",
                                             negative=NegativeSet(global_terms=["extra limbs"])),
                       parameters=GenerationParameters(seed=11), lora={})
    out = await adapter.bind(s).generate(req)
    g = comfy.graphs[0]
    assert g["10"]["inputs"]["image"] == "ref.png" and g["38"]["inputs"]["denoise"] == 0.65
    assert g["41"]["inputs"]["destination"] == ["10", 0]  # cola de volta na FOTO original
    x, y, w, h = replace_crop(s.person_box(), 832, 1216)
    assert (g["30"]["inputs"]["x"], g["30"]["inputs"]["width"]) == (x, w) and w % 8 == 0
    assert json.loads(g["25"]["inputs"]["coordinates_negative"]) == [{"x": 630, "y": 115}]
    assert out.image.locator == "p1_00001_.png [output]" and (out.image.width, out.image.height) == (832, 1216)
    assert out.effective_parameters["clip_mask"] == "p1_mask_00001_.png [output]"
    assert "extra limbs" in out.effective_parameters["negative"] and "heavy makeup" in out.effective_parameters["negative"]


async def test_person_replace_requires_the_reference_sheet():
    from app.providers.base import ProviderConfigurationError
    with pytest.raises(ProviderConfigurationError):
        await replace_adapter(ReplaceComfy()).generate(SceneRequest(PromptSections("a", "", "", ""), GenerationParameters(seed=1), {}))


async def test_region_pass_is_clipped_to_the_person():
    comfy = FakeComfyV2()
    a = ComfyRegionPassAdapter(ComfySession(comfy, WorkflowManager(REPO / "workflows")), CFG.model("realvisxl"), CFG.lora)
    from app.core.generation.multipass import body_region
    from tests.fakes import face
    reg = body_region(body(), face(0.5), "body_full", 832, 1216).to_dict()
    await a.refine(ProviderImage("comfyui", "x.png [output]", "u", 832, 1216),
                   RegionPassRequest(reg, "body", "neg", 0.25, 0.6, 3, "body_1", clip_mask="m.png [output]"))
    g = comfy.graphs[0]
    assert g["c1"]["inputs"]["image"] == "m.png [output]" and g["c4"]["inputs"]["operation"] == "multiply"
    assert g["18"]["inputs"]["mask"] == ["c4", 0] and g["c3"]["inputs"]["x"] == reg["crop"]["x"]


async def test_runner_forwards_the_person_mask_to_every_pass():
    r, region = runner({"base": look(0.40)})

    async def base_with_mask(request):
        out = await type(r.base).generate(r.base, request)
        out.effective_parameters["clip_mask"] = "pessoa.png [output]"
        return out

    r.base.generate = base_with_mask
    await run(r)
    assert {c[1].clip_mask for c in region.calls} == {"pessoa.png [output]"}


def test_replicate_settings_follow_the_users_decisions():
    rep = CFG.replicate
    assert rep["denoise"] == 0.65 and rep["max_drift_retries"] == 2 and rep["checks"]["min_final_face"] == 0.70
    assert "grain" not in rep["positive"] and "muted" not in rep["positive"]  # sem filtro inventado
    assert np is not None



# --- regras da persona no modo replicar: cabelo dela, sem tatuagem ---------------------------


def test_prep_passes_are_configured_for_hair_and_tattoos():
    assert [(p.kind, p.mask, p.denoise) for p in CFG.pre_passes] == [("prep", "hair", 0.8), ("prep", "arms", 0.55)]
    rep = CFG.replicate
    assert "very dark brown" in rep["pre_pass_prompts"]["hair"] and "no tattoos" in rep["pre_pass_prompts"]["arms"]
    assert "tattoos" in rep["extra_negative"] and "blonde hair" in rep["extra_negative"]


def test_hair_region_covers_the_long_hair_but_not_the_face():
    from app.core.generation.multipass import hair_region
    f = woman()
    reg = hair_region(f, 832, 1216)
    inc = [s for s in reg.shapes if s.include][0]
    exc = [s for s in reg.shapes if not s.include][0]
    assert reg.crop.y + reg.crop.h > f.bbox[3] + 2 * (f.bbox[3] - f.bbox[1])  # ate abaixo do peito
    assert exc.rx < (f.bbox[2] - f.bbox[0]) / 2 and inc.rx > exc.rx


def test_arms_region_stops_before_the_hands_and_needs_arms():
    from app.core.generation.multipass import arms_region
    b = body()
    reg = arms_region(b, woman(), 832, 1216)
    inc = [s for s in reg.shapes if s.include]
    wrist = b.keypoints[4]
    assert len(inc) == 20 and all(abs(s.cx + reg.crop.x - wrist[0]) + abs(s.cy + reg.crop.y - wrist[1]) > 5 for s in inc)
    no_arms = body([(x, y, 0.0 if i in (3, 4, 6, 7) else c) for i, (x, y, c) in enumerate(STANDING)])
    assert arms_region(no_arms, None, 832, 1216) is None


async def test_prep_runs_before_the_locked_passes_and_ignores_the_photo_persons_identity():
    r, region = runner({"base": look(0.30), "prep_1": look(0.20), "prep_2": look(0.21), "face_1": look(0.78)})
    prompts = {**CFG.pass_prompts, **CFG.replicate["pre_pass_prompts"], "body": "x"}
    from app.providers.base import GenerationParameters, PromptSections, SceneRequest
    res = await r.run(SceneRequest(PromptSections("lunavox, a woman", "", "x", ""), GenerationParameters(seed=1), {}), MASTER,
                      list(CFG.face_passes), list(CFG.body_passes), prompts, "neg", (CFG.lora.file, 1.0), ("realvisxl", "x"),
                      pre_passes=list(CFG.pre_passes))
    names = [c[1].name for c in region.calls]
    assert names[:3] == ["prep_1", "prep_2", "face_1"]
    assert region.calls[1][0] == "prep_1" and region.calls[2][0] == "prep_2"  # cada passada parte da anterior
    assert [d["status"] for d in res.decisions[:2]] == ["ACCEPTED", "ACCEPTED"]  # rosto da pessoa da foto caiu: nao importa


async def test_prep_that_moves_the_pose_is_rolled_back():
    from tests.fakes import WALKING
    r, region = runner({"base": look(0.30), "prep_1": look(0.30, kps=WALKING)})
    from app.providers.base import GenerationParameters, PromptSections, SceneRequest
    prompts = {**CFG.pass_prompts, **CFG.replicate["pre_pass_prompts"], "body": "x"}
    res = await r.run(SceneRequest(PromptSections("a", "", "x", ""), GenerationParameters(seed=1), {}), MASTER,
                      [], [], prompts, "neg", (CFG.lora.file, 1.0), ("realvisxl", "x"), pre_passes=list(CFG.pre_passes))
    assert res.decisions[0]["status"] == "ROLLBACK" and "pose mudou" in res.decisions[0]["reasons"][0]
