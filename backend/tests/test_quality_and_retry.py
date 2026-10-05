"""QualityGate, FailureAnalyzer e RetryManager, e o fluxo inteiro
Persona -> Geracao -> Validacao com o validador de verdade (so o modelo e o
detector de rosto sao falsos)."""
import time

import pytest

from app.core.generation.history import ACCEPTED, FAILED
from app.core.generation.request import MAX_ATTEMPTS_LIMIT, GenerationRequest
from app.core.generation.retry import FACE_VISIBLE, RetryManager
from app.core.persona import PersonaProfile, PersonaRepository
from app.core.validation.config import IdentityValidationConfig, ThresholdPolicy
from app.core.validation.failure import FailureAnalyzer
from app.core.validation.gate import QualityGate
from app.core.validation.types import ACCEPT, REJECT, Failure
from app.core.validation.validator import IdentityValidator
from app.providers.base import GenerationParameters, ProviderCapabilities
from tests.fakes import FakeAdapter, build_orchestrator, result_with
from tests.helpers import client, engine_app
from tests.test_identity_validator import KPS, face

CONFIG = IdentityValidationConfig()
CAPS = ProviderCapabilities(name="fake", title="Fake", supports_face_restore=True)


# --- QualityGate ---------------------------------------------------------


def test_gate_accepts_at_or_above_threshold():
    gate = QualityGate(CONFIG)
    assert gate.decide(result_with(0.95), 0.9).status == ACCEPT
    # exatamente no limiar: ACEITA (definido e documentado no gate)
    assert gate.decide(result_with(0.9), 0.9).status == ACCEPT
    assert gate.decide(result_with(0.9 - 1e-9), 0.9).status == ACCEPT  # ruido de ponto flutuante
    assert gate.decide(result_with(0.8999), 0.9).status == REJECT


def test_gate_rejects_hard_failures_even_with_high_score():
    result = result_with(0.99)
    result.hard_failures = ["sex_mismatch"]
    decided = QualityGate(CONFIG).decide(result, 0.5)
    assert decided.status == REJECT and "sexo" in decided.reasons[0]


def test_gate_rejects_low_coverage_and_weak_face():
    gate = QualityGate(CONFIG)
    low = result_with(0.95)
    low.coverage = 0.3
    assert gate.decide(low, 0.9).status == REJECT
    weak_face = result_with(0.95, face_similarity=0.4)
    assert any("Rosto pouco parecido" in r for r in gate.decide(weak_face, 0.9).reasons)


def test_gate_rejects_unmeasured_identity():
    assert QualityGate(CONFIG).decide(result_with(None), 0.9).status == REJECT


# --- FailureAnalyzer -----------------------------------------------------


async def measured(generated, reference=None, persona=None):
    from tests.test_identity_validator import FakeAnalyzer, REF, IMG
    p = persona or PersonaProfile(id="luna", name="Luna")
    p.identity.sex = p.identity.sex or "F"
    validator = IdentityValidator(FakeAnalyzer(reference or face(), generated), CONFIG)
    return await validator.validate([REF], IMG, p)


async def test_analyzer_finds_face_and_part_mismatch():
    wide_mouth = [*KPS[:3], (95, 210), (205, 210)]
    result = await measured([face(sim=0.30, kps=wide_mouth)])
    kinds = [f.failure_type for f in FailureAnalyzer(CONFIG).analyze(result, 0.9)]
    assert kinds[0] == "facial_structure_mismatch"
    assert "mouth_mismatch" in kinds


async def test_analyzer_age_and_no_face():
    persona = PersonaProfile(id="luna", name="Luna")
    persona.identity.apparent_age = 25
    result = await measured([face(sim=0.6, age=50)], persona=persona)
    assert "age_mismatch" in [f.failure_type for f in FailureAnalyzer(CONFIG).analyze(result, 0.99)]
    no_face = await measured([])
    assert [f.failure_type for f in FailureAnalyzer(CONFIG).analyze(no_face, 0.9)] == ["face_not_found"]


# --- RetryManager --------------------------------------------------------


def luna(personas_dir) -> PersonaProfile:
    return PersonaRepository(personas_dir).get("luna")


def test_retry_raises_identity_and_changes_seed(personas_dir):
    params = GenerationParameters(seed=10, identity_strength=0.5)
    plan = RetryManager().plan(1, params, [], result_with(0.74),
                               [Failure("facial_structure_mismatch", "high", "x")], CAPS, luna(personas_dir))
    assert plan.parameters.identity_strength == 0.65
    assert plan.parameters.seed != 10
    assert "oval face with full cheeks" in plan.emphasis
    assert plan.changes["identity_strength"] == [0.5, 0.65]
    assert not plan.parameters.face_restore  # so a partir da 2a reprovacao


def test_retry_turns_on_face_restore_later(personas_dir):
    params = GenerationParameters(identity_strength=0.65)
    plan = RetryManager().plan(2, params, [], result_with(0.8),
                               [Failure("eye_mismatch", "medium", "x")], CAPS, luna(personas_dir))
    assert plan.parameters.face_restore and plan.changes["face_restore"] == [False, True]
    assert "dark brown eyes" in plan.emphasis


def test_retry_never_returns_identical_request(personas_dir):
    params = GenerationParameters(seed=5, identity_strength=1.0, face_restore=True)
    plan = RetryManager().plan(3, params, [], result_with(0.8),
                               [Failure("low_identity_confidence", "low", "x")], CAPS, luna(personas_dir))
    assert plan.parameters != params


def test_retry_for_missing_face_asks_for_visible_face(personas_dir):
    plan = RetryManager().plan(1, GenerationParameters(), [], result_with(None),
                               [Failure("face_not_found", "high", "x")], CAPS, luna(personas_dir))
    assert FACE_VISIBLE in plan.emphasis
    assert plan.parameters.identity_strength == 0.5


# --- Integracao: Persona -> Geracao -> Validacao -------------------------


class ImageFaces:
    """Detector falso: os rostos de cada imagem gerada (img1, img2...)."""

    def __init__(self, by_image):
        self.by_image = by_image

    async def check_ready(self):
        return []

    async def reference_face(self, reference):
        return face()

    async def generated_faces(self, image, reference):
        return self.by_image.get(image.locator, self.by_image.get("*", []))


def real_pipeline(personas_dir, by_image, adapter=None, threshold=0.9):
    config = IdentityValidationConfig(default_threshold=threshold)
    return build_orchestrator(
        personas_dir, adapter=adapter or FakeAdapter(),
        validator=IdentityValidator(ImageFaces(by_image), config),
        gate=QualityGate(config), analyzer=FailureAnalyzer(config), retry=RetryManager(),
        thresholds=ThresholdPolicy(config),
    )


GOOD = [face(sim=0.62, age=27)]
OTHER_PERSON = [face(sim=0.05, age=45, kps=[*KPS[:2], (150, 185), (100, 235), (200, 235)])]


async def test_correct_identity_is_accepted(personas_dir):
    job = await real_pipeline(personas_dir, {"*": GOOD}).submit(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    assert job["status"] == ACCEPTED and job["attempt"] == 1
    result = job["results"][0]
    assert result["status"] == ACCEPT and result["identity_score"] >= 0.9
    assert result["validation"]["metrics"]["hair"]["measured"] is False


async def test_different_identity_rejected_then_regenerated(personas_dir):
    adapter = FakeAdapter()
    orch = real_pipeline(personas_dir, {"img1": OTHER_PERSON, "img2": GOOD}, adapter=adapter)
    job = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    assert job["status"] == ACCEPTED and job["attempt"] == 2
    assert job["results"][0]["status"] == REJECT
    assert job["failures"][0]["failure_type"] == "facial_structure_mismatch"
    assert job["failures"][0]["result_id"] == job["results"][0]["id"]
    retry = job["retries"][0]
    assert retry["attempt_number"] == 2 and retry["failure_reason"] == "facial_structure_mismatch"
    assert retry["previous_score"] == job["results"][0]["identity_score"]
    # a 2a tentativa NAO e o mesmo pedido
    assert adapter.calls[1].parameters.identity_strength > adapter.calls[0].parameters.identity_strength


async def test_all_attempts_failing_is_failed_and_respects_max_attempts(personas_dir):
    adapter = FakeAdapter()
    orch = real_pipeline(personas_dir, {"*": OTHER_PERSON}, adapter=adapter)
    job = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe", max_attempts=3))
    assert job["status"] == FAILED
    assert len(adapter.calls) == 3 and len(job["results"]) == 3
    assert len(job["retries"]) == 2  # nao planeja depois da ultima
    assert job["best_result_id"] in {r["id"] for r in job["results"]}


async def test_threshold_from_job_wins(personas_dir):
    orch = real_pipeline(personas_dir, {"*": [face(sim=0.42, age=27)]}, threshold=0.99)
    req = GenerationRequest(persona_id="luna", scene_prompt="cafe", validation_threshold=0.5, max_attempts=1)
    job = await orch.submit(req)
    assert job["threshold"] == 0.5 and job["status"] == ACCEPTED


def _wait(api, job_id):
    for _ in range(200):
        job = api.get(f"/api/engine/generation/{job_id}").json()
        if job["status"] != "RUNNING":
            return job
        time.sleep(0.01)
    raise AssertionError("o job nao terminou")


def test_api_end_to_end(personas_dir):
    orch = real_pipeline(personas_dir, {"img1": OTHER_PERSON, "img2": GOOD})
    app = engine_app(personas_dir, generation_orchestrator=orch, generation_history=orch.history,
                     provider_registry=orch.providers)
    with client(app) as api:
        started = api.post("/api/engine/generation", json={"persona_id": "luna", "scene_prompt": "Luna num cafe"})
        assert started.status_code == 202 and started.json()["status"] == "RUNNING"
        job = _wait(api, started.json()["id"])
        assert job["status"] == ACCEPTED and job["best_result"]["status"] == ACCEPT
        results = api.get(f"/api/engine/generation/{job['id']}/results").json()
        assert len(results["results"]) == 2 and results["failures"]
        history = api.get("/api/engine/personas/luna/generations").json()["generations"]
        assert history[0]["id"] == job["id"]
        again = api.post(f"/api/engine/generation/{job['id']}/retry", json={"max_attempts": 1})
        assert again.status_code == 202 and again.json()["retry_of"] == job["id"]
        _wait(api, again.json()["id"])
        assert api.get("/api/engine/providers").json()["default"] == "fake"


def test_api_controlled_errors(personas_dir):
    orch = real_pipeline(personas_dir, {"*": GOOD})
    app = engine_app(personas_dir, generation_orchestrator=orch, generation_history=orch.history,
                     provider_registry=orch.providers)
    with client(app) as api:
        body = {"persona_id": "luna", "scene_prompt": "cafe"}
        assert api.post("/api/engine/generation", json={**body, "provider": "midjourney"}).status_code == 400
        assert api.post("/api/engine/generation", json={**body, "persona_id": "ninguem"}).status_code == 404
        too_many = api.post("/api/engine/generation", json={**body, "max_attempts": MAX_ATTEMPTS_LIMIT + 1})
        assert too_many.status_code == 422
        assert api.get("/api/engine/generation/00000000-0000-0000-0000-000000000000").status_code == 404
    with client(app, authorized=False) as anon:
        assert anon.post("/api/engine/generation", json=body).status_code == 401
        assert anon.get("/api/engine/personas/luna/generations").status_code == 401
