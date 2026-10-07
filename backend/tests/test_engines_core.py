"""V2: politicas, escada A..H, Validation V2 por dimensao, retry especifico, telemetria, integracao."""
import numpy as np
import pytest

from app.core.engines.integration import distance_ramp, integrate
from app.core.engines.policies import LADDER, MAX_QUALITY, POLICIES, QUALITY, plan_for
from app.core.engines.retry import RetryPolicyV2
from app.core.engines.telemetry import JobTelemetry
from app.core.engines.validation import PASS, REJECT, UNKNOWN, WARN, texture_energy, validate_v2

BOM = {"identity": 0.80, "original_sim": 0.05, "pose": 0.05, "background": 0.0, "tattoo_residual": 0.0,
       "hair_residual": 0.0, "texture_final": 4.0, "texture_ref": 4.2, "tone_delta": 3.0, "persona_instances": 1,
       "faces": 1, "shoulder_ratio": 1.01, "composition_shift": 0.0, "person_found": True}


def test_ladder_turns_on_one_stage_at_a_time():
    a, b, c, d, e, f, g, h = (LADDER[k] for k in "ABCDEFGH")
    assert not any([a.face_reference, a.pose, a.depth, a.segmentation, a.tattoo_cleanup, a.photographic_integration])
    assert b.face_reference and not b.pose and c.pose and not c.depth and d.depth and not d.segmentation
    assert e.segmentation and not e.body_refinement and f.body_refinement and f.hires and not f.tattoo_cleanup
    assert g.tattoo_cleanup and not g.photographic_integration and h.photographic_integration


def test_policies_and_overrides():
    assert POLICIES[MAX_QUALITY].body_refinement and POLICIES[MAX_QUALITY].hires and POLICIES[MAX_QUALITY].steps == 40
    assert plan_for("quality", {"steps": 35}).steps == 35 and plan_for("H").name == "H"
    with pytest.raises(ValueError):
        plan_for("ULTRA")


def test_good_result_passes_with_anatomy_unknown():
    r = validate_v2(BOM)
    assert r.status == PASS and r.checks["anatomy"].status == UNKNOWN


def test_identity_can_be_high_and_still_reject_for_original_residual():
    r = validate_v2({**BOM, "identity": 0.85, "tattoo_residual": 0.4})
    assert r.checks["identity"].status == PASS and r.checks["original_residual"].status == REJECT
    assert r.status == REJECT and "original_residual" in r.failures()


def test_original_face_still_present_is_a_hard_fail():
    r = validate_v2({**BOM, "original_sim": 0.55})
    assert r.checks["original_residual"].status == REJECT


def test_second_luna_rejects_but_background_people_are_allowed():
    assert validate_v2({**BOM, "persona_instances": 2, "faces": 2}).checks["duplicate_persona"].status == REJECT
    assert validate_v2({**BOM, "persona_instances": 1, "faces": 3}).checks["duplicate_persona"].status == PASS


def test_plastic_skin_and_face_body_tone_are_warnings_not_the_only_criterion():
    r = validate_v2({**BOM, "texture_final": 1.0, "texture_ref": 4.0, "tone_delta": 14.0})
    assert r.checks["skin"].status == WARN and "plastica" in r.checks["skin"].reason and "tom do rosto" in r.checks["skin"].reason
    assert r.status == WARN


def test_missing_face_and_destroyed_background_reject():
    r = validate_v2({**BOM, "identity": None, "background": 0.2})
    assert r.checks["identity"].status == REJECT and r.checks["background"].status == REJECT


def test_texture_energy_tells_plastic_from_textured():
    rng = np.random.default_rng(0)
    liso = np.full((80, 80, 3), 150, np.uint8)
    poros = np.clip(150 + rng.normal(0, 6, (80, 80, 3)), 0, 255).astype(np.uint8)
    m = np.ones((80, 80), np.float32)
    assert texture_energy(poros, m) > 5 * texture_energy(liso, m) + 0.1


def test_retry_is_failure_specific_and_never_touches_the_lora():
    rp = RetryPolicyV2()
    p, step = rp.next_plan(POLICIES[QUALITY], ["identity"], 1)
    assert step.failure_type == "identity" and p.face_reference_strength > POLICIES[QUALITY].face_reference_strength
    assert "lora" not in " ".join(step.parameters).lower()
    p2, s2 = rp.next_plan(POLICIES[QUALITY], ["tattoo", "skin"], 1)
    assert s2.failure_type == "tattoo" and p2.tattoo_denoise > POLICIES[QUALITY].tattoo_denoise
    p3, s3 = rp.next_plan(POLICIES[QUALITY], ["pose"], 1)
    assert p3.pose_strength > POLICIES[QUALITY].pose_strength
    assert rp.next_plan(POLICIES[QUALITY], ["identity"], 99) is None  # limite


def test_telemetry_records_passes_and_cost_fields():
    t = JobTelemetry("j1", "luna", "replacement", QUALITY, model="realvisxl")
    t.add_pass("identity", 20.0, {"denoise": 0.9}, True)
    d = t.to_dict()
    assert d["gpu_seconds"] == 20.0 and d["passes"][0]["pass"] == "identity" and "checkpoint_hash" in d


def test_integration_changes_only_the_region_and_fixes_the_skin_step():
    h, w = 80, 80
    orig = np.full((h, w, 3), (200, 150, 120), np.uint8)
    cur = orig.copy()
    reg = np.zeros((h, w), np.float32)
    reg[20:60, 20:60] = 1
    cur[reg > 0] = (230, 160, 110)  # pele gerada mais clara/alaranjada
    body = np.ones((h, w), np.float32) - reg
    out, rel = integrate(orig, cur, reg, body_skin=body, band=6, radius=8, tone_harmony=0.0)
    assert (out[reg < 0.5] == cur[reg < 0.5]).all()
    assert abs(int(out[21, 40, 0]) - 200) < abs(230 - 200)  # a borda chegou perto da pele original
    assert rel["aplicado"]
    assert distance_ramp(reg, 12)[21, 40] > distance_ramp(reg, 12)[40, 40]
