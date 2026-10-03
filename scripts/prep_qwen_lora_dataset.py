"""Monta o dataset da LoRA da Luna para o Qwen-Image-Edit 2511 (pack com a persona).

Cada foto de treino da Luna vira um par IGUAL ao que o pack manda para o Qwen,
gerado pelo proprio workflows/qwen-person-swap.json (sem rodar o Qwen):
- control 1: o recorte em volta dela com o retangulo vermelho, o cabelo
  escondido e o rosto borrado (no "ff6" do workflow);
- control 2: o recorte do rosto da foto da persona (no "13");
- alvo: o mesmo recorte da foto original (no "14");
- legenda: a mesma instrucao do pack com a LoRA (person_swap.qwen_prompt, ja com o gatilho).
Assim a LoRA aprende a pintar a Luna exatamente nessa situacao.

O rosto vai bem mais escondido que no pack (FACE_HIDE_*): aqui o rosto de
baixo JA E a Luna, e com o borrado leve a LoRA so aprenderia a copiar o rosto
de baixo (no pack ali esta o rosto da original). Com a LoRA, o pack usa o
mesmo valor (person_swap.LORA_FACE_HIDE).

Roda no pod com o venv do backend e o ComfyUI no ar:
  /workspace/lunapersona/.venv-persist/bin/python scripts/prep_qwen_lora_dataset.py DESTINO
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/workspace/lunapersona/backend")

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.services import person_swap as ps  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

COMFY = Path("/workspace/runpod-slim/ComfyUI")
# Fotos novas (geradas em 2026-10-02, personas/luna/lora_qwen_novas, sem
# tatuagem) + as antigas do treino do Chroma em que a tatuagem do antebraco
# nao aparece (a Luna nao tem mais tatuagem).
SOURCES = [Path("/workspace/lora_luna_src/novas")]
OLD = COMFY / "input" / "lora_luna"
OLD_KEEP = ("luna_02", "luna_03", "luna_07", "luna_08", "luna_11", "luna_12", "luna_13")
PERSONA = "luna_ref_full.png"  # foto principal da persona no input/ do ComfyUI
MAX_PIXELS = 2_400_000  # o mesmo teto do pack (frontend PackSwapPage.tsx)


# Fotos do pack de teste (casal) que o treino desenha a cada N passos para
# acompanhar a LoRA: so as entradas do Qwen (sem alvo).
SAMPLE_PHOTOS = ("hist_01.jpg", "hist_03.jpg", "hist_08.jpg")


async def main(dest: Path, samples: Path | None = None) -> None:
    c = ComfyUIClient("http://127.0.0.1:8188", generation_timeout=900)
    wm = WorkflowManager(Path("/workspace/lunapersona/workflows"))
    for sub in ("target", "control1", "control2"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    psize = ps.image_size((COMFY / "input" / PERSONA).read_bytes())
    paths = [p for src in SOURCES for p in sorted(src.glob("*.png")) + sorted(src.glob("*.jpg"))]
    paths += [OLD / f"{name}.jpg" for name in OLD_KEEP]
    if samples is not None:
        samples.mkdir(parents=True, exist_ok=True)
        paths = [COMFY / "input" / name for name in SAMPLE_PHOTOS]
    n = 0
    for path in paths:
        if samples is None and (dest / "target" / f"{path.stem}.txt").exists():
            n += 1  # ja feito numa rodada anterior
            continue
        name = path.name if samples is not None else f"lora_{path.stem}{path.suffix}"
        if samples is None:
            shutil.copy(path, COMFY / "input" / name)
        w, h = ps.image_size(path.read_bytes())
        s = min(1, (MAX_PIXELS / (w * h)) ** 0.5)
        W, H = round(w * s / 16) * 16, round(h * s / 16) * 16
        plan = await ps.plan_person_swap(c, name, W, H, PERSONA)
        if plan.face is None:
            print("sem rosto dela, fica de fora:", path.name)
            continue
        params = {
            "REFERENCE_IMAGE": name, "PERSONA_IMAGE": PERSONA, "WIDTH": W, "HEIGHT": H, "SEED": 1,
            "POINTS_POS": plan.points_pos, "POINTS_NEG": plan.points_neg,
            **ps.qwen_swap_params(plan, W, H, psize, lora=True),
        }
        g = wm.render("qwen-person-swap", params)
        g.pop("33", None)  # sem o Qwen: so as entradas dele
        for key, node in (("target", "14"), ("control1", "ff6"), ("control2", "13")):
            g[f"s_{key}"] = {"class_type": "SaveImage", "inputs": {"images": [node, 0], "filename_prefix": f"lora_ds/{key}_{path.stem}"}}
        entry = await c.wait_for_completion(await c.queue_prompt(g))
        saved = {}
        for node_id, out in entry.get("outputs", {}).items():
            for img in out.get("images", []):
                saved[node_id[2:]] = COMFY / "output" / img["subfolder"] / img["filename"]
        if len(saved) < 3:
            print("falhou:", path.name, entry.get("status"))
            continue
        if samples is not None:
            shutil.copy(saved["control1"], samples / f"c1_{path.stem}.png")
            shutil.copy(saved["control2"], samples / f"c2_{path.stem}.png")
            (samples / f"p_{path.stem}.txt").write_text(params["PROMPT"])
            print("amostra", path.name, flush=True)
            continue
        for key, src in saved.items():
            shutil.copy(src, dest / key / f"{path.stem}.png")
        (dest / "target" / f"{path.stem}.txt").write_text(params["PROMPT"])
        n += 1
        print("ok", path.name, params["PROMPT"][:90], flush=True)
    print(f"{n} pares em {dest}", flush=True)


if __name__ == "__main__":
    # uso: prep_qwen_lora_dataset.py DESTINO [--samples PASTA_DAS_AMOSTRAS]
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--samples" else None))
