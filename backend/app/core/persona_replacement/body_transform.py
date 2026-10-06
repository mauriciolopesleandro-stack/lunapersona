"""Transformacao do CORPO: so na PELE do corpo (bracos, pernas, colo), nunca na
roupa - a roupa pertence a foto. O que a roupa cobre nao e reconstruido.

  body_pass_1  estrutura (silhueta/proporcao visiveis, sem tatuagem), forca media
  body_pass_2  integracao de detalhes (maos, bracos, transicoes), forca baixa

As etapas usam o mesmo contrato das do rosto (face_transform.build_request);
aqui fica so a regra do que e corpo.
"""
from __future__ import annotations

from app.core.persona_replacement.contracts import StageSpec


def is_body_stage(stage: StageSpec) -> bool:
    return stage.kind == "body" and stage.mask == "body_skin"


__all__ = ["is_body_stage"]
