"""Persona Engine V2, teste 1 (base): RealVisXL x Lustify, 3 cenas cada, mesma
LoRA SDXL da Luna, mesmas sementes, mesmos parametros, sem Qwen e sem
pos-processamento. Roda no pod com o codigo da branch em /workspace/v2test.

Uso: python benchmark_v2_base.py <modelo> [--autorizado US$]
  (um modelo por vez: o run_benchmark_v2.sh baixa, roda e apaga cada checkpoint,
   para caber no disco temporario do pod)

As cenas e sementes sao 3 das 10 do benchmark V1.1 (7101, 7103, 7106): as imagens
da V1 dessas cenas ja existem para comparar.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("V2_ROOT", "/workspace/v2test"))
sys.path.insert(0, str(ROOT / "pylib"))
sys.path.insert(0, str(ROOT / "backend"))

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.core.generation.budget import BudgetGuard, ExperimentPlan  # noqa: E402
from app.core.generation.negative import NegativePromptBuilder  # noqa: E402
from app.core.generation.pass_telemetry import base_record, cost_of  # noqa: E402
from app.core.generation.prompt_builder import PromptBuilder  # noqa: E402
from app.core.generation.v2_config import load_v2_config  # noqa: E402
from app.core.persona import PersonaRepository  # noqa: E402
from app.core.persona.sheet import PersonaSheetRepository  # noqa: E402
from app.core.validation.checks import (  # noqa: E402
    AgeValidator, AnatomyValidator, BodyConsistencyValidator, FaceIdentityValidator, PoseValidator,
    SkinRealismValidator, SubjectCountValidator, ValidationContext,
)
from app.core.validation.engine import ValidationEngine  # noqa: E402
from app.providers.base import GenerationParameters, ReferenceImage, SceneRequest  # noqa: E402
from app.providers.comfyui.checkpoint import v2_model_adapters  # noqa: E402
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.skin import PillowSkinTextureAnalyzer  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

SCENES = [
    (7101, "full body photo, standing at the counter of a cozy coffee shop in Sao Paulo, holding a cup of coffee, wearing a white blouse and blue jeans, afternoon light"),
    (7103, "close-up portrait by an apartment window in Rio de Janeiro, soft overcast daylight, wearing a grey t-shirt, relaxed expression"),
    (7106, "full body photo sitting on the grass in Ibirapuera park, Sao Paulo, wearing denim shorts and a white tank top, afternoon light"),
]
PRICE = 0.57


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--autorizado", type=float, default=None)
    args = ap.parse_args()

    cfg = load_v2_config(ROOT / "config" / "persona_engine_v2.json")
    guard = BudgetGuard(cfg.benchmark_limit_usd)
    # Teste 1 inteiro (os 2 modelos): ligar pod + subir LoRA + baixar 2 checkpoints + 6 imagens.
    plan = ExperimentPlan("V2 teste 1 (base)", "comparar a qualidade BASE RealVisXL x Lustify",
                          "um checkpoint SDXL fotografico com a LoRA da Luna da pele mais natural que a V1, "
                          "com identidade menor (sem troca de cabeca)", images=6, seconds_per_image=30,
                          overhead_seconds=420, price_per_hour=PRICE)
    budget = guard.check(plan, args.autorizado)

    personas = ROOT / "personas_run"
    sheet = PersonaSheetRepository(personas).get("luna")
    profile = PersonaRepository(personas).get("luna")
    m = sheet.master("master_face")
    master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)  # confere sha256

    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=10.0, generation_timeout=900.0)
    adapter = v2_model_adapters(ComfySession(client, WorkflowManager(ROOT / "workflows")), cfg)[args.model]
    problems = await adapter.validate_configuration()
    if problems:
        print("CONFIG", problems, flush=True)
        return 2
    analyzer = ComfyImageAnalyzer(client)
    skin_meter = PillowSkinTextureAnalyzer(client)
    engine = ValidationEngine([FaceIdentityValidator(), SubjectCountValidator(), AnatomyValidator(), PoseValidator(),
                               BodyConsistencyValidator(), AgeValidator(), SkinRealismValidator()])
    builder = PromptBuilder(NegativePromptBuilder(json.loads((ROOT / "config" / "persona_engine.json").read_text())["global_negative"]))

    out_path = ROOT / "bench_v2_base.json"
    report = json.loads(out_path.read_text()) if out_path.exists() else {"budget": budget, "results": []}
    for seed, scene in SCENES:
        prompt = builder.build(sheet, profile, scene, {}, [], single_subject=True)
        t0 = time.monotonic()
        stage = await adapter.generate(SceneRequest(prompt=prompt, parameters=GenerationParameters(seed=seed), lora={}))
        analysis = await analyzer.analyze(stage.image, master)
        skin = await skin_meter.analyze(stage.image, analysis.persona_face(), sheet.validation["skin_realism"])
        validation = await engine.run(ValidationContext(sheet=sheet, image=stage.image, analysis=analysis, skin=skin),
                                      seconds=time.monotonic() - t0 - stage.seconds)
        rec = base_record(model=args.model, model_version=adapter.profile.title, lora=cfg.lora.file,
                          lora_strength=cfg.lora.strength, seed=seed, prompt=stage.effective_parameters["prompt"],
                          negative=stage.effective_parameters["negative"])
        checks = validation.checks
        rec.face_identity_score = validation.face_score
        rec.age_score = checks["age"].evidence.get("consistency") if "age" in checks else None
        rec.skin_score = skin.score
        rec.body_score = checks["body_consistency"].score
        rec.subject_count = len(analysis.faces)
        rec.duration = stage.seconds
        rec.gpu = stage.gpu.get("name")
        rec.vram = stage.gpu.get("vram_used_mb")
        rec.cost = cost_of(stage.seconds, PRICE)
        rec.image = stage.image.url
        report["results"].append({
            "record": rec.to_dict(), "scene": scene, "stage": stage.to_dict(), "validation": validation.to_dict(),
            "skin": skin.to_dict(), "age": checks["age"].score, "validation_seconds": validation.seconds,
        })
        out_path.write_text(json.dumps(report, indent=1, default=str))
        print(args.model, seed, "rosto", validation.face_score, "pele", skin.score, "idade", checks["age"].score,
              "tempo", stage.seconds, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
