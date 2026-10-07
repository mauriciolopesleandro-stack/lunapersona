"""Persona Transfer v1.2 (fluxo do usuario): identidade so em rosto + cabelo, bracos/maos/roupa
com a geometria original, tatuagem removida so na pele, integracao das bordas sem tocar no
rosto; fundo travado, workflow com pose, rollback, validacao e trava de custo."""
import copy
import json

import numpy as np
import pytest

from app.core.generation.budget import BudgetExceeded, BudgetGuard, ExperimentPlan
from app.core.persona_replacement.contracts import RawSegments, TransformRequest
from app.core.persona_replacement.segmentation import build_masks
from app.core.persona_replacement.transfer import (
    FACE_REFINE,
    INTEGRATION,
    TATTOO,
    TRANSFER,
    TransferOrchestrator,
    edge_ring,
    fill_tattoos,
    identity_mask,
    load_transfer_config,
    plausible_accessories,
    skin_region,
    skin_tattoo_mask,
)
from tests.conftest import REPO
from tests.test_persona_replacement import FACE_BOX, KPS, SHIRT, Analyzer, Reader, Store, Transformer, photo
from tests.test_v2_multipass import MASTER

CFG_PATH = REPO / "config" / "persona_transfer.json"
CFG = load_transfer_config(CFG_PATH)
RAW = json.loads(CFG_PATH.read_text(encoding="utf-8"))
WF = json.loads((REPO / "workflows" / "realvis-persona-transfer.json").read_text(encoding="utf-8"))


def write_cfg(tmp_path, mutate):
    d = copy.deepcopy(RAW)
    mutate(d)
    p = tmp_path / "t.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


# --- configuracao e workflow -------------------------------------------------------------


def test_config_follows_the_spec():
    t = CFG.transfer
    assert 0.85 <= t["denoise"] <= 1.0 and t["controlnet"]["type"] == "openpose" and "lunavox" in t["prompt"]
    fr = CFG.face_refinement
    assert fr["identity_adapter"] == "instantid" and fr["adapter_weight"] <= 0.6  # identidade das referencias, sem forcar
    assert CFG.checks["max_background_changed"] <= 0.002  # fundo TRAVADO
    assert CFG.tattoo_removal["denoise"] <= 0.7 and CFG.tattoo_removal["lora"] is False  # geometria da foto
    assert {"heavy makeup", "text", "second face", "hoop earrings", "tattoo ghosting"} <= set(CFG.negative_extra)
    cn = CFG.tattoo_removal["controlnet"]
    assert cn["type"] == "depth" and cn["strength"] >= 0.8  # bracos e maos seguem a estrutura da foto
    assert CFG.integration["denoise"] <= 0.25 and CFG.integration["lora"] is False
    assert CFG.post_lighting_match["enabled"] is False  # sem filtro/grao depois
    assert CFG.budget_limit_usd == 0.05 and "tattoo" in CFG.negative_extra
    assert CFG.generation_config == "config/persona_engine_v2.json"


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["transfer"].update(denoise=0.3), "GERADA"),
    (lambda d: d["integration"].update(denoise=0.5), "leve"),
    (lambda d: d["face_refinement"].update(identity_adapter="instantid", adapter_weight=1.0), "InstantID"),
    (lambda d: d["tattoo_removal"].update(denoise=0.9), "geometria"),
])
def test_invalid_config_is_refused(tmp_path, mutate, message):
    with pytest.raises(ValueError, match=message):
        load_transfer_config(write_cfg(tmp_path, mutate))


def test_workflow_uses_pose_and_composites_over_the_original():
    g = WF["graph"]
    kinds = {n["class_type"] for n in g.values()}
    assert {"DWPreprocessor", "SetUnionControlNetType", "ControlNetApplyAdvanced", "SetLatentNoiseMask"} <= kinds
    assert g["14"]["inputs"]["positive"] == ["23", 0] and g["14"]["inputs"]["model"] == ["2", 0]  # pose + LoRA
    assert g["20"]["inputs"]["image"] == ["12", 0]  # a pose vem do proprio recorte da foto
    assert g["18"]["inputs"]["destination"] == ["10", 0] and g["18"]["inputs"]["mask"] == ["31", 0]
    assert set(WF["meta"]["required_params"]) >= {"IMAGE", "MASK", "DENOISE", "LORA_NAME", "CROP_X", "WORK_W"}


# --- mascaras -----------------------------------------------------------------------------


def masks(protect=(), tattoo=False):
    img, person, hair = photo(tattoo=tattoo)
    return build_masks(RawSegments(person, hair, list(protect)), FACE_BOX, KPS, img), img


def test_identity_is_only_face_and_hair():
    m, _ = masks(protect=[(66, 44, 94, 56)])  # oculos
    ident = identity_mask(m, FACE_BOX, 0.12)
    assert ident[60, 80] == 1 and ident[30, 80] == 1  # rosto e cabelo
    assert ident[150, 46] == 0 and ident[170, 80] == 0  # braco e camisa: geometria da foto
    assert ident[50, 80] == 0  # oculos preservados
    assert (ident * (1 - m.person)).max() == 0  # nunca fora da pessoa (fundo travado)


def test_tattoo_mask_is_skin_only_and_away_from_the_clothing_edge():
    m, _ = masks(tattoo=True)
    ink = skin_tattoo_mask(m, identity_mask(m, FACE_BOX, 0.12), 3)
    assert ink[160, 46] == 1  # tinta no braco
    assert ink[160, 51] == 0 and ink[170, 80] == 0  # borda da roupa e roupa
    assert ink[60, 80] == 0  # rosto e da passada de identidade


def wide_tattoo_photo():
    """Tatuagem que cobre a largura toda do braco (encosta no contorno): nao e "buraco" na pele."""
    img, person, hair = photo()
    img[124:134, 40:52] = (70, 70, 80)
    img[200:230, 40:52] = (170, 190, 215)  # punho de outra peca clara (nao e pele nem tinta)
    clothes = np.zeros(person.shape, np.float32)
    clothes[110:230, 52:120] = 1
    clothes[200:230, 40:52] = 1
    return img, person, hair, clothes


def test_visible_skin_includes_ink_that_does_not_look_like_skin():
    img, person, hair, clothes = wide_tattoo_photo()
    m = build_masks(RawSegments(person, hair, []), FACE_BOX, KPS, img)
    assert m.clothing[128, 46] == 1  # sem o Florence a tinta larga caia na roupa (protegida)
    body, ink = skin_region(m, clothes, identity_mask(m, FACE_BOX, 0.12), img, 3, 10, 12)
    assert body[128, 46] == 1 and ink[128, 46] == 1 and body[150, 45] == 1 and ink[150, 45] == 0
    assert body[170, 80] == 0 and body[215, 46] == 0  # roupa (inclusive a peca clara) fica fora
    assert body[60, 80] == 0  # rosto e da passada de identidade


def test_accessory_boxes_bigger_than_the_face_are_false_positives():
    glasses, top = (66, 44, 94, 56), (40, 100, 120, 200)
    assert plausible_accessories([glasses, top], FACE_BOX, 1.0) == [glasses]


def test_tattoo_ink_is_removed_from_the_input_only():
    img, person, hair = photo(tattoo=True)
    m = build_masks(RawSegments(person, hair, []), FACE_BOX, KPS, img)
    clean = fill_tattoos(img, m.tattoos, m.skin, 8)
    assert abs(int(clean[160, 46, 0]) - 205) <= 3  # tinta virou a pele de volta
    assert (clean[200, 90] == SHIRT).all() and (clean[5, 5] == img[5, 5]).all()
    assert (fill_tattoos(photo()[0], np.zeros((240, 160)), m.skin, 8) == photo()[0]).all()


def test_edge_ring_is_a_band_on_both_sides():
    r = np.zeros((40, 40), np.float32)
    r[10:30, 10:30] = 1
    ring = edge_ring(r, 3)
    assert ring[10, 20] == 1 and ring[8, 20] == 1 and ring[20, 20] == 0 and ring[2, 20] == 0


# --- orquestrador -------------------------------------------------------------------------


def orchestrator(faces, original_similarity=0.1, tattoo=False, protect=(), wide=False):
    img = wide_tattoo_photo()[0] if wide else photo(tattoo=tattoo)[0]
    store = Store(img)
    tr = Transformer(store)

    class Seg:
        async def segment(self, image, sheet):
            if wide:
                _, person, hair, clothes = wide_tattoo_photo()
                return RawSegments(person, hair, list(protect), clothes=clothes)
            _, person, hair = photo()
            return RawSegments(person, hair, list(protect))

    orch = TransferOrchestrator(reader=Reader(), segmenter=Seg(), transformer=tr,
                                analyzer=Analyzer(faces, original_similarity), store=store, config=CFG,
                                duplicate_similarity=0.9, price_per_hour=0.57)
    return orch, tr, store


async def test_identity_on_face_and_hair_keeps_arms_clothing_and_background():
    orch, tr, store = orchestrator({"foto": 0.10, TRANSFER: 0.78, INTEGRATION: 0.775})
    res = await orch.run("foto.png", MASTER, "plastic skin", 7)
    assert [c.name for c in tr.calls] == [TRANSFER, INTEGRATION]
    first = tr.calls[0]
    assert first.use_lora and first.identity_adapter is None and first.denoise == CFG.transfer["denoise"]
    assert "lunavox" in first.prompt and "tattoo" in first.negative and "{description}" not in first.prompt
    assert first.mask[60, 80] == 1 and first.mask[150, 46] == 0  # rosto sim, braco nao
    integ = tr.calls[1]
    assert integ.use_lora is False and integ.denoise == CFG.integration["denoise"]
    assert integ.mask[60, 80] == 0  # a integracao nao toca no rosto
    rec = {r.stage: r for r in res.records}
    assert not rec[FACE_REFINE].accepted and "nao necessario" in rec[FACE_REFINE].rollback_reason
    assert not rec[TATTOO].accepted and "sem tatuagem" in rec[TATTOO].rollback_reason
    assert res.checkpoints == ["original", TRANSFER, INTEGRATION]
    original, final = store.images["foto.png"], res.final.pixels
    _, person, _ = photo()
    outside = person < 0.5
    assert (final[outside] == original[outside]).all()  # fundo TRAVADO: pixel a pixel
    assert (final[200, 90] == SHIRT).all() and (final[150, 46] == original[150, 46]).all()  # roupa e braco
    assert res.report.identity == 0.775 and res.report.status == "PASS" and res.report.integration_score is not None
    assert {"identity", "visible_skin", "ink", "seam"} <= set(res.mask_areas)


async def test_face_refinement_runs_only_when_identity_is_low():
    orch, tr, _ = orchestrator({"foto": 0.10, TRANSFER: 0.55, FACE_REFINE: 0.74, INTEGRATION: 0.74})
    res = await orch.run("foto.png", MASTER, "neg", 7)
    assert [c.name for c in tr.calls] == [TRANSFER, FACE_REFINE, INTEGRATION]
    assert tr.calls[1].image.startswith(TRANSFER) and tr.calls[1].identity_adapter == "instantid"
    assert tr.calls[1].adapter_weight <= 0.6 and tr.calls[1].reference is MASTER
    assert res.checkpoints == ["original", TRANSFER, FACE_REFINE, INTEGRATION] and res.report.identity == 0.74


async def test_tattoos_are_removed_only_on_the_skin_from_a_clean_input():
    faces = {"foto": 0.1, TRANSFER: 0.8, TATTOO: 0.8, INTEGRATION: 0.8}
    orch, tr, store = orchestrator(faces, tattoo=True, protect=[(40, 100, 120, 200)])  # caixa falsa (top inteiro)
    res = await orch.run("foto.png", MASTER, "neg", 7)
    assert [c.name for c in tr.calls] == [TRANSFER, TATTOO, INTEGRATION]
    tat = tr.calls[1]
    src = store.images[tat.image]
    assert tat.image.startswith("tattoo_prefill") and abs(int(src[160, 46, 0]) - 205) <= 3
    assert tat.mask[160, 46] == 1 and tat.mask[60, 80] == 0 and tat.mask[170, 80] == 0  # so a tinta na pele
    assert tat.use_lora is False and tat.denoise == CFG.tattoo_removal["denoise"]
    img = store.images["foto.png"]
    assert res.final.name == INTEGRATION and (res.final.pixels[0:5, 0:5] == img[0:5, 0:5]).all()


async def test_visible_skin_is_redrawn_with_the_original_structure_and_the_top_stays_exact():
    faces = {"foto": 0.1, TRANSFER: 0.8, TATTOO: 0.8, INTEGRATION: 0.8}
    orch, tr, store = orchestrator(faces, wide=True)
    res = await orch.run("foto.png", MASTER, "neg", 7)
    assert [c.name for c in tr.calls] == [TRANSFER, TATTOO, INTEGRATION]
    skin = tr.calls[1]
    assert skin.control == "foto.png"  # estrutura (profundidade) da foto ORIGINAL
    assert skin.mask[128, 46] == 1 and skin.mask[150, 45] == 1 and skin.mask[170, 80] == 0
    assert tr.calls[2].mask[112, 80] == 0 and tr.calls[2].mask[60, 80] == 0  # integracao: nem top nem rosto
    original = store.images["foto.png"]
    clothes = wide_tattoo_photo()[3] > 0.5
    assert (res.final.pixels[clothes] == original[clothes]).all()  # o top (e a outra peca) exatos
    assert res.report.clothing_changed == 0.0 and res.final.name == INTEGRATION
    assert res.mask_areas["clothes_source"] == "florence"


async def test_weak_clothes_segmentation_falls_back_and_never_turns_the_top_into_skin():
    faces = {"foto": 0.1, TRANSFER: 0.8, TATTOO: 0.8, INTEGRATION: 0.8}
    orch, tr, store = orchestrator(faces, wide=True)
    seg = orch.segmenter

    class Weak:
        async def segment(self, image, sheet):
            raw = await seg.segment(image, sheet)
            raw.clothes[:] = 0  # o Florence nao achou a roupa
            return raw

    orch.segmenter = Weak()
    res = await orch.run("foto.png", MASTER, "neg", 7)
    assert res.mask_areas["clothes_source"] == "heuristic"
    assert all(c.mask[170, 80] == 0 for c in tr.calls)  # o top nunca entra em mascara nenhuma


async def test_integration_that_drops_identity_is_rolled_back():
    orch, tr, _ = orchestrator({"foto": 0.10, TRANSFER: 0.80, INTEGRATION: 0.70})
    res = await orch.run("foto.png", MASTER, "neg", 7)
    rec = {r.stage: r for r in res.records}
    assert not rec[INTEGRATION].accepted and "identidade caiu" in rec[INTEGRATION].rollback_reason
    assert res.final.name == TRANSFER


async def test_transfer_that_does_not_bring_the_persona_is_rejected():
    orch, tr, _ = orchestrator({"foto": 0.30, TRANSFER: 0.20, INTEGRATION: 0.20})
    res = await orch.run("foto.png", MASTER, "neg", 7)
    assert [c.name for c in tr.calls] == [TRANSFER]
    assert res.final.name == "original" and res.report.status == "FAIL" and "transfer_rejected" in res.report.failures


async def test_mixed_identity_with_the_original_person_fails():
    orch, _, _ = orchestrator({"foto": 0.10, TRANSFER: 0.80, INTEGRATION: 0.80}, original_similarity=0.52)
    res = await orch.run("foto.png", MASTER, "neg", 7)
    assert "identity_mixing" in res.report.failures and res.report.status == "FAIL"


# --- adapter do ComfyUI -------------------------------------------------------------------


async def test_transfer_transformer_dispatches_by_stage():
    from app.core.generation.v2_config import load_v2_config
    from app.providers.comfyui.region_pass import ComfyRegionPassAdapter
    from app.providers.comfyui.replacement import ComfyReplacementTransformer, ComfyTransferTransformer, crop_for
    from app.providers.comfyui.session import ComfySession
    from app.workflow_manager.manager import WorkflowManager
    from tests.test_v2_models import FakeComfyV2

    v2 = load_v2_config(REPO / "config" / "persona_engine_v2.json")
    comfy = FakeComfyV2()
    session = ComfySession(comfy, WorkflowManager(REPO / "workflows"))
    region = ComfyRegionPassAdapter(session, v2.model(), v2.lora, identity_adapters=v2.identity_adapters)
    tr = ComfyTransferTransformer(session, ComfyReplacementTransformer(region, comfy), v2.model(), v2.lora, CFG.transfer)
    m, _ = masks()
    ident = identity_mask(m, FACE_BOX, 0.12)
    out = await tr.transform(TransformRequest("foto.png", ident, "lunavox, a woman", "n", 1.0, 0.9, 5, TRANSFER, use_lora=True))
    g = comfy.graphs[0]
    crop = crop_for(ident, margin=0.12)
    assert g["22"]["inputs"]["type"] == "openpose" and g["21"]["inputs"]["control_net_name"] == CFG.transfer["controlnet"]["file"]
    assert g["14"]["inputs"]["denoise"] == 0.9 and g["2"]["inputs"]["strength_model"] == v2.lora.strength
    assert g["11"]["inputs"]["x"] == crop["x"] and g["30"]["inputs"]["image"].startswith("xfermask_")
    assert out.metadata["workflow"] == "realvis-persona-transfer"
    await tr.transform(TransformRequest("foto.png", m.face_inner, "p", "n", 0.4, 0.18, 9, INTEGRATION))
    assert "c1" in comfy.graphs[1]  # integracao: region pass do replacement, recortado pela mascara
    skin_tr = ComfyTransferTransformer(session, ComfyReplacementTransformer(region, comfy), v2.model(), v2.lora,
                                       CFG.transfer, skin=CFG.tattoo_removal)
    out = await skin_tr.transform(TransformRequest("limpa.png", m.body_skin, "skin", "n", 1.0, 0.7, 3, TATTOO,
                                                   use_lora=False, control="foto.png"))
    g = comfy.graphs[2]
    assert out.metadata["workflow"] == "realvis-skin-depth" and g["22"]["inputs"]["type"] == "depth"
    assert g["40"]["inputs"]["image"] == "foto.png" and g["10"]["inputs"]["image"] == "limpa.png"
    assert g["20"]["class_type"] == "DepthAnythingV2Preprocessor" and g["2"]["inputs"]["strength_model"] == 0.0


# --- custo ------------------------------------------------------------------------------------


def test_first_test_is_blocked_above_five_cents():
    over = ExperimentPlan("transfer 1o teste", "1 imagem", "pose", images=1, seconds_per_image=200,
                          overhead_seconds=200, price_per_hour=0.57)
    with pytest.raises(BudgetExceeded):
        BudgetGuard(CFG.budget_limit_usd).check(over)
    fits = ExperimentPlan("transfer 1o teste", "1 imagem", "pose", images=1, seconds_per_image=120,
                          overhead_seconds=180, price_per_hour=0.57)
    assert BudgetGuard(CFG.budget_limit_usd).check(fits)["estimated_cost_usd"] <= 0.05
