"""Spec V2.1 (Physical Identity + Skin Continuity): Persona Canon imutavel, Physical Identity Profile so com o que a
ficha define, continuidade de pele rosto->pescoco->corpo (tom da Persona + luz da foto), Qwen como refinamento SO do
rosto com contexto de preservacao, validadores de corpo/pele/fotometria e config separada da V2. GPU mockada."""
import dataclasses
import json

import numpy as np
import pytest

from app.core.engines.persona_canon import CanonViolation, apply_scene, guard_master_promotion, load_canon
from app.core.engines.quality_gate import QualityGate, check_v21
from app.core.engines.replacement import ReplacementEngine, ReplacementRequestError
from app.core.engines.skin_continuity import continuity_residuals, harmonize, lab_to_rgb, rgb_to_lab
from app.core.engines.validation import PASS, REJECT, WARN
from tests.conftest import REPO
from tests.test_engines_replacement import engine, req

LUNA = json.loads((REPO / "personas" / "luna" / "persona_sheet.json").read_text(encoding="utf-8"))
CFG21 = ReplacementEngine.load_config(REPO / "config" / "persona_replacement_v2_1.json")
FACES = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "face_lock": 0.85, "hand": 0.8, "tattoo": 0.8,
         "integrated": 0.8, "final": 0.8}


# --- Persona Canon ------------------------------------------------------------------------------------

def test_canon_reads_only_what_the_sheet_defines_and_is_immutable():
    c = load_canon(LUNA)
    p = c.physical
    assert p.height.value is None and "nao inventado" in p.height.note  # ficha: UNKNOWN
    assert p.weight.value is None
    assert dict(p.proportions) == LUNA["master_references"]["master_body"]["ratios"]
    assert p.waist == "cintura fina" and p.hip == "quadril arredondado" and "bronzeado" in p.skin["tone"]
    assert c.canon_hash and p.version.startswith("1.0+")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.physical = p  # type: ignore[misc]
    with pytest.raises(TypeError):
        p.proportions["hips"] = 0.9  # type: ignore[index]
    assert load_canon(LUNA).canon_hash == c.canon_hash  # deterministico


def test_scene_cannot_change_the_canon_and_generated_never_becomes_master():
    c = load_canon(LUNA)
    s = apply_scene(c, {"location": "praia", "clothing": "biquini", "pose": "sentada"})
    assert s.location == "praia"
    for bad in ({"body": "magra"}, {"height": 180}, {"skin": "palida"}, {"proportions": {}}):
        with pytest.raises(CanonViolation):
            apply_scene(c, bad)
    for target in ("master_face", "master_body", "new_body_reference"):
        with pytest.raises(CanonViolation):
            guard_master_promotion("repl_final_1.png", target)


# --- continuidade de pele ------------------------------------------------------------------------------

def scene_masks(h=120, w=80):
    z = np.zeros((h, w), np.float32)
    person = z.copy(); person[5:115, 10:70] = 1
    face = z.copy(); face[10:35, 25:55] = 1
    neck = z.copy(); neck[35:45, 30:50] = 1
    clothing = z.copy(); clothing[45:75, 10:70] = 1
    legs = z.copy(); legs[75:115, 15:65] = 1
    return {"person_mask": person, "face_mask": face, "neck_mask": neck, "clothing_mask": clothing, "leg_mask": legs,
            "hair_mask": z.copy(), "accessory_mask": z.copy(), "hand_mask": z.copy(), "arm_mask": z.copy()}


def paint(img, mask, rgb, rng):
    sel = mask > 0.5
    img[sel] = np.clip(np.array(rgb) + rng.normal(0, 6, (int(sel.sum()), 3)), 0, 255).astype(np.uint8)


def test_skin_continuity_matches_the_photo_light_and_keeps_texture():
    rng = np.random.default_rng(3)
    m = scene_masks()
    orig = np.full((120, 80, 3), (90, 90, 95), np.uint8)
    for k, c in (("face_mask", (210, 160, 135)), ("neck_mask", (200, 152, 128)), ("leg_mask", (205, 158, 132))):
        paint(orig, m[k], c, rng)
    orig[m["clothing_mask"] > 0.5] = (40, 40, 40)
    final = orig.copy()
    paint(final, m["face_mask"], (190, 135, 100), rng)  # rosto da Luna: mais moreno
    paint(final, m["neck_mask"], (225, 175, 150), rng)  # pescoco ficou com o tom da pessoa ORIGINAL (claro)
    paint(final, m["leg_mask"], (160, 95, 60), rng)  # pernas laranja-escuras
    before = continuity_residuals(final, orig, m, None, (25, 10, 55, 35))["worst"]
    res = harmonize(final, orig, m, None, (25, 10, 55, 35))
    after = continuity_residuals(res.pixels, orig, m, None, (25, 10, 55, 35))["worst"]
    assert res.applied and after < before / 2 and after < 6
    # textura (alta frequencia) preservada: desvio local da perna quase o mesmo
    sel = m["leg_mask"] > 0.5
    assert abs(float(res.pixels[sel].astype(float).std(axis=0).mean()) - float(final[sel].astype(float).std(axis=0).mean())) < 3
    # roupa e rosto (referencia) intocados
    assert (res.pixels[m["clothing_mask"] > 0.5] == final[m["clothing_mask"] > 0.5]).all()
    assert (res.pixels[15:30, 30:50] == final[15:30, 30:50]).all()


def test_lab_roundtrip_is_exact_enough():
    x = (np.random.default_rng(0).random((40, 40, 3)) * 255).astype(np.uint8)
    assert int(np.abs(lab_to_rgb(rgb_to_lab(x)).astype(int) - x).max()) <= 1


def test_v21_checks_flag_skin_seam_and_original_body():
    thr = CFG21["skin_continuity"]["thresholds"]
    ok = check_v21({"skin_continuity": {"worst": 3.0, "transitions": {}}}, thr, True, 0.05)
    assert ok["skin_continuity"].status == PASS
    bad = check_v21({"skin_continuity": {"worst": 15.0, "transitions": {"face->neck": {"dE": 15.0}}}}, thr, True, 0.6)
    assert bad["skin_continuity"].status == REJECT and "face->neck" in bad["skin_continuity"].reason
    assert bad["body_identity"].status == REJECT  # corpo da pessoa original sobrou
    nc = check_v21({"skin_continuity": {"worst": 3.0}, "body_identity": {"status": "NOT_COMPARABLE", "reason": "pose"}},
                   thr, True, 0.05)
    assert nc["body_identity"].status == "UNKNOWN"  # sem inventar nota de proporcao fora da pose da master
    meas = check_v21({"skin_continuity": {"worst": 3.0}, "body_identity": {"status": "MEASURED", "deviation": 0.3,
                                                                          "max_deviation": 0.15}}, thr, True, 0.05)
    assert meas["body_identity"].status == WARN


def test_quality_score_never_hides_a_hard_fail():
    gate = QualityGate(CFG21)
    m = {"identity": 0.95, "original_sim": 0.05, "pose": 0.01, "background": 0.0, "tattoo_residual": 0.3,
         "texture_final": 10.0, "texture_ref": 10.0, "persona_instances": 1, "faces": 1, "faces_original": 1,
         "person_found": True, "skin_continuity": {"worst": 2.0}}
    rep = gate.evaluate(m, {"tattoos": "REMOVE"})
    g = gate.decide(rep, can_retry=False)
    assert g.decision == "REJECT" and QualityGate.quality_score(rep) > 0.5  # media alta, decisao continua REJECT


# --- engine V2.1 ---------------------------------------------------------------------------------------

def engine21(faces=FACES):
    eng, ad, store = engine(faces)
    eng.cfg = CFG21
    return eng, ad, store


class FakeLock:
    def __init__(self, store):
        self.store, self.calls = store, []

    async def lock_face(self, image, master, seed, guidance=None):
        from app.providers.base import ProviderImage, StageOutput

        self.calls.append(guidance)
        px = np.clip(self.store.images[image.locator].astype(int) + 30, 0, 255).astype(np.uint8)  # muda TUDO
        key = await self.store.save(px, "qwen_raw")
        return StageOutput(image=ProviderImage("comfyui", key, "", px.shape[1], px.shape[0]), stage="face_lock",
                           adapter="fake", seconds=20.0, seed=seed, effective_parameters={})


async def test_v21_runs_canon_skin_continuity_and_face_only_qwen():
    eng, ad, store = engine21()
    eng.face_lock = FakeLock(store)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v2.1", keep_intermediates=True))
    passes = {p["pass"]: p for p in out.telemetry.passes}
    assert "skin_continuity" in passes and "face_lock" in passes
    fl = passes["face_lock"]
    assert fl["params"]["scope"] == "face" and fl["params"]["guidance"] and "earrings" in " ".join(eng.face_lock.calls[0].positive)
    body = next(c for c in ad.calls if c.stage == "body_identity")
    assert "hourglass" in body.prompt and "olive" in body.prompt  # corpo e pele do Physical Identity Profile
    for chk in ("skin_continuity", "skin_identity", "photometric", "body_identity"):
        assert chk in out.report.checks, chk
    assert {"SkinContinuityValidator", "BodyIdentityValidator", "PhotometricIntegrationValidator"} <= set(out.telemetry.gate["validators"])
    assert out.telemetry.attributes["persona_canon"]["canon_hash"] == load_canon(LUNA).canon_hash
    rl = out.telemetry.run_log
    assert rl["persona_version"] == "1.0" and rl["body_profile_version"] and rl["qwen_strength"] == 1.0
    assert out.telemetry.gate["quality_score"] is not None


async def test_qwen_face_scope_leaves_hair_and_body_untouched():
    eng, ad, store = engine21()
    eng.face_lock = FakeLock(store)
    eng.cfg = {**CFG21, "skin_continuity": {"enabled": False}}
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v2.1", keep_intermediates=True))
    before = store.images[next(p for p in [out.intermediates.get("identity"), out.intermediates.get("face_refine")] if p)]
    after = store.images[out.intermediates["face_lock"]]
    changed = np.abs(after.astype(int) - before.astype(int)).max(axis=2) > 0
    from app.core.persona_replacement.segmentation import dilate
    face = store.images[out.intermediates["mask_face"]][..., 0] > 127
    outside = ~(dilate(face.astype(np.float32), 2) > 0.5)
    assert changed.any() and not (changed & outside).any()  # o Qwen mudou a imagem toda; so o rosto voltou


async def test_version_and_config_must_match():
    eng, _, _ = engine(FACES)  # config V2
    with pytest.raises(ReplacementRequestError, match="config"):
        await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v2.1"))
    with pytest.raises(ReplacementRequestError):
        req(persona_sheet=LUNA, replacement_version="v9").validate()


def test_v2_config_is_untouched_by_v21():
    v2 = json.loads((REPO / "config" / "persona_replacement_v2.json").read_text(encoding="utf-8"))
    assert v2["replacement_version"] == "v2" and "skin_continuity" not in v2 and "qwen_identity_refinement" not in v2
    assert CFG21["replacement_version"] == "v2.1" and CFG21["qwen_identity_refinement"]["scope"] == "face"
