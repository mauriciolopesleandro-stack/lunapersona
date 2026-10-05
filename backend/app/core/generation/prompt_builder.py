"""PromptBuilder: monta o pedido em secoes, na ordem IDENTITY, APPEARANCE,
STYLE, SCENE, CONSTRAINTS - sem colar tudo num texto so.

O texto nao e o que segura a identidade (isso e a LoRA, a foto de
referencia e a validacao depois); e uma camada a mais. Por isso cada
adapter recebe as secoes e decide o texto final do seu modelo; o campo
`text` e a versao completa, para log e para modelos que aceitam instrucoes.
"""
from __future__ import annotations

from app.core.generation.vocabulary import CLOTHES_WORDS, EXPRESSION_WORDS
from app.core.persona.profile import APPEARANCE_FIELDS, STYLE_FIELDS, PersonaProfile
from app.providers.base import PromptSections

IDENTITY_DIRECTIVE = (
    "Maintain the exact identity of the referenced persona. "
    "Preserve facial structure, facial proportions and distinctive characteristics."
)
BASE_CONSTRAINTS = [
    "Do not change identity.",
    "Do not create a different person.",
    "Do not alter defining facial characteristics.",
]
# Sem roupa no pedido nem na persona: o Z-Image escolhia biquini num
# quiosque sem ninguem pedir (ver GenerationService).
DEFAULT_CLOTHES = "wearing casual everyday clothes that suit the place"


class PromptBuilder:
    def build(
        self,
        persona: PersonaProfile,
        scene: str,
        style_overrides: dict[str, str] | None = None,
        emphasis: list[str] | None = None,
    ) -> PromptSections:
        scene = scene.strip()
        traits = persona.identity.text()
        if persona.identity.apparent_age:
            traits = ", ".join(p for p in (traits, f"apparent age {persona.identity.apparent_age}") if p)

        appearance = dict(persona.appearance.values)
        if EXPRESSION_WORDS.search(scene):
            appearance.pop("expressao", None)
        if CLOTHES_WORDS.search(scene):
            appearance.pop("roupa", None)
        elif not appearance.get("roupa"):
            appearance["roupa"] = DEFAULT_CLOTHES
        style = {**persona.style.values, **{k: v for k, v in (style_overrides or {}).items() if v and v.strip()}}

        sections = PromptSections(
            identity_directive=IDENTITY_DIRECTIVE,
            identity_traits=traits,
            appearance=", ".join(appearance[k] for k in APPEARANCE_FIELDS if appearance.get(k)),
            style=", ".join(style[k] for k in STYLE_FIELDS if style.get(k)),
            scene=scene,
            constraints=[*BASE_CONSTRAINTS, *persona.constraints.rules],
            negative=list(persona.constraints.negative),
            emphasis=[e for e in dict.fromkeys(emphasis or []) if e],
        )
        sections.text = render_text(sections)
        return sections


def render_text(s: PromptSections) -> str:
    identity = "\n".join(p for p in (s.identity_directive, s.identity_traits, *s.emphasis) if p)
    blocks = [
        ("IDENTITY", identity),
        ("APPEARANCE", s.appearance),
        ("STYLE", s.style),
        ("SCENE", s.scene),
        ("CONSTRAINTS", "\n".join(s.constraints)),
    ]
    return "\n\n".join(f"[{name}]\n{body}" for name, body in blocks if body)
