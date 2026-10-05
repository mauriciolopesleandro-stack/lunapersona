"""Persona Engine V1.1 (pele natural): SkinRealismAnalyzer, faixas, UNKNOWN,
politica de correcao, guarda de identidade, validador, telemetria e integracao
com o orquestrador - e a regressao: com tudo desligado a V1 nao muda."""
import json

import numpy as np
import pytest
from PIL import Image, ImageFilter

from app.core.generation.history import ACCEPTED, ERROR
from app.core.generation.orchestrator import face_guidance
from app.core.generation.request import GenerationRequest
from app.core.persona.sheet import PersonaSheetRepository
from app.core.validation.analysis import DetectedFace
from app.core.validation.checks import WARN, AgeValidator, SkinRealismValidator, ValidationContext
from app.core.validation.engine import ValidationEngine
from app.core.validation.skin import (
    FAIL,
    OVERSMOOTHING_DETECTED,
    PASS,
    SKIN_REALISM_LOW,
    UNKNOWN,
    UNMEASURED_ASPECTS,
    IdentityPreservationGuard,
    SkinCorrectionPolicy,
    grade,
    unknown,
)
from app.providers.base import FaceLockGuidance, GenerationParameters, ProviderImage, ReferenceImage, SkinCorrectionRequest
from app.validation_backends.skin import measure_skin
from tests.conftest import SHEET
from tests.fakes import (
    GOOD,
    LOW_FACE,
    WALKING,
    FakeFace,
    FakeScene,
    FakeSkinCorrection,
    ScriptedAnalyzer,
    ScriptedSkin,
    analysis,
    body,
    build,
    face,
)

SKIN_CFG = json.loads(SHEET.read_text(encoding="utf-8"))["validation_profile"]["skin_realism"]
CORRECTION = json.loads(SHEET.read_text(encoding="utf-8"))["generation_profile"]["skin_correction"]


def req(**kw) -> GenerationRequest:
    return GenerationRequest(**{"persona_id": "luna", "scene_prompt": "walking in a cafe",
                                "generation_parameters": GenerationParameters(seed=42), **kw})


def configure(engine_dir, correction=None, texture=None, age_lock=None, skin=None, age_check=None):
    """Liga recursos da V1.1 na copia da ficha usada pelo teste (a real fica desligada)."""
    path = engine_dir / "luna" / "persona_sheet.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    gp, vp = data["generation_profile"], data["validation_profile"]
    if correction is not None:
        gp["skin_correction"].update(correction)
    if texture is not None:
        gp["face_lock"]["texture_preservation"].update(texture)
    if age_lock is not None:
        gp["face_lock"]["age_lock"].update(age_lock)
    if skin is not None:
        vp["skin_realism"].update(skin)
    if age_check is not None:
        vp["age"]["consistency_check"] = age_check
    path.write_text(json.dumps(data), encoding="utf-8")


def by_locator(face_analysis, skin_analysis=None):
    """Imagens do Face Lock (face*) e da correcao (skin*) mostram analises diferentes."""
    def pick(locator, n):
        if locator.startswith("skin"):
            return skin_analysis or face_analysis
        if locator.startswith("face"):
            return face_analysis
        return analysis([], [body()])
    return pick


# --- medidor (imagens sinteticas) -------------------------------------------


def textured(sigma=5.0, size=600, seed=1) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = np.clip(150 + rng.normal(0, sigma, (size, size)), 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "L").convert("RGB")


def kps_face(iod=100.0, cx=300.0, cy=260.0) -> DetectedFace:
    h = iod / 2
    kps = [(cx - h, cy), (cx + h, cy), (cx, cy + 0.5 * iod), (cx - 0.4 * iod, cy + iod), (cx + 0.4 * iod, cy + iod)]
    return DetectedFace(bbox=(cx - iod, cy - iod, cx + iod, cy + 1.5 * iod), similarity=0.7, age=27, sex="F",
                        det_score=0.9, kps=kps)


def test_meter_orders_natural_blurred_and_plastic_skin():
    natural = measure_skin(textured(), kps_face(), SKIN_CFG)
    blurred = measure_skin(textured().filter(ImageFilter.GaussianBlur(1.2)), kps_face(), SKIN_CFG)
    plastic = measure_skin(Image.new("RGB", (600, 600), (150, 150, 150)), kps_face(), SKIN_CFG)
    assert natural.score > blurred.score > plastic.score
    assert natural.status == PASS and plastic.status == FAIL
    assert OVERSMOOTHING_DETECTED in plastic.reasons and SKIN_REALISM_LOW in plastic.reasons
    assert natural.raw["regions"] == "kps" and natural.raw["patches_used"] == 3
    # escala e honesta sobre o que nao mede
    assert natural.unknown_aspects == UNMEASURED_ASPECTS and natural.raw["calibration_status"] == "PROVISIONAL"


def test_meter_is_unknown_when_it_cannot_measure():
    assert measure_skin(textured(), None, SKIN_CFG).status == UNKNOWN
    small = measure_skin(textured(), kps_face(iod=20), SKIN_CFG)
    assert small.status == UNKNOWN and small.score is None and "pequeno" in small.note
    # todas as areas fora da imagem
    outside = measure_skin(textured(size=200), kps_face(cx=900, cy=900), SKIN_CFG)
    assert outside.status == UNKNOWN and outside.score is None


def test_meter_falls_back_to_face_box_without_landmarks():
    f = kps_face()
    f.kps = []
    result = measure_skin(textured(), f, SKIN_CFG)
    assert result.status == PASS and result.raw["regions"] == "bbox"


# --- faixas ----------------------------------------------------------------


@pytest.mark.parametrize("texture, status, label", [
    (1.5, FAIL, "FAIL"), (2.0, "WARN", "WARN"), (2.5, PASS, "ACCEPTABLE"), (3.0, PASS, "GOOD"), (3.4, PASS, "EXCELLENT"),
])
def test_bands(texture, status, label):
    result = grade(texture, 0.0, SKIN_CFG, {})
    assert (result.status, result.grade) == (status, label)
    assert (SKIN_REALISM_LOW in result.reasons) == (status != PASS)


def test_oversmoothing_caps_the_score():
    result = grade(3.4, 0.7, SKIN_CFG, {})  # textura otima, mas 70% dos blocos lisos
    assert result.score == pytest.approx(0.3) and result.status == "WARN"
    assert OVERSMOOTHING_DETECTED in result.reasons and result.raw["texture_component"] == pytest.approx(0.96)


# --- politica ----------------------------------------------------------------


def test_policy_only_corrects_measured_low_skin():
    policy = SkinCorrectionPolicy()
    on = {**CORRECTION, "enabled": True}
    low, ok = grade(1.5, 0.0, SKIN_CFG, {}), grade(3.0, 0.0, SKIN_CFG, {})
    assert policy.decide(low, CORRECTION, 0).apply is False  # desligada na ficha real
    decision = policy.decide(low, on, 0)
    assert decision.apply and decision.reason == SKIN_REALISM_LOW
    assert policy.decide(ok, on, 0).apply is False
    assert policy.decide(unknown("x"), on, 0).apply is False
    assert policy.decide(low, on, 1).apply is False  # max_attempts = 1


# --- guarda de identidade -----------------------------------------------------


def guard(before, after, skin_before=0.2, skin_after=0.6):
    return IdentityPreservationGuard().evaluate(
        before, after, grade(1 + skin_before * 2.5, 0.0, SKIN_CFG, {}), grade(1 + skin_after * 2.5, 0.0, SKIN_CFG, {}),
        CORRECTION["guard"])


def test_guard_accepts_when_only_skin_changes():
    verdict = guard(analysis([face(0.68)]), analysis([face(0.67)]))
    assert verdict.accepted and verdict.deltas["face_identity_before"] == 0.68
    assert verdict.deltas["pose_distance"] == pytest.approx(0.0)


@pytest.mark.parametrize("after, skin_after, expected", [
    (analysis([face(0.60)]), 0.6, "identidade caiu"),
    (analysis([face(0.68, age=19)]), 0.6, "idade mudou"),
    (analysis([face(0.68)], [body(WALKING)]), 0.6, "pose mudou"),
    (analysis([face(0.68), face(0.1, bbox=(600.0, 80.0, 680.0, 180.0))]), 0.6, "numero de rostos"),
    (analysis([]), 0.6, "rosto nao encontrado"),
    (analysis([face(0.68)]), 0.2, "nao melhorou"),
])
def test_guard_rejects_corrections_that_change_the_person(after, skin_after, expected):
    verdict = guard(analysis([face(0.68)]), after, skin_after=skin_after)
    assert not verdict.accepted and any(expected in r for r in verdict.reasons)


# --- validadores --------------------------------------------------------------


def context(engine_dir, skin, faces=None):
    sheet = PersonaSheetRepository(engine_dir).get("luna")
    return ValidationContext(sheet=sheet, image=ProviderImage("fake", "x", ""),
                             analysis=analysis(faces or [face(0.68)]), skin=skin)


async def test_skin_validator_warns_without_blocking(engine_dir):
    engine = ValidationEngine([SkinRealismValidator()])
    report = await engine.run(context(engine_dir, grade(2.0, 0.0, SKIN_CFG, {})))
    assert report.accepted and report.warnings == ["skin_realism"] and report.checks["skin_realism"].status == WARN
    missing = await engine.run(context(engine_dir, None))
    assert missing.checks["skin_realism"].status == UNKNOWN and missing.unverified == []  # nao bloqueante


async def test_skin_validator_can_block_when_sheet_says_so(engine_dir):
    configure(engine_dir, skin={"blocking": True})
    report = await ValidationEngine([SkinRealismValidator()]).run(context(engine_dir, grade(1.2, 0.0, SKIN_CFG, {})))
    assert not report.accepted and report.failures == ["skin_realism_low"]


async def test_age_consistency(engine_dir):
    info = await AgeValidator().check(context(engine_dir, None, [face(0.68, age=22)]))
    assert info.status == "INFORMATIONAL" and info.evidence["consistency"] == pytest.approx(0.5)
    configure(engine_dir, age_check=True)
    young = await AgeValidator().check(context(engine_dir, None, [face(0.68, age=20)]))
    assert young.status == WARN and young.blocking is False


# --- Face Lock: texto de textura e idade vindo da ficha -------------------------


def test_face_guidance_comes_from_the_sheet(engine_dir):
    assert face_guidance(PersonaSheetRepository(engine_dir).get("luna")) is None  # ficha real: tudo desligado
    configure(engine_dir, texture={"enabled": True}, age_lock={"enabled": True})
    g = face_guidance(PersonaSheetRepository(engine_dir).get("luna"))
    assert "microtexture" in g.positive[0] and "27 years old" in g.positive[-1]
    assert "plastic skin" in g.stage_negative


# --- orquestrador ---------------------------------------------------------------


async def test_v1_regression_without_skin_analyzer(engine_dir):
    orch, scene, face_adapter = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD)))
    job = await orch.run(orch.prepare(req())["id"])
    result = job["results"][0]
    assert job["status"] == ACCEPTED and result["skin"] is None
    assert face_adapter.guidance == [None]  # chamada identica a V1
    assert [s["stage"] for s in result["stages"]] == ["scene", "face_lock"]
    assert result["image_url"] == "http://fake/face1.png" and result["face_lock_image_url"] is None
    assert result["validation"]["checks"]["skin_realism"]["status"] == UNKNOWN
    assert job["model_versions"]["skin_correction"] is None


async def test_skin_measured_but_not_corrected_when_disabled(engine_dir):
    skin = ScriptedSkin({"face": 0.2})
    fix = FakeSkinCorrection()
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD)), skin_adapter=fix, skin_analyzer=skin)
    job = await orch.run(orch.prepare(req())["id"])
    info = job["results"][0]["skin"]
    assert job["status"] == ACCEPTED and fix.calls == []
    assert info["skin_realism_score"] == pytest.approx(0.2) and info["skin_realism_status"] == FAIL
    assert info["skin_correction_applied"] is False and info["skin_correction_attempts"] == 0
    assert info["face_identity_before"] == 0.68 and info["age_score"] == pytest.approx(1.0)
    assert job["results"][0]["validation"]["warnings"] == ["skin_realism"]  # FAIL nao bloqueante = aviso


async def test_correction_kept_when_guard_approves(engine_dir):
    configure(engine_dir, correction={"enabled": True})
    fix = FakeSkinCorrection()
    orch, scene, _ = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD, analysis([face(0.675)]))),
                           skin_adapter=fix, skin_analyzer=ScriptedSkin({"face": 0.2, "skin": 0.65}))
    job = await orch.run(orch.prepare(req())["id"])
    result = job["results"][0]
    info = result["skin"]
    assert job["status"] == ACCEPTED
    locator, request = fix.calls[0]
    assert locator == "face1<scene1" and request.denoise == CORRECTION["denoise"] and request.seed == 44
    assert request.face_bbox == (380.0, 60.0, 460.0, 170.0)
    assert result["image_url"] == "http://fake/skin1.png" and result["face_lock_image_url"] == "http://fake/face1.png"
    assert info["skin_correction_applied"] and info["skin_correction_attempts"] == 1
    assert info["skin_correction_reason"] == SKIN_REALISM_LOW
    assert info["skin_realism_before_correction"] == pytest.approx(0.2) and info["skin_realism_score"] == pytest.approx(0.65)
    assert (info["face_identity_before"], info["face_identity_after"]) == (0.68, 0.675)
    assert info["skin_correction_duration"] == 9.0
    assert info["skin_correction_cost"] == pytest.approx(0.57 * 9 / 3600, abs=1e-5)
    assert [s["stage"] for s in result["stages"]] == ["scene", "face_lock", "skin_correction"]
    assert result["metrics"]["total_seconds"] == pytest.approx(13 + 82 + 9, abs=0.5)
    assert result["face_score"] == 0.675  # validou a imagem corrigida
    assert job["model_versions"]["skin_correction"]["lora"] == "nenhuma"
    assert scene.calls[0].lora["strength"] == 1.0


async def test_correction_discarded_when_identity_drops(engine_dir):
    configure(engine_dir, correction={"enabled": True})
    fix = FakeSkinCorrection()
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD, analysis([face(0.58)]))),
                       skin_adapter=fix, skin_analyzer=ScriptedSkin({"face": 0.2, "skin": 0.8}))
    job = await orch.run(orch.prepare(req())["id"])
    result = job["results"][0]
    info = result["skin"]
    assert len(fix.calls) == 1 and info["skin_correction_applied"] is False
    assert result["image_url"] == "http://fake/face1.png"
    assert any("identidade caiu" in r for r in info["guard"][0]["reasons"])
    assert info["skin_realism_score"] == pytest.approx(0.2) and info["face_identity_after"] == 0.58
    # a tentativa descartada tambem custa GPU: entra no tempo e no custo
    assert "skin_correction" in result["metrics"]["stage_seconds"]


async def test_unknown_skin_is_never_corrected(engine_dir):
    configure(engine_dir, correction={"enabled": True})
    fix = FakeSkinCorrection()
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD)), skin_adapter=fix,
                       skin_analyzer=ScriptedSkin({"face": None}))
    job = await orch.run(orch.prepare(req())["id"])
    info = job["results"][0]["skin"]
    assert fix.calls == [] and info["skin_realism_status"] == UNKNOWN and "UNKNOWN" in info["skin_correction_note"]


async def test_correction_enabled_without_adapter_is_an_error(engine_dir):
    configure(engine_dir, correction={"enabled": True})
    orch, scene, _ = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD)), skin_analyzer=ScriptedSkin({"face": 0.2}))
    job = await orch.run(orch.prepare(req())["id"])
    assert job["status"] == ERROR and "correcao de pele" in job["error"] and scene.calls == []


async def test_correction_failure_keeps_face_lock_and_says_so(engine_dir):
    configure(engine_dir, correction={"enabled": True})
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD)), skin_adapter=FakeSkinCorrection(fail=True),
                       skin_analyzer=ScriptedSkin({"face": 0.2}))
    job = await orch.run(orch.prepare(req())["id"])
    info = job["results"][0]["skin"]
    assert job["status"] == ACCEPTED and job["results"][0]["image_url"] == "http://fake/face1.png"
    assert "falhou" in info["skin_correction_note"] and info["skin_correction_attempts"] == 0


async def test_retry_does_not_touch_lora_or_qwen_and_measures_each_attempt(engine_dir):
    configure(engine_dir, correction={"enabled": True}, texture={"enabled": True})
    fix = FakeSkinCorrection()
    skin = ScriptedSkin({"face": 0.2, "skin": 0.6})

    def pick(locator, n):
        if locator.startswith("skin"):
            return analysis([face(0.42 if "face1" in locator else 0.67)])
        return LOW_FACE if locator.startswith("face1") else GOOD
    scene = FakeScene()
    face_adapter = FakeFace()
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(pick), scene=scene, face_adapter=face_adapter,
                       skin_adapter=fix, skin_analyzer=skin)
    job = await orch.run(orch.prepare(req(max_attempts=2))["id"])
    assert job["status"] == ACCEPTED and job["attempt"] == 2
    assert [r["skin"]["skin_correction_attempts"] for r in job["results"]] == [1, 1]
    assert all(c.lora["strength"] == 1.0 for c in scene.calls)
    # o texto de textura e o mesmo em todas as tentativas (nada "mais forte" no retry)
    assert face_adapter.guidance[0] == face_adapter.guidance[1] and isinstance(face_adapter.guidance[0], FaceLockGuidance)


async def test_batch_mode_records_skin(engine_dir):
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(by_locator(GOOD)), skin_analyzer=ScriptedSkin({"face": 0.75}))
    ids = [orch.prepare(req(generation_parameters=GenerationParameters(seed=s)))["id"] for s in (1, 2)]
    jobs = await orch.run_batch(ids)
    assert [j["results"][0]["skin"]["skin_realism_grade"] for j in jobs] == ["GOOD", "GOOD"]


# --- adapters do ComfyUI -----------------------------------------------------------


async def test_qwen_guidance_only_extends_the_prompt():
    from tests.test_adapters_comfy import FakeComfy, provider
    comfy = FakeComfy()
    p = provider(comfy)
    master = ReferenceImage("f7e8", "references/f7e8.png", b"face", "a" * 64)
    image = ProviderImage("comfyui", "base.png [output]", "u", 832, 1216)
    plain = await p.face.lock_face(image, master, 9)
    guided = await p.face.lock_face(image, master, 9, guidance=FaceLockGuidance(["natural skin texture."], ["plastic skin"]))
    g1, g2 = comfy.graphs
    assert g2["20"]["inputs"]["prompt"] == g1["20"]["inputs"]["prompt"] + " natural skin texture."
    g2["20"]["inputs"]["prompt"] = g1["20"]["inputs"]["prompt"]
    assert g1 == g2  # resto do grafo identico (BFS, Lightning, CFG, semente)
    assert plain.effective_parameters["texture_guidance"] is None
    assert guided.effective_parameters["stage_negative_applied"] is False


async def test_texture_adapter_crops_face_and_uses_no_lora():
    from tests.test_adapters_comfy import FakeComfy, provider
    comfy = FakeComfy()
    out = await provider(comfy).skin.correct(
        ProviderImage("comfyui", "face.png [output]", "u", 832, 1216),
        SkinCorrectionRequest(face_bbox=(380, 60, 460, 170), prompt="natural skin", denoise=0.25, seed=5))
    g = comfy.graphs[0]
    assert not [n for n in g.values() if "Lora" in n["class_type"]]
    sampler = next(n for n in g.values() if n["class_type"] == "KSampler")["inputs"]
    assert (sampler["denoise"], sampler["seed"], sampler["cfg"], sampler["steps"]) == (0.25, 5, 1.0, 8)
    crop = next(n for n in g.values() if n["class_type"] == "ImageCrop")["inputs"]
    assert (crop["x"], crop["y"], crop["width"], crop["height"]) == (360, 32, 120, 165)
    assert out.stage == "skin_correction" and out.effective_parameters["lora"] is None
