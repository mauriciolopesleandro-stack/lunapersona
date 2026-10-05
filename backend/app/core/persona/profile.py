"""PersonaProfile: a persona como recurso do sistema, independente do modelo
que gera a imagem.

Quatro partes que nao se misturam:
- IdentityProfile: o que NAO muda (rosto, olhos, cabelo, corpo, idade,
  tracos marcantes).
- AppearanceProfile: o que e configuravel (roupa padrao, expressao,
  maquiagem, acessorios).
- StyleProfile: o tratamento da foto (fotografia, luz, composicao, realismo).
- PersonaConstraints: regras ("nao mudar a identidade") e o que evitar.

A cena nunca faz parte da persona: vem em cada pedido (core/generation).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.persona_manager.manager import FIXED_IDENTITY_FIELDS

APPEARANCE_FIELDS = ["roupa", "expressao", "maquiagem", "acessorios"]
STYLE_FIELDS = ["fotografia", "iluminacao", "composicao", "estetica", "tratamento", "realismo", "camera"]
# Estes ja existiam em identity.variable_defaults e o pipeline atual le de la.
LEGACY_APPEARANCE_FIELDS = {"roupa", "expressao"}
LEGACY_STYLE_FIELDS = {"iluminacao", "camera"}
SEXES = {"", "F", "M"}
MAX_TEXT = 500


class PersonaValidationError(ValueError):
    pass


@dataclass
class IdentityProfile:
    # Chaves de FIXED_IDENTITY_FIELDS (formato_rosto, olhos, nariz, boca...).
    traits: dict[str, str] = field(default_factory=dict)
    apparent_age: int | None = None
    # "F" / "M": a validacao recusa rosto do outro sexo. "" = nao confere.
    sex: str = ""
    # Tracos marcantes conferidos na imagem gerada (ex.: "choker").
    distinctive_keywords: list[str] = field(default_factory=list)

    def text(self) -> str:
        return ", ".join(v.strip() for k in FIXED_IDENTITY_FIELDS if (v := self.traits.get(k, "")) and v.strip())


@dataclass
class AppearanceProfile:
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class StyleProfile:
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class PersonaConstraints:
    rules: list[str] = field(default_factory=list)
    negative: list[str] = field(default_factory=list)


@dataclass
class PersonaValidationSettings:
    # Limiar da persona (0-1). None = o do sistema.
    threshold: float | None = None


@dataclass
class PersonaProfile:
    id: str
    name: str
    description: str = ""
    identity: IdentityProfile = field(default_factory=IdentityProfile)
    appearance: AppearanceProfile = field(default_factory=AppearanceProfile)
    style: StyleProfile = field(default_factory=StyleProfile)
    constraints: PersonaConstraints = field(default_factory=PersonaConstraints)
    validation: PersonaValidationSettings = field(default_factory=PersonaValidationSettings)
    active: bool = True
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    # Mecanismos de identidade do modelo (LoRA etc.) que o adapter pode usar;
    # so leitura aqui, vem do persona.json.
    identity_assets: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not self.name.strip() or len(self.name) > 80:
            raise PersonaValidationError("O nome e obrigatorio (ate 80 caracteres).")
        _check_keys("identidade", self.identity.traits, FIXED_IDENTITY_FIELDS)
        _check_keys("aparencia", self.appearance.values, APPEARANCE_FIELDS)
        _check_keys("estilo", self.style.values, STYLE_FIELDS)
        age = self.identity.apparent_age
        if age is not None and not 1 <= age <= 120:
            raise PersonaValidationError("Idade aparente fora de 1-120.")
        if self.identity.sex not in SEXES:
            raise PersonaValidationError("Sexo deve ser 'F', 'M' ou vazio.")
        threshold = self.validation.threshold
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise PersonaValidationError("O limiar deve ficar entre 0 e 1.")
        for items in (self.identity.distinctive_keywords, self.constraints.rules, self.constraints.negative):
            if any(len(i) > MAX_TEXT for i in items):
                raise PersonaValidationError(f"Texto com mais de {MAX_TEXT} caracteres.")


def _check_keys(section: str, values: dict[str, str], allowed: list[str]) -> None:
    unknown = set(values) - set(allowed)
    if unknown:
        raise PersonaValidationError(f"Campos desconhecidos em {section}: {', '.join(sorted(unknown))}.")
    if any(not isinstance(v, str) or len(v) > MAX_TEXT for v in values.values()):
        raise PersonaValidationError(f"Valor invalido em {section} (texto de ate {MAX_TEXT} caracteres).")
