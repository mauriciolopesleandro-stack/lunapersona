"""Tipos da validacao de identidade, usados pelo validador, pelo QualityGate,
pelo FailureAnalyzer, pelo RetryManager e pelo historico."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

ACCEPT = "ACCEPT"
REJECT = "REJECT"


@dataclass
class MetricScore:
    name: str
    weight: float
    # 0-1; None = nao medido (sem modelo para isso, ou rosto de perfil).
    score: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def measured(self) -> bool:
        return self.score is not None


@dataclass
class IdentityValidationResult:
    identity_score: float | None
    face_similarity: float | None
    facial_structure_score: float | None
    appearance_score: float | None
    distinctive_features_score: float | None
    # Parte do peso total que foi de fato medida (0-1).
    coverage: float
    face_found: bool
    metrics: dict[str, MetricScore] = field(default_factory=dict)
    # Preenchidos pelo QualityGate.
    status: str = REJECT
    reasons: list[str] = field(default_factory=list)
    # Problemas que reprovam mesmo com nota alta (sexo trocado, sem rosto).
    hard_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metrics"] = {k: {**asdict(m), "measured": m.measured} for k, m in self.metrics.items()}
        return data


@dataclass
class Failure:
    failure_type: str
    severity: str  # low | medium | high
    details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
