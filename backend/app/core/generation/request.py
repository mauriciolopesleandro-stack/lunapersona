"""GenerationRequest: o pedido do Persona Engine. Separa PERSONA (pelo id),
CENA (scene_prompt), ESTILO (style_overrides) e os parametros; as
restricoes vem da persona."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.persona.profile import STYLE_FIELDS
from app.providers.base import GenerationParameters

DEFAULT_MAX_ATTEMPTS = 4
# Teto fixo: nem um pedido com max_attempts enorme prende a GPU para sempre.
MAX_ATTEMPTS_LIMIT = 8
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
    validation_threshold: float | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def validate(self) -> None:
        if not self.scene_prompt.strip() or len(self.scene_prompt) > MAX_SCENE:
            raise InvalidGenerationRequestError(f"Descreva a cena (ate {MAX_SCENE} caracteres).")
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_LIMIT:
            raise InvalidGenerationRequestError(f"Tentativas devem ficar entre 1 e {MAX_ATTEMPTS_LIMIT}.")
        if self.validation_threshold is not None and not 0.0 <= self.validation_threshold <= 1.0:
            raise InvalidGenerationRequestError("O limiar deve ficar entre 0 e 1.")
        unknown = set(self.style_overrides) - set(STYLE_FIELDS)
        if unknown:
            raise InvalidGenerationRequestError(f"Campos de estilo desconhecidos: {', '.join(sorted(unknown))}.")
