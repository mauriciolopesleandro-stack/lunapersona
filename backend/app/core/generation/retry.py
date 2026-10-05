"""RetryManager: o que mudar na proxima tentativa, conforme o motivo da
reprovacao. Nunca repete o mesmo pedido: no minimo a semente muda.

- Rosto/olhos/nariz/boca diferentes ou identidade fraca: sobe a forca da
  identidade; da 2a tentativa em diante (ou com a forca no maximo) liga o
  retoque do rosto pela foto da persona, se o modelo tiver.
- Idade, cabelo, corpo, traco marcante: reforca esse traco no prompt.
- Sem rosto: pede o rosto visivel.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.core.persona import PersonaProfile
from app.core.validation.types import Failure, IdentityValidationResult
from app.providers.base import GenerationParameters, ProviderCapabilities

IDENTITY_FAILURES = {
    "facial_structure_mismatch", "eye_mismatch", "nose_mismatch", "mouth_mismatch",
    "low_identity_confidence", "sex_mismatch",
}
# Traco da persona reforcado no prompt para cada falha.
TRAIT_EMPHASIS = {
    "eye_mismatch": ("olhos", "sobrancelhas"),
    "nose_mismatch": ("nariz",),
    "mouth_mismatch": ("boca",),
    "facial_structure_mismatch": ("formato_rosto", "caracteristicas_faciais"),
    "hairstyle_mismatch": ("formato_cabelo", "cor_cabelo", "textura_cabelo"),
    "body_mismatch": ("caracteristicas_corporais",),
    "distinctive_feature_missing": ("caracteristicas_visuais_permanentes",),
}
FACE_VISIBLE = "face clearly visible, looking towards the camera"
MAX_EMPHASIS = 6
SEED_STEP = 7919


@dataclass
class RetryPlan:
    parameters: GenerationParameters
    emphasis: list[str]
    changes: dict[str, Any] = field(default_factory=dict)


class RetryManager:
    def __init__(self, strength_step: float = 0.15) -> None:
        self.strength_step = strength_step

    def plan(
        self,
        attempt: int,
        parameters: GenerationParameters,
        emphasis: list[str],
        result: IdentityValidationResult | None,
        failures: list[Failure],
        capabilities: ProviderCapabilities,
        persona: PersonaProfile,
    ) -> RetryPlan:
        kinds = [f.failure_type for f in failures]
        changes: dict[str, Any] = {}
        new = replace(parameters)

        if parameters.seed is None:
            changes["seed"] = "nova semente aleatoria"
        else:
            new.seed = (parameters.seed + SEED_STEP * attempt) % (2**32)
            changes["seed"] = [parameters.seed, new.seed]

        if IDENTITY_FAILURES & set(kinds):
            high = any(f.severity == "high" for f in failures if f.failure_type in IDENTITY_FAILURES)
            step = self.strength_step if high else self.strength_step * 2 / 3
            new.identity_strength = round(min(1.0, parameters.identity_strength + step), 3)
            if new.identity_strength != parameters.identity_strength:
                changes["identity_strength"] = [parameters.identity_strength, new.identity_strength]
            maxed = parameters.identity_strength >= 1.0
            if capabilities.supports_face_restore and not parameters.face_restore and (attempt >= 2 or maxed):
                new.face_restore = True
                changes["face_restore"] = [False, True]

        added: list[str] = []
        traits = persona.identity.traits
        for kind in kinds:
            for key in TRAIT_EMPHASIS.get(kind, ()):
                if traits.get(key):
                    added.append(traits[key])
        if "distinctive_feature_missing" in kinds and result is not None:
            added.extend(result.metrics["distinctive_features"].raw.get("missing", []))
        if "age_mismatch" in kinds and persona.identity.apparent_age:
            added.append(f"apparent age {persona.identity.apparent_age}")
        if "face_not_found" in kinds:
            added.append(FACE_VISIBLE)
        if "sex_mismatch" in kinds and persona.identity.sex:
            added.append("a woman" if persona.identity.sex == "F" else "a man")

        merged = list(dict.fromkeys([*emphasis, *added]))[-MAX_EMPHASIS:]
        if merged != emphasis:
            changes["emphasis"] = [e for e in merged if e not in emphasis]
        return RetryPlan(parameters=new, emphasis=merged, changes=changes)
