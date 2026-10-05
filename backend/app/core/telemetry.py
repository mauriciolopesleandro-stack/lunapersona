"""Telemetria de custo e tempo do Persona Engine V1.

Custo = preco/hora real da GPU x tempo total (cena + Face Lock + validacao,
inclusive troca de modelo). Sem preco do provedor o custo fica None (NOT
MEASURED): nunca supor o preco de outra GPU.

BATCH_MODE: todas as cenas no Z-Image, depois todos os Face Locks (benchmark
~103 s/imagem). SINGLE_REQUEST_MODE: cena e rosto alternados, com troca de
modelo na GPU de 24 GB (~223 s/imagem).
"""
from __future__ import annotations

from typing import Any, Protocol

BATCH_MODE = "BATCH_MODE"
SINGLE_REQUEST_MODE = "SINGLE_REQUEST_MODE"


class PriceProvider(Protocol):
    async def gpu_price(self) -> dict[str, Any]:
        """{'price_per_hour': float | None, 'gpu': str | None, 'source': str}"""


class CostEstimator:
    def __init__(self, provider: PriceProvider | None = None, fallback_price: float | None = None) -> None:
        self.provider = provider
        self.fallback_price = fallback_price
        self._cached: dict[str, Any] | None = None

    async def price(self) -> dict[str, Any]:
        if self._cached is None:
            info: dict[str, Any] = {"price_per_hour": None, "gpu": None, "source": "NOT MEASURED"}
            if self.provider is not None:
                try:
                    info = await self.provider.gpu_price()
                except Exception:  # preco e informativo: nunca derruba a geracao
                    info = {"price_per_hour": None, "gpu": None, "source": "erro ao consultar o provedor"}
            if info.get("price_per_hour") is None and self.fallback_price is not None:
                info = {**info, "price_per_hour": self.fallback_price, "source": "config (fallback)"}
            # So guarda o que deu certo: um erro passageiro nao trava o "NOT MEASURED".
            if info.get("price_per_hour") is not None:
                self._cached = info
            return info
        return self._cached

    async def metrics(self, stages: list[dict[str, Any]], validation_seconds: float, mode: str) -> dict[str, Any]:
        info = await self.price()
        stage_seconds = sum(s.get("seconds") or 0 for s in stages)
        total = round(stage_seconds + (validation_seconds or 0), 2)
        price = info.get("price_per_hour")
        gpus = [s.get("gpu") or {} for s in stages]
        vram = [g.get("vram_used_mb") for g in gpus if g.get("vram_used_mb") is not None]
        return {
            "execution_mode": mode,
            "stage_seconds": {s["stage"]: s.get("seconds") for s in stages},
            "generation_seconds": next((s.get("seconds") for s in stages if s["stage"] == "scene"), None),
            "postprocess_seconds": next((s.get("seconds") for s in stages if s["stage"] == "face_lock"), None),
            "validation_seconds": validation_seconds,
            "total_seconds": total,
            "model_switches": sum(1 for s in stages if s.get("model_switch")),
            # O ComfyUI nao separa carga e geracao: com troca de modelo a etapa inclui a carga.
            "model_load_seconds": "NOT MEASURED (incluido na etapa quando model_switch=true)",
            "gpu": next((g.get("name") for g in gpus if g.get("name")), info.get("gpu")),
            "vram_used_mb_max": max(vram) if vram else None,
            "vram_total_mb": next((g.get("vram_total_mb") for g in gpus if g.get("vram_total_mb")), None),
            "gpu_price_per_hour": price,
            "price_source": info.get("source"),
            "estimated_cost_usd": round(price * total / 3600, 5) if price is not None else None,
        }
