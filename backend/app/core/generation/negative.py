"""NegativePromptBuilder: negativo em tres camadas, sem um texto gigante.

  GLOBAL   do sistema (config/persona_engine.json)
  PERSONA  da Persona Sheet (negative_profile.persona)
  SCENE    da cena (pessoa unica -> negative_profile.scene_single_subject)

Quem decide se o negativo entra no modelo e o adapter (SceneAdapter
.supports_negative_prompt). No Z-Image Turbo do benchmark (CFG 1,
ConditioningZeroOut) ele NAO entra: os termos ficam no registro da geracao e
servem de regra para a validacao.
"""
from __future__ import annotations

import hashlib
import json

from app.core.persona.sheet import PersonaSheet
from app.providers.base import NegativeSet

NEGATIVE_BUILDER_VERSION = "negative-v1.0"


class NegativePromptBuilder:
    def __init__(self, global_terms: list[str]) -> None:
        self.global_terms = list(global_terms)

    def build(self, sheet: PersonaSheet, single_subject: bool = True, scene_terms: list[str] | None = None) -> NegativeSet:
        persona = list(sheet.negative.get("persona", []))
        scene = list(sheet.negative.get("scene_single_subject", [])) if single_subject else []
        scene += list(scene_terms or [])
        content = json.dumps([self.global_terms, persona, scene], sort_keys=True).encode()
        return NegativeSet(
            global_terms=self.global_terms, persona_terms=persona, scene_terms=scene,
            version=f"{NEGATIVE_BUILDER_VERSION}+{hashlib.sha256(content).hexdigest()[:8]}",
        )
