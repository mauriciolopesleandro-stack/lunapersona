"""Benchmark A/B/C/D do Persona Engine V1.1 (pele natural) no pod, com o codigo
da branch feature/persona-v1.1-natural-skin em /workspace/v11test.

A: V1 (Face Lock igual a producao). So mede a pele.
B: V1 + texto de preservacao de textura e trava de idade no Qwen.
C: imagens de A + analisador + correcao condicional (caminho real: _finish_attempt).
D: imagens de B + a mesma correcao.

Mesmas cenas, sementes, masters, resolucao, GPU e LoRA. max_attempts=1 (sem retry)
para comparar uma imagem por cena. C e D reaproveitam a cena e o Face Lock de A/B
(identicos por construcao): o tempo/custo deles = os de A/B + a correcao.
"""
import asyncio
import copy
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path("/workspace/v11test")
VOLUME_LUNA = Path("/workspace/lunapersona/personas/luna")
sys.path.insert(0, str(ROOT / "pylib"))  # pillow + numpy isolados (sem mexer no venv do pod)
sys.path.insert(0, str(ROOT / "backend"))

SCENES = [
    "full body photo, standing at the counter of a cozy coffee shop in Sao Paulo, holding a cup of coffee, wearing a white blouse and blue jeans, afternoon light",
    "medium shot, walking on the Copacabana boardwalk in Rio de Janeiro at sunset, wearing a light summer dress, golden hour light",
    "close-up portrait by an apartment window in Rio de Janeiro, soft overcast daylight, wearing a grey t-shirt, relaxed expression",
    "medium shot at a street market (feira livre) in Sao Paulo, holding a papaya, wearing a striped shirt, morning sun",
    "medium shot on a street in Lapa, Rio de Janeiro at night, neon signs and street lights, wearing a black jacket",
    "full body photo sitting on the grass in Ibirapuera park, Sao Paulo, wearing denim shorts and a white tank top, afternoon light",
    "medium shot cooking in a home kitchen in Brazil, stirring a pot, wearing an apron over a green blouse, warm indoor light",
    "full body photo walking among the colorful colonial houses of Pelourinho, Salvador, wearing a yellow sundress, bright midday sun",
    "medium shot at an office desk with a laptop in Sao Paulo, overcast window light, wearing a navy blazer",
    "close-up smiling at a rooftop bar in Sao Paulo at dusk, city skyline behind, wearing a red top, warm ambient light",
]
SEEDS = [7101 + i for i in range(len(SCENES))]

sheet_base = json.loads((ROOT / "personas" / "luna" / "persona_sheet.json").read_text())


def persona_dir(name: str, mutate) -> Path:
    d = ROOT / f"personas_{name}"
    if d.exists():
        shutil.rmtree(d)
    (d / "luna").mkdir(parents=True)
    shutil.copy(VOLUME_LUNA / "persona.json", d / "luna" / "persona.json")
    (d / "luna" / "references").symlink_to(VOLUME_LUNA / "references")
    data = copy.deepcopy(sheet_base)
    mutate(data)
    (d / "luna" / "persona_sheet.json").write_text(json.dumps(data, indent=1))
    return d


def texture_on(d):
    d["generation_profile"]["face_lock"]["texture_preservation"]["enabled"] = True
    d["generation_profile"]["face_lock"]["age_lock"]["enabled"] = True


def correction_on(d):
    d["generation_profile"]["skin_correction"]["enabled"] = True


report = {"hashes": {}, "configs": {}}
for role, ref in sheet_base["master_references"].items():
    if isinstance(ref, dict) and "sha256" in ref:
        path = VOLUME_LUNA / ref["file"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        report["hashes"][role] = actual == ref["sha256"]
print("hashes", report["hashes"], flush=True)
if not all(report["hashes"].values()):
    print("HASH MISMATCH - benchmark NAO executado", flush=True)
    sys.exit(2)

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.core.generation.history import GenerationHistory  # noqa: E402
from app.core.generation.negative import NegativePromptBuilder  # noqa: E402
from app.core.generation.orchestrator import AttemptState, GenerationOrchestrator  # noqa: E402
from app.core.generation.prompt_builder import PromptBuilder  # noqa: E402
from app.core.generation.request import GenerationRequest  # noqa: E402
from app.core.generation.retry_policy import RetryPolicy  # noqa: E402
from app.core.persona import PersonaRepository  # noqa: E402
from app.core.persona.sheet import PersonaSheetRepository  # noqa: E402
from app.core.telemetry import BATCH_MODE, CostEstimator  # noqa: E402
from app.core.validation.checks import (  # noqa: E402
    AgeValidator, AnatomyValidator, BodyConsistencyValidator, FaceIdentityValidator,
    PoseValidator, SkinRealismValidator, SubjectCountValidator, TriggerLeakValidator,
)
from app.core.validation.engine import ValidationEngine  # noqa: E402
from app.infrastructure.runpod import RunPodProvider  # noqa: E402
from app.providers.base import GenerationParameters, ProviderImage, ProviderRegistry, StageOutput  # noqa: E402
from app.providers.comfyui import comfyui_provider_set  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.skin import PillowSkinTextureAnalyzer  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

config = json.loads((ROOT / "config" / "persona_engine.json").read_text())
client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=10.0, generation_timeout=900.0)
registry = ProviderRegistry()
registry.register(comfyui_provider_set(client, WorkflowManager(ROOT / "workflows")))
cost = CostEstimator(RunPodProvider(os.environ.get("RUNPOD_API_KEY", ""), os.environ.get("RUNPOD_POD_ID", "")),
                     config["cost"].get("fallback_gpu_price_per_hour"))


def orchestrator(personas: Path) -> GenerationOrchestrator:
    return GenerationOrchestrator(
        personas=PersonaRepository(personas), sheets=PersonaSheetRepository(personas), providers=registry,
        analyzer=ComfyImageAnalyzer(client),
        validation=ValidationEngine([FaceIdentityValidator(), SubjectCountValidator(), AnatomyValidator(), PoseValidator(),
                                     BodyConsistencyValidator(), AgeValidator(), TriggerLeakValidator(),
                                     SkinRealismValidator()]),
        retry=RetryPolicy(config["retry"]["face_relock_before_regenerate"]), history=GenerationHistory(personas),
        prompt_builder=PromptBuilder(NegativePromptBuilder(config["global_negative"])),
        cost=cost, skin_analyzer=PillowSkinTextureAnalyzer(client),
    )


def request(i: int) -> GenerationRequest:
    return GenerationRequest(persona_id="luna", scene_prompt=SCENES[i], max_attempts=1,
                             generation_parameters=GenerationParameters(seed=SEEDS[i]))


def stage_from(d: dict) -> StageOutput:
    return StageOutput(**{**d, "image": ProviderImage(**d["image"])})


def save():
    (ROOT / "bench_v11.json").write_text(json.dumps(report, indent=1, default=str))


async def generate(name: str, personas: Path):
    orch = orchestrator(personas)
    ids = [orch.prepare(request(i))["id"] for i in range(len(SCENES))]
    t0 = time.time()
    jobs = await orch.run_batch(ids)
    report["configs"][name] = {"wall_seconds": round(time.time() - t0, 1), "jobs": jobs}
    save()
    print(name, "pronto", report["configs"][name]["wall_seconds"], "s", flush=True)
    return jobs


async def correct(name: str, personas: Path, source_jobs, source: str):
    """Mesmas imagens de A/B; roda o caminho real de validacao + correcao do orquestrador."""
    orch = orchestrator(personas)
    out = []
    t0 = time.time()
    for i, src in enumerate(source_jobs):
        job = orch.history.get(orch.prepare(request(i))["id"])
        job["execution_mode"] = BATCH_MODE
        ctx = await orch._context(job)
        res = src["results"][0]
        stages = {s["stage"]: stage_from(s) for s in res["stages"]}
        state = AttemptState(attempt=1, scene_seed=res["seeds"]["scene"], face_seed=res["seeds"]["face_lock"])
        state.prompt = None
        state.base = state.scene_stage = stages.get("scene")
        state.face_stage = stages.get("face_lock")
        job["attempt"] = 1
        done = await orch._finish_attempt(job, ctx, state, BATCH_MODE)
        if not done:
            orch._fail(job)
        orch.history.save(job)
        out.append(orch.history.get(job["id"]))
    report["configs"][name] = {"wall_seconds": round(time.time() - t0, 1), "source": "imagens de " + source, "jobs": out}
    save()
    print(name, "pronto", report["configs"][name]["wall_seconds"], "s", flush=True)


async def main():
    pa = persona_dir("A", lambda d: None)
    pb = persona_dir("B", texture_on)
    pc = persona_dir("C", correction_on)
    pd = persona_dir("D", lambda d: (texture_on(d), correction_on(d)))
    a = await generate("A", pa)
    b = await generate("B", pb)
    await correct("C", pc, a, "A")
    await correct("D", pd, b, "B")
    print("FIM", flush=True)


asyncio.run(main())
