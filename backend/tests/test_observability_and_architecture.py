import json
import logging
import re
from pathlib import Path

import pytest

from app.core.generation.request import GenerationRequest
from app.core.observability import log_event
from tests.fakes import GOOD, LOW_FACE, ScriptedAnalyzer, build, sequence

CORE = Path(__file__).resolve().parents[1] / "app" / "core"


def events(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "luna.engine"]


async def test_structured_events(engine_dir, caplog):
    caplog.set_level(logging.INFO, logger="luna.engine")
    orch, _, _ = build(engine_dir, ScriptedAnalyzer(sequence(LOW_FACE, GOOD)))
    job = await orch.run(orch.prepare(GenerationRequest(persona_id="luna", scene_prompt="cafe"))["id"])
    names = [e["event"] for e in events(caplog)]
    assert names == [
        "scene_started", "scene_completed", "face_lock_started", "face_lock_completed",
        "validation_started", "validation_completed", "generation_rejected",
        "regeneration_started", "face_lock_started", "face_lock_completed",
        "validation_started", "validation_completed", "generation_accepted",
    ]
    assert all(e.get("job_id") == job["id"] for e in events(caplog))


def test_secrets_are_masked(caplog):
    caplog.set_level(logging.INFO, logger="luna.engine")
    record = log_event("scene_started", job_id="x", api_key="abc", nested={"X-Luna-Token": "t", "ok": 1})
    assert record["api_key"] == "***" and record["nested"] == {"X-Luna-Token": "***", "ok": 1}
    assert "abc" not in caplog.text


def test_unknown_event_rejected():
    with pytest.raises(ValueError):
        log_event("algo_qualquer")


# O nucleo nao pode depender de modelo, de ComfyUI nem de RunPod: so das interfaces.
FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+app\.(?:clients|services|providers\.(?!base\b)|validation_backends|routes|infrastructure)"
    r"|comfyui|runpod|flux|sdxl|midjourney|gemini|gpt.?image|chroma",
    re.IGNORECASE | re.MULTILINE,
)


@pytest.mark.parametrize("path", sorted(CORE.rglob("*.py")), ids=lambda p: str(p.relative_to(CORE)))
def test_core_is_provider_agnostic(path):
    imports = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                        if line.lstrip().startswith(("from ", "import ")))
    assert not FORBIDDEN.search(imports), f"{path.name} importa um provider/modelo/infraestrutura"
