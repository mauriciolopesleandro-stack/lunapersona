"""Telemetria do replacement: um registro por etapa (passadas de rosto/corpo,
cabelo, integracao) com parametros, metricas, tempo, custo e rollback."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StageRecord:
    stage: str
    kind: str  # hair | face | body | integration | final
    mask: str | None = None
    strength: float | None = None
    denoise: float | None = None
    seed: int | None = None
    identity_adapter: str | None = None
    lora: bool | None = None
    image: str | None = None
    identity: float | None = None
    age: float | None = None
    pose: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    seconds: float | None = None
    gpu: str | None = None
    cost_usd: float | None = None
    accepted: bool = True
    rollback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cost(seconds: float | None, price_per_hour: float | None) -> float | None:
    if seconds is None or price_per_hour is None:
        return None
    return round(price_per_hour * seconds / 3600, 5)


__all__ = ["StageRecord", "cost"]
