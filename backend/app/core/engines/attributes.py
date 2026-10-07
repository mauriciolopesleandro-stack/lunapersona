"""Persona Attribute Authority System (spec 45).

A foto original NAO e a fonte de verdade da identidade: ela so manda nos atributos marcados PRESERVE.
A Persona Sheet manda em tudo que e identidade. Nada presente na foto e "da Persona" por inferencia.

Cada atributo tem uma politica:
  PRESERVE     fica como na foto (pixels/estrutura da foto)
  RECONSTRUCT  gerado pela Persona (LoRA + referencias), respeitando a luz/pose da foto
  REMOVE       caracteristica da pessoa ORIGINAL: sai e e reconstruida como pele/rosto da Persona
  OPTIONAL     sem exigencia (nao validado)
  IGNORE       fora do escopo

Precedencia: padrao do Replacement < replacement_policy da Persona Sheet < pedido explicito do usuario.
A politica resolvida vai para prompts, mascaras, condicionamento, inpainting e validacao (nao so a UI).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PRESERVE, RECONSTRUCT, REMOVE, OPTIONAL, IGNORE = "PRESERVE", "RECONSTRUCT", "REMOVE", "OPTIONAL", "IGNORE"
POLICIES = (PRESERVE, RECONSTRUCT, REMOVE, OPTIONAL, IGNORE)

# atributo -> (padrao do Replacement, politicas que o motor sabe EXECUTAR). Politica fora da lista = erro claro:
# o fundo e a roupa nunca sao regenerados (o motor nao tem como "reconstruir" a roupa sem inventar).
ATTRIBUTES: dict[str, tuple[str, tuple[str, ...]]] = {
    # contexto (a foto manda)
    "pose": (PRESERVE, (PRESERVE,)),
    "composition": (PRESERVE, (PRESERVE,)),
    "camera_angle": (PRESERVE, (PRESERVE,)),
    "perspective": (PRESERVE, (PRESERVE,)),
    "clothing": (PRESERVE, (PRESERVE,)),
    "background": (PRESERVE, (PRESERVE,)),
    "lighting": (PRESERVE, (PRESERVE,)),
    "objects": (PRESERVE, (PRESERVE,)),
    "expression": (PRESERVE, (PRESERVE, RECONSTRUCT)),
    "accessories": (PRESERVE, (PRESERVE, REMOVE)),  # oculos, bone, relogio (regra da Luna: oculos mantidos)
    # identidade (a Persona Sheet manda)
    "face": (RECONSTRUCT, (RECONSTRUCT,)),
    "body": (RECONSTRUCT, (RECONSTRUCT, PRESERVE)),
    "skin": (RECONSTRUCT, (RECONSTRUCT,)),
    "hair": (RECONSTRUCT, (RECONSTRUCT, PRESERVE)),
    "age": (RECONSTRUCT, (RECONSTRUCT, OPTIONAL)),
    # marcas da pessoa ORIGINAL (nunca sao da Persona por inferencia)
    "tattoos": (REMOVE, (REMOVE, PRESERVE)),
    "scars": (REMOVE, (REMOVE, PRESERVE)),
    "piercings": (REMOVE, (REMOVE, PRESERVE)),
    "birthmarks": (REMOVE, (REMOVE, PRESERVE)),
    "makeup": (REMOVE, (REMOVE, PRESERVE)),  # PRESERVE so quando pedido explicitamente
    "jewelry": (REMOVE, (REMOVE, PRESERVE)),  # PRESERVE so quando pedido explicitamente
    "original_person_marks": (REMOVE, (REMOVE,)),
}

ALIASES = {
    "roupa": "clothing", "top": "clothing", "cenario": "background", "fundo": "background", "iluminacao": "lighting",
    "luz": "lighting", "enquadramento": "composition", "angulo": "camera_angle", "perspectiva": "perspective",
    "expressao": "expression", "acessorios": "accessories", "oculos": "accessories", "sunglasses": "accessories",
    "objeto": "objects", "rosto": "face", "corpo": "body", "pele": "skin", "cabelo": "hair", "idade": "age",
    "tatuagem": "tattoos", "tatuagens": "tattoos", "tattoo": "tattoos", "cicatriz": "scars", "cicatrizes": "scars",
    "piercing": "piercings", "marca_de_nascenca": "birthmarks", "pintas": "birthmarks", "maquiagem": "makeup",
    "joias": "jewelry", "brincos": "jewelry", "earrings": "jewelry", "marcas": "original_person_marks",
    "marks": "original_person_marks", "unwanted_marks": "original_person_marks",
    "original_person_marks": "original_person_marks", "source_marks": "original_person_marks",
}

# marcas da pessoa original que a limpeza de pele cobre (deteccao de tinta/marca na pele)
SKIN_MARKINGS = ("tattoos", "scars", "birthmarks", "original_person_marks")

# condicionamento de EXCLUSAO (spec 45.3): camada auxiliar - a remocao de verdade e por mascara/inpaint
EXCLUSION_POSITIVE = {
    "tattoos": ["clean natural skin", "no tattoos", "no visible tattoo marks"],
    "scars": ["no scars"],
    "birthmarks": ["natural pigmentation"],
    "original_person_marks": ["no unwanted body markings", "no residual markings from source person"],
    "skin": ["natural skin texture", "natural pores"],
}
EXCLUSION_NEGATIVE = {
    "tattoos": ["tattoo", "tattoos", "tattoo remnants", "tattoo ghosting", "tattoo outline", "ink on skin"],
    "scars": ["scar", "scars"],
    "piercings": ["piercing", "nose ring", "lip ring"],
    "birthmarks": ["birthmark"],
    "original_person_marks": ["body markings", "skin blemish stains"],
    "makeup": ["heavy makeup", "contour makeup", "false eyelashes", "glossy lipstick"],
    "jewelry": ["new earrings", "large hoop earrings", "new jewelry"],
}


class AttributePolicyError(ValueError):
    pass


def canonical(name: str) -> str:
    key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(key, key)


@dataclass
class AttributePolicy:
    """Politica resolvida (uma por atributo) + de onde veio + itens nomeados livres (ex.: 'black top')."""
    policy: dict[str, str]
    source: dict[str, str]
    preserve_items: list[str] = field(default_factory=list)  # nomes livres a preservar (vao para prompt e relatorio)
    remove_items: list[str] = field(default_factory=list)  # nomes livres a remover (vao para o negativo)
    persona_features: dict[str, str] = field(default_factory=dict)  # tracos DA PERSONA (nao sao removidos dela)

    def get(self, attr: str) -> str:
        return self.policy.get(canonical(attr), IGNORE)

    def is_(self, attr: str, pol: str) -> bool:
        return self.get(attr) == pol

    def with_policy(self, attr: str, pol: str) -> list[str]:
        return [a for a, p in self.policy.items() if p == pol]

    def removes_skin_markings(self) -> bool:
        """A limpeza de marcas usa o detector de TINTA/marca escura na pele: ele nao separa tatuagem de cicatriz.
        Com tatuagens PRESERVE ela nao roda (apagaria a tatuagem pedida); cicatriz/marca ficam sem detector."""
        return self.is_("tattoos", REMOVE)

    def positive_conditioning(self) -> list[str]:
        terms: list[str] = []
        for attr, words in EXCLUSION_POSITIVE.items():
            if self.get(attr) in (REMOVE, RECONSTRUCT):
                terms += words
        if self.is_("expression", PRESERVE):
            terms.append("same facial expression as in the photo")
        if self.is_("makeup", PRESERVE):
            terms.append("same makeup as in the photo")
        elif self.is_("makeup", REMOVE):
            terms.append("no makeup")
        terms += [f"keep the {i}" for i in self.preserve_items]
        return list(dict.fromkeys(terms))

    def negative_conditioning(self) -> list[str]:
        terms: list[str] = []
        for attr, words in EXCLUSION_NEGATIVE.items():
            if self.is_(attr, REMOVE):
                terms += words
        terms += self.remove_items
        return list(dict.fromkeys(terms))

    def to_dict(self) -> dict[str, Any]:
        return {"policy": dict(self.policy), "source": dict(self.source), "preserve_items": list(self.preserve_items),
                "remove_items": list(self.remove_items), "persona_features": dict(self.persona_features)}


def persona_policy(sheet_data: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str]]:
    """(politica da Persona Sheet, tracos proprios da Persona). Le `replacement_policy` e, como reforco,
    `identity_exclusions` (lista vazia de tatuagens = a Persona NAO tem tatuagem -> REMOVE)."""
    data = sheet_data or {}
    pol: dict[str, str] = {}
    rp = data.get("replacement_policy") or {}
    for key, value in (("preserve", PRESERVE), ("reconstruct", RECONSTRUCT), ("remove", REMOVE), ("optional", OPTIONAL),
                       ("ignore", IGNORE)):
        for name in rp.get(key, []) or []:
            pol[canonical(name)] = value
    for name in (data.get("identity_exclusions") or {}):
        pol.setdefault(canonical(name), REMOVE)
    features = {}
    for name, feat in ((data.get("identity_attributes") or {}).get("distinctive_features") or {}).items():
        features[str(name)] = feat.get("value", "") if isinstance(feat, dict) else str(feat)
    return pol, features


def resolve(sheet_data: dict[str, Any] | None = None, preserve: list[str] | None = None, remove: list[str] | None = None,
            reconstruct: list[str] | None = None) -> AttributePolicy:
    """padrao < Persona Sheet < pedido explicito. Erros claros: atributo em duas listas, politica que o motor
    nao executa (ex.: RECONSTRUCT da roupa), atributo desconhecido em reconstruct."""
    policy = {a: d for a, (d, _) in ATTRIBUTES.items()}
    source = {a: "default" for a in ATTRIBUTES}
    ppol, features = persona_policy(sheet_data)
    for attr, pol in ppol.items():
        if attr in ATTRIBUTES:
            _check(attr, pol, "Persona Sheet")
            policy[attr], source[attr] = pol, "persona"
    seen: dict[str, str] = {}
    preserve_items, remove_items = [], []
    for pol, names in ((PRESERVE, preserve), (REMOVE, remove), (RECONSTRUCT, reconstruct)):
        for raw in names or []:
            attr = canonical(raw)
            if attr in seen and seen[attr] != pol:
                raise AttributePolicyError(f"'{raw}' pedido como {seen[attr]} e {pol} ao mesmo tempo")
            seen[attr] = pol
            if attr not in ATTRIBUTES:
                if pol == PRESERVE:
                    preserve_items.append(str(raw).replace("_", " "))  # item nomeado (ex.: black_top)
                    continue
                if pol == REMOVE:
                    remove_items.append(str(raw).replace("_", " "))
                    continue
                raise AttributePolicyError(f"atributo desconhecido para RECONSTRUCT: '{raw}'")
            _check(attr, pol, "pedido")
            policy[attr], source[attr] = pol, "request"
    return AttributePolicy(policy, source, preserve_items, remove_items, features)


def _check(attr: str, pol: str, where: str) -> None:
    if pol not in POLICIES:
        raise AttributePolicyError(f"politica invalida para {attr}: {pol}")
    allowed = ATTRIBUTES[attr][1]
    if pol not in allowed:
        raise AttributePolicyError(f"{attr}: {pol} nao e suportado no Replacement ({where}); permitido: {', '.join(allowed)}")


def catalog() -> list[dict[str, Any]]:
    return [{"attribute": a, "default": d, "allowed": list(al)} for a, (d, al) in ATTRIBUTES.items()]


__all__ = ["ATTRIBUTES", "IGNORE", "OPTIONAL", "PRESERVE", "RECONSTRUCT", "REMOVE", "SKIN_MARKINGS", "AttributePolicy",
           "AttributePolicyError", "canonical", "catalog", "persona_policy", "resolve"]
