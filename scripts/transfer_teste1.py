"""Persona Transfer - primeiro teste (UMA foto, UMA geracao). Spec "PERSONA REPLACEMENT - INTEGRACAO FOTOGRAFICA".

A Luna inteira gerada na pose da pessoa da foto (RealVisXL + LoRA lunavox + ControlNet Union openpose);
roupa, acessorios e cenario sao os pixels da foto. Refino de rosto so se a identidade ficar baixa;
integracao leve (sem LoRA) no fim. Sem filtro/grao depois.

Nao roda sem autorizacao quando a estimativa passa de US$ 0,05 (config/persona_transfer.json).
Uso no pod (V2_ROOT = pasta com backend/, config/, workflows/, personas_run/):
  python transfer_teste1.py --foto fotos_ref/1_quarto_top_preto.png [--autorizado 0.09]
Saida: transfer_teste1.json + checkpoints (original, persona_transfer, [face_refinement], integration).
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
from app.core.persona_replacement.transfer import TransferOrchestrator, load_transfer_config  # noqa: E402
from app.providers.base import ReferenceImage  # noqa: E402
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter  # noqa: E402
from app.providers.comfyui.replacement import (  # noqa: E402
    ComfyImageStore,
    ComfyReplacementTransformer,
    ComfySegmenter,
    ComfyTransferTransformer,
)
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.reference import ComfyReferenceReader  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

PRICE = 0.57
# 1 foto: transferencia (~45-60 s com ControlNet/DWPose) + integracao (~12 s) + medidas; fixo: ComfyUI, leitura,
# segmentacao (SAM2/Florence) e carga dos modelos. O boot do pod e o download do ControlNet ficam no "overhead".
SECONDS_PER_IMAGE, OVERHEAD_SECONDS = 120, 300


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--foto", required=True)
    ap.add_argument("--autorizado", type=float, default=None)
    ap.add_argument("--semente", type=int, default=7801)
    ap.add_argument("--saida", default="transfer_teste1.json")
    args = ap.parse_args()

    cfg = load_transfer_config(ROOT / "config" / "persona_transfer.json")
    gen = load_v2_config(ROOT / cfg.generation_config)  # so leitura: modelo, LoRA e amostragem travados
    plan = ExperimentPlan("Persona Transfer 1o teste", "a Luna inteira na pose da foto, sem cara de colagem",
                          "gerar a pessoa inteira com pose (ControlNet) integra melhor que transformar so o rosto",
                          images=1, seconds_per_image=SECONDS_PER_IMAGE, overhead_seconds=OVERHEAD_SECONDS,
                          price_per_hour=PRICE)
    budget = BudgetGuard(cfg.budget_limit_usd).check(plan, args.autorizado)

    sheet = PersonaSheetRepository(ROOT / "personas_run").get("luna")
    m = sheet.master("master_face")
    master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)

    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=10.0, generation_timeout=900.0)
    session = ComfySession(client, WorkflowManager(ROOT / "workflows"))
    store = ComfyImageStore(client)
    region = ComfyRegionPassAdapter(session, gen.model(), gen.lora, steps=gen.pass_steps, identity_adapters=gen.identity_adapters)
    transformer = ComfyTransferTransformer(session, ComfyReplacementTransformer(region, client), gen.model(), gen.lora,
                                           cfg.transfer)
    problems = await region.validate_configuration() + await transformer.validate_configuration()
    if problems:
        print("CONFIG", problems, flush=True)
        return 2
    keep = {"ka1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": gen.model().checkpoint}},
            "ka2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["ka1", 0], "lora_name": gen.lora.file,
                                                                    "strength_model": gen.lora.strength}},
            "ka3": {"class_type": "PreviewAny", "inputs": {"source": ["ka2", 0]}}}
    orch = TransferOrchestrator(
        reader=ComfyReferenceReader(client), segmenter=ComfySegmenter(session, store), transformer=transformer,
        analyzer=ComfyImageAnalyzer(client, keep_alive=keep), store=store, config=cfg,
        duplicate_similarity=float(sheet.validation["subject_count"]["persona_duplicate_similarity"]), price_per_hour=PRICE)

    negative = ", ".join(NegativePromptBuilder(json.loads((ROOT / "config" / "persona_engine.json").read_text())["global_negative"])
                         .build(sheet, single_subject=True).all_terms())
    img = Image.open(args.foto).convert("RGB")
    img.thumbnail((1600, 1600), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    locator = await client.upload_image(f"xfer_input_{Path(args.foto).stem[:30]}.png", buf.getvalue())
    res = await orch.run(locator, master, negative, args.semente)
    out = {"budget": budget, "config_version": cfg.version, "foto": args.foto, "input": locator, **res.to_dict()}
    (ROOT / args.saida).write_text(json.dumps(out, indent=1, default=str))
    r = res.report
    print("FINAL", Path(args.foto).name, res.final.name, "status", r.status, "identidade", r.identity, "original",
          r.original_similarity, "pose", r.pose, "fundo", r.background_changed, "roupa", r.clothing_changed, "luz",
          r.lighting.get("score"), "textura", r.texture.get("score"), "borda", r.edge.get("score"), "integracao",
          r.integration_score, "falhas", r.failures, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
