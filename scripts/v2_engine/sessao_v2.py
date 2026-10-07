"""Sessao GPU REAL das engines V2 (smoke test + benchmark progressivo + regressao da geracao V1).

Roda NO POD (ComfyUI em 127.0.0.1:8188), com o codigo da branch em V2_ROOT. Nada e simulado:
cada execucao chama o ComfyUI de verdade e mede com o analisador do estudio.

  python sessao_v2.py --plano plano.json --saida resultado.json --autorizado 0.60

plano.json: {"execucoes": [{"id": "smoke_quarto", "foto": "fotos_ref/1_quarto_top_preto.png",
                            "modo": "QUALITY", "semente": 7801, "max_retries": 1}, ...],
             "regressao_v1": {"cena": "...", "semente": 4242} }
"""
import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("V2_ROOT", "/workspace/v2test"))
sys.path.insert(0, str(ROOT / "pylib"))
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image  # noqa: E402

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.core.engines.models import ModelRegistry  # noqa: E402
from app.core.engines.replacement import ReplacementEngine, ReplacementRequest  # noqa: E402
from app.core.generation.budget import BudgetGuard, ExperimentPlan  # noqa: E402
from app.core.generation.negative import NegativePromptBuilder  # noqa: E402
from app.core.generation.v2_config import load_v2_config  # noqa: E402
from app.core.persona.sheet import PersonaSheetRepository  # noqa: E402
from app.providers.base import ReferenceImage  # noqa: E402
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter  # noqa: E402
from app.providers.comfyui.replacement import ComfyImageStore, ComfySegmenter  # noqa: E402
from app.providers.comfyui.sdxl_engine import RealVisXLAdapter  # noqa: E402
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.reference import ComfyReferenceReader  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

PRICE = float(os.environ.get("V2_PRECO_HORA", "0.57"))
CN = "controlnet-union-sdxl-1.0-promax.safetensors"


class Cache:
    """Leitura e segmentacao da MESMA foto sao feitas uma vez (nao gasta GPU repetindo)."""

    def __init__(self, inner, method):
        self.inner, self.method, self.memo = inner, method, {}

    async def __call__(self, *a):
        key = a[0] if isinstance(a[0], str) else a[0].locator
        if key not in self.memo:
            self.memo[key] = await getattr(self.inner, self.method)(*a)
        return self.memo[key]


class CachedReader:
    def __init__(self, inner):
        self.read = Cache(inner, "read")


class CachedSegmenter:
    def __init__(self, inner):
        self.segment = Cache(inner, "segment")


def vram_pico(desde: float) -> float | None:
    try:
        linhas = Path("/workspace/v2test/vram_v2.log").read_text().splitlines()
    except OSError:
        return None
    vals = [float(l.split(",")[1]) for l in linhas if l.count(",") >= 1 and float(l.split(",")[0]) >= desde]
    return max(vals) if vals else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plano", required=True)
    ap.add_argument("--saida", required=True)
    ap.add_argument("--autorizado", type=float, default=None)
    a = ap.parse_args()
    plano = json.loads(Path(a.plano).read_text())
    execs = plano["execucoes"]
    guard = BudgetGuard(0.10).check(ExperimentPlan("V2 engines GPU", "smoke + benchmark", "pipeline V2",
                                                   images=len(execs), seconds_per_image=int(plano.get("segundos_por_imagem", 180)),
                                                   overhead_seconds=int(plano.get("overhead_segundos", 360)),
                                                   price_per_hour=PRICE), a.autorizado)

    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=90.0, generation_timeout=900.0)
    session = ComfySession(client, WorkflowManager(ROOT / "workflows"))
    store = ComfyImageStore(client)
    v2 = load_v2_config(ROOT / "config" / "persona_engine_v2.json")
    reg = ModelRegistry.load(ROOT / "config" / "model_registry_v2.json")
    region = ComfyRegionPassAdapter(session, v2.model("realvisxl"), v2.lora, steps=v2.pass_steps,
                                    identity_adapters=v2.identity_adapters)
    adapter = RealVisXLAdapter(session, reg.select_checkpoint("auto"), reg.get("lunavox_sdxl_v1"), CN, region=region,
                               price_per_hour=PRICE)
    problemas = await adapter.load()
    if problemas:
        print("CONFIG", problemas, flush=True)
        return 2
    keep = {"ka1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": adapter.model.file}},
            "ka2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["ka1", 0], "lora_name": adapter.lora.file,
                                                                    "strength_model": 1.0}},
            "ka3": {"class_type": "PreviewAny", "inputs": {"source": ["ka2", 0]}}}
    engine = ReplacementEngine(reader=CachedReader(ComfyReferenceReader(client)),
                               segmenter=CachedSegmenter(ComfySegmenter(session, store)),
                               analyzer=ComfyImageAnalyzer(client, keep_alive=keep), store=store, adapter=adapter,
                               config=ReplacementEngine.load_config(ROOT / "config" / "replacement_engine_v2.json"),
                               price_per_hour=PRICE, provider="comfyui@runpod")
    sheet = PersonaSheetRepository(ROOT / "personas_run").get("luna")
    m = sheet.master("master_face")
    master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)
    negative = ", ".join(NegativePromptBuilder(json.loads((ROOT / "config" / "persona_engine.json").read_text())["global_negative"])
                         .build(sheet, single_subject=True).all_terms())
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    cuda = subprocess.run(["bash", "-c", "nvidia-smi | grep -o 'CUDA Version: [0-9.]*'"], capture_output=True, text=True).stdout.strip()
    saida = {"budget": guard, "gpu": gpu, "cuda": cuda, "registry": reg.version, "preco_hora": PRICE,
             "workflow": adapter.metadata().get("workflow"), "resultados": []}
    enviados = {}
    for ex in execs:
        if ex["foto"] not in enviados:
            img = Image.open(ROOT / ex["foto"]).convert("RGB")
            img.thumbnail((1600, 1600), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            enviados[ex["foto"]] = await client.upload_image(f"v2in_{Path(ex['foto']).stem[:30]}.png", buf.getvalue())
        t0 = time.time()
        req = ReplacementRequest(image=enviados[ex["foto"]], persona_id="luna", master=master, mode=ex["modo"],
                                 seed=int(ex["semente"]), advanced={"max_retries": int(ex.get("max_retries", 0)), **ex.get("advanced", {})},
                                 negative=negative, keep_intermediates=True, persona_sheet=sheet.data,
                                 preserve_attributes=list(ex.get("preserve", [])), remove_attributes=list(ex.get("remove", [])),
                                 reconstruct_attributes=list(ex.get("reconstruct", [])))
        try:
            out = await engine.run(req)
            r = {"id": ex["id"], **ex, **out.to_dict(), "vram_pico_mb": vram_pico(t0), "segundos_parede": round(time.time() - t0, 1)}
            v = out.report
            print("FINAL", ex["id"], out.status, "identidade", out.measures.get("identity"), "original", out.measures.get("original_sim"),
                  "tatuagem", out.measures.get("tattoo_residual"), "pose", out.measures.get("pose"), "fundo", out.measures.get("background"),
                  "falhas", v.failures(), "avisos", v.warnings(), f"{r['segundos_parede']}s", flush=True)
        except Exception as exc:  # noqa: BLE001 - registra e segue para a proxima execucao
            r = {"id": ex["id"], **ex, "erro": f"{type(exc).__name__}: {exc}", "segundos_parede": round(time.time() - t0, 1)}
            print("ERRO", ex["id"], r["erro"], flush=True)
        saida["resultados"].append(r)
        Path(a.saida).write_text(json.dumps(saida, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
