"""Auto-desligamento por inatividade.

Roda como uma tarefa de fundo dentro do proprio backend (que so existe
enquanto o pod esta ligado). Conta o tempo desde a ultima chamada a
/api/generate ou /api/chat e, se passar de `idle_shutdown_minutes` sem uso,
chama a API da RunPod para desligar o proprio pod.

Precisa de RUNPOD_API_KEY + RUNPOD_POD_ID configurados; se faltar qualquer
um dos dois, a tarefa fica parada (nao quebra o backend, so nao desliga
sozinho).
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger("idle_shutdown")

RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"

STOP_MUTATION = """
mutation StopPod($podId: String!) {
  podStop(input: { podId: $podId }) { id desiredStatus }
}
"""


class IdleShutdownTracker:
    def __init__(self, api_key: str, pod_id: str, idle_minutes: float, check_interval_seconds: float = 30.0):
        self.api_key = api_key
        self.pod_id = pod_id
        self.idle_seconds = idle_minutes * 60
        self.check_interval_seconds = check_interval_seconds
        self.last_activity = time.monotonic()
        self._task: asyncio.Task | None = None
        self._stopped_already = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.pod_id and self.idle_seconds > 0)

    def touch(self) -> None:
        self.last_activity = time.monotonic()
        self._stopped_already = False

    async def _stop_pod(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{RUNPOD_GRAPHQL_URL}?api_key={self.api_key}",
                    json={"query": STOP_MUTATION, "variables": {"podId": self.pod_id}},
                )
                res.raise_for_status()
                logger.info("Pod %s desligado por inatividade (%s).", self.pod_id, res.json())
        except Exception:
            logger.exception("Falha ao tentar desligar o pod %s por inatividade.", self.pod_id)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.check_interval_seconds)
            idle_for = time.monotonic() - self.last_activity
            if idle_for >= self.idle_seconds and not self._stopped_already:
                logger.info("Pod ocioso ha %.0fs (limite %.0fs) - desligando.", idle_for, self.idle_seconds)
                self._stopped_already = True
                await self._stop_pod()

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
