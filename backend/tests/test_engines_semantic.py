"""Spec 46: separacao semantica (acessorio por objeto, oculos com lente clara/escura, mao com pose travada,
politica estruturada, classificacao de cada detalhe, vereditos de mao e acessorio). GPU mockada."""
import json

import numpy as np

from app.core.engines.accessories import build_layer, composite_layers
from app.core.engines.attributes import PRESERVE, RECONSTRUCT, REMOVE, from_structured, resolve
from app.core.engines.replacement import hand_ratio
from app.core.engines.retry import RetryPolicyV2
from app.core.engines.policies import POLICIES
from app.core.engines.validation import REJECT, validate_v2
from tests.conftest import REPO
from tests.test_engines_replacement import engine, req

LUNA = json.loads((REPO / "personas" / "luna" / "persona_sheet.json").read_text(encoding="utf-8"))
FACES = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "face_lock": 0.85, "hand": 0.8, "tattoo": 0.8,
         "integrated": 0.8, "final": 0.8}


def face_photo(lens):
    """Rosto de pele com oculos: armacao preta fina e lente `lens` (clara ou escura) sobre olhos originais."""
    img = np.full((80, 120, 3), (205, 160, 135), np.uint8)
    img[30:50, 20:100] = lens
    img[38:42, 35:45] = (40, 30, 30)  # olho ORIGINAL atras da lente
    img[30:32, 20:100] = (10, 10, 10)  # armacao: barra de cima
    img[30:50, 20:22] = (10, 10, 10)
    img[30:50, 98:100] = (10, 10, 10)
    img[30:50, 59:61] = (10, 10, 10)
    return img


def test_spec_46_10_structured_example_becomes_object_policies():
    p, r, c = from_structured({"preserve": {"accessories": ["glasses", "earrings", "bracelet"], "clothing": ["top"],
                                            "pose": {"enabled": True}},
                               "remove": {"markings": ["tattoos", "scars"]},
                               "reconstruct": {"identity": ["face", "body", "skin"]}})
    pol = resolve(LUNA, p, r, c)
    assert pol.items == {"glasses": PRESERVE, "earrings": PRESERVE, "bracelet": PRESERVE}
    assert pol.get("clothing") == PRESERVE and pol.get("tattoos") == REMOVE and pol.get("face") == RECONSTRUCT
    assert pol.item_policy("pair of sunglasses") == ("glasses", PRESERVE)
    only_earrings_out = resolve(LUNA, [], ["earrings"], [])
    assert only_earrings_out.item_policy("earrings")[1] == REMOVE and only_earrings_out.item_policy("bracelet")[1] == PRESERVE


def test_luna_defaults_follow_spec_46_same_clothes_same_accessories_hand_pose_lock():
    pol = resolve(LUNA)
    assert pol.get("clothing") == PRESERVE and pol.get("jewelry") == PRESERVE and pol.get("accessories") == PRESERVE
    assert pol.get("hands") == RECONSTRUCT and pol.get("body") == RECONSTRUCT and pol.get("tattoos") == REMOVE


def test_sunglasses_come_back_whole_but_clear_glasses_only_the_frame():
    """46.3: lente escura volta inteira; lente clara volta so a armacao - o olho ORIGINAL nao volta pela lente."""
    dark = face_photo((25, 25, 30))
    ld = build_layer(dark, (18, 28, 102, 52), "sunglasses", "glasses", PRESERVE)
    assert ld.kind == "glasses_dark" and ld.mask[40, 50] > 0.5
    clear = face_photo((200, 165, 145))
    lc = build_layer(clear, (18, 28, 102, 52), "glasses", "glasses", PRESERVE)
    assert lc.kind == "glasses_clear" and lc.mask[30, 50] > 0.5 and lc.mask[40, 40] < 0.5  # armacao sim, olho nao
    new_face = np.full_like(clear, (190, 150, 125))  # rosto da Persona gerado por baixo
    out = composite_layers(new_face, clear, [lc])
    assert (out[30, 50] == clear[30, 50]).all()  # armacao da foto
    assert np.abs(out[40, 40].astype(int) - np.array([40, 30, 30])).sum() > 100  # o olho original NAO voltou


def test_hand_ratio_flags_lost_fingers():
    assert hand_ratio([21, 18], [21, 17]) == round(17 / 18, 3)
    assert hand_ratio([21], [9]) == round(9 / 21, 3)
    assert hand_ratio([4], [0]) is None  # mao que a foto nao mostra bem nao e medida


def test_hand_and_accessory_failures_have_their_own_retry():
    rep = validate_v2({"identity": 0.8, "person_found": True, "hand_anatomy": 0.4, "accessory_change": 0.3,
                       "persona_instances": 1, "original_sim": 0.05})
    assert rep.checks["hands"].status == REJECT and rep.checks["accessories"].status == REJECT
    p, step = RetryPolicyV2().next_plan(POLICIES["QUALITY"], ["hands"], 1)
    assert step.failure_type == "hands" and p.hand_denoise < POLICIES["QUALITY"].hand_denoise and p.extra["hand_retry"] == 1
    _, step2 = RetryPolicyV2().next_plan(POLICIES["QUALITY"], ["accessories"], 1)
    assert step2.failure_type == "accessories"


async def test_hand_pose_lock_stage_uses_the_gesture_not_the_pixels():
    """46.4: a mao e refeita (LoRA) com openpose forte + estrutura da mao original; nao e copia de pixels."""
    eng, ad, _ = engine(FACES)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    stages = [c.stage for c in ad.calls]
    if "hand_gesture_lock" in stages:  # o esqueleto falso tem pulsos: a etapa roda
        hand = next(c for c in ad.calls if c.stage == "hand_gesture_lock")
        assert hand.controls.pose_strength == 1.0 and hand.controls.depth_strength > 0 and hand.identity.use_lora
        assert hand.denoise < 0.6 and stages.index("hand_gesture_lock") < stages.index("identity")
    details = {d["detail"]: d for d in out.telemetry.attributes["source_details"]}
    assert details["hands"]["mode"] == "POSE_LOCK" and details["tattoos"]["policy"] == REMOVE
    assert details["clothing"]["policy"] == PRESERVE and "hands" in out.report.checks and "accessories" in out.report.checks
    eng2, ad2, _ = engine(FACES)
    await eng2.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, preserve_attributes=["hands"]))
    assert "hand_gesture_lock" not in [c.stage for c in ad2.calls]  # mao PRESERVE pedida = pixels da foto


async def test_hand_stage_really_runs_with_a_wrist_on_the_arm():
    """Esqueleto com o pulso sobre o braco da foto sintetica: a etapa da mao roda e fica registrada."""
    from dataclasses import replace as dc_replace

    from tests.fakes import body
    from tests.test_persona_replacement import Reader

    kps = [(0.0, 0.0, 0.0)] * 18
    kps[3], kps[4] = (46.0, 150.0, 0.9), (46.0, 200.0, 0.9)  # cotovelo e pulso direitos no braco (x 40-52)
    for i in (0, 1, 2, 5, 8, 11):
        kps[i] = (80.0, 60.0 + 10 * i, 0.9)

    class WristReader(Reader):
        async def read(self, image, master):
            sheet = await super().read(image, master)
            return dc_replace(sheet, target_body=body(kps))

    eng, ad, _ = engine(FACES)
    eng.reader = WristReader()
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    stages = [c.stage for c in ad.calls]
    assert "hand_gesture_lock" in stages and stages.index("hand_gesture_lock") < stages.index("identity")
    hand = next(c for c in ad.calls if c.stage == "hand_gesture_lock")
    assert hand.controls.pose_strength == 1.0 and hand.controls.depth_strength > 0 and hand.identity.use_lora
    assert hand.mask[200, 46] > 0.5 and hand.denoise < 0.6
    hp = next(p for p in out.telemetry.passes if p["pass"] == "hand_gesture_lock")
    assert "hand_points" in hp  # medida de dedos registrada (sem maos no detector falso: razao None, etapa aceita)
    body_call = next(c for c in ad.calls if c.stage == "body_identity")
    assert body_call.mask[200, 46] < 0.5  # o corpo nao refaz a mao: ela tem etapa propria


def test_no_stage_name_contains_the_pose_preview_marker():
    """2026-10-08: a etapa 'hand_pose_lock' gerava arquivo com '_pose_' e a sessao do ComfyUI descarta essas
    imagens (sao previas de pose) -> 'terminou sem devolver imagem' na GPU real. Nome de etapa nunca pode ter isso."""
    import re

    from tests.conftest import BACKEND

    src = (BACKEND / "app" / "core" / "engines" / "replacement.py").read_text(encoding="utf-8")
    stages = re.findall(r'self\._pass\(\s*"([^"]+)"', src) + re.findall(r'try_stage\(\s*"([^"]+)"', src)
    assert stages and not [s for s in stages if "_pose_" in f"repl_{s}_x"]
