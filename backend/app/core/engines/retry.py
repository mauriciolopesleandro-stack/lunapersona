"""Retry ESPECIFICO por tipo de falha (nunca "sobe a LoRA"). Cada tentativa registra tipo, estrategia,
parametros e resultado. Numero de retries limitado pelo plano."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.engines.policies import StagePlan

# ordem de prioridade: corrige o mais grave primeiro (um ajuste por tentativa)
PRIORITY = ["background", "duplicate_persona", "original_residual", "identity", "tattoo", "pose", "body", "skin", "composition"]


@dataclass
class RetryStep:
    attempt: int
    failure_type: str
    strategy: str
    parameters: dict[str, Any]
    result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"attempt": self.attempt, "failure_type": self.failure_type, "strategy": self.strategy,
                "parameters": self.parameters, "result": self.result}


@dataclass
class RetryPolicyV2:
    history: list[RetryStep] = field(default_factory=list)

    def next_plan(self, plan: StagePlan, failures: list[str], attempt: int) -> tuple[StagePlan, RetryStep] | None:
        if attempt > plan.max_retries:
            return None
        alvo = next((f for f in PRIORITY if f in failures), None)
        if alvo is None:
            return None
        if alvo == "identity":
            # mais condicionamento de referencia + refino de rosto obrigatorio; LoRA fica fixa
            p = plan.with_(refinement=True, face_reference=True,
                           face_reference_strength=min(0.6, plan.face_reference_strength + 0.1),
                           face_denoise=min(0.55, plan.face_denoise + 0.08), face_refine_if_identity_below=1.01)
            strategy = "identidade baixa: condicionamento de identidade (mais referencia facial + refino obrigatorio; LoRA fixa)"
        elif alvo == "original_residual":
            p = plan.with_(identity_denoise=min(1.0, plan.identity_denoise + 0.05), refinement=True,
                           face_refine_if_identity_below=1.01)
            strategy = "rosto original sobrando: reconstrucao do rosto (identidade mais forte + refino obrigatorio)"
        elif alvo == "tattoo":
            p = plan.with_(tattoo_cleanup=True, tattoo_denoise=min(0.9, plan.tattoo_denoise + 0.05),
                           extra={**plan.extra, "tattoo_margin_boost": plan.extra.get("tattoo_margin_boost", 0) + 1})
            strategy = "marcas da pessoa original: mascara ampliada + reconstrucao de pele (RealVisXL + LoRA) + integracao de textura"
        elif alvo == "pose":
            p = plan.with_(pose=True, pose_strength=min(1.0, plan.pose_strength + 0.15), control_end=min(1.0, plan.control_end + 0.1))
            strategy = "ControlNet de pose mais forte"
        elif alvo == "background" or alvo == "composition":
            p = plan.with_(segmentation=True, identity_denoise=max(0.7, plan.identity_denoise - 0.1),
                           extra={**plan.extra, "mask_shrink": plan.extra.get("mask_shrink", 0) + 1})
            strategy = "mascara mais justa + menos denoise na area"
        elif alvo == "body":
            p = plan.with_(body_refinement=True, depth=True)
            strategy = "refino de corpo + profundidade"
        elif alvo == "skin":
            p = plan.with_(photographic_integration=True, hires=True, extra={**plan.extra, "skin_refine": True})
            strategy = "pele: refino de pele (hi-res do rosto) + integracao fotografica"
        else:  # duplicate_persona
            p = plan.with_(identity_denoise=max(0.7, plan.identity_denoise - 0.1), extra={**plan.extra, "mask_shrink": 1})
            strategy = "regiao mais justa (evita segunda Luna)"
        changed = {k: v for k, v in p.to_dict().items() if plan.to_dict().get(k) != v}
        step = RetryStep(attempt, alvo, strategy, changed)
        self.history.append(step)
        return p, step


__all__ = ["PRIORITY", "RetryPolicyV2", "RetryStep"]
