"""Persona Replacement V1: mascaras, etapas de rosto/corpo, integracao de luz/cor/
grao, bordas, validacao (fundo, roupa, pose, identidade), rollback, orquestrador,
adapters do ComfyUI, trava de custo e independencia da geracao."""
import copy
import io
import json
import re

import numpy as np
import pytest
from PIL import Image

from app.core.generation.budget import BudgetExceeded, BudgetGuard, ExperimentPlan
from app.core.generation.multipass import Measure
from app.core.generation.reference import LightStats, ReferenceSheet
from app.core.persona_replacement.blending import composite, edge_metrics
from app.core.persona_replacement.contracts import (
    RawSegments,
    ReplacementConfigError,
    TransformResult,
    load_replacement_config,
    parse_replacement_config,
)
from app.core.persona_replacement.face_transform import build_request, stage_kind
from app.core.persona_replacement.lighting import (
    match_grain,
    match_lighting,
    needs_hair_recolor,
    noise_level,
    recolor_hair,
    to_ycc,
)
from app.core.persona_replacement.replacement_orchestrator import ReplacementOrchestrator
from app.core.persona_replacement.rollback import Checkpoint, CheckpointStore, stage_reasons
from app.core.persona_replacement.segmentation import build_masks, dilate, ellipse, skin_pixels
from app.core.persona_replacement.validation import (
    background_change,
    clothing_change,
    lighting_consistency,
    texture_consistency,
    validate,
)
from app.core.validation.analysis import DetectedFace
from tests.conftest import BACKEND, REPO
from tests.fakes import STANDING, analysis, body, face
from tests.test_v2_multipass import MASTER

CFG = load_replacement_config(REPO / "config" / "persona_replacement.json")
RAW = json.loads((REPO / "config" / "persona_replacement.json").read_text(encoding="utf-8"))
H, W = 240, 160
SKIN = (205, 150, 120)
SHIRT = (40, 60, 160)
BG = (120, 170, 210)
BLONDE = (225, 200, 140)
FACE_BOX = (60.0, 30.0, 100.0, 80.0)
KPS = [(72.0, 50.0), (88.0, 50.0), (80.0, 60.0), (73.0, 70.0), (87.0, 70.0)]


def photo():
    """Foto sintetica: fundo azul, cabelo loiro, rosto/pescoco/braco de pele, camisa azul escura."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = BG
    person = np.zeros((H, W), np.float32)
    person[20:230, 40:120] = 1
    hair = np.zeros((H, W), np.float32)
    hair[20:40, 50:110] = 1
    img[person > 0] = SKIN
    img[110:230, 40:120] = SHIRT  # roupa
    img[110:230, 40:52] = SKIN  # braco de fora
    img[hair > 0] = BLONDE
    return img, person, hair


def masks_and_photo(protect=()):
    img, person, hair = photo()
    return build_masks(RawSegments(person, hair, list(protect)), FACE_BOX, KPS, img), img


# --- configuracao ----------------------------------------------------------------------


def test_config_follows_the_spec():
    names = [s.name for s in CFG.stages]
    assert names == ["face_pass_1", "face_pass_2", "face_pass_3", "body_pass_1", "body_pass_2"]
    s = {x.name: x for x in CFG.stages}
    assert 0.70 <= s["face_pass_1"].strength <= 0.80 and s["face_pass_1"].identity_adapter == "instantid"
    assert 0.30 <= s["face_pass_2"].strength <= 0.45
    assert 0.10 <= s["face_pass_3"].strength <= 0.20 and s["face_pass_3"].identity_adapter is None and not s["face_pass_3"].lora
    assert 0.40 <= s["body_pass_1"].strength <= 0.60 and 0.15 <= s["body_pass_2"].strength <= 0.30
    assert all(x.mask == "body_skin" for x in CFG.stages if x.kind == "body")
    assert CFG.budget_limit_usd == 0.05 and CFG.generation_config == "config/persona_engine_v2.json"


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["stages"][2].update(identity_adapter="instantid", adapter_weight=0.5), "INTEGRACAO"),
    (lambda d: d["stages"][2].update(denoise=0.4), "INTEGRACAO"),
    (lambda d: d["stages"][3].update(mask="face_full"), "roupa"),
    (lambda d: d["stages"][0].update(strength=1.3), "fora de 0-1"),
    (lambda d: d["stages"][1].update(mask="body"), "invalida"),
    (lambda d: d["checks"].pop("max_background_changed"), "max_background_changed"),
    (lambda d: d["stages"].append(dict(d["stages"][0])), "repetidos"),
])
def test_invalid_config_is_refused(mutate, message):
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(ReplacementConfigError, match=message):
        parse_replacement_config(data)


# --- mascaras ---------------------------------------------------------------------------


def test_masks_follow_the_person_and_protect_the_clothing():
    m, img = masks_and_photo()
    assert (m.face_full * (1 - m.person)).sum() == 0  # rosto so dentro da pessoa
    assert m.face_inner.sum() < m.face_full.sum()
    assert m.clothing[150, 80] == 1 and m.body_skin[150, 80] == 0  # camisa = roupa, nunca corpo
    assert m.body_skin[150, 45] == 1  # braco de fora = pele do corpo
    assert m.hair[25, 80] == 1 and m.face_transition[25, 80] == 0  # transicao nao invade o cabelo
    assert (m.body_skin * m.face_full).sum() == 0
    assert m.areas()["person"] == pytest.approx((210 * 80) / (H * W), abs=1e-3)


def test_protected_objects_never_enter_a_face_stage():
    m, _ = masks_and_photo(protect=[(68.0, 45.0, 92.0, 56.0)])  # oculos
    assert m.protect[50, 80] == 1 and m.face_full[50, 80] == 0 and m.face_inner[50, 80] == 0


def test_dilate_does_not_wrap_around_the_image():
    mask = np.zeros((20, 20), np.float32)
    mask[:, 0] = 1
    d = dilate(mask, 3)
    assert d[:, :4].all() and d[:, -1].sum() == 0


def test_skin_classifier():
    px = np.array([[SKIN, SHIRT, BG]], np.uint8)
    assert list(skin_pixels(px)[0]) == [1.0, 0.0, 0.0]


# --- etapas de rosto e corpo --------------------------------------------------------------


def test_face_and_body_requests():
    m, _ = masks_and_photo()
    f1 = build_request(CFG.stage("face_pass_1"), m, "img.png", "neg", 5, MASTER)
    assert f1.identity_adapter == "instantid" and f1.reference is MASTER and f1.denoise == 0.45
    b1 = build_request(CFG.stage("body_pass_1"), m, "img.png", "neg", 5, MASTER)
    assert b1.reference is None and (b1.mask * m.clothing).sum() == 0
    assert [stage_kind(CFG.stage(n)) for n in ("face_pass_1", "face_pass_2", "face_pass_3", "body_pass_1")] == [
        "identity", "face", "integration", "body"]


def test_empty_mask_skips_the_stage():
    m, _ = masks_and_photo()
    m.body_skin[:] = 0
    assert build_request(CFG.stage("body_pass_1"), m, "img.png", "neg", 5, None) is None


# --- luz, cor, cabelo, grao e bordas -------------------------------------------------------


def test_lighting_takes_the_original_light_only_inside_the_region():
    original = np.full((50, 50, 3), 90, np.uint8)
    new = np.full((50, 50, 3), 180, np.uint8)
    region = np.zeros((50, 50), np.float32)
    region[10:40, 10:40] = 1
    out = match_lighting(new, original, region, luma=1.0, chroma=0.5)
    assert abs(int(out[25, 25, 0]) - 90) <= 2 and (out[0, 0] == new[0, 0]).all()


def test_hair_is_recolored_without_regeneration():
    img, person, hair = photo()
    assert needs_hair_recolor(img, hair, 95)
    out = recolor_hair(img, hair, 48, 122, 140, 0.85)
    y = to_ycc(out)[..., 0]
    assert y[hair > 0].mean() < 80 and (out[hair == 0] == img[hair == 0]).all()
    assert not needs_hair_recolor(out, hair, 95)


def test_grain_is_matched_to_the_photo():
    rng = np.random.default_rng(1)
    noisy = np.clip(128 + rng.normal(0, 6, (60, 60, 3)), 0, 255).astype(np.uint8)
    smooth = np.full((60, 60, 3), 128, np.uint8)
    region = np.ones((60, 60), np.float32)
    ref = noise_level(noisy, region)
    out = match_grain(smooth, region, ref, seed=3)
    assert noise_level(out, region) == pytest.approx(ref, rel=0.35)


def test_composite_never_leaks_outside_the_mask():
    base = np.zeros((40, 40, 3), np.uint8)
    top = np.full((40, 40, 3), 255, np.uint8)
    mask = np.zeros((40, 40), np.float32)
    mask[10:30, 10:30] = 1
    out = composite(base, top, mask, 4)
    assert out[mask == 0].max() == 0 and out[20, 20, 0] > 200


def test_edge_metrics_detect_a_halo():
    img, person, _ = photo()
    clean = edge_metrics(img, img.copy(), person, 4)
    assert clean["halo"] == 0.0
    halo = img.copy()
    ring = np.clip(dilate(person, 4) - person, 0, 1) > 0
    halo[ring] = (30, 30, 30)
    assert edge_metrics(img, halo, person, 4)["halo"] > 50


# --- validacao -------------------------------------------------------------------------------


def test_background_and_clothing_preservation():
    img, person, _ = photo()
    m, _ = masks_and_photo()
    out = img.copy()
    out[30:40, 70:90] = (10, 10, 10)  # dentro do rosto: permitido
    assert background_change(img, out, person) == 0.0 and clothing_change(img, out, m.clothing) == 0.0
    out[0:10, 0:10] = (0, 0, 0)  # fundo
    out[150:160, 70:90] = (200, 0, 0)  # roupa
    assert background_change(img, out, person) > 0 and clothing_change(img, out, m.clothing) > 0


def test_lighting_and_texture_scores():
    img, person, _ = photo()
    region = np.zeros((H, W), np.float32)
    region[40:80, 60:100] = 1
    assert lighting_consistency(img, img, region)["score"] == 1.0
    darker = img.copy()
    darker[region > 0] = (np.asarray(SKIN) * 0.5).astype(np.uint8)
    assert lighting_consistency(img, darker, region)["score"] < 0.3
    grainy = np.clip(img.astype(np.int16) + np.random.default_rng(2).normal(0, 5, img.shape), 0, 255).astype(np.uint8)
    same = texture_consistency(grainy, grainy, region, person)
    plastic = grainy.copy()
    plastic[region > 0] = SKIN  # regiao lisa numa foto granulada
    assert same["score"] > 0.8 and texture_consistency(grainy, plastic, region, person)["score"] < 0.3


def test_validate_reports_independent_metrics_and_failures():
    img, person, _ = photo()
    m, _ = masks_and_photo()
    region = m.face_full
    ok = validate(img, img, m, region, Measure(0.8, 27.0, None, 0.01, 1, 1), CFG.checks, 4)
    assert ok.status == "PASS" and ok.background_changed == 0.0 and ok.body == "UNKNOWN" and ok.anatomy == "UNKNOWN"
    bad_img = img.copy()
    bad_img[0:40, 0:40] = 0
    bad = validate(img, bad_img, m, region, Measure(0.6, 27.0, None, 0.2, 2, 2), CFG.checks, 4)
    assert set(bad.failures) == {"identity_low", "background_changed", "pose_changed", "persona_duplicated"}


# --- rollback ---------------------------------------------------------------------------------


def test_rollback_rules_per_stage_kind():
    before = Measure(0.40, 27.0, None, 0.0, 0, 1)
    assert stage_reasons("identity", before, Measure(0.80, 27.0, None, 0.01, 1, 1), {}, CFG.checks) == []
    assert "nao aumentou" in stage_reasons("identity", before, Measure(0.38, 27.0, None, 0.0, 0, 1), {}, CFG.checks)[0]
    mid = Measure(0.80, 27.0, None, 0.0, 1, 1)
    assert stage_reasons("integration", mid, Measure(0.785, 27.0, None, 0.0, 1, 1), {}, CFG.checks)  # > 0.01
    assert stage_reasons("face", mid, Measure(0.785, 27.0, None, 0.0, 1, 1), {}, CFG.checks) == []  # <= 0.02
    reasons = stage_reasons("body", mid, mid, {"background_changed": 0.05, "clothing_changed": 0.2}, CFG.checks)
    assert any("fundo" in r for r in reasons) and any("roupa" in r for r in reasons)


def test_checkpoint_store_keeps_the_best_state():
    s = CheckpointStore()
    a = Checkpoint("original", "o", np.zeros((2, 2, 3)), None)
    b = Checkpoint("face_pass_1", "f1", np.ones((2, 2, 3)), None)
    c = Checkpoint("face_pass_2", "f2", np.ones((2, 2, 3)), None)
    s.add(a, True)
    s.add(b, True)
    s.add(c, False)
    assert s.current is b and s.names() == ["original", "face_pass_1", "face_pass_2"]


# --- orquestrador com pecas falsas ----------------------------------------------------------------


class Store:
    def __init__(self, img):
        self.images = {"foto.png": img}

    async def load(self, image):
        return self.images[image].copy()

    async def save(self, pixels, name):
        key = f"{name}_{len(self.images)}.png"
        self.images[key] = pixels.copy()
        return key


class Reader:
    async def read(self, image, master):
        f = DetectedFace(bbox=FACE_BOX, similarity=0.3, age=30.0, sex="F", det_score=0.9, kps=KPS)
        return ReferenceSheet(image=image.locator, width=W, height=H, target_face=f, target_body=body(), others=[],
                              caption="a woman", light=LightStats(120, 40, 10, 0.3, 0.0, 1.0))


class Segmenter:
    async def segment(self, image, sheet):
        _, person, hair = photo()
        return RawSegments(person, hair, [])


class Transformer:
    """Pinta a mascara de cinza E suja o fundo (o orquestrador tem de desfazer)."""

    def __init__(self, store):
        self.store = store
        self.calls = []

    async def transform(self, req):
        self.calls.append(req)
        px = self.store.images[req.image].copy()
        px[req.mask > 0.5] = (150, 120, 110)
        px[0:5, 0:5] = (0, 0, 0)
        key = await self.store.save(px, req.name + "_raw")
        return TransformResult(key, 7.0, {"name": "Fake GPU"})


class Analyzer:
    def __init__(self, faces):
        self.faces = faces  # nome da etapa -> semelhanca

    async def analyze(self, image, master):
        name = next((k for k in self.faces if image.locator.startswith(k)), "foto")
        return analysis([face(self.faces[name], age=27)], [body(STANDING)])


def orchestrator(faces, img=None):
    img = img if img is not None else photo()[0]
    store = Store(img)
    tr = Transformer(store)
    orch = ReplacementOrchestrator(reader=Reader(), segmenter=Segmenter(), transformer=tr, analyzer=Analyzer(faces),
                                   store=store, config=CFG, duplicate_similarity=0.9, price_per_hour=0.57)
    return orch, tr, store


async def test_orchestrator_runs_all_stages_and_preserves_the_background():
    faces = {"foto": 0.30, "hair": 0.30, "face_pass_1": 0.80, "face_pass_2": 0.81, "face_pass_3": 0.805,
             "body_pass_1": 0.80, "body_pass_2": 0.80, "integrated": 0.80}
    orch, tr, store = orchestrator(faces)
    res = await orch.run("foto.png", MASTER, "plastic skin", 7)
    assert [c.name for c in tr.calls] == ["face_pass_1", "face_pass_2", "face_pass_3", "body_pass_1", "body_pass_2"]
    assert res.checkpoints[:2] == ["original", "hair_recolor"] and res.checkpoints[-1] == "integrated"
    original = store.images["foto.png"]
    final = res.final.pixels
    _, person, _ = photo()
    assert background_change(original, final, person) == 0.0  # o fundo sujo pelo modelo foi desfeito
    assert res.report.background_changed == 0.0 and res.report.identity == 0.80
    assert "tattoos" in tr.calls[0].negative and tr.calls[0].reference is MASTER
    assert all(r.accepted for r in res.records) and res.records[1].cost_usd == pytest.approx(0.57 * 7 / 3600, abs=1e-5)


async def test_orchestrator_rolls_back_a_stage_that_lowers_identity():
    faces = {"foto": 0.30, "hair": 0.30, "face_pass_1": 0.80, "face_pass_2": 0.70, "face_pass_3": 0.80,
             "body_pass_1": 0.80, "body_pass_2": 0.80, "integrated": 0.80}
    orch, tr, _ = orchestrator(faces)
    res = await orch.run("foto.png", MASTER, "neg", 7)
    rec = {r.stage: r for r in res.records}
    assert not rec["face_pass_2"].accepted and "identidade caiu" in rec["face_pass_2"].rollback_reason
    assert tr.calls[2].image.startswith("face_pass_1")  # a passada 3 partiu do checkpoint bom


async def test_dark_hair_is_not_recolored():
    img, person, hair = photo()
    img[hair > 0] = (40, 30, 25)
    orch, _, _ = orchestrator({"foto": 0.3}, img)
    res = await orch.run("foto.png", MASTER, "neg", 7)
    assert "hair_recolor" not in res.checkpoints


# --- adapters do ComfyUI ------------------------------------------------------------------------------


async def test_transformer_uses_the_stage_mask_as_clip():
    from app.core.generation.v2_config import load_v2_config
    from app.providers.comfyui.region_pass import ComfyRegionPassAdapter
    from app.providers.comfyui.replacement import ComfyReplacementTransformer, crop_for
    from app.providers.comfyui.session import ComfySession
    from app.workflow_manager.manager import WorkflowManager
    from tests.test_v2_models import FakeComfyV2
    from app.core.persona_replacement.contracts import TransformRequest

    v2 = load_v2_config(REPO / "config" / "persona_engine_v2.json")
    comfy = FakeComfyV2()
    region = ComfyRegionPassAdapter(ComfySession(comfy, WorkflowManager(REPO / "workflows")), v2.model(), v2.lora,
                                    identity_adapters=v2.identity_adapters)
    m, _ = masks_and_photo()
    tr = ComfyReplacementTransformer(region, comfy)
    await tr.transform(TransformRequest("foto.png", m.face_inner, "p", "n", 0.4, 0.3, 9, "face_pass_2"))
    g = comfy.graphs[0]
    crop = crop_for(m.face_inner)
    assert g["11"]["inputs"]["x"] == crop["x"] and g["c1"]["inputs"]["image"].startswith("replmask_")
    assert g["18"]["inputs"]["mask"] == ["c4", 0] and g["14"]["inputs"]["denoise"] == 0.3


def test_segment_workflow_only_measures():
    wf = json.loads((REPO / "workflows" / "replacement-segment.json").read_text(encoding="utf-8"))
    kinds = {n["class_type"] for n in wf["graph"].values()}
    assert {"Sam2Segmentation", "Florence2Run"} <= kinds and not kinds & {"KSampler", "CheckpointLoaderSimple"}


def test_parse_boxes():
    from app.providers.comfyui.replacement import parse_boxes
    assert parse_boxes('{"bboxes": [[1, 2, 3, 4]], "labels": ["sunglasses"]}') == [(1.0, 2.0, 3.0, 4.0)]
    assert parse_boxes("lixo") == []


# --- custo e independencia ---------------------------------------------------------------------------------


def test_first_test_estimate_is_blocked_above_five_cents_without_authorization():
    plan_max = ExperimentPlan("replacement 1o teste", "1 imagem", "integracao", images=1, seconds_per_image=200,
                              overhead_seconds=200, price_per_hour=0.57)
    with pytest.raises(BudgetExceeded):
        BudgetGuard(CFG.budget_limit_usd).check(plan_max)
    assert BudgetGuard(CFG.budget_limit_usd).check(plan_max, authorized_usd=0.07)["allowed_usd"] == 0.07


def test_generation_engine_never_imports_the_replacement():
    pattern = re.compile(r"^\s*(from|import)\s+app\.core\.persona_replacement", re.M)
    for path in (BACKEND / "app" / "core" / "generation").rglob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name
    for path in (BACKEND / "app" / "providers" / "comfyui").glob("*.py"):
        if path.name != "replacement.py":
            assert not pattern.search(path.read_text(encoding="utf-8")), path.name
