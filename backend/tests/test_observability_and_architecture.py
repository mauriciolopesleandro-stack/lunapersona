import json
import logging
import re
from pathlib import Path

import pytest

from app.core.generation.request import GenerationRequest
from app.core.observability import log_event
from tests.test_quality_and_retry import GOOD, OTHER_PERSON, real_pipeline

CORE = Path(__file__).resolve().parents[1] / "app" / "core"


def events(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "luna.engine"]


async def test_structured_events_for_reject_then_accept(personas_dir, caplog):
    caplog.set_level(logging.INFO, logger="luna.engine")
    orch = real_pipeline(personas_dir, {"img1": OTHER_PERSON, "img2": GOOD})
    job = await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe"))
    names = [e["event"] for e in events(caplog)]
    assert names == [
        "generation_started", "generation_completed", "identity_validation_started", "identity_validation_completed",
        "generation_rejected",
        "regeneration_started",
        "generation_started", "generation_completed", "identity_validation_started", "identity_validation_completed",
        "generation_accepted",
    ]
    assert all(e.get("job_id") == job["id"] for e in events(caplog))


async def test_failed_event(personas_dir, caplog):
    caplog.set_level(logging.INFO, logger="luna.engine")
    orch = real_pipeline(personas_dir, {"*": OTHER_PERSON})
    await orch.submit(GenerationRequest(persona_id="luna", scene_prompt="cafe", max_attempts=2))
    assert [e["event"] for e in events(caplog)][-1] == "generation_failed"


def test_secrets_are_masked(caplog):
    caplog.set_level(logging.INFO, logger="luna.engine")
    record = log_event("generation_started", job_id="x", api_key="abc", nested={"X-Luna-Token": "t", "ok": 1})
    assert record["api_key"] == "***" and record["nested"] == {"X-Luna-Token": "***", "ok": 1}
    assert "abc" not in caplog.text


def test_unknown_event_rejected():
    with pytest.raises(ValueError):
        log_event("algo_qualquer")


# O nucleo nao pode depender de modelo nenhum: so da interface ModelAdapter.
FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+app\.(?:clients|services|providers\.(?!base\b)|validation_backends|routes)"
    r"|comfyui|flux|sdxl|midjourney|gemini|gpt.?image",
    re.IGNORECASE | re.MULTILINE,
)


@pytest.mark.parametrize("path", sorted(CORE.rglob("*.py")), ids=lambda p: str(p.relative_to(CORE)))
def test_core_is_provider_agnostic(path):
    code = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
    imports = "\n".join(line for line in code.splitlines() if line.lstrip().startswith(("from ", "import ")))
    assert not FORBIDDEN.search(imports), f"{path.name} importa um provider/modelo"
