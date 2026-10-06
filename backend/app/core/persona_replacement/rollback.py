"""Checkpoints e rollback do replacement: cada etapa parte do MELHOR estado aceito;
se piorar o que importa, o checkpoint anterior continua valendo.

Uma etapa e recusada se: a identidade cai alem do limite (etapas de rosto: nao
pode cair; integracao: quase nada), a pose muda, aparece outra persona/rosto, o
fundo muda ou a roupa muda alem do limite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Checkpoint:
    name: str
    image: str
    pixels: np.ndarray
    measure: Any  # app.core.generation.multipass.Measure
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckpointStore:
    items: list[Checkpoint] = field(default_factory=list)
    current: Checkpoint | None = None

    def add(self, cp: Checkpoint, accept: bool) -> None:
        self.items.append(cp)
        if accept or self.current is None:
            self.current = cp

    def names(self) -> list[str]:
        return [c.name for c in self.items]


def stage_reasons(kind: str, before: Any, after: Any, pixel: dict[str, Any], checks: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    drop_limit = float(checks["max_identity_drop"]) if kind != "integration" else float(checks.get("max_identity_drop_integration", 0.01))
    if after.face is None:
        reasons.append("rosto sumiu")
    elif before.face is not None and kind != "identity" and before.face - after.face > drop_limit:
        reasons.append(f"identidade caiu {before.face - after.face:.3f} (> {drop_limit})")
    elif before.face is not None and kind == "identity" and after.face < before.face:
        reasons.append(f"a passada de identidade nao aumentou a semelhanca ({before.face:.3f} -> {after.face:.3f})")
    if after.pose is not None and after.pose > float(checks["max_pose_distance"]):
        reasons.append(f"pose mudou ({after.pose:.3f})")
    if after.persona_instances > max(1, before.persona_instances) or after.faces > before.faces:
        reasons.append("apareceu outra pessoa/rosto")
    bg = pixel.get("background_changed")
    if bg is not None and bg > float(checks["max_background_changed"]):
        reasons.append(f"fundo mudou ({bg:.4f})")
    cl = pixel.get("clothing_changed")
    if cl is not None and cl > float(checks["max_clothing_changed"]):
        reasons.append(f"roupa mudou ({cl:.4f})")
    return reasons


__all__ = ["Checkpoint", "CheckpointStore", "stage_reasons"]
