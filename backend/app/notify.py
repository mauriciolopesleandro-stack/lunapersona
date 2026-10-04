"""Aviso no celular (Telegram) quando uma tarefa longa termina.

O token do bot fica so na Vercel (o usuario cola em "Meu perfil"): o backend
do pod nao tem login, entao nada aqui guarda ou devolve segredo. Ao terminar,
o pod manda o aviso para /api/notify do site, assinado com HMAC-SHA256 usando
uma chave derivada da RUNPOD_API_KEY (os dois lados ja tem ela) - e o site
repassa para o Telegram. Sem a chave, ou se o site nao responder, nao avisa
e segue normal: o aviso nunca atrapalha a geracao.

A foto vai como link do /view da ComfyUI em JPEG (preview=jpeg) - o PNG
grande passava do limite de 5 MB do Telegram para foto por link.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time

import httpx

from app.config import get_settings

# Tarefa mais curta que isso nao avisa (a pessoa ainda esta olhando a tela).
MIN_SECONDS = 20.0
TIMEOUT = 15.0

log = logging.getLogger(__name__)
_pending: set[asyncio.Task] = set()


def _key(api_key: str) -> bytes:
    return hashlib.sha256(f"luna-notify:{api_key}".encode()).digest()


def photo_link(url: str) -> str:
    return f"{url}&preview=jpeg;88" if "/view?" in url else url


def took(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


async def send(text: str, photo: str | None = None, video: str | None = None) -> bool:
    settings = get_settings()
    if not settings.runpod_api_key or not settings.notify_url:
        return False
    body = json.dumps({"ts": int(time.time()), "text": text, "photo": photo, "video": video}).encode()
    signature = hmac.new(_key(settings.runpod_api_key), body, hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                settings.notify_url, content=body,
                headers={"Content-Type": "application/json", "X-Luna-Signature": signature},
            )
        if resp.status_code >= 400:
            log.info("Aviso no celular nao enviado (%s): %s", resp.status_code, resp.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.info("Aviso no celular nao enviado: %s", exc)
        return False


def fire(text: str, started: float | None = None, photo: str | None = None, video: str | None = None) -> None:
    """Avisa em segundo plano (nao espera nem falha). started = monotonic() do
    inicio da tarefa: entra no texto e tarefas rapidas nao avisam."""
    if started is not None:
        elapsed = time.monotonic() - started
        if elapsed < MIN_SECONDS:
            return
        text = f"{text} (levou {took(elapsed)})"
    try:
        task = asyncio.get_running_loop().create_task(send(text, photo, video))
    except RuntimeError:
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)
