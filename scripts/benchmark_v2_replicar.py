"""Persona Engine V2, modo "replicar foto" (fases 1 e 2 do docs/PLANO_V2_SITE.md).

Para cada foto de referencia:
  1. reduz para no maximo `replicate.max_reference_side` e envia ao ComfyUI;
  2. le a foto (LunaFaces + DWPose + Florence-2 PromptGen + luz medida; Qwen3 do pod
     organiza a descricao em campos, se responder);
  3. troca a pessoa pela Luna mantendo a foto (realvis-person-replace) e roda as
     passadas TRAVADAS (rosto 1 InstantID, rosto 2, rosto 3 sem LoRA, corpo 1, corpo 2),
     todas presas a mascara da pessoa;
  4. confere: rosto >= 0,70, pose parecida com a da foto, fundo intacto;
  5. persona perdida -> refaz com outra semente (ate max_drift_retries).

Uso: V2_ROOT=... python benchmark_v2_replicar.py --fotos pasta_com_fotos --autorizado 0.30
"""
import argparse
import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("V2_ROOT", "/workspace/v2test"))
sys.path.insert(0, str(ROOT / "pylib"))
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image  # noqa: E402

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.clients.llm_client import ChatMessage, OllamaClient  # noqa: E402
from app.core.generation.budget import BudgetGuard, ExperimentPlan  # noqa: E402
from app.core.generation.multipass import MultiPassRunner  # noqa: E402
from app.core.generation.negative import NegativePromptBuilder  # noqa: E402
from app.core.generation.prompt_builder import PromptBuilder  # noqa: E402
from app.core.generation.reference import ReferenceError  # noqa: E402
from app.core.generation.v2_config import load_v2_config  # noqa: E402
from app.core.persona import PersonaRepository  # noqa: E402
from app.core.persona.sheet import PersonaSheetRepository  # noqa: E402
from app.core.validation.checks import (  # noqa: E402
    AgeValidator, AnatomyValidator, FaceIdentityValidator, SkinRealismValidator, SubjectCountValidator, ValidationContext,
)
from app.core.validation.engine import ValidationEngine  # noqa: E402
from app.core.validation.geometry import pose_distance  # noqa: E402
from app.providers.base import GenerationParameters, ProviderImage, ReferenceImage, SceneRequest  # noqa: E402
from app.providers.comfyui.person_replace import ComfyPersonReplaceAdapter  # noqa: E402
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter  # noqa: E402
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.reference import ComfyReferenceReader, background_change  # noqa: E402
from app.validation_backends.skin import PillowSkinTextureAnalyzer, _split_locator  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

PRICE = 0.57
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


async def fetch(client, locator: str) -> Image.Image:
    name, sub, folder = _split_locator(locator)
    return Image.open(io.BytesIO(await client.download_file(name, sub, folder))).convert("RGB")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fotos", required=True)
    ap.add_argument("--autorizado", type=float, default=None)
    ap.add_argument("--saida", default="bench_replicar.json")
    ap.add_argument("--semente", type=int, default=7601)
    ap.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", ""))
    args = ap.parse_args()

    photos = sorted(p for p in Path(args.fotos).iterdir() if p.suffix.lower() in EXTS)
    cfg = load_v2_config(ROOT / "config" / "persona_engine_v2.json")
    rep = cfg.replicate
    passes = [p for p in (*cfg.face_passes, *cfg.body_passes) if p.enabled]
    plan = ExperimentPlan("V2 replicar foto", "trocar a pessoa da foto pela Luna mantendo pose, ambiente e luz",
                          "SAM2 + RealVisXL/LoRA a partir dos pixels + passadas travadas dao rosto >= 0,70 com fundo intacto",
                          images=len(photos), seconds_per_image=60 + 30 * len(passes), overhead_seconds=150,
                          price_per_hour=PRICE)
    budget = BudgetGuard(cfg.benchmark_limit_usd).check(plan, args.autorizado)

    personas = ROOT / "personas_run"
    sheet = PersonaSheetRepository(personas).get("luna")
    profile = PersonaRepository(personas).get("luna")
    m = sheet.master("master_face")
    master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)

    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=10.0, generation_timeout=900.0)
    session = ComfySession(client, WorkflowManager(ROOT / "workflows"))
    model = cfg.model()
    llm = OllamaClient("http://127.0.0.1:11434", args.llm_model) if args.llm_model else None

    async def ask(prompt: str) -> str:
        return await llm.chat([ChatMessage("user", prompt)], timeout=120)

    reader = ComfyReferenceReader(client, ask=ask if llm else None)
    replacer = ComfyPersonReplaceAdapter(session, model, cfg.lora, cfg.negative_for(model), rep)
    region = ComfyRegionPassAdapter(session, model, cfg.lora, steps=cfg.pass_steps, identity_adapters=cfg.identity_adapters)
    problems = [*await replacer.validate_configuration(), *await region.validate_configuration()]
    if problems:
        print("CONFIG", problems, flush=True)
        return 2
    keep = {"ka1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model.checkpoint}},
            "ka2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["ka1", 0], "lora_name": cfg.lora.file,
                                                                    "strength_model": cfg.lora.strength}},
            "ka3": {"class_type": "PreviewAny", "inputs": {"source": ["ka2", 0]}}}
    analyzer = ComfyImageAnalyzer(client, keep_alive=keep)
    engine = ValidationEngine([FaceIdentityValidator(), SubjectCountValidator(), AnatomyValidator(), AgeValidator(),
                               SkinRealismValidator()])
    builder = PromptBuilder(NegativePromptBuilder(json.loads((ROOT / "config" / "persona_engine.json").read_text())["global_negative"]))
    skin_meter = PillowSkinTextureAnalyzer(client)
    checks = rep["checks"]

    report = {"budget": budget, "identity_lock": {k: cfg.identity_lock.get(k) for k in ("version", "fingerprint")},
              "replicate": rep, "photos": []}
    out_path = ROOT / args.saida
    for n, photo in enumerate(photos):
        t0 = time.monotonic()
        img = Image.open(photo).convert("RGB")
        img.thumbnail((rep["max_reference_side"], rep["max_reference_side"]), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        locator = await client.upload_image(f"ref_{photo.stem[:40]}.png", buf.getvalue())
        entry = {"photo": photo.name, "size": list(img.size), "attempts": []}
        try:
            ref = await reader.read(ProviderImage("comfyui", locator, "", img.width, img.height), master)
        except ReferenceError as exc:
            entry["error"] = str(exc)
            report["photos"].append(entry)
            out_path.write_text(json.dumps(report, indent=1, default=str))
            print(photo.name, "RECUSADA:", exc, flush=True)
            continue
        entry["reading"] = ref.to_dict()
        print(photo.name, "leitura:", ref.camera, "|", ref.description()[:160], "|", ref.warnings, flush=True)
        scene = f"{ref.description()}, {rep['positive']}"  # luz e camera vem da foto: sem estilo inventado
        prompt = builder.build(sheet, profile, scene, {}, [], single_subject=True)
        negative = ", ".join(dict.fromkeys([*prompt.negative.all_terms(), *cfg.negative_for(model)]))
        prompts = {k: v.replace("{style}", rep["positive"]) for k, v in cfg.pass_prompts.items() if k != "body"}
        prompts["body"] = "lunavox, a woman, " + cfg.pass_prompts["body"].replace("{scene}", ref.description()).replace("{style}", rep["positive"])
        runner = MultiPassRunner(base=replacer.bind(ref), region=region, analyzer=analyzer, skin_analyzer=skin_meter,
                                 rules=cfg.acceptance, age_target=cfg.age_target,
                                 duplicate_similarity=float(sheet.validation["subject_count"]["persona_duplicate_similarity"]),
                                 skin_config=sheet.validation["skin_realism"], price_per_hour=PRICE)
        for attempt in range(int(rep["max_drift_retries"]) + 1):
            seed = args.semente + 100 * n + 17 * attempt
            res = await runner.run(SceneRequest(prompt=prompt, parameters=GenerationParameters(seed=seed), lora={}),
                                   master, list(cfg.face_passes), list(cfg.body_passes), prompts, negative,
                                   (cfg.lora.file, cfg.lora.strength), (model.id, model.title))
            final = res.final
            skin = await skin_meter.analyze(final.image, final.analysis.persona_face(), sheet.validation["skin_realism"])
            validation = await engine.run(ValidationContext(sheet=sheet, image=final.image, analysis=final.analysis, skin=skin))
            body = final.analysis.main_body()
            pose = (pose_distance(ref.target_body.keypoints, body.keypoints)
                    if ref.target_body is not None and body is not None else None)
            mask_locator = res.base_parameters.get("clip_mask")
            bg = None
            if mask_locator:
                bg = background_change(img, await fetch(client, final.image.locator), await fetch(client, mask_locator))
            face = final.measure.face
            drift = face is None or face < checks["min_final_face"]
            att = {"seed": seed, "multipass": res.to_dict(), "final_validation": validation.to_dict(),
                   "pose_to_reference": pose, "background_changed": bg, "persona_drift": drift,
                   "checks": {"rosto": not drift,
                              "pose": pose is not None and pose <= checks["max_pose_distance_to_reference"],
                              "fundo": bg is not None and bg <= checks["max_background_changed"]}}
            entry["attempts"].append(att)
            print(photo.name, "tentativa", attempt + 1, "rosto", face, "pose", pose, "fundo", bg,
                  "drift" if drift else "ok", flush=True)
            if not drift:
                break
        entry["wall_seconds"] = round(time.monotonic() - t0, 1)
        report["photos"].append(entry)
        out_path.write_text(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
