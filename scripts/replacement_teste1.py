"""Persona Replacement V1 - primeiro teste (UMA imagem: a foto da varanda, caso de regressao).

Nao roda sem autorizacao quando a estimativa passa de US$ 0,05 (config/persona_replacement.json).
Uso no pod (V2_ROOT = pasta com backend/, config/, workflows/, personas_run/):
  python replacement_teste1.py --foto fotos_ref/5_varanda_copacabana_oculos.png [--autorizado 0.07]
Saida: replacement_teste1.json + checkpoints (original, hair_recolor, face_pass_1..3, body_pass_1..2, integrated).
"""
import argparse
import asyncio
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("V2_ROOT", "/workspace/v2test"))
sys.path.insert(0, str(ROOT / "pylib"))
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image  # noqa: E402

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.core.generation.budget import BudgetGuard, ExperimentPlan  # noqa: E402
from app.core.generation.negative import NegativePromptBuilder  # noqa: E402
from app.core.generation.v2_config import load_v2_config  # noqa: E402
from app.core.persona.sheet import PersonaSheetRepository  # noqa: E402
from app.core.persona_replacement.contracts import load_replacement_config  # noqa: E402
from app.core.persona_replacement.replacement_orchestrator import ReplacementOrchestrator  # noqa: E402
from app.providers.base import ReferenceImage  # noqa: E402
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter  # noqa: E402
from app.providers.comfyui.replacement import ComfyImageStore, ComfyReplacementTransformer, ComfySegmenter  # noqa: E402
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.reference import ComfyReferenceReader  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

PRICE = 0.57


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--foto", action="append", required=True, help="repetir para varias fotos")
    ap.add_argument("--autorizado", type=float, default=None)
    ap.add_argument("--semente", type=int, default=7701)
    ap.add_argument("--saida", default="replacement_teste1.json")
    args = ap.parse_args()

    cfg = load_replacement_config(ROOT / "config" / "persona_replacement.json")
    gen = load_v2_config(ROOT / cfg.generation_config)  # so leitura: modelo, LoRA, InstantID travados
    plan = ExperimentPlan("Persona Replacement 1o teste", "integrar a Luna na foto da varanda sem parecer colada",
                          "transformacao localizada + luz/grao da propria foto melhora a integracao sem perder identidade",
                          images=len(args.foto), seconds_per_image=240, overhead_seconds=150, price_per_hour=PRICE)
    budget = BudgetGuard(cfg.budget_limit_usd).check(plan, args.autorizado)

    personas = ROOT / "personas_run"
    sheet = PersonaSheetRepository(personas).get("luna")
    m = sheet.master("master_face")
    master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)

    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=10.0, generation_timeout=900.0)
    session = ComfySession(client, WorkflowManager(ROOT / "workflows"))
    store = ComfyImageStore(client)
    region = ComfyRegionPassAdapter(session, gen.model(), gen.lora, steps=gen.pass_steps, identity_adapters=gen.identity_adapters)
    problems = await region.validate_configuration()
    if problems:
        print("CONFIG", problems, flush=True)
        return 2
    keep = {"ka1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": gen.model().checkpoint}},
            "ka2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["ka1", 0], "lora_name": gen.lora.file,
                                                                    "strength_model": gen.lora.strength}},
            "ka3": {"class_type": "PreviewAny", "inputs": {"source": ["ka2", 0]}}}
    orch = ReplacementOrchestrator(
        reader=ComfyReferenceReader(client), segmenter=ComfySegmenter(session, store),
        transformer=ComfyReplacementTransformer(region, client), analyzer=ComfyImageAnalyzer(client, keep_alive=keep),
        store=store, config=cfg,
        duplicate_similarity=float(sheet.validation["subject_count"]["persona_duplicate_similarity"]), price_per_hour=PRICE)

    negative = ", ".join(NegativePromptBuilder(json.loads((ROOT / "config" / "persona_engine.json").read_text())["global_negative"])
                         .build(sheet, single_subject=True).all_terms())
    results = []
    for foto in args.foto:
        img = Image.open(foto).convert("RGB")
        img.thumbnail((1600, 1600), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        locator = await client.upload_image(f"repl_input_{Path(foto).stem[:30]}.png", buf.getvalue())
        res = await orch.run(locator, master, negative, args.semente)
        results.append({"foto": foto, "input": locator, **res.to_dict()})
        (ROOT / args.saida).write_text(json.dumps({"budget": budget, "config_version": cfg.version, "results": results},
                                                  indent=1, default=str))
        r = res.report
        print("FINAL", Path(foto).name, res.final.name, "status", r.status, "identidade", r.identity, "fundo",
              r.background_changed, "roupa", r.clothing_changed, "luz", r.lighting.get("score"), "textura",
              r.texture.get("score"), "borda", r.edge.get("score"), "falhas", r.failures, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
