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
