"""Politicas da V2 (FAST / QUALITY / MAX_QUALITY) e a escada do benchmark progressivo (A..H).

Um StagePlan diz QUAIS etapas rodam e com que parametros. Nada aqui conhece provider ou modelo.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

FAST, QUALITY, MAX_QUALITY = "FAST", "QUALITY", "MAX_QUALITY"


@dataclass(frozen=True)
class StagePlan:
    name: str
    # etapas (cada uma liga uma capacidade; o benchmark A..H liga uma por vez)
    face_reference: bool = True        # referencia facial (adaptador de identidade) no refino de rosto
    pose: bool = True                  # DWPose -> ControlNet (pose da foto e o alvo)
    depth: bool = True                 # profundidade da foto (perspectiva/escala)
    segmentation: bool = True          # mascara real da pessoa (sem ela: elipse grosseira da cabeca)
    refinement: bool = True            # refino de rosto (passe 2) + correcoes localizadas
    body_refinement: bool = False      # refino de corpo (maos/bracos/transicoes) - opcional
    tattoo_cleanup: bool = True        # tatuagem/residuo da pessoa original
    photographic_integration: bool = True  # luz/cor/grao/borda medidos na propria foto
    hires: bool = False                # refino em alta resolucao do rosto
    # parametros
    identity_denoise: float = 0.9
    pose_strength: float = 0.8
    depth_strength: float = 0.5
    control_end: float = 0.8
    face_denoise: float = 0.4
    face_reference_strength: float = 0.5
    body_denoise: float = 0.35
    # spec 45.5: a pele das marcas e RECONSTRUIDA pelo modelo (denoise alto + LoRA), nao pintada;
    # anatomia segura por profundidade da pele limpa + pose; depois refino local leve e integracao de textura
    tattoo_denoise: float = 0.8
    tattoo_depth_strength: float = 0.8
    tattoo_pose_strength: float = 0.6
    skin_refine_denoise: float = 0.3
    hires_side: int = 1536
    steps: int = 30
    cfg: float = 5.0
    work_side: int = 1024
    face_refine_if_identity_below: float = 0.72
    max_retries: int = 2
    extra: dict[str, Any] = field(default_factory=dict)

    def with_(self, **kw) -> "StagePlan":
        return replace(self, **kw)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


POLICIES: dict[str, StagePlan] = {
    FAST: StagePlan(FAST, depth=False, refinement=True, steps=25, max_retries=1, face_refine_if_identity_below=0.68),
    QUALITY: StagePlan(QUALITY),
    MAX_QUALITY: StagePlan(MAX_QUALITY, body_refinement=True, hires=True, steps=40, max_retries=3,
                           face_refine_if_identity_below=1.01),  # MAX: refino de rosto sempre
}

# Escada do benchmark progressivo: cada letra liga UMA etapa a mais (para saber o que ajuda/piora).
_BASE = StagePlan("A", face_reference=False, pose=False, depth=False, segmentation=False, refinement=False,
                  tattoo_cleanup=False, photographic_integration=False)
LADDER: dict[str, StagePlan] = {
    "A": _BASE,                                                  # RealVisXL + LoRA
    "B": _BASE.with_(name="B", refinement=True, face_reference=True),  # + referencia facial (refino com referencia)
    "C": None, "D": None, "E": None, "F": None, "G": None, "H": None,  # preenchidos abaixo
}
LADDER["C"] = LADDER["B"].with_(name="C", pose=True)                  # + DWPose
LADDER["D"] = LADDER["C"].with_(name="D", depth=True)                 # + depth
LADDER["E"] = LADDER["D"].with_(name="E", segmentation=True)          # + segmentacao
LADDER["F"] = LADDER["E"].with_(name="F", body_refinement=True, hires=True)  # + refinement (corpo + hi-res)
LADDER["G"] = LADDER["F"].with_(name="G", tattoo_cleanup=True)        # + tattoo cleanup
LADDER["H"] = LADDER["G"].with_(name="H", photographic_integration=True)  # + integracao fotografica


def plan_for(mode: str, overrides: dict[str, Any] | None = None) -> StagePlan:
    key = mode.upper()
    base = POLICIES.get(key) or LADDER.get(key)
    if base is None:
        raise ValueError(f"modo desconhecido: {mode}")
    return base.with_(**(overrides or {}))


__all__ = ["FAST", "LADDER", "MAX_QUALITY", "POLICIES", "QUALITY", "StagePlan", "plan_for"]
