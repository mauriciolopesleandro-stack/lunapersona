"""Persona Engine V2, teste 2: RealVisXL + LoRA SDXL da Luna com 3 passadas de
rosto e 2 de corpo (rollback automatico), 3 cenas pedidas pelo usuario.
Roda no pod com o codigo da branch em /workspace/v2test (V2_ROOT muda a pasta).

Uso: python benchmark_v2_multipass.py [--teto US$] [--overhead s]
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
from app.core.generation.multipass import MultiPassRunner  # noqa: E402
from app.core.generation.negative import NegativePromptBuilder  # noqa: E402
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
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter  # noqa: E402
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.skin import PillowSkinTextureAnalyzer  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

SCENES = [
    (7201, "academia", "full body photo in a modern gym, wearing a black sports top and grey high-waisted leggings, white sneakers, standing next to a dumbbell rack, holding a water bottle, bright gym lighting"),
    (7202, "selfie_espelho", "mirror selfie in a cozy bedroom, holding a smartphone at chest height, face fully visible in the mirror, wearing an oversized white t-shirt and black shorts, unmade bed behind her, soft morning window light"),
    (7203, "toalha_maquiagem", "medium shot sitting at a bathroom vanity, hair wrapped in a white towel, applying makeup with a brush on her cheek, wearing a white bathrobe, warm bathroom light"),
]
PRICE = 0.57


class KeepAliveAnalyzer:
    """Mantem o checkpoint no cache do ComfyUI entre as passadas. Se o no extra
    falhar no pod, desliga o truque (registrado no log) e segue sem ele."""

    def __init__(self, inner):
        self.inner = inner
        self.disabled_reason = None

    async def analyze(self, image, master):
        try:
            return await self.inner.analyze(image, master)
        except Exception as exc:
            if not self.inner.keep_alive:
                raise
            self.disabled_reason = f"{exc.__class__.__name__}: {exc}"
            print("KEEP_ALIVE desligado:", self.disabled_reason, flush=True)
            self.inner.keep_alive = {}
            return await self.inner.analyze(image, master)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teto", type=float, default=None)
    ap.add_argument("--overhead", type=float, default=180)
    ap.add_argument("--cena", action="append", default=[],
                    help="semente|rotulo|texto em ingles (repetir para varias); sem isso, as 3 cenas do teste 2")
    ap.add_argument("--saida", default="bench_v2_multipass.json")
    args = ap.parse_args()
    scenes = [tuple(c.split("|", 2)) for c in args.cena] if args.cena else SCENES
    scenes = [(int(s), lab, txt) for s, lab, txt in scenes]

    cfg = load_v2_config(ROOT / "config" / "persona_engine_v2.json")
    limit = min(cfg.benchmark_limit_usd, args.teto) if args.teto else cfg.benchmark_limit_usd
    passes = [p for p in (*cfg.face_passes, *cfg.body_passes) if p.enabled]
    plan = ExperimentPlan("V2 teste 2 (multi-pass RealVisXL)", "subir a identidade da base RealVisXL (0,37) sem perder a pele natural",
                          "passadas de rosto com a LoRA em recorte ampliado aumentam a semelhanca; rollback segura as que pioram",
                          images=len(scenes), seconds_per_image=14 + 13 * len(passes), overhead_seconds=args.overhead,
                          price_per_hour=PRICE)
    budget = BudgetGuard(limit).check(plan)

    personas = ROOT / "personas_run"
    sheet = PersonaSheetRepository(personas).get("luna")
    profile = PersonaRepository(personas).get("luna")
    m = sheet.master("master_face")
    master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)

    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=10.0, generation_timeout=900.0)
    session = ComfySession(client, WorkflowManager(ROOT / "workflows"))
    model = cfg.model("realvisxl")
    base = v2_model_adapters(session, cfg)["realvisxl"]
    region = ComfyRegionPassAdapter(session, model, cfg.lora, steps=cfg.pass_steps, identity_adapters=cfg.identity_adapters)
    problems = [*await base.validate_configuration(), *await region.validate_configuration()]
    if problems:
        print("CONFIG", problems, flush=True)
        return 2
    keep = {"ka1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model.checkpoint}},
            "ka2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["ka1", 0], "lora_name": cfg.lora.file,
                                                                    "strength_model": cfg.lora.strength}},
            "ka3": {"class_type": "PreviewAny", "inputs": {"source": ["ka2", 0]}}}
    analyzer = KeepAliveAnalyzer(ComfyImageAnalyzer(client, keep_alive=keep))
    runner = MultiPassRunner(base=base, region=region, analyzer=analyzer, skin_analyzer=PillowSkinTextureAnalyzer(client),
                             rules=cfg.acceptance, age_target=cfg.age_target,
                             duplicate_similarity=float(sheet.validation["subject_count"]["persona_duplicate_similarity"]),
                             skin_config=sheet.validation["skin_realism"], price_per_hour=PRICE)
    engine = ValidationEngine([FaceIdentityValidator(), SubjectCountValidator(), AnatomyValidator(), PoseValidator(),
                               BodyConsistencyValidator(), AgeValidator(), SkinRealismValidator()])
    builder = PromptBuilder(NegativePromptBuilder(json.loads((ROOT / "config" / "persona_engine.json").read_text())["global_negative"]))
    skin_meter = PillowSkinTextureAnalyzer(client)

    out_path = ROOT / args.saida
    report = {"budget": budget, "passes": [p.to_dict() for p in passes], "style": cfg.style.id if cfg.style else None,
              "scenes": []}
    for seed, label, scene in scenes:
        prompt = builder.build(sheet, profile, cfg.styled(scene), {}, [], single_subject=True)
        negative = ", ".join(dict.fromkeys([*prompt.negative.all_terms(), *cfg.negative_for(model)]))
        prompts = {k: cfg.styled(v) for k, v in cfg.pass_prompts.items() if k != "body"}
        prompts["body"] = "lunavox, a woman, " + cfg.styled(cfg.pass_prompts["body"].replace("{scene}", scene))
        t0 = time.monotonic()
        res = await runner.run(SceneRequest(prompt=prompt, parameters=GenerationParameters(seed=seed), lora={}), master,
                               list(cfg.face_passes), list(cfg.body_passes), prompts, negative,
                               (cfg.lora.file, cfg.lora.strength), (model.id, model.title))
        final = res.final
        skin = await skin_meter.analyze(final.image, final.analysis.persona_face(), sheet.validation["skin_realism"])
        validation = await engine.run(ValidationContext(sheet=sheet, image=final.image, analysis=final.analysis, skin=skin))
        wall = round(time.monotonic() - t0, 1)
        report["keep_alive"] = analyzer.disabled_reason or "ligado"
        report["scenes"].append({"seed": seed, "label": label, "scene": scene, "prompts": prompts, "negative": negative,
                                 "multipass": res.to_dict(), "final_validation": validation.to_dict(), "wall_seconds": wall})
        out_path.write_text(json.dumps(report, indent=1, default=str))
        print(label, "base", res.checkpoints[0].measure.face, "final", final.name, final.measure.face,
              [(d["pass"], d["status"]) for d in res.decisions], "wall", wall, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
