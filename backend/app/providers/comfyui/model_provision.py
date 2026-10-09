"""Modelos da troca de pessoa (V2/V3) que NAO ficam no volume do estudio (volume quase cheio): na primeira troca
depois que o pod liga, baixa do Hugging Face OFICIAL para o disco temporario do pod e linka nas pastas do ComfyUI
(o mesmo que scripts/v2_engine/rodar_pod.sh fazia nos testes). Arquivo REAL ja presente no ComfyUI = usa ele.
Falhou o download = erro claro (sem fallback). Lista e pastas em config/engines_v2.json -> model_provision.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)
_LOCK = asyncio.Lock()


class ProvisionError(RuntimeError):
    pass


async def _download(url: str, target: Path) -> None:
    part = target.with_suffix(target.suffix + ".part")
    timeout = httpx.Timeout(60.0, read=120.0)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise ProvisionError(f"download {url} devolveu HTTP {resp.status_code}")
            with part.open("wb") as fh:
                async for chunk in resp.aiter_bytes(8 << 20):
                    fh.write(chunk)
    if part.stat().st_size < 1 << 20:
        part.unlink(missing_ok=True)
        raise ProvisionError(f"download {url} veio vazio")
    part.replace(target)


async def ensure_models(cfg: dict[str, Any] | None) -> list[str]:
    """Garante os arquivos; devolve os que foram baixados agora. Sem config (testes, local) = nada a fazer."""
    if not cfg or not cfg.get("enabled", True):
        return []
    models_dir = Path(cfg["comfyui_models_dir"])
    if not models_dir.is_dir():
        return []  # sem ComfyUI nesta maquina
    cache = Path(cfg.get("cache_dir", "/root/v2models"))
    fetched: list[str] = []
    async with _LOCK:
        missing = [f for f in cfg.get("files", []) if not (models_dir / f["dest"]).exists()]
        if not missing:
            return []
        cache.mkdir(parents=True, exist_ok=True)

        async def one(f: dict[str, str]) -> None:
            dest = models_dir / f["dest"]
            local = cache / Path(f["dest"]).name
            if not (local.exists() and local.stat().st_size > 1 << 20):
                logger.info("baixando %s", f["url"])
                await _download(f["url"], local)
                fetched.append(f["dest"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink():
                dest.unlink()  # link quebrado (pod religado: disco temporario zerado)
            os.symlink(local, dest)

        results = await asyncio.gather(*(one(f) for f in missing), return_exceptions=True)
        errors = [str(r) for r in results if isinstance(r, BaseException)]
        if errors:
            raise ProvisionError("; ".join(errors))
    return fetched
