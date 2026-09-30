"""Tarefas longas (voz, video) que o site inicia e depois consulta.

O proxy da RunPod corta respostas com mais de ~100 s: a rota devolve um
job_id na hora e o site pergunta ate o resultado ficar pronto. Tudo em
memoria - um processo por pod; reiniciar o backend perde os jobs.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

from app.clients.comfyui_client import ComfyUIError


class JobRegistry:
    def __init__(self, max_jobs: int = 30) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._max_jobs = max_jobs

    def start(self, work: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, str]:
        while len(self._jobs) >= self._max_jobs:
            self._jobs.pop(next(iter(self._jobs)))
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"status": "running"}
        task = asyncio.create_task(self._run(job_id, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"job_id": job_id, "status": "running"}

    async def _run(self, job_id: str, work: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        job = self._jobs[job_id]
        try:
            job["result"] = await work()
            job["status"] = "done"
        except Exception as exc:
            job["status"] = "error"
            job["error_status"] = 502 if isinstance(exc, ComfyUIError) else 500
            job["detail"] = str(exc) or exc.__class__.__name__

    def get(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Tarefa nao encontrada (o backend pode ter reiniciado).")
        if job["status"] == "error":
            return {"status": "error", "error_status": job["error_status"], "detail": job["detail"]}
        if job["status"] == "done":
            return {"status": "done", "result": job["result"]}
        return {"status": "running"}
