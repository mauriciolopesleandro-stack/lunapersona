"""Telemetria por job da V2 (tudo que e preciso para reproduzir e custear o resultado)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class JobTelemetry:
    job_id: str
    persona_id: str
    engine: str
    mode: str
    model: str = ""
    checkpoint_hash: str = ""
    lora_hash: str = ""
    seed: int | None = None
    steps: int | None = None
    cfg: float | None = None
    resolution: list[int] | None = None
    controlnet_strength: dict[str, float] = field(default_factory=dict)
    reference_strength: float | None = None
    denoise: dict[str, float] = field(default_factory=dict)
    passes: list[dict[str, Any]] = field(default_factory=list)
    vram_peak_mb: float | None = None
    duration_s: float = 0.0
    gpu_seconds: float = 0.0
    estimated_cost_usd: float | None = None
    validation_result: str = ""
    failure_reason: str | None = None
    retry_count: int = 0
    retries: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    engine_version: str = ""
    workflow_versions: dict[str, str] = field(default_factory=dict)
    license: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)  # politica de atributos resolvida (spec 45) e a origem

    def add_pass(self, name: str, seconds: float, params: dict[str, Any], accepted: bool, reason: str | None = None) -> None:
        self.passes.append({"pass": name, "seconds": seconds, "params": params, "accepted": accepted, "reason": reason})
        self.gpu_seconds += seconds or 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["JobTelemetry"]
