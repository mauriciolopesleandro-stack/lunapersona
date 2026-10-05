"""GenerationRequest do Persona Engine V1.

Modos:
  FREE             persona -> Z-Image + LoRA -> Face Lock -> validacao
  POSE_CONTROLLED  o mesmo, com DWPose + ControlNet a partir de uma imagem de pose
                   (o texto da cena deve descrever a mesma pose)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.persona.profile import STYLE_FIELDS
from app.providers.base import GenerationParameters

MODE_FREE = "FREE"
MODE_POSE = "POSE_CONTROLLED"
MODES = (MODE_FREE, MODE_POSE)
DEFAULT_MAX_ATTEMPTS = 3
# Teto fixo: nenhum pedido prende a GPU indefinidamente.
MAX_ATTEMPTS_LIMIT = 6
MAX_SCENE = 2000


class InvalidGenerationRequestError(ValueError):
    pass


@dataclass
class GenerationRequest:
    persona_id: str
    scene_prompt: str
    provider: str | None = None
    style_overrides: dict[str, str] = field(default_factory=dict)
    generation_parameters: GenerationParameters = field(default_factory=GenerationParameters)
    mode: str = MODE_FREE
    # Imagem de pose ja enviada ao provider (POST /api/generate/reference).
    pose_reference: str | None = None
    # Limiar de rosto so deste pedido; None = o da Persona Sheet.
    validation_threshold: float | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    # Uma instancia da persona por imagem (figurantes e outras pessoas sao permitidos).
    single_subject: bool = True

    def validate(self) -> None:
        if not self.scene_prompt.strip() or len(self.scene_prompt) > MAX_SCENE:
            raise InvalidGenerationRequestError(f"Descreva a cena (ate {MAX_SCENE} caracteres).")
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_LIMIT:
            raise InvalidGenerationRequestError(f"Tentativas devem ficar entre 1 e {MAX_ATTEMPTS_LIMIT}.")
        if self.mode not in MODES:
            raise InvalidGenerationRequestError(f"Modo '{self.mode}' invalido. Use {' ou '.join(MODES)}.")
        if self.mode == MODE_POSE and not self.pose_reference:
            raise InvalidGenerationRequestError("O modo POSE_CONTROLLED precisa da imagem de pose.")
        if self.mode == MODE_FREE and self.pose_reference:
            raise InvalidGenerationRequestError("Imagem de pose so no modo POSE_CONTROLLED.")
        if self.validation_threshold is not None and not 0.0 < self.validation_threshold < 1.0:
            raise InvalidGenerationRequestError("O limiar deve ficar entre 0 e 1.")
        unknown = set(self.style_overrides) - set(STYLE_FIELDS)
        if unknown:
            raise InvalidGenerationRequestError(f"Campos de estilo desconhecidos: {', '.join(sorted(unknown))}.")
