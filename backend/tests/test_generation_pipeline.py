import pytest

from app.core.generation.history import ACCEPTED, ERROR, FAILED, GenerationHistory, JobNotFoundError
from app.core.generation.prompt_builder import DEFAULT_CLOTHES, PromptBuilder
from app.core.generation.request import GenerationRequest, InvalidGenerationRequestError
from app.core.persona import PersonaRepository
from app.persona_manager.manager import PersonaNotFoundError
from app.providers.base import GenerationParameters, UnknownProviderError
from tests.fakes import FakeAdapter, ScriptedValidator, build_orchestrator


# --- PromptBuilder -------------------------------------------------------


def luna(personas_dir):
    return PersonaRepository(personas_dir).get("luna")


def test_prompt_sections_in_order(personas_dir):
    s = PromptBuilder().build(luna(personas_dir), "walking in a cafe in Sao Paulo")
    text = s.text
    order = [text.index(f"[{n}]") for n in ("IDENTITY", "APPEARANCE", "SCENE", "CONSTRAINTS")]
    assert order == sorted(order)
    assert "dark brown eyes" in s.identity_traits
    assert s.scene == "walking in a cafe in Sao Paulo"
    assert "Do not change identity." in s.constraints


def test_scene_never_mixed_into_identity(personas_dir):
    s = PromptBuilder().build(luna(personas_dir), "at the beach")
    assert "beach" not in s.identity_traits and "beach" not in s.appearance


def test_default_expression_dropped_when_scene_has_one(personas_dir):
    persona = luna(personas_dir)
    assert "half smile" in PromptBuilder().build(persona, "in a park").appearance
    assert "half smile" not in PromptBuilder().build(persona, "laughing in a park").appearance


def test_default_clothes_only_without_clothes_in_scene(personas_dir):
    persona = luna(personas_dir)
    assert DEFAULT_CLOTHES in PromptBuilder().build(persona, "in a kiosk").appearance
    assert DEFAULT_CLOTHES not in PromptBuilder().build(persona, "wearing a red dress in a kiosk").appearance


def test_style_overrides_win_and_emphasis_is_deduplicated(personas_dir):
    persona = luna(personas_dir)
    persona.style.values["iluminacao"] = "soft light"
    s = PromptBuilder().build(persona, "x", {"iluminacao": "golden hour"}, ["apparent age 26", "apparent age 26"])
    assert s.style == "golden hour"
    assert s.emphasis == ["apparent age 26"]


# --- GenerationRequest ---------------------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"scene_prompt": " "}, {"max_attempts": 0}, {"max_attempts": 99},
    {"validation_threshold": 1.2}, {"style_overrides": {"cor": "x"}},
])
def test_request_validation(kwargs):
    req = GenerationRequest(**{"persona_id": "luna", "scene_prompt": "cafe", **kwargs})
    with pytest.raises(InvalidGenerationRequestError):
        req.validate()


# --- Orquestrador --------------------------------------------------------


async def test_accepts_first_good_image(personas_dir):
    orch = build_orchestrator(personas_dir, validator=ScriptedValidator([0.95]))
    job = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    assert job["status"] == ACCEPTED and job["attempt"] == 1
    assert job["accepted_result_id"] == job["results"][0]["id"]
    assert job["prompt"]["scene"] == "cafe"
    # o historico ficou gravado e da para ler de novo
    assert GenerationHistory(personas_dir).get(job["id"])["status"] == ACCEPTED


async def test_persona_references_reach_the_model(personas_dir):
    adapter = FakeAdapter()
    orch = build_orchestrator(personas_dir, adapter=adapter)
    await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    sent = adapter.calls[0]
    assert sent.references and sent.references[0].type == "PRIMARY"
    assert sent.identity_assets["lora"]["trigger"] == "lunavox"


async def test_unknown_persona_and_provider_are_controlled(personas_dir):
    orch = build_orchestrator(personas_dir)
    with pytest.raises(PersonaNotFoundError):
        orch.prepare(GenerationRequest(persona_id="ninguem", scene_prompt="cafe"))
    with pytest.raises(UnknownProviderError):
        orch.prepare(GenerationRequest(persona_id="luna", scene_prompt="cafe", provider="midjourney"))


async def test_persona_without_reference_errors_without_generating(personas_dir):
    adapter = FakeAdapter()
    orch = build_orchestrator(personas_dir, adapter=adapter, with_reference=False)
    job = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    assert job["status"] == ERROR and "foto de rosto" in job["error"]
    assert adapter.calls == []


async def test_provider_offline_is_error(personas_dir):
    orch = build_orchestrator(personas_dir, adapter=FakeAdapter(problems=["ComfyUI fora do ar"]))
    job = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    assert job["status"] == ERROR and "fora do ar" in job["error"]


async def test_provider_error_counts_as_attempt_and_retries(personas_dir):
    orch = build_orchestrator(personas_dir, adapter=FakeAdapter(fail_on={1}), validator=ScriptedValidator([0.95]))
    job = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe", max_attempts=2))
    assert job["status"] == ACCEPTED and job["attempt"] == 2
    assert job["failures"][0]["failure_type"] == "provider_error"
    assert job["results"][0]["status"] == "ERROR"


async def test_retry_endpoint_creates_linked_job(personas_dir):
    orch = build_orchestrator(personas_dir, validator=ScriptedValidator([0.1]))
    first = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe", max_attempts=1))
    again = orch.prepare_retry(first["id"])
    assert again["retry_of"] == first["id"] and again["scene_prompt"] == "cafe"
    assert again["parameters"]["seed"] is None


def test_unknown_job_is_not_found(personas_dir):
    with pytest.raises(JobNotFoundError):
        GenerationHistory(personas_dir).get("../../etc/passwd")


async def test_interrupted_jobs_marked_on_restart(personas_dir):
    orch = build_orchestrator(personas_dir)
    job = orch.prepare(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    assert GenerationHistory(personas_dir).mark_interrupted() == 1
    assert GenerationHistory(personas_dir).get(job["id"])["status"] == ERROR
