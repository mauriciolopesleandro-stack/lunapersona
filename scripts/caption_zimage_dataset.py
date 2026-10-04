"""Legendas do dataset da LoRA da Luna no Z-Image Turbo (scripts/train_zimage_lora.sh).

As 45 fotos da Luna do treino do Qwen (/workspace/lora_qwen_luna/target, as
fotos que o usuario gerou no ChatGPT) tinham legenda de edicao ("troque a
mulher do retangulo..."), que nao serve para texto-para-imagem. Aqui o
Florence-2 descreve cada foto, sem os tracos da pessoa (clean_reference_caption:
cabelo, olhos, pele, corpo saem - eles ficam na palavra-gatilho), e a legenda
vira "lunavox, a woman, <roupa, pose, cenario>".

Roda no pod com o venv do backend (a ComfyUI precisa estar no ar):
  cd /workspace/lunapersona/backend && ../.venv-persist/bin/python3 ../scripts/caption_zimage_dataset.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services.reference_caption import clean_reference_caption  # noqa: E402
from app.services.scene_describer import describe_image  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

SRC = Path("/workspace/lora_qwen_luna/target")
DST = Path("/root/zimage_dataset")
COMFY_INPUT = Path("/workspace/runpod-slim/ComfyUI/input")
TRIGGER = "lunavox"


async def main() -> None:
    settings = get_settings()
    comfy = ComfyUIClient(
        base_url="http://127.0.0.1:8188", api_key=settings.comfyui_api_key,
        connect_timeout=10.0, generation_timeout=300.0,
    )
    workflows = WorkflowManager(settings.workflows_dir)
    DST.mkdir(parents=True, exist_ok=True)
    for image in sorted(SRC.iterdir()):
        if image.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        name = f"zimage_ds_{image.name}"
        shutil.copy(image, COMFY_INPUT / name)
        scene = clean_reference_caption(await describe_image(comfy, workflows, name))
        caption = f"{TRIGGER}, a woman, {scene}" if scene else f"{TRIGGER}, a woman"
        shutil.copy(image, DST / image.name)
        (DST / f"{image.stem}.txt").write_text(caption, encoding="utf-8")
        print(f"{image.name} | {caption[:180]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
