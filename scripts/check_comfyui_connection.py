"""Diagnostico standalone: testa a conexao com o ComfyUI e confirma se os
nos usados pelo workflow flux-kontext-txt2img existem na instancia atual.

Uso:
    python scripts/check_comfyui_connection.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_NODE_CLASSES = [
    "UNETLoader",
    "DualCLIPLoader",
    "VAELoader",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "FluxGuidance",
    "RandomNoise",
    "KSamplerSelect",
    "BasicScheduler",
    "BasicGuider",
    "SamplerCustomAdvanced",
    "VAEDecode",
    "SaveImage",
]


def load_env() -> dict[str, str]:
    env_path = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def get_json(url: str, timeout: float = 10.0):
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def main() -> int:
    env = load_env()
    base_url = os.environ.get("COMFYUI_URL") or env.get("COMFYUI_URL")
    if not base_url:
        print("ERRO: COMFYUI_URL nao definido (nem em .env nem no ambiente).")
        return 1
    base_url = base_url.rstrip("/")

    print(f"Testando conexao com: {base_url}")
    try:
        status, stats = get_json(f"{base_url}/system_stats")
    except error.HTTPError as exc:
        print(f"FALHOU: HTTP {exc.code} em /system_stats.")
        print("Isso geralmente indica que o pod da RunPod esta parado ou o")
        print("servico ComfyUI nao esta escutando nessa porta.")
        return 1
    except error.URLError as exc:
        print(f"FALHOU: erro de rede ao conectar - {exc.reason}")
        return 1

    print(f"OK: /system_stats respondeu {status}.")
    print(json.dumps(stats, indent=2)[:800])

    print("\nBuscando /object_info para validar nos do workflow flux-kontext-txt2img...")
    try:
        _, object_info = get_json(f"{base_url}/object_info", timeout=30.0)
    except (error.HTTPError, error.URLError) as exc:
        print(f"FALHOU ao buscar /object_info: {exc}")
        return 1

    missing = [cls for cls in REQUIRED_NODE_CLASSES if cls not in object_info]
    if missing:
        print(f"ATENCAO: nos ausentes nesta instancia do ComfyUI: {missing}")
        print("O workflow workflows/flux-kontext-txt2img.json precisa ser ajustado.")
    else:
        print("OK: todos os nos esperados pelo workflow de teste estao disponiveis.")

    unet_loader = object_info.get("UNETLoader", {})
    unet_options = (
        unet_loader.get("input", {}).get("required", {}).get("unet_name", [[]])[0]
    )
    print(f"\nArquivos UNET visiveis pelo ComfyUI: {unet_options}")

    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
