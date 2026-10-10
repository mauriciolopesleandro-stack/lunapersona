"""Recorte da pessoa (U2Net human seg, ONNX, CPU) - o mesmo modelo que a fase 0 usou via rembg (u2net_human_seg).
Serve so para achar o cabelo ORIGINAL inteiro na composicao. Pre-processamento igual ao do rembg (U2netSession):
320x320, divide pelo maximo, normaliza ImageNet; saida min-max e volta ao tamanho da foto."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
from PIL import Image

_SESS: dict[str, object] = {}
_LOCK = asyncio.Lock()


def _session(model_path: Path):
    import onnxruntime as ort

    key = str(model_path)
    if key not in _SESS:
        _SESS[key] = ort.InferenceSession(key, providers=["CPUExecutionProvider"])
    return _SESS[key]


def matte_rgb(rgb: np.ndarray, model_path: Path) -> np.ndarray:
    """rgb uint8 (H, W, 3) -> mascara bool da pessoa (H, W)."""
    sess = _session(model_path)
    img = Image.fromarray(rgb).convert("RGB")
    im = np.asarray(img.resize((320, 320), Image.LANCZOS)).astype(np.float32)
    im = im / max(float(im.max()), 1e-6)
    mean, std = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)
    x = ((im - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][:, 0, :, :]
    mi, ma = float(out.min()), float(out.max())
    pred = (out - mi) / max(ma - mi, 1e-6)
    m = Image.fromarray((np.squeeze(pred) * 255).astype(np.uint8)).resize(img.size, Image.LANCZOS)
    return np.asarray(m) > 127


async def ensure_model(path: Path, url: str) -> Path:
    """Baixa o modelo uma vez (release oficial do rembg) para o disco do pod."""
    from app.providers.comfyui.model_provision import _download

    async with _LOCK:
        if not (path.exists() and path.stat().st_size > 1 << 20):
            path.parent.mkdir(parents=True, exist_ok=True)
            await _download(url, path)
    return path
