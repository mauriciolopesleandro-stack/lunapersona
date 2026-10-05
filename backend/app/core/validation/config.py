"""Configuracao da validacao de identidade: pesos da nota composta,
calibragem e limiares. Nada disso fica fixo no validador - vem de
config/identity_validation.json (ou dos padroes abaixo)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.storage import read_json

# Modelo inicial da especificacao.
DEFAULT_WEIGHTS = {
    "face_similarity": 0.40,
    "facial_structure": 0.20,
    "hair": 0.10,
    "body": 0.10,
    "age": 0.10,
    "distinctive_features": 0.10,
}
APPEARANCE_METRICS = ("hair", "body", "age")


class InvalidValidationConfigError(ValueError):
    pass


@dataclass
class IdentityValidationConfig:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # Cosseno do ArcFace -> nota 0-1. Pessoas diferentes ficam ~0.0-0.13;
    # a mesma pessoa, ~0.35 para cima. Calibrar com fotos reais da persona.
    similarity_floor: float = 0.15
    similarity_ceiling: float = 0.60
    # Idade: ate age_tolerance anos de diferenca = nota cheia; zera em +age_span.
    age_tolerance: float = 5.0
    age_span: float = 10.0
    # Proporcoes do rosto so com o rosto de frente (|yaw| em distancias entre olhos).
    structure_max_yaw: float = 0.25
    # Desvio relativo de uma proporcao que zera a nota dela.
    structure_tolerance: float = 0.25
    # Parte minima do peso total que precisa ter sido medida.
    min_coverage: float = 0.5
    # Nota minima so do rosto (0-1), alem do limiar da nota composta.
    min_face_similarity: float = 0.5
    require_face: bool = True
    check_sex: bool = True
    # Limiar padrao do sistema e por provider (job > persona > provider > sistema).
    default_threshold: float = 0.90
    provider_thresholds: dict[str, float] = field(default_factory=dict)

    def validate(self) -> None:
        if any(w < 0 for w in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise InvalidValidationConfigError("Pesos devem ser >= 0 e somar mais que 0.")
        unknown = set(self.weights) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise InvalidValidationConfigError(f"Metricas desconhecidas: {', '.join(sorted(unknown))}.")
        if not 0 <= self.similarity_floor < self.similarity_ceiling <= 1:
            raise InvalidValidationConfigError("similarity_floor deve ser menor que similarity_ceiling (0-1).")
        for value in (self.default_threshold, self.min_coverage, self.min_face_similarity,
                      *self.provider_thresholds.values()):
            if not 0 <= value <= 1:
                raise InvalidValidationConfigError("Limiares devem ficar entre 0 e 1.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdentityValidationConfig":
        known = cls.__dataclass_fields__
        config = cls(**{k: v for k, v in data.items() if k in known})
        if "weights" in data:
            config.weights = {**{k: 0.0 for k in DEFAULT_WEIGHTS}, **data["weights"]}
        config.validate()
        return config

    @classmethod
    def load(cls, path: Path) -> "IdentityValidationConfig":
        return cls.from_dict(read_json(path, {}) or {})


class ThresholdPolicy:
    """Limiar de cada geracao: o do pedido vence o da persona, que vence o
    do provider, que vence o do sistema."""

    def __init__(self, config: IdentityValidationConfig) -> None:
        self.config = config

    def resolve(self, job: float | None, persona: float | None, provider: str) -> float:
        for value in (job, persona, self.config.provider_thresholds.get(provider)):
            if value is not None:
                return float(value)
        return self.config.default_threshold
