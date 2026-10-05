"""Integracao: Persona Sheet -> cena (Z-Image fake) -> Face Lock (Qwen fake) -> Validation Engine."""
import time

import pytest

from app.core.generation.history import ACCEPTED, ERROR, FAILED, GenerationHistory
from app.core.generation.request import MAX_ATTEMPTS_LIMIT, GenerationRequest, InvalidGenerationRequestError
from app.core.generation.retry_policy import RELOCK_FACE, REGENERATE_SCENE
from app.core.persona.sheet import PersonaSheetMissingError, PersonaSheetRepository
from app.core.telemetry import BATCH_MODE, SINGLE_REQUEST_MODE
from app.persona_manager.manager import PersonaNotFoundError
from app.providers.base import UnknownProviderError
from tests.fakes import DUPLICATE, GOOD, LOW_FACE, WITH_EXTRA, FakeFace, FakeScene, ScriptedAnalyzer, build, sequence
from tests.helpers import client, engine_app


def req(**kw) -> GenerationRequest:
    return GenerationRequest(**{"persona_id": "luna", "scene_prompt": "walking in a cafe", **kw})


async def test_free_mode_accepts_and_records_audit(engine_dir):
    orch, scene, face = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)))
    job = await orch.run(orch.prepare(req(generation_parameters=__import__("app.providers.base", fromlist=["x"]).GenerationParameters(seed=42)))["id"])
    assert job["status"] == ACCEPTED and job["attempt"] == 1
    # Face Lock recebeu a imagem da cena e a master_face (bytes conferidos)
    locator, master, seed = face.calls[0]
    assert locator == "scene1" and master.content == b"face-master" and seed == 43
    assert scene.calls[0].lora == {"file": "luna_zimage_v1.safetensors", "trigger": "lunavox", "strength": 1.0}
    assert scene.calls[0].pose is None
    result = job["results"][0]
    assert result["validation"]["status"] == "PASS_WITH_UNKNOWN" and result["validation"]["unverified"] == ["anatomy"]
    assert result["seeds"] == {"scene": 42, "face_lock": 43}
    # auditoria para reproduzir
    sheet = PersonaSheetRepository(engine_dir).get("luna")
    assert job["persona_version"] == "1.0" and job["pipeline_version"] == "luna-v1.0"
    assert job["master_reference_versions"] == sheet.master_reference_versions
    assert job["prompt_version"] == "prompt-v1.0" and job["negative_version"].startswith("negative-v1.0")
    assert job["model_versions"]["scene"]["lora"] == "luna_zimage_v1.safetensors"
    assert result["prompt"]["text"].startswith("lunavox, a woman")
    # telemetria e custo
    m = result["metrics"]
    assert m["execution_mode"] == SINGLE_REQUEST_MODE and m["total_seconds"] == pytest.approx(95.0)
    assert m["estimated_cost_usd"] == pytest.approx(0.57 * 95 / 3600, abs=1e-5)
    assert m["gpu"] == "Fake GPU" and m["vram_used_mb_max"] == 23700


async def test_face_failure_relocks_face_then_regenerates(engine_dir):
    orch, scene, face = build(engine_dir, ScriptedAnalyzer(sequence(LOW_FACE, LOW_FACE, GOOD)))
    job = await orch.run(orch.prepare(req(max_attempts=3))["id"])
    assert job["status"] == ACCEPTED and job["attempt"] == 3
    assert [r["strategy"] for r in job["retries"]] == [RELOCK_FACE, REGENERATE_SCENE]
    assert len(scene.calls) == 2 and len(face.calls) == 3
    assert face.calls[1][0] == "scene1"  # relock reaproveitou a cena
    # a tentativa de relock nao conta tempo de cena
    assert "scene" not in job["results"][1]["metrics"]["stage_seconds"]


async def test_duplicate_persona_regenerates_with_directive(engine_dir):
    orch, scene, _ = build(engine_dir, ScriptedAnalyzer(sequence(DUPLICATE, GOOD)))
    job = await orch.run(orch.prepare(req())["id"])
    assert job["status"] == ACCEPTED
    assert job["failures"][0]["failure_type"] == "persona_duplicated"
    assert "only one woman in the photo, no mirrors" in scene.calls[1].prompt.directives


async def test_other_people_in_scene_are_allowed(engine_dir):
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(sequence(WITH_EXTRA)))
    job = await orch.run(orch.prepare(req())["id"])
    assert job["status"] == ACCEPTED and job["attempt"] == 1


async def test_all_attempts_failing_is_failed_and_bounded(engine_dir):
    orch, scene, face = build(engine_dir, ScriptedAnalyzer(sequence(LOW_FACE)))
    job = await orch.run(orch.prepare(req(max_attempts=4))["id"])
    assert job["status"] == FAILED and len(job["results"]) == 4 and len(job["retries"]) == 3
    assert job["best_result_id"] in {r["id"] for r in job["results"]}
    with pytest.raises(InvalidGenerationRequestError):
        orch.prepare(req(max_attempts=MAX_ATTEMPTS_LIMIT + 1))


async def test_pose_mode_uses_pose_adapter_and_validates_pose(engine_dir):
    orch, scene, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)))
    job = await orch.run(orch.prepare(req(mode="POSE_CONTROLLED", pose_reference="pose.png"))["id"])
    assert job["status"] == ACCEPTED
    assert scene.calls[0].pose.source == "pose.png" and scene.calls[0].pose.strength == 0.8
    assert job["results"][0]["validation"]["checks"]["pose"]["status"] == "PASS"
    with pytest.raises(InvalidGenerationRequestError):
        orch.prepare(req(mode="POSE_CONTROLLED"))


async def test_no_lora_on_pod_is_error_not_fallback(engine_dir):
    orch, scene, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)),
                           scene=FakeScene(problems=["LoRA da persona nao esta no pod"]))
    job = await orch.run(orch.prepare(req())["id"])
    assert job["status"] == ERROR and "LoRA" in job["error"]
    assert scene.calls == []


async def test_changed_master_stops_generation(engine_dir):
    orch, scene, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)))
    job = orch.prepare(req())
    sheet = PersonaSheetRepository(engine_dir).get("luna")
    (engine_dir / "luna" / sheet.master("master_face").file).write_bytes(b"outra pessoa")
    done = await orch.run(job["id"])
    assert done["status"] == ERROR and "imutaveis" in done["error"] and scene.calls == []


async def test_controlled_errors(personas_dir, engine_dir):
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)))
    with pytest.raises(PersonaNotFoundError):
        orch.prepare(req(persona_id="ninguem"))
    with pytest.raises(UnknownProviderError):
        orch.prepare(req(provider="midjourney"))
    (engine_dir / "luna" / "persona_sheet.json").unlink()
    with pytest.raises(PersonaSheetMissingError):
        orch.prepare(req())


async def test_batch_runs_all_scenes_before_face_locks(engine_dir):
    scene, face = FakeScene(), FakeFace()
    order: list[str] = []
    scene.log = face.log = order
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD, GOOD, LOW_FACE, GOOD)), scene=scene, face_adapter=face)
    ids = [orch.prepare(req(scene_prompt=f"scene {i}"), batch_id="b1")["id"] for i in range(3)]
    jobs = await orch.run_batch(ids)
    assert order[:6] == ["scene1", "scene2", "scene3", "face1", "face2", "face3"]
    assert [j["status"] for j in jobs] == [ACCEPTED, ACCEPTED, ACCEPTED]
    assert jobs[0]["results"][0]["metrics"]["execution_mode"] == BATCH_MODE
    # o 3o falhou no rosto e seguiu sozinho (refez o Face Lock)
    assert jobs[2]["retries"][0]["strategy"] == RELOCK_FACE
    assert jobs[2]["results"][1]["metrics"]["execution_mode"] == SINGLE_REQUEST_MODE


async def test_cost_not_measured_without_price(engine_dir):
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)), price=False)
    job = await orch.run(orch.prepare(req())["id"])
    m = job["results"][0]["metrics"]
    assert m["estimated_cost_usd"] is None and m["price_source"] == "NOT MEASURED"


def _wait(api, job_id):
    for _ in range(200):
        job = api.get(f"/api/engine/generation/{job_id}").json()
        if job["status"] != "RUNNING":
            return job
        time.sleep(0.01)
    raise AssertionError("o job nao terminou")


def test_api_end_to_end(engine_dir):
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)))
    app = engine_app(engine_dir, generation_orchestrator=orch, generation_history=orch.history,
                     provider_registry=orch.providers, persona_sheets=orch.sheets)
    with client(app) as api:
        started = api.post("/api/engine/generation", json={"persona_id": "luna", "scene_prompt": "num cafe"})
        assert started.status_code == 202
        job = _wait(api, started.json()["id"])
        assert job["status"] == "ACCEPTED" and job["best_result"]["validation"]["status"] == "PASS_WITH_UNKNOWN"
        sheet = api.get("/api/engine/personas/luna/sheet").json()
        assert sheet["persona_version"] == "1.0" and "master_face" in sheet["masters"]
        assert "file" not in sheet["masters"]["master_face"]  # sem caminho interno
        batch = api.post("/api/engine/generation/batch", json={"persona_id": "luna", "scenes": ["a", "b"]})
        assert batch.status_code == 202 and len(batch.json()["jobs"]) == 2
        for j in batch.json()["jobs"]:
            assert _wait(api, j["id"])["execution_mode"] == "BATCH_MODE"
        assert api.post("/api/engine/generation", json={"persona_id": "luna", "scene_prompt": "x", "mode": "POSE_CONTROLLED"}).status_code == 400
        assert api.post("/api/engine/generation", json={"persona_id": "luna", "scene_prompt": "x", "max_attempts": 7}).status_code == 422
    with client(app, authorized=False) as anon:
        assert anon.post("/api/engine/generation", json={"persona_id": "luna", "scene_prompt": "x"}).status_code == 401


def test_history_survives_restart(engine_dir):
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(sequence(GOOD)))
    job = orch.prepare(req())
    assert GenerationHistory(engine_dir).mark_interrupted() == 1
    assert GenerationHistory(engine_dir).get(job["id"])["status"] == ERROR
