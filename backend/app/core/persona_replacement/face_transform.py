"""Transformacao do ROSTO (nao e head swap): a foto da posicao, escala, perspectiva,
luz, expressao e orientacao; a persona da a identidade.

  face_pass_1  identidade (InstantID + master_face) no rosto inteiro
  face_pass_2  refinamento no miolo (olhos, nariz, boca), mascara menor
  face_pass_3  INTEGRACAO: faixa rosto/pescoco/linha do cabelo, liberdade minima,
               sem adaptador e sem LoRA - "parecer que sempre esteve na foto"
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.core.persona_replacement.contracts import StageSpec, TransformRequest
from app.core.persona_replacement.segmentation import MaskSet

MIN_AREA = 200  # pixels: menos que isso nao ha o que transformar


def stage_mask(stage: StageSpec, masks: MaskSet) -> np.ndarray:
    m = np.clip(masks.get(stage.mask) - masks.protect, 0, 1)
    if stage.kind == "body":
        m = np.clip(m - masks.clothing, 0, 1)  # a roupa nunca entra
    return m


def build_request(stage: StageSpec, masks: MaskSet, image: str, negative: str, seed: int, master: Any) -> TransformRequest | None:
    mask = stage_mask(stage, masks)
    if float((mask > 0.5).sum()) < MIN_AREA:
        return None
    return TransformRequest(image=image, mask=mask, prompt=stage.prompt, negative=negative, strength=stage.strength,
                            denoise=stage.denoise, seed=seed, name=stage.name, use_lora=stage.lora,
                            identity_adapter=stage.identity_adapter, adapter_weight=stage.adapter_weight,
                            reference=master if stage.identity_adapter else None)


def stage_kind(stage: StageSpec) -> str:
    """Como a etapa e julgada no rollback."""
    if stage.identity_adapter:
        return "identity"
    if stage.name == "face_pass_3":
        return "integration"
    return stage.kind


__all__ = ["MIN_AREA", "build_request", "stage_kind", "stage_mask"]
