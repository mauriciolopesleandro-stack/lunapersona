"""Replacement V3.1: roupa aceita pelo material (nao pela cor), roupa original preservada por imagem (pixels alinhados +
emendas), pele so onde a segmentacao diz que e pele, borda pela pessoa nova (fundo original exato) e halo multiescala.
Casos montados a partir da auditoria de 09/10 (calca bege virando 'pele', manchas, faixa de fundo repintado)."""
import json

import numpy as np

from app.core.engines.replacement import ReplacementEngine
from app.core.engines.skin_continuity import harmonize
from app.core.engines.v31_integration import (
    boundary_alpha,
    halo_multiscale,
    preserve_clothing,
    trusted_clothes,
    validated_skin,
)
from tests.conftest import REPO
from tests.test_engines_replacement import req
from tests.test_engines_v3 import SegV3, kp_standing
from tests.test_engines_replacement import FakeAdapter
from tests.test_persona_replacement import Analyzer, Reader, Store
from tests.test_persona_transfer import wide_tattoo_photo

LUNA = json.loads((REPO / "personas" / "luna" / "persona_sheet.json").read_text(encoding="utf-8"))
CFG31 = ReplacementEngine.load_config(REPO / "config" / "persona_replacement_v3_1.json")
BEIGE = (222, 180, 150)  # tecido com cor de pele


def beige_scene():
    rng = np.random.default_rng(4)
    img = (np.array([90, 140, 80]) + rng.normal(0, 18, (200, 120, 3))).clip(0, 255).astype(np.uint8)  # cerca viva
    person = np.zeros((200, 120), np.float32)
    person[20:195, 30:90] = 1
    img[person > 0.5] = (205, 160, 135)  # pele
    pants = np.zeros_like(person)
    pants[110:190, 34:86] = 1
    ribs = ((np.arange(34, 86) // 3) % 2) * 14  # textura canelada vertical
    img[110:190, 34:86] = (np.array(BEIGE)[None, None, :] + ribs[None, :, None]).clip(0, 255)
    return img, person, pants


def test_beige_pants_are_clothing_by_material_not_color():
    img, person, pants = beige_scene()
    tc, info = trusted_clothes(pants, person)
    assert tc is not None and info["status"] == "aceita"  # cor de pele, mas o detector disse roupa
    outside = np.zeros_like(person)
    outside[0:15, 0:120] = 1
    bad, info2 = trusted_clothes(np.maximum(outside, pants * 0.0), person)
    assert bad is None  # "roupa" fora da pessoa: rejeitada


def test_validated_skin_never_includes_skin_colored_fabric():
    img, person, pants = beige_scene()
    sk = validated_skin(person, pants, None, None)
    assert not ((sk > 0.5) & (pants > 0.5)).any()
    assert sk[50, 60] > 0.5  # tronco segue pele


def test_skin_continuity_does_not_touch_beige_fabric():
    img, person, pants = beige_scene()
    final = img.copy()
    final[25:50, 45:75] = (170, 120, 90)  # rosto mais moreno (Persona)
    z = np.zeros_like(person)
    face = z.copy()
    face[25:50, 45:75] = 1
    legs = z.copy()
    legs[110:195, 34:86] = 1
    masks = {"person_mask": person, "face_mask": face, "neck_mask": z, "clothing_mask": z, "leg_mask": legs,
             "hair_mask": z, "accessory_mask": z, "hand_mask": z, "arm_mask": z,
             "validated_skin_final": validated_skin(person, pants, None, None),
             "validated_skin_original": validated_skin(person, pants, None, None)}
    res = harmonize(final, img, masks, None, (45, 25, 75, 50))
    changed = np.abs(res.pixels.astype(int) - final.astype(int)).max(axis=2) > 0
    assert not (changed & (pants > 0.5)).any()  # calca bege intocada


def test_clothing_pixels_follow_the_new_garment_and_keep_the_texture():
    img, person, pants = beige_scene()
    kp = kp_standing()
    cur = img.copy()
    cur[pants > 0.5] = (226, 186, 158)  # reconstrucao: calca LISA (perdeu o canelado)
    new = np.zeros_like(pants)
    new[112:192, 33:87] = 1  # corpo da Persona: calca 1-2 px maior/deslocada
    cp = preserve_clothing(cur, img, pants, new, kp, kp)
    g = cp.garments[0]
    assert g.strategy in ("pixel", "aligned") and g.coverage > 0.8
    core = (cp.pasted > 0.5)
    col_std = cp.pixels[core].reshape(-1, 3).std(axis=0).mean()
    assert col_std > 3  # o canelado voltou (a reconstrucao era lisa: desvio ~0)
    assert (cp.to_reconstruct > 0.5).sum() > 0  # emendas ficam para o passe local leve
    huge = np.zeros_like(pants)
    huge[60:199, 10:110] = 1  # deformacao grande demais: nao estica o tecido
    cp2 = preserve_clothing(cur, img, pants, huge, kp, kp)
    assert cp2.garments[0].strategy == "reconstruct" and (cp2.pasted < 0.5).all()


def test_boundary_restores_exact_textured_background_outside_the_new_person():
    img, person, _ = beige_scene()
    region = np.zeros_like(person)
    region[10:199, 20:100] = 1
    new_person = np.zeros_like(person)
    new_person[22:195, 33:87] = 1  # Persona um pouco mais estreita
    a = boundary_alpha(new_person, person, region)
    gen = img.copy()
    gen[region > 0.5] = np.clip(gen[region > 0.5].astype(int) + 20, 0, 255)  # fundo repintado (halo)
    final = (img * (1 - a[..., None]) + gen * a[..., None]).astype(np.uint8)
    bg = (region > 0.5) & (person < 0.5) & ~(np.pad(new_person, 3)[3:-3, 3:-3] > 0.5)
    far = bg.copy()
    far[:, 26:94] = False
    assert (final[far] == img[far]).all()  # fundo texturizado volta EXATO (antes: tolerancia de cor 28)
    assert a[100, 31] > 0.5  # 'fantasma' da pessoa original (agora fundo) continua gerado
    h = halo_multiscale(img, final, new_person, region)
    assert h["pior"] is not None and h["pior"] < 3


async def test_v31_engine_runs_new_segmentation_clothing_preservation_and_boundary():
    img = wide_tattoo_photo()[0]
    store = Store(img)
    ad = FakeAdapter(store)
    from app.core.engines.replacement_v3 import PersonaReplacementV3

    eng = PersonaReplacementV3(reader=Reader(), segmenter=SegV3(), analyzer=Analyzer(
        {"foto": 0.1, "v3_full": 0.8, "final": 0.8}, 0.1), store=store, adapter=ad, config=CFG31, price_per_hour=0.57,
        provider="comfyui")
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, replacement_version="v3.1",
                            keep_intermediates=True, debug=True))
    passes = [p["pass"] for p in out.telemetry.passes]
    assert "clothing_preservation" in passes
    assert len(eng.segmenter.protect) >= 2  # foto original + imagem reconstruida
    assert "necklace" in (eng.segmenter.protect[0] or "")
    for k in ("person_new", "clothes_new", "skin_validated", "boundary_alpha", "diff_map", "clothing_preserved"):
        assert k in out.intermediates, k
    assert "multiescala" in out.measures["boundary"] or out.measures["boundary"].get("status") == "UNKNOWN"
    assert out.telemetry.attributes["v31_segmentation"]["clothes_new"]["status"] == "aceita"


def test_grounding_boxes_need_evidence_glasses_and_held_objects():
    """Mascaras de 09/10: 'oculos de sol' nos olhos de quem nao usa oculos e 'celular' em cima das maos."""
    from app.core.engines.replacement import _unconfirmed

    img = np.full((100, 100, 3), (60, 60, 70), np.uint8)
    img[60:90, 60:90] = (205, 160, 135)  # maos (pele)
    gate = ["glasses", "object"]
    assert _unconfirmed("sunglasses", "glasses", (20, 20, 50, 35), gate, "a woman with long dark hair", img, 0.35)
    assert _unconfirmed("sunglasses", "glasses", (20, 20, 50, 35), gate, "a woman wearing sunglasses", img, 0.35) is None
    assert "pele" in _unconfirmed("cell phone", None, (60, 60, 90, 90), gate, "selfie in a mirror", img, 0.35)
    assert _unconfirmed("cell phone", None, (5, 5, 30, 40), gate, "a mirror selfie of a woman", img, 0.35) is None
    assert _unconfirmed("cell phone", None, (5, 5, 30, 40), gate, "a woman on a cobblestone path", img, 0.35)
    assert _unconfirmed("necklace", "necklace", (5, 5, 30, 40), gate, "", img, 0.35) is None  # joias: sem esse portao
    assert _unconfirmed("sunglasses", "glasses", (20, 20, 50, 35), [], "", img, 0.35) is None  # V3/V2: sem portao


def test_production_0910_seam_contour_and_waist_patch_are_harmonized():
    """1a troca em producao (rua bege): contorno escuro em volta do top (faixa da peca que os originais nao cobrem)
    e mancha na cintura (lugar das maos originais) com a cor da reconstrucao."""
    img, person, pants = beige_scene()
    kp = kp_standing()
    top_o = np.zeros_like(person)
    top_o[50:80, 36:84] = 1
    clothes_o = np.maximum(top_o, pants)
    img[top_o > 0.5] = (214, 176, 140)
    img[100:120, 50:70] = (200, 150, 125)  # maos originais por cima da cintura: fora da roupa original
    clothes_o[100:120, 50:70] = 0
    cur = img.copy()
    cur[pants > 0.5] = (236, 200, 160)  # reconstrucao mais clara/saturada que a calca original
    cur[100:120, 50:70] = (236, 200, 160)
    top_n = np.zeros_like(person)
    top_n[49:81, 35:85] = 1
    cur[top_n > 0.5] = (214, 176, 140)
    edge = (top_n > 0.5) & ~(np.pad(top_n, 1)[2:, 1:-1] * np.pad(top_n, 1)[:-2, 1:-1]
                             * np.pad(top_n, 1)[1:-1, 2:] * np.pad(top_n, 1)[1:-1, :-2] > 0.5)
    ring = np.zeros_like(person, bool)
    ring[49:81, 35:85] = True
    ring[52:78, 38:82] = False
    cur[ring] = (90, 95, 60)  # contorno oliva desenhado pela reconstrucao
    clothes_n = np.maximum(top_n, pants)
    clothes_n[100:120, 50:70] = 1  # maos da Persona em outro lugar: ali agora e calca
    old = preserve_clothing(cur, img, clothes_o, clothes_n, kp, kp, harmonize=False)
    new = preserve_clothing(cur, img, clothes_o, clothes_n, kp, kp)
    ref_top = np.array((214, 176, 140), np.float32)
    d_old = np.linalg.norm(old.pixels[ring].astype(np.float32) - ref_top, axis=-1).mean()
    d_new = np.linalg.norm(new.pixels[ring].astype(np.float32) - ref_top, axis=-1).mean()
    assert d_old > 60 and d_new < 15, (d_old, d_new)  # o contorno sumiu
    waist = new.pixels[104:116, 54:66].reshape(-1, 3).astype(np.float32).mean(axis=0)
    pants_o = img[150:180, 40:80].reshape(-1, 3).astype(np.float32).mean(axis=0)
    assert np.linalg.norm(waist - pants_o) < 12  # sem mancha: a cintura tem a cor da calca original
    assert edge.any()


def test_jewelry_negatives_respect_what_the_photo_has():
    from types import SimpleNamespace

    from app.core.engines.replacement_v3 import _extra_negative

    v31 = CFG31["v31"]
    plain = SimpleNamespace(sheet=SimpleNamespace(caption="a woman standing on a cobblestone path"), box_policy=[])
    assert "pendant" in _extra_negative(v31, plain)
    with_necklace = SimpleNamespace(sheet=SimpleNamespace(caption="a woman wearing a gold necklace and rings"),
                                    box_policy=[])
    neg = _extra_negative(v31, with_necklace)
    assert "pendant" not in neg and "rings on every finger" not in neg and "bangles" in neg
    by_box = SimpleNamespace(sheet=SimpleNamespace(caption=""), box_policy=[{"label": "necklace", "policy": "PRESERVE"}])
    assert "pendant" not in _extra_negative(v31, by_box)
