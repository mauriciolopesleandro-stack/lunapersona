"""Spec 'Master Implementation' do Replacement V2: config dedicada, Qwen so experimental, ReplacementRequest com
atalhos e forcas, analise estruturada da foto, mascaras com nome (tatuagem com prioridade sobre a pele),
QualityGate com validadores nomeados e hard fails, retry de residuo da pessoa original, modo debug e registro
por execucao. GPU mockada: prova o fluxo e as regras, nao a qualidade visual."""
import json

import numpy as np
import pytest

from app.core.engines.accessories import AccessoryLayer
from app.core.engines.policies import POLICIES
from app.core.engines.quality_gate import (
    QualityGate,
    check_person_count,
    measure_accessories,
    source_pixel_residual,
)
from app.core.engines.replacement import ReplacementRequestError
from app.core.engines.retry import RetryPolicyV2
from app.core.engines.scene_analysis import body_part, check_hierarchy, components, render_pose_map
from app.core.engines.validation import PASS, REJECT, WARN
from tests.conftest import REPO
from tests.test_engines_replacement import CFG, engine, req

LUNA = json.loads((REPO / "personas" / "luna" / "persona_sheet.json").read_text(encoding="utf-8"))
FACES = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "hand": 0.8, "tattoo": 0.8, "integrated": 0.8, "final": 0.8}

GOOD = {"identity": 0.8, "original_sim": 0.1, "pose": 0.02, "background": 0.0, "tattoo_residual": 0.0,
        "hair_residual": 0.0, "texture_final": 10.0, "texture_ref": 10.0, "tone_delta": 1.0, "persona_instances": 1,
        "faces": 1, "faces_original": 1, "shoulder_ratio": 1.0, "composition_shift": 0.0, "person_found": True,
        "seam_excess": 3.0, "straight_edges": 2.0, "hand_anatomy": 1.0, "accessory_change": 0.0, "clothing_change": 0.0,
        "source_face_pixels": 0.02, "source_body_pixels": 0.03, "accessory_objects": []}


# --- config dedicada e Qwen ----------------------------------------------------------------------------

def test_dedicated_config_has_the_spec_fields_and_qwen_is_off():
    for key in ("base_model", "lora", "identity", "pose", "depth", "skin_reconstruction", "tattoo_removal",
                "accessory_preservation", "source_identity_residual", "adaptive_retry", "qwen"):
        assert key in CFG, key
    assert CFG["qwen"] == {**CFG["qwen"], "enabled": False, "experimental_only": True}
    assert 0.25 <= CFG["depth"]["strength"] <= 0.45 and 0.75 <= CFG["pose"]["strength"] <= 0.9
    assert CFG["lora"]["trigger"] == "lunavox" and CFG["base_model"] == "RealVisXL_V5"


async def test_default_run_has_no_qwen_and_uses_config_strengths():
    eng, ad, _ = engine(FACES)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    assert "face_lock" not in [p["pass"] for p in out.telemetry.passes]
    assert out.telemetry.experimental_stages == [] and out.telemetry.fallback_used is False
    assert out.telemetry.controlnet_strength == {"pose": 0.8, "depth": 0.35}
    assert out.telemetry.reference_strength == 0.5


async def test_request_strengths_beat_the_config():
    eng, _, _ = engine(FACES)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, pose_strength=0.9, depth_strength=0.25,
                            identity_strength=0.4))
    assert out.telemetry.controlnet_strength == {"pose": 0.9, "depth": 0.25} and out.telemetry.reference_strength == 0.4
    rl = out.telemetry.run_log
    assert rl["pose_strength"] == 0.9 and rl["depth_strength"] == 0.25 and rl["instantid_strength"] == 0.4


async def test_qwen_requested_without_the_port_fails_loudly():
    eng, _, _ = engine(FACES)
    with pytest.raises(ReplacementRequestError, match="Qwen"):
        await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, qwen_face_lock=True))


# --- ReplacementRequest -------------------------------------------------------------------------------

def test_request_shortcuts_become_attribute_policy():
    r = req(persona_sheet=LUNA, remove_tattoos=False, accessories_required=True, clothing_required=False)
    pol = r.attributes()
    assert pol.get("tattoos") == "PRESERVE" and pol.get("accessories") == "PRESERVE" and pol.get("clothing") == "RECONSTRUCT"
    assert req(persona_sheet=LUNA, quality_profile="hyperrealistic").plan().name == "MAX_QUALITY"
    for bad in (dict(identity_strength=0.95), dict(pose_strength=1.5), dict(quality_profile="ultra"),
                dict(replacement_version="v1")):
        with pytest.raises(ReplacementRequestError):
            req(persona_sheet=LUNA, **bad).validate()


# --- analise da foto e mascaras --------------------------------------------------------------------------

async def test_scene_analysis_is_structured_data_with_named_undesired_marks():
    eng, ad, _ = engine(FACES)
    calls_before = len(ad.calls)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    sa = out.telemetry.attributes["scene_analysis"]
    for key in ("person_count", "scene", "pose", "clothing", "hair", "accessories", "undesired_attributes", "markings"):
        assert key in sa, key
    assert sa["person_count"] >= 1 and sa["pose"]["head_rotation"]
    assert any(u.startswith("tattoo_") for u in sa["undesired_attributes"])  # a foto sintetica tem tinta no braco
    assert len(ad.calls) > calls_before  # (a analise em si nao gera imagem: so as passadas chamam o adapter)


async def test_debug_mode_saves_every_named_mask_pose_map_and_report(monkeypatch):
    monkeypatch.setenv("REPLACEMENT_DEBUG", "true")
    eng, _, store = engine(FACES)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    inter = out.intermediates
    for name in ("original", "person_mask", "face_mask", "skin_mask", "hair_mask", "hand_mask", "clothing_mask",
                 "accessory_mask", "tattoo_mask", "background_mask", "pose_map", "initial_generation", "face_pass",
                 "skin_pass", "integration_pass", "final"):
        assert name in inter, name
    # o corpo da Persona ja refez a pele do braco: a limpeza de tatuagem nao precisou rodar (e isso fica dito)
    assert "tattoo_pass" not in inter and out.debug["passes_not_run"] == ["tattoo_pass"]
    tat, skin = store.images[inter["tattoo_mask"]][..., 0] > 127, store.images[inter["skin_mask"]][..., 0] > 127
    assert tat.any() and not (tat & skin).any()  # tatuagem tem prioridade: nunca e "pele"
    assert {"scene_analysis", "validation_report", "run_log", "gate"} <= set(out.debug)
    assert out.debug["mask_hierarchy_issues"] == []


def test_mask_hierarchy_rule_and_components():
    p = np.zeros((40, 40), np.float32)
    p[5:35, 5:35] = 1
    skin = p.copy()
    tat = np.zeros_like(p)
    tat[10:14, 10:14] = 1
    assert check_hierarchy({"person_mask": p, "skin_mask": skin, "tattoo_mask": tat}) == ["tatuagem dentro da mascara de pele"]
    assert check_hierarchy({"person_mask": p, "skin_mask": skin * (1 - tat), "tattoo_mask": tat}) == []
    tat[25:30, 25:30] = 1
    assert len(components(tat)) == 2


def test_body_part_names_follow_the_skeleton():
    kp = [(0.0, 0.0, 0.0)] * 18
    kp[2], kp[3], kp[4] = (100.0, 100.0, 0.9), (90.0, 180.0, 0.9), (85.0, 260.0, 0.9)  # ombro/cotovelo/pulso direitos
    kp[5], kp[8], kp[11] = (200.0, 100.0, 0.9), (110.0, 300.0, 0.9), (190.0, 300.0, 0.9)
    assert body_part(kp, (88.0, 225.0), None) == "right_forearm"
    assert body_part(kp, (95.0, 140.0), None) == "right_upper_arm"
    assert body_part(kp, (150.0, 150.0), None) == "chest"
    img = render_pose_map(320, 320, kp)
    assert img.shape == (320, 320, 3) and img.any()


# --- QualityGate ---------------------------------------------------------------------------------------

def test_gate_hard_fails_beat_a_great_arcface():
    gate = QualityGate(CFG)
    pol = {"tattoos": "REMOVE", "accessories": "PRESERVE", "face": "RECONSTRUCT"}
    assert gate.evaluate({**GOOD, "identity": 0.95}, pol).status == PASS
    cases = {"tattoo": {"tattoo_residual": 0.05},  # residuo pequeno: sem hard fail seria so aviso
             "skin": {"texture_final": 3.0},  # pele extremamente plastica
             "source_pixel_residual": {"source_face_pixels": 0.6},  # rosto original sobrando
             "person_count": {"faces": 2, "persona_instances": 1}}  # pessoa nova
    for check, bad in cases.items():
        rep = gate.evaluate({**GOOD, "identity": 0.95, **bad}, pol)
        assert rep.checks[check].status == REJECT and rep.status == REJECT, check
    soft = QualityGate({**CFG, "tattoo_removal": {"enabled": True, "hard_fail": False}})
    assert soft.evaluate({**GOOD, "tattoo_residual": 0.05}, pol).checks["tattoo"].status == WARN


def test_gate_decision_pass_retry_reject_and_named_validators():
    gate = QualityGate(CFG)
    pol = {"tattoos": "REMOVE"}
    bad = gate.evaluate({**GOOD, "tattoo_residual": 0.2}, pol)
    assert gate.decide(bad, can_retry=True).decision == "RETRY"
    final = gate.decide(bad, can_retry=False)
    assert final.decision == "REJECT" and "tattoo" in final.hard_fails
    assert final.validators["TattooResidualValidator"]["status"] == REJECT
    assert set(final.validators) >= {"IdentityValidator", "PoseValidator", "AnatomyValidator", "TattooResidualValidator",
                                     "SourceIdentityResidualValidator", "SkinConsistencyValidator",
                                     "AccessoryPreservationValidator", "ClothingPreservationValidator", "HandValidator",
                                     "HairValidator", "SceneConsistencyValidator", "PersonCountValidator"}
    assert gate.decide(gate.evaluate(GOOD, pol), can_retry=True).decision == "PASS"


def test_accessory_validator_measures_presence_shape_color_and_position():
    orig = np.full((60, 80, 3), (200, 160, 140), np.uint8)
    orig[20:24, 10:70] = (10, 10, 10)  # armacao
    mask = np.zeros((60, 80), np.float32)
    mask[20:24, 10:70] = 1
    layer = AccessoryLayer("glasses", "glasses", "PRESERVE", (8, 18, 72, 26), mask, 30, "glasses_dark")
    same = measure_accessories(orig, orig.copy(), [layer])[0]
    assert same["presence"] == 1.0 and same["color_delta"] == 0.0 and same["shape_iou"] == 1.0
    gone = orig.copy()
    gone[20:24, 10:70] = (200, 160, 140)  # oculos apagados
    m = measure_accessories(orig, gone, [layer])[0]
    assert m["presence"] < 0.5
    rep = QualityGate(CFG).evaluate({**GOOD, "accessory_objects": [m]}, {"accessories": "PRESERVE"})
    assert rep.checks["accessory_objects"].status == REJECT  # acessorio obrigatorio sumiu


def test_source_pixel_residual_and_person_count():
    a = np.full((30, 30, 3), 120, np.uint8)
    reg = np.ones((30, 30), np.float32)
    assert source_pixel_residual(a, a.copy(), reg) == 1.0
    assert source_pixel_residual(a, a + 40, reg) == 0.0
    assert check_person_count(1, 2).status == REJECT and check_person_count(2, 2).status == PASS


# --- retry por falha -------------------------------------------------------------------------------------

def test_source_residual_retry_grows_the_identity_mask():
    p, step = RetryPolicyV2().next_plan(POLICIES["QUALITY"], ["original_residual"], 1)
    assert p.extra["identity_grow"] == 1 and "ampliada" in step.strategy
    assert p.face_reference_strength == POLICIES["QUALITY"].face_reference_strength  # nao "sobe tudo"


async def test_engine_maps_gate_failures_to_their_own_retry():
    eng, ad, _ = engine({**FACES, "final": 0.8})
    out = await eng.run(req(persona_sheet=LUNA, advanced={"max_retries": 1}))
    rl = out.telemetry.run_log
    for key in ("replacement_id", "persona_id", "source_image", "master_face", "base_model", "lora", "lora_strength",
                "instantid_strength", "pose_strength", "depth_strength", "seed", "passes", "masks", "validators", "scores",
                "retry_reason", "final_status", "processing_time", "cost", "fallback_used"):
        assert key in rl, key
    assert out.telemetry.gate["decision"] in ("PASS", "REJECT")
    assert all(a["decision"] in ("PASS", "RETRY", "REJECT") for a in out.attempts)


async def test_identity_grow_enlarges_the_reconstructed_region():
    eng, ad, _ = engine(FACES)
    base = POLICIES["QUALITY"]
    await eng.run(req(persona_sheet=LUNA, advanced={"max_retries": 0}))
    first = next(c for c in ad.calls if c.stage == "identity").mask.sum()
    eng2, ad2, _ = engine(FACES)
    eng2._configured_orig = eng2._configured

    def grown(plan, r):
        p = eng2._configured_orig(plan, r)
        return p.with_(extra={**p.extra, "identity_grow": 1})

    eng2._configured = grown
    await eng2.run(req(persona_sheet=LUNA, advanced={"max_retries": 0}))
    assert next(c for c in ad2.calls if c.stage == "identity").mask.sum() > first
    assert base.extra == {}


# --- correcoes do A/B real (2026-10-08) ------------------------------------------------------------------

async def test_unmeasurable_hand_is_kept_from_the_photo_and_said_so():
    """Quarto: punho fechado sem dedos no DWPose virou mao 'fantasma'. Sem gesto medivel a etapa nao roda."""
    from dataclasses import replace as dc_replace

    from tests.fakes import body
    from tests.test_persona_replacement import Reader

    kps = [(0.0, 0.0, 0.0)] * 18
    kps[3], kps[4] = (46.0, 150.0, 0.9), (46.0, 200.0, 0.9)
    for i in (0, 1, 2, 5, 8, 11):
        kps[i] = (80.0, 60.0 + 10 * i, 0.9)

    class WristReader(Reader):
        async def read(self, image, master):
            return dc_replace(await super().read(image, master), target_body=body(kps))

    eng, ad, _ = engine(FACES)
    eng.reader = WristReader()
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    assert "hand_gesture_lock" not in [c.stage for c in ad.calls]
    hp = next(p for p in out.telemetry.passes if p["pass"] == "hand_gesture_lock")
    assert hp["accepted"] is False and "nao medivel" in hp["reason"]


def test_hand_mask_follows_the_forearm_not_the_face_height():
    """Close-up: rosto enorme. A mao tem o tamanho do antebraco, nao 0,8 x altura do rosto."""
    from types import SimpleNamespace

    from app.core.engines.replacement import ReplacementEngine

    kps = [(0.0, 0.0, 0.0)] * 18
    kps[3], kps[4] = (300.0, 500.0, 0.9), (300.0, 600.0, 0.9)  # antebraco de 100 px
    scene = SimpleNamespace(base_pose=kps, original=np.zeros((900, 700, 3), np.uint8), hands=[],
                            sheet=SimpleNamespace(target_face=SimpleNamespace(bbox=(200.0, 50.0, 500.0, 450.0))))
    m = ReplacementEngine._hand_mask(None, scene)
    assert m is not None and m.sum() < np.pi * 130 ** 2  # rosto de 400 px: antes eram ~320 px de raio
    assert ReplacementEngine._hand_mask(None, scene, measurable_only=True) is None
    pts = [(290.0 + (i % 5) * 5, 610.0 + (i // 5) * 6, 0.9) for i in range(21)]
    scene.hands = [pts]
    mm = ReplacementEngine._hand_mask(None, scene, measurable_only=True)
    assert mm is not None and mm[620, 300] > 0.5 and mm[450, 300] < 0.5


def test_background_halo_is_restored_and_new_hair_kept():
    from app.core.engines.replacement import restore_background

    orig = np.full((40, 40, 3), 200, np.uint8)
    person = np.zeros((40, 40), np.float32)
    person[10:30, 10:30] = 1
    px = orig.copy()
    px[:, :10] = 212  # parede repintada um pouco mais clara (halo)
    px[0:5, 30:40] = 40  # cabelo novo sobre a parede
    region = np.ones((40, 40), np.float32)
    out = restore_background(px, orig, person, region)
    assert np.abs(out[20, 2].astype(int) - 200).max() <= 1 and out[2, 35, 0] == 40


def test_texture_ratio_flags_a_smeared_patch():
    from app.core.engines.replacement import zone_texture_ratio

    rng = np.random.default_rng(1)
    img = (150 + rng.normal(0, 12, (60, 60, 3))).clip(0, 255).astype(np.uint8)
    zone = np.zeros((60, 60), np.float32)
    zone[20:40, 20:40] = 1
    known = np.ones((60, 60), np.float32)
    assert zone_texture_ratio(img, zone, known) > 0.7
    smeared = img.copy()
    smeared[20:40, 20:40] = (230, 160, 90)  # mancha laranja lisa
    assert zone_texture_ratio(smeared, zone, known) < 0.2


def test_runaway_hair_mask_is_rebuilt_from_the_real_hair_color():
    """Espelho com braco na cabeca: 'cabelo' = 78% da foto (top branco e braco). Refeito pela cor do cabelo."""
    from app.core.engines.hair import clean_hair_mask

    img = np.full((200, 120, 3), (235, 230, 225), np.uint8)  # top branco / parede
    img[20:200, 20:100] = (238, 236, 232)  # top
    img[10:120, 25:95] = (25, 20, 18)  # cabelo escuro
    img[40:90, 40:80] = (205, 160, 135)  # rosto
    face = np.zeros((200, 120), np.float32)
    face[40:90, 40:80] = 1
    runaway = np.zeros((200, 120), np.float32)
    runaway[5:200, 15:105] = 1  # o segmentador marcou a pessoa toda
    out, info = clean_hair_mask(img, runaway, (40, 40, 80, 90), face)
    assert info["cleaned"] and out[100, 30] > 0.5 and out[150, 60] < 0.5  # cabelo fica, top sai
    ok, info2 = clean_hair_mask(img, (img.sum(axis=2) < 200).astype(np.float32), (40, 40, 80, 90), face)
    assert info2["cleaned"] is False  # mascara plausivel nao e tocada


async def test_preserved_clothing_without_segmentation_locks_the_body():
    from tests.test_engines_replacement import Seg
    from app.core.persona_replacement.contracts import RawSegments
    from tests.test_persona_transfer import wide_tattoo_photo

    class NoClothes(Seg):
        async def segment(self, image, sheet):
            _, person, hair, _ = wide_tattoo_photo()
            return RawSegments(person, hair, [], clothes=None)

    eng, ad, _ = engine(FACES)
    eng.segmenter = NoClothes()
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    assert "body_identity" not in [c.stage for c in ad.calls]
    bp = next(p for p in out.telemetry.passes if p["pass"] == "body_identity")
    assert bp["accepted"] is False and "nao segmentada" in bp["reason"]
    assert out.report.checks["clothing"].status in ("WARN", "REJECT")
