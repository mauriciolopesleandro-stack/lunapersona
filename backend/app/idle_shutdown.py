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


# Sincronizar os volumes antes de desligar pode demorar se houver modelo
# novo para copiar - melhor atrasar o desligamento do que perder a copia.
PRE_STOP_SYNC_TIMEOUT_SECONDS = 3600


class IdleShutdownTracker:
    def __init__(
        self,
        api_key: str,
        pod_id: str,
        idle_minutes: float,
        check_interval_seconds: float = 30.0,
        pre_stop_command: list[str] | None = None,
    ):
        self.api_key = api_key
        self.pod_id = pod_id
        self.idle_seconds = idle_minutes * 60
        self.check_interval_seconds = check_interval_seconds
        # Roda antes de desligar (ex: scripts/volume_sync.py all), para o
        # outro volume ficar com tudo que mudou neste pod.
        self.pre_stop_command = pre_stop_command
        self.last_activity = time.monotonic()
        self._task: asyncio.Task | None = None
        self._stopped_already = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.pod_id and self.idle_seconds > 0)

    def touch(self) -> None:
        self.last_activity = time.monotonic()
        self._stopped_already = False

    async def _run_pre_stop(self) -> None:
        if not self.pre_stop_command:
            return
        try:
            proc = await asyncio.create_subprocess_exec(*self.pre_stop_command)
            await asyncio.wait_for(proc.wait(), timeout=PRE_STOP_SYNC_TIMEOUT_SECONDS)
            logger.info("Comando antes de desligar terminou (codigo %s).", proc.returncode)
        except Exception:
            logger.exception("Falha no comando antes de desligar - desligando mesmo assim.")

    async def _stop_pod(self) -> None:
        await self._run_pre_stop()
        if time.monotonic() - self.last_activity < self.idle_seconds:
            # Alguem voltou a usar o estudio enquanto sincronizava.
            logger.info("Atividade durante a sincronizacao - cancelando o desligamento.")
            self._stopped_already = False
            return
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
