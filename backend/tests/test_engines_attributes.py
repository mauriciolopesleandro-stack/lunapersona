"""Spec 45: Persona Attribute Authority System (GPU mockada).

A foto original manda so no que e PRESERVE; a Persona Sheet manda na identidade; o pedido tem prioridade.
A politica chega a prompt, mascara, condicionamento, inpaint e validacao - e identidade nao compensa violacao.
"""
import json

import numpy as np
import pytest

from app.core.engines.attributes import PRESERVE, RECONSTRUCT, REMOVE, AttributePolicyError, resolve
from app.core.engines.replacement import ReplacementRequestError
from app.core.engines.retry import RetryPolicyV2
from app.core.engines.policies import POLICIES
from app.core.engines.validation import REJECT, validate_v2
from tests.conftest import REPO
from tests.test_engines_replacement import engine, req

LUNA = json.loads((REPO / "personas" / "luna" / "persona_sheet.json").read_text(encoding="utf-8"))
FACES = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "tattoo": 0.8, "skin_refine": 0.8, "integrated": 0.8, "final": 0.8}
GOOD = {"identity": 0.94, "original_sim": 0.03, "pose": 0.02, "background": 0.0, "tattoo_residual": 0.02,
        "texture_final": 5.0, "texture_ref": 5.0, "tone_delta": 1.0, "persona_instances": 1, "faces": 1,
        "shoulder_ratio": 1.0, "composition_shift": 0.0, "person_found": True, "clothing_change": 0.0,
        "seam_excess": 4.0, "straight_edges": 5.0}


def test_precedence_default_then_persona_sheet_then_request():
    base = resolve()
    assert base.get("tattoos") == REMOVE and base.get("clothing") == PRESERVE and base.get("face") == RECONSTRUCT
    luna = resolve(LUNA)
    assert luna.get("accessories") == PRESERVE and luna.source["accessories"] == "persona"  # oculos mantidos (regra da Luna)
    asked = resolve(LUNA, preserve=["makeup", "black_top"], remove=["tattoos", "original_person_marks"])
    assert asked.get("makeup") == PRESERVE and asked.source["makeup"] == "request"
    assert asked.preserve_items == ["black top"]  # item nomeado vai para o prompt, nao some


def test_luna_sheet_declares_no_tattoos_and_never_infers_from_the_photo():
    assert LUNA["identity_exclusions"]["tattoos"] == [] and "tattoos" in LUNA["replacement_policy"]["remove"]
    pol = resolve(LUNA)
    assert all(pol.get(a) == REMOVE for a in ("tattoos", "scars", "piercings", "birthmarks", "original_person_marks"))
    assert "earrings" in pol.persona_features  # traco DA Luna (argolas pequenas) nao vem da foto


def test_conflicts_and_unsupported_policies_are_refused():
    with pytest.raises(AttributePolicyError):
        resolve(preserve=["tattoos"], remove=["tatuagens"])  # alias do mesmo atributo em duas listas
    with pytest.raises(AttributePolicyError):
        resolve(reconstruct=["background"])  # o motor nao regenera o cenario
    with pytest.raises(AttributePolicyError):
        resolve(reconstruct=["unicornio"])
    with pytest.raises(ReplacementRequestError):
        req(preserve_attributes=["face"]).attributes()  # Replacement sempre reconstroi o rosto


def test_identity_never_compensates_a_policy_violation():
    """45.10/45.12: identidade 0,94 com tatuagem residual 0,32 = REJECT."""
    pol = resolve(LUNA).policy
    bad = validate_v2({**GOOD, "tattoo_residual": 0.32}, policy=pol)
    ap = bad.checks["attribute_policy"]
    assert bad.status == REJECT and ap.status == REJECT and "tattoos" in ap.metadata["violations"]
    ok = validate_v2(GOOD, policy=pol)
    assert ok.checks["attribute_policy"].status != REJECT
    src = validate_v2({**GOOD, "original_sim": 0.45}, policy=pol)
    assert "source_identity_residual" in src.checks["attribute_policy"].metadata["violations"]
    roupa = validate_v2({**GOOD, "clothing_change": 0.2}, policy=resolve(LUNA, preserve=["clothing"]).policy)
    assert "clothing" in roupa.checks["attribute_policy"].metadata["violations"]


def test_preserved_tattoos_are_not_residue_and_turn_the_cleanup_off():
    r = req(preserve_attributes=["tattoos"], persona_sheet=LUNA)
    assert r.plan().tattoo_cleanup is False
    rep = validate_v2({**GOOD, "tattoo_residual": 0.5}, policy=r.attributes().policy)
    assert rep.checks["tattoo"].status == "PASS" and rep.checks["attribute_policy"].status != REJECT


def test_policy_violation_maps_to_the_specific_retry():
    from app.core.engines.replacement import _specific

    pol = resolve(LUNA).policy
    rep = validate_v2({**GOOD, "tattoo_residual": 0.32}, policy=pol)
    fails = _specific(rep, rep.failures())
    p, step = RetryPolicyV2().next_plan(POLICIES["QUALITY"], fails, 1)
    assert step.failure_type == "tattoo" and p.extra["tattoo_margin_boost"] == 1 and "reconstrucao de pele" in step.strategy


async def test_example_45_13_shoulder_tattoo_is_reconstructed_as_luna_skin():
    eng, ad, store = engine(FACES)
    out = await eng.run(req(advanced={"max_retries": 0}, keep_intermediates=True, persona_sheet=LUNA,
                            preserve_attributes=["pose", "clothing", "background", "lighting", "composition", "black_top"],
                            remove_attributes=["tattoos"], reconstruct_attributes=["face", "body", "skin"]))
    stages = [c.stage for c in ad.calls]
    # corpo RECONSTRUCT (spec 46): a pele do braco e refeita como pele da Luna na etapa do corpo (a tatuagem sai ali)
    body = next(c for c in ad.calls if c.stage == "body_identity")
    assert "clean natural skin" in body.prompt and "tattoo outline" in body.negative and body.identity.use_lora
    ident = next(c for c in ad.calls if c.stage == "identity")
    assert "no tattoos" in ident.negative or "tattoo" in ident.negative
    assert "keep the black top" in ident.prompt
    # com o corpo da FOTO pedido, a tatuagem sai pela reconstrucao de pele propria
    eng2, ad2, _ = engine(FACES)
    await eng2.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, remove_attributes=["tattoos"],
                       preserve_attributes=["body", "hands"]))
    tat = next(c for c in ad2.calls if c.stage == "tattoo_cleanup")
    assert "clean natural skin" in tat.prompt and "tattoo outline" in tat.negative and tat.identity.use_lora
    t = out.telemetry.to_dict()["attributes"]
    assert t["policy"]["tattoos"] == REMOVE and t["source"]["tattoos"] == "request" and t["preserve_items"] == ["black top"]
    assert {"source_identity_mask", "source_markings_mask", "source_face_mask", "source_body_mask"} <= set(out.intermediates)
    assert "attribute_policy" in out.report.checks


async def test_makeup_preserve_reaches_prompt_and_negative():
    eng, ad, _ = engine(FACES)
    await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, preserve_attributes=["makeup"]))
    ident = next(c for c in ad.calls if c.stage == "identity")
    assert "no makeup" not in ident.prompt and "same makeup as in the photo" in ident.prompt
    assert "heavy makeup" not in ident.negative


async def test_hair_preserve_keeps_the_source_hair_out_of_the_identity_mask():
    eng, ad, store = engine(FACES)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, preserve_attributes=["hair"]))
    ident_mask = next(c for c in ad.calls if c.stage == "identity").mask
    from tests.test_persona_transfer import wide_tattoo_photo

    hair = wide_tattoo_photo()[2]
    assert float((ident_mask[hair > 0.5] > 0.5).mean()) < 0.2  # cabelo da foto fica fora da geracao
    assert out.measures["hair_change"] is not None


async def test_sunglasses_preserved_but_earrings_removed_by_label():
    from app.core.persona_replacement.contracts import RawSegments
    from tests.test_persona_transfer import wide_tattoo_photo

    class LabeledSeg:
        async def segment(self, image, sheet):
            _, person, hair, clothes = wide_tattoo_photo()
            return RawSegments(person, hair, [(64.0, 46.0, 96.0, 56.0), (55.0, 60.0, 59.0, 68.0)], clothes=clothes,
                               protect_labels=["sunglasses", "earrings"])

    eng, ad, store = engine(FACES)
    store.images["foto.png"][48:55, 66:94] = (15, 15, 20)
    eng.segmenter = LabeledSeg()
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, remove_attributes=["earrings"]))
    boxes = {b["label"]: b["policy"] for b in out.telemetry.attributes["boxes"]}
    assert boxes == {"sunglasses": PRESERVE, "earrings": REMOVE}  # objeto a objeto: oculos ficam, brinco sai (pedido)
    assert (out.pixels[50:53, 70:90] == (15, 15, 20)).all()  # oculos da foto mantidos


async def test_luna_body_redraws_body_with_pose_only_and_keeps_hands():
    """Corpo da Luna na pose da foto, com a LoRA e o corpo da Persona Sheet, SEM profundidade. Roupa redesenhada
    so quando pedida (spec 46: o padrao e a MESMA roupa - so a pele visivel e refeita)."""
    from tests.test_persona_transfer import wide_tattoo_photo

    clothes = wide_tattoo_photo()[3]
    eng0, ad0, _ = engine(FACES)
    await eng0.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA))
    same = next(c for c in ad0.calls if c.stage == "body_identity")
    assert float((same.mask[clothes > 0.5] > 0.5).mean()) < 0.05  # mesma roupa: a roupa nao entra na mascara
    eng, ad, _ = engine(FACES)
    out = await eng.run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, reconstruct_attributes=["clothing"]))
    body = next(c for c in ad.calls if c.stage == "body_identity")
    assert float((body.mask[clothes > 0.5] > 0.5).mean()) > 0.5  # roupa redesenhada pedida: entra
    assert body.controls.depth_strength == 0.0 and body.controls.pose_strength > 0 and body.identity.use_lora
    assert "hourglass" in body.prompt and "curly" not in body.prompt
    assert [c.stage for c in ad.calls].index("body_identity") < [c.stage for c in ad.calls].index("identity")
    assert out.report.checks["attribute_policy"].metadata["verdicts"]["clothing"]["policy"] == RECONSTRUCT
    pres = await engine(FACES)[0].run(req(advanced={"max_retries": 0}, persona_sheet=LUNA, preserve_attributes=["body"]))
    assert "body_identity" not in [p["pass"] for p in pres.telemetry.passes]  # corpo da foto pedido


def test_caption_loses_the_original_persons_hair_and_face():
    from app.core.engines.replacement import scrub_identity

    cap = ("The image shows a young woman with long, curly dark hair standing in front of a wooden door. "
           "She is wearing a black strapless crop top and black shorts. Her makeup is natural.")
    out = scrub_identity(cap)
    assert "curly" not in out and "makeup" not in out and "black strapless crop top" in out and "wooden door" in out
