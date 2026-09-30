"""Gera falas com o VoxCPM2 (roda no venv de scripts/setup_voxcpm.sh).

Recebe um JSON com uma lista de falas e carrega o modelo uma vez so:

  python voxcpm_tts.py jobs.json

Cada item: {"text", "out"} e opcionalmente "description" (voz criada por
descricao), "ref_wav" + "ref_text" (clonar a voz de referencia com o
texto falado nela), "seed", "cfg", "steps".
Imprime uma linha JSON por fala gerada: {"out", "seconds"}.
"""
from __future__ import annotations

import json
import os
import sys

import soundfile as sf
import torch
from voxcpm import VoxCPM

MODEL_DIR = os.environ.get("VOXCPM_MODEL", "/root/voxcpm/VoxCPM2")


def main() -> int:
    jobs = json.load(open(sys.argv[1], encoding="utf-8"))
    model = VoxCPM.from_pretrained(MODEL_DIR, load_denoiser=False)
    rate = model.tts_model.sample_rate
    for job in jobs:
        text = job["text"]
        kwargs = {
            "cfg_value": float(job.get("cfg", 2.0)),
            "inference_timesteps": int(job.get("steps", 10)),
        }
        # A versao do pip nao aceita seed em generate(): fixa o gerador global.
        torch.manual_seed(int(job.get("seed", 0)))
        if job.get("ref_wav"):
            # "Ultimate cloning": referencia + transcricao copia timbre, ritmo e sotaque.
            kwargs["reference_wav_path"] = job["ref_wav"]
            if job.get("ref_text"):
                kwargs["prompt_wav_path"] = job["ref_wav"]
                kwargs["prompt_text"] = job["ref_text"]
        elif job.get("description"):
            text = f"({job['description']}){text}"
        wav = model.generate(text=text, **kwargs)
        os.makedirs(os.path.dirname(job["out"]) or ".", exist_ok=True)
        sf.write(job["out"], wav, rate)
        print(json.dumps({"out": job["out"], "seconds": round(len(wav) / rate, 2)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
