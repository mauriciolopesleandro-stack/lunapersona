"""RunPodProvider: preco/hora e GPU reais do pod em que o backend roda.

Usa RUNPOD_API_KEY e RUNPOD_POD_ID (o pod recebe os dois da Vercel). Fora do
pod (PC proprio) devolve preco None: o custo fica NOT MEASURED. A chave vai
so no cabecalho e nunca aparece em log nem em resposta.
"""
from __future__ import annotations

from typing import Any

import httpx

GRAPHQL_URL = "https://api.runpod.io/graphql"
QUERY = "query Pod($podId: String!) { pod(input: {podId: $podId}) { costPerHr machine { gpuDisplayName } } }"


class RunPodProvider:
    def __init__(self, api_key: str, pod_id: str, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.pod_id = pod_id
        self.timeout = timeout

    async def gpu_price(self) -> dict[str, Any]:
        if not (self.api_key and self.pod_id):
            return {"price_per_hour": None, "gpu": None, "source": "NOT MEASURED (fora do RunPod)"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                GRAPHQL_URL,
                json={"query": QUERY, "variables": {"podId": self.pod_id}},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
        pod = (resp.json().get("data") or {}).get("pod") or {}
        price = pod.get("costPerHr")
        return {
            "price_per_hour": float(price) if price is not None else None,
            "gpu": (pod.get("machine") or {}).get("gpuDisplayName"),
            "source": "runpod" if price is not None else "NOT MEASURED (RunPod sem preco)",
        }
