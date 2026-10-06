"""Trava de custo dos experimentos pagos (regra da V2: nada grande sem amostra
pequena antes, e nada acima do limite sem autorizacao do usuario).

A estimativa e explicita (imagens x segundos por imagem + overhead de ligar o
pod e baixar modelos) e vai junto no relatorio; acima do limite o experimento
nao roda e a mensagem diz o que seria preciso autorizar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class BudgetExceeded(RuntimeError):
    """Estimativa acima do limite sem autorizacao."""


@dataclass(frozen=True)
class ExperimentPlan:
    name: str
    objective: str
    hypothesis: str
    images: int
    seconds_per_image: float
    overhead_seconds: float
    price_per_hour: float

    @property
    def total_seconds(self) -> float:
        return round(self.images * self.seconds_per_image + self.overhead_seconds, 1)

    @property
    def estimated_cost_usd(self) -> float:
        return round(self.price_per_hour * self.total_seconds / 3600, 4)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total_seconds": self.total_seconds, "estimated_cost_usd": self.estimated_cost_usd}

    def summary(self) -> str:
        return (f"{self.name}: {self.images} imagens, ~{self.total_seconds / 60:.1f} min, ~US$ {self.estimated_cost_usd:.3f}. "
                f"Objetivo: {self.objective}. Hipotese: {self.hypothesis}.")


class BudgetGuard:
    def __init__(self, limit_usd: float) -> None:
        if limit_usd <= 0:
            raise ValueError("O limite de custo precisa ser positivo.")
        self.limit_usd = limit_usd

    def check(self, plan: ExperimentPlan, authorized_usd: float | None = None) -> dict[str, Any]:
        """Passa se a estimativa cabe no limite, ou no valor que o usuario autorizou."""
        allowed = max(self.limit_usd, authorized_usd or 0.0)
        if plan.images <= 0:
            raise BudgetExceeded("Experimento sem imagens.")
        if plan.estimated_cost_usd > allowed:
            raise BudgetExceeded(
                f"Estimativa acima do limite (US$ {allowed:.2f}). PARADO, aguardando autorizacao. {plan.summary()}")
        return {**plan.to_dict(), "limit_usd": self.limit_usd, "authorized_usd": authorized_usd, "allowed_usd": allowed}


__all__ = ["BudgetExceeded", "BudgetGuard", "ExperimentPlan"]
