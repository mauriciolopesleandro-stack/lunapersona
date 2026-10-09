"""Replacement V3 (Full Person Reconstruction): pedido no formato da spec, condicoes estruturais (roupa medida, alcas,
geometria do rosto/olhar), UMA reconstrucao da pessoa inteira, refino de identidade adaptativo (InstantID; Qwen so no
miolo do rosto e so se a identidade cair), halo, roupa inventada, reflexo e o gate V3. GPU mockada."""
import json

import numpy as np
import pytest

from app.core.engines.conditions_v3 import (
    boundary_halo,
    clothing_condition,
    clothing_consistency,
    compare_face_geometry,
    face_geometry,
    gaze,
)
from app.core.engines.quality_gate import check_v3
from app.core.engines.replacement import ReplacementEngine, ReplacementRequestError
from app.core.engines.replacement_v3 import PersonaReplacementV3, ReplacementV3Request
from app.core.engines.retry import RetryPolicyV2
from app.core.engines.policies import POLICIES
from app.core.engines.validation import PASS, REJECT, WARN
from tests.conftest import REPO
from tests.test_engines_replacement import FakeAdapter, Seg, req
from tests.test_engines_v21 import FakeLock
from tests.test_persona_replacement import Analyzer, Reader, Store
from tests.test_persona_transfer import wide_tattoo_photo
from tests.test_v2_multipass import MASTER

LUNA = json.loads((REPO / "personas" / "luna" / "persona_sheet.json").read_text(encoding="utf-8"))
CFG3 = ReplacementEngine.load_config(REPO / "config" / "persona_replacement_v3.json")


class SegV3(Seg):
    """Segmentador falso que aceita os objetos protegidos da V3 (celular, bolsa)."""

    def __init__(self):
        self.protect = []

    async def segment(self, image, sheet, protect=None):
        self.protect.append(protect)
        return await super().segment(image, sheet)


class Describer:
    def __init__(self):
        self.calls = []

    async def describe(self, image, box):
        self.calls.append(box)
        return ("The image shows a black ribbed knit crop top with a sweetheart neckline. The woman has long hair. "
                "Black tailored shorts with a button closure and belt loops.")


def engine3(faces):
    img = wide_tattoo_photo()[0]
    store = Store(img)
    ad = FakeAdapter(store)
    eng = PersonaReplacementV3(reader=Reader(), segmenter=SegV3(), analyzer=Analyzer(faces, 0.1), store=store, adapter=ad,
                               config=CFG3, price_per_hour=0.57, provider="comfyui")
    return eng, ad, store


def kp_standing():
    kp = [(0.0, 0.0, 0.0)] * 18
    pts = {2: (40, 40), 5: (80, 40), 3: (35, 70), 6: (85, 70), 4: (33, 95), 7: (87, 95), 8: (48, 110), 11: (72, 110),
           9: (48, 150), 12: (72, 150), 10: (48, 190), 13: (72, 190), 1: (60, 38), 0: (60, 25)}
    for i, (x, y) in pts.items():
        kp[i] = (float(x), float(y), 0.9)
    return kp


def outfit(straps=False):
    img = np.full((200, 120, 3), (200, 160, 140), np.uint8)
    clothes = np.zeros((200, 120), np.float32)
    clothes[50:95, 38:82] = 1  # top tomara-que-caia (cropped)
    clothes[110:128, 40:80] = 1  # short
    if straps:
        clothes[35:50, 38:43] = 1
        clothes[35:50, 77:82] = 1
    img[clothes > 0.5] = (15, 15, 18)
    person = np.zeros((200, 120), np.float32)
    person[20:195, 30:90] = 1
    return img, clothes, person


# --- pedido ------------------------------------------------------------------------------------------

def test_v3_request_maps_the_spec_lists():
    r = ReplacementV3Request("foto.png", "luna", MASTER, preserve=["pose", "gaze", "camera", "clothing", "accessories", "scene"],
                             reconstruct=["face", "body", "skin", "hair", "hands", "anatomy", "clothing"],
                             remove=["tattoos", "source_identity", "source_marks"], persona_sheet=LUNA,
                             identity_refinement={"provider": "adaptive", "qwen_allowed": False}).to_request()
    pol = r.attributes()
    assert r.replacement_version == "v3" and r.reconstruction_mode == "full_reconstruction" and r.qwen_face_lock is False
    assert pol.get("clothing") == "RECONSTRUCT" and pol.get("camera_angle") == "PRESERVE" and pol.get("background") == "PRESERVE"
    assert pol.get("original_person_marks") == "REMOVE" and pol.get("tattoos") == "REMOVE"
    with pytest.raises(ReplacementRequestError):
        ReplacementV3Request("foto.png", "luna", MASTER, mode="magic").to_request()


# --- condicoes ---------------------------------------------------------------------------------------

def test_clothing_condition_measures_color_straps_and_cut():
    img, clothes, _ = outfit()
    c = clothing_condition(img, clothes, kp_standing(), caption="ribbed crop top and tailored shorts")
    parts = {g.part: g for g in c.garments}
    assert parts["top"].color == "black" and parts["top"].straps is False and "cropped" in parts["top"].length
    assert parts["bottom"].color == "black" and "short" in parts["bottom"].length
    assert "strapless" in c.prompt() and "no straps" in c.prompt() and "straps" in c.negative()


def test_invented_strap_and_recolored_clothes_are_rejected():
    img, clothes, person = outfit()
    out, clothes_f, _ = outfit(straps=True)
    c = clothing_consistency(img, out, clothes, clothes_f, person, kp_standing())
    assert c["straps_invented"] is True
    assert check_v3({"clothing_v3": c}, CFG3["v3_thresholds"])["clothing_v3"].status == REJECT
    same = clothing_consistency(img, img.copy(), clothes, clothes, person, kp_standing())
    assert check_v3({"clothing_v3": same}, CFG3["v3_thresholds"])["clothing_v3"].status == PASS
    red = img.copy()
    red[clothes > 0.5] = (170, 20, 30)
    rc = clothing_consistency(img, red, clothes, clothes, person, kp_standing())
    assert check_v3({"clothing_v3": rc}, CFG3["v3_thresholds"])["clothing_v3"].status == REJECT


def test_halo_is_measured_on_the_background_band():
    rng = np.random.default_rng(2)
    img = (120 + rng.normal(0, 8, (100, 100, 3))).clip(0, 255).astype(np.uint8)
    person = np.zeros((100, 100), np.float32)
    person[30:70, 35:65] = 1
    region = np.zeros_like(person)
    region[22:78, 27:73] = 1
    halo = img.copy()
    band = (region > 0.5) & ~(person > 0.5)
    halo[band] = np.clip(halo[band].astype(int) + 25, 0, 255).astype(np.uint8)
    hb = boundary_halo(img, halo, person, region)
    assert check_v3({"boundary": hb}, CFG3["v3_thresholds"])["boundary"].status == REJECT
    ok = boundary_halo(img, img.copy(), person, region)
    assert check_v3({"boundary": ok}, CFG3["v3_thresholds"])["boundary"].status == PASS


def test_face_geometry_and_gaze_from_the_photo():
    from types import SimpleNamespace

    src = SimpleNamespace(bbox=(40.0, 40.0, 80.0, 90.0), kps=[(52, 60), (68, 60), (60, 70), (54, 80), (66, 80)])
    turned = SimpleNamespace(bbox=(40.0, 40.0, 80.0, 90.0), kps=[(52, 60), (68, 64), (67, 70), (54, 80), (66, 80)])
    g0, g1 = face_geometry(src), face_geometry(turned)
    assert g0.roll_deg == 0.0 and g0.yaw == 0.0 and gaze(g0).toward_camera is True
    d = compare_face_geometry(g0, g1)
    res = check_v3({"face_geometry": d}, CFG3["v3_thresholds"])
    assert res["face_geometry"].status in (WARN, REJECT) and res["gaze"].status == REJECT  # rosto virado/olhar outro lado


def test_v3_retry_has_its_own_strategies():
    p, s = RetryPolicyV2().next_plan(POLICIES["QUALITY"], ["halo"], 1)
    assert p.extra["region_grow"] == 1 and "halo" in s.strategy
    p, s = RetryPolicyV2().next_plan(POLICIES["QUALITY"], ["clothing"], 1)
    assert p.depth_strength > POLICIES["QUALITY"].depth_strength and s.failure_type == "clothing"


# --- engine -------------------------------------------------------------------------------------------

async def test_v3_rebuilds_the_whole_person_in_one_pass():
    eng, ad, store = engine3({"foto": 0.1, "v3_full": 0.8, "final": 0.8})
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v3",
                            reconstruction_mode="full_reconstruction", keep_intermediates=True, debug=True))
    stages = [c.stage for c in ad.calls]
    assert stages == ["full_reconstruction"]  # nada de rosto/corpo/tatuagem em passes separados
    full = ad.calls[0]
    _, person, _, clothes = wide_tattoo_photo()
    assert (full.mask[person > 0.5] > 0.5).mean() > 0.95  # a pessoa INTEIRA, inclusive a roupa
    assert full.controls.pose_strength == 0.85 and 0 < full.controls.depth_strength <= 0.45
    assert full.image.startswith("v3_clean")  # entrada sem as marcas da pessoa original
    assert "hourglass" in full.prompt and "earrings" in full.negative and "heavy makeup" in full.negative
    assert out.telemetry.engine_version.startswith("replacement-v3")
    cond = out.telemetry.attributes["v3_conditions"]
    assert {"pose", "face_geometry", "gaze", "depth", "clothing", "accessories"} <= set(cond)
    for chk in ("boundary", "clothing_v3", "face_geometry", "gaze", "reflection"):
        assert chk in out.report.checks, chk
    assert {"ReplacementBoundaryValidator", "ClothingValidator", "GazeValidator"} <= set(out.telemetry.gate["validators"])
    for name in ("initial_reconstruction", "scene_composite", "person_mask", "background_mask", "pose_map", "final"):
        assert name in out.intermediates, name
    orig = store.images["foto.png"]
    assert (out.pixels[0:4, 0:4] == orig[0:4, 0:4]).all()  # cena intocada


async def test_identity_refinement_is_adaptive_instantid_then_face_only_qwen():
    eng, ad, store = engine3({"foto": 0.1, "v3_full": 0.5, "face_refine": 0.6, "face_lock": 0.8, "final": 0.8})
    eng.face_lock = FakeLock(store)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v3", keep_intermediates=True))
    stages = [c.stage for c in ad.calls]
    assert stages == ["full_reconstruction", "face_refine"]
    fr = ad.calls[1]
    assert fr.identity.reference is MASTER and fr.denoise <= 0.4  # refino leve (sem embelezar)
    fl = next(p for p in out.telemetry.passes if p["pass"] == "face_lock")
    assert fl["accepted"] and fl["params"]["scope"] == "face_inner" and out.telemetry.experimental_stages == ["qwen_face_lock"]
    # o Qwen mexeu so no miolo do rosto (sem orelhas): fora do face_full nada mudou
    before = store.images[out.intermediates["identity_refinement"]]
    after = store.images[out.intermediates["face_lock"]]
    changed = np.abs(after.astype(int) - before.astype(int)).max(axis=2) > 0
    face = store.images[out.intermediates["mask_face"]][..., 0] > 127
    assert changed.any() and not (changed & ~face).any()
    eng2, ad2, _ = engine3({"foto": 0.1, "v3_full": 0.8, "final": 0.8})
    eng2.face_lock = FakeLock(store)
    out2 = await eng2.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v3"))
    assert "face_lock" not in [p["pass"] for p in out2.telemetry.passes] and [c.stage for c in ad2.calls] == ["full_reconstruction"]


async def test_v3_needs_its_own_config_and_v2_is_untouched():
    from tests.test_engines_replacement import engine

    eng, _, _ = engine({"foto": 0.1, "identity": 0.8, "final": 0.8})
    with pytest.raises(ReplacementRequestError, match="config"):
        await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v3"))
    v2 = json.loads((REPO / "config" / "persona_replacement_v2.json").read_text(encoding="utf-8"))
    assert v2["replacement_version"] == "v2" and "full_reconstruction" not in v2


# --- correcoes da 1a rodada na GPU (09/10) ---------------------------------------------------------------

def test_clothing_sentences_keep_only_the_garments():
    from app.core.engines.conditions_v3 import clothing_sentences

    cap = ("The image shows a young woman with long curly hair standing in front of a wooden door. She is wearing a "
           "black strapless ribbed crop top and black tailored shorts with a button and belt loops. A white towel hangs on the wall.")
    out = clothing_sentences(cap)
    assert "ribbed crop top" in out and "belt loops" in out
    assert "curly" not in out and "door" not in out and "towel" not in out


def test_garment_details_go_to_the_prompt_and_unmentioned_inventions_to_the_negative():
    img, clothes, _ = outfit()
    c = clothing_condition(img, clothes, kp_standing())
    c.garments[0].details = "black ribbed knit crop top with a sweetheart neckline"
    c.garments[1].details = "black tailored shorts with a button closure and belt loops"
    p = c.prompt()
    assert "sweetheart neckline" in p and "belt loops" in p and "strapless" in p
    neg = c.negative()
    assert "drawstring" in neg and "white trim" in neg and "straps" in neg


async def test_v3_describes_each_garment_and_protects_held_objects():
    eng0, _, _ = engine3({"foto": 0.1, "v3_full": 0.8, "final": 0.8})
    eng0.describer = Describer()
    await eng0.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v3"))
    assert eng0.describer.calls == []  # padrao: desligado (a legenda detalhada inventou cordao/botoes em 09/10)
    eng, ad, store = engine3({"foto": 0.1, "v3_full": 0.8, "final": 0.8})
    eng.cfg = {**CFG3, "full_reconstruction": {**CFG3["full_reconstruction"], "garment_details": True}}
    eng.describer = Describer()
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v3"))
    assert eng.describer.calls  # uma descricao por peca recortada
    assert "button closure" in ad.calls[0].prompt or "ribbed" in ad.calls[0].prompt
    assert "cell phone" in (eng.segmenter.protect[0] or "")  # celular entra na deteccao de objetos protegidos
    assert out.telemetry.attributes["v3_conditions"]["clothing"]["garments"]


def test_body_change_and_hair_are_not_invented_clothing():
    img, clothes, person = outfit()
    wider = clothes.copy()
    wider[50:95, 34:86] = 1  # a roupa vestindo um corpo um pouco maior (avanca alguns pixels para os lados)
    out = img.copy()
    out[wider > 0.5] = (15, 15, 18)
    c = clothing_consistency(img, out, clothes, wider, person, kp_standing())
    assert c["new_clothing_on_skin"] < 0.01
    hair = np.zeros_like(clothes)
    hair[35:50, 38:43] = 1  # cabelo escuro caindo no ombro (segmentado como "roupa")
    with_hair = clothes.copy()
    with_hair[35:50, 38:43] = 1
    c2 = clothing_consistency(img, img.copy(), clothes, with_hair, person, kp_standing(), hair_f=hair)
    assert c2["straps_invented"] is False


def test_held_objects_only_near_a_wrist():
    from app.core.engines.replacement import _held

    kp = kp_standing()  # pulsos em (33,95) e (87,95)
    face = (50.0, 10.0, 70.0, 35.0)
    assert _held((80, 85, 95, 110), kp, face) is True  # celular na mao
    assert _held((0, 160, 20, 199), kp, face) is False  # "bolsa" no travesseiro, longe das maos
    assert _held((80, 85, 95, 110), None, face) is False  # sem esqueleto: nao arrisca colar pixels
