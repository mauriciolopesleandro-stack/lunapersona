"""PromptBuilder do Persona Engine V1.

Monta o pedido em partes (quem, aparencia, cena, estilo, diretivas, negativo):
- QUEM vem da Persona Sheet: gatilho interno da LoRA + "a woman". O gatilho
  nunca vem do texto do usuario: se aparecer na cena/estilo ele e removido
  (o benchmark viu "lunavox" escrito em placas; o gatilho nao deve virar
  conteudo semantico da cena).
- APARENCIA e ESTILO vem do perfil editavel (atributos VARIABLE/FREE da ficha).
- A identidade do rosto NAO vem do texto: e o Face Lock (Qwen BFS + master).
"""
from __future__ import annotations

import re

from app.core.generation.negative import NegativePromptBuilder
from app.core.generation.vocabulary import CLOTHES_WORDS, EXPRESSION_WORDS
from app.core.persona.profile import APPEARANCE_FIELDS, STYLE_FIELDS, PersonaProfile
from app.core.persona.sheet import PersonaSheet
from app.providers.base import PromptSections

PROMPT_BUILDER_VERSION = "prompt-v1.0"
# Sem roupa no pedido nem no perfil: o Z-Image escolhia biquini num quiosque.
DEFAULT_CLOTHES = "wearing casual everyday clothes that suit the place"


def strip_trigger(text: str, trigger: str) -> str:
    """Tira o gatilho (e grafias proximas: 'luna vox', 'Lunavox') do texto."""
    letters = r"\W*".join(re.escape(c) for c in trigger)
    cleaned = re.sub(letters, " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+,", ",", cleaned)).strip(" ,")


class PromptBuilder:
    def __init__(self, negative: NegativePromptBuilder) -> None:
        self.negative = negative

    def build(
        self,
        sheet: PersonaSheet,
        profile: PersonaProfile,
        scene: str,
        style_overrides: dict[str, str] | None = None,
        directives: list[str] | None = None,
        single_subject: bool = True,
    ) -> PromptSections:
        gen = sheet.generation
        trigger = gen["scene"]["trigger"]
        rules = gen.get("prompt_rules", {})
        clean = (lambda t: strip_trigger(t, trigger)) if rules.get("strip_trigger_from_user_text", True) else (lambda t: t)

        scene = clean(scene.strip())
        appearance = {k: clean(v) for k, v in profile.appearance.values.items()}
        if EXPRESSION_WORDS.search(scene):
            appearance.pop("expressao", None)
        if CLOTHES_WORDS.search(scene):
            appearance.pop("roupa", None)
        elif not appearance.get("roupa"):
            appearance["roupa"] = DEFAULT_CLOTHES
        style = {**profile.style.values, **{k: v for k, v in (style_overrides or {}).items() if v and v.strip()}}
        style = {k: clean(v) for k, v in style.items()}

        extra = list(directives or [])
        if rules.get("apply_trigger_leak_directive") and rules.get("trigger_leak_directive"):
            extra.append(rules["trigger_leak_directive"])

        sections = PromptSections(
            subject=f"{trigger}, {gen['scene'].get('subject_phrase', 'a person')}",
            appearance=", ".join(appearance[k] for k in APPEARANCE_FIELDS if appearance.get(k)),
            scene=scene,
            style=", ".join(style[k] for k in STYLE_FIELDS if style.get(k)),
            directives=[d for d in dict.fromkeys(extra) if d],
            negative=self.negative.build(sheet, single_subject=single_subject),
            version=PROMPT_BUILDER_VERSION,
        )
        sections.text = ", ".join(p for p in (sections.subject, sections.appearance, sections.scene,
                                              sections.style, *sections.directives) if p)
        return sections
