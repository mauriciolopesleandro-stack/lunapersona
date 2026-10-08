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
    "clothing": (PRESERVE, (PRESERVE, RECONSTRUCT)),  # RECONSTRUCT = redesenhada parecida (mesmo tipo/cor) no corpo da Persona
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
    # spec 46.4: mao = HAND_POSE_LOCK (posicao/gesto/relacao com objetos da foto; anatomia reconstruida).
    # PRESERVE = travar os PIXELS da mao (so por pedido explicito)
    "hands": (RECONSTRUCT, (RECONSTRUCT, PRESERVE)),
    # marcas da pessoa ORIGINAL (nunca sao da Persona por inferencia)
    "tattoos": (REMOVE, (REMOVE, PRESERVE)),
    "scars": (REMOVE, (REMOVE, PRESERVE)),
    "piercings": (REMOVE, (REMOVE, PRESERVE)),
    "birthmarks": (REMOVE, (REMOVE, PRESERVE)),
    "makeup": (REMOVE, (REMOVE, PRESERVE)),  # PRESERVE so quando pedido explicitamente
    # spec 46.9/46.12: MESMOS acessorios (oculos, brincos, pulseiras...) - joia da foto fica, salvo pedido de tirar
    "jewelry": (PRESERVE, (PRESERVE, REMOVE)),
    "original_person_marks": (REMOVE, (REMOVE,)),
}

ALIASES = {
    "roupa": "clothing", "top": "clothing", "cenario": "background", "fundo": "background", "iluminacao": "lighting",
    "luz": "lighting", "enquadramento": "composition", "angulo": "camera_angle", "perspectiva": "perspective",
    "expressao": "expression", "acessorios": "accessories", "maos": "hands", "mao": "hands", "hand": "hands",
    "objeto": "objects", "rosto": "face", "corpo": "body", "pele": "skin", "cabelo": "hair", "idade": "age",
    "tatuagem": "tattoos", "tatuagens": "tattoos", "tattoo": "tattoos", "cicatriz": "scars", "cicatrizes": "scars",
    "piercing": "piercings", "marca_de_nascenca": "birthmarks", "pintas": "birthmarks", "maquiagem": "makeup",
    "joias": "jewelry", "marcas": "original_person_marks", "markings": "original_person_marks",
    "marks": "original_person_marks", "unwanted_marks": "original_person_marks",
    "original_person_marks": "original_person_marks", "source_marks": "original_person_marks",
}

# spec 46.2: cada OBJETO detectado tem politica propria (o item manda; sem item, vale a classe)
ITEMS = {"glasses": "accessories", "hat": "accessories", "watch": "accessories", "bag": "accessories",
         "earrings": "jewelry", "necklace": "jewelry", "bracelet": "jewelry", "ring": "jewelry"}
ITEM_ALIASES = {"oculos": "glasses", "sunglasses": "glasses", "eyeglasses": "glasses", "brinco": "earrings",
                "brincos": "earrings", "earring": "earrings", "colar": "necklace", "pulseira": "bracelet",
                "pulseiras": "bracelet", "bracelets": "bracelet", "relogio": "watch", "anel": "ring", "aneis": "ring",
                "bone": "hat", "chapeu": "hat", "cap": "hat", "bolsa": "bag"}


def item_of(label: str) -> str | None:
    """Rotulo do detector ('sunglasses', 'earrings'...) ou nome pedido -> item canonico."""
    key = str(label or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = ITEM_ALIASES.get(key, key)
    if key in ITEMS:
        return key
    for item in ITEMS:  # rotulos compostos do Florence ("pair of sunglasses")
        if item.rstrip("s") in key:
            return item
    for alias, item in ITEM_ALIASES.items():
        if alias in key:
            return item
    return None


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
    items: dict[str, str] = field(default_factory=dict)  # politica por OBJETO (oculos, brincos, pulseira...)

    def get(self, attr: str) -> str:
        return self.policy.get(canonical(attr), IGNORE)

    def item_policy(self, label: str) -> tuple[str | None, str]:
        """(item, politica) de um objeto detectado: o item pedido manda; senao, a politica da classe."""
        item = item_of(label)
        if item is None:
            return None, self.get("accessories")
        return item, self.items.get(item, self.get(ITEMS[item]))

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
                "remove_items": list(self.remove_items), "persona_features": dict(self.persona_features),
                "items": dict(self.items)}


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
    items: dict[str, str] = {}
    for pol, names in ((PRESERVE, preserve), (REMOVE, remove), (RECONSTRUCT, reconstruct)):
        for raw in names or []:
            item = item_of(raw) if canonical(raw) not in ATTRIBUTES else None
            if item is not None:  # objeto nomeado (oculos, brincos...): politica so dele
                if item in items and items[item] != pol:
                    raise AttributePolicyError(f"'{raw}' pedido como {items[item]} e {pol} ao mesmo tempo")
                if pol not in (PRESERVE, REMOVE):
                    raise AttributePolicyError(f"{item}: so PRESERVE ou REMOVE (objeto nao e reconstruido)")
                items[item] = pol
                source[f"item:{item}"] = "request"
                continue
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
    return AttributePolicy(policy, source, preserve_items, remove_items, features, items)


def from_structured(spec: dict[str, Any] | None) -> tuple[list[str], list[str], list[str]]:
    """Spec 46.10: configuracao ESTRUTURADA -> listas (preserve, remove, reconstruct).

        preserve: {accessories: [glasses, earrings, bracelet], clothing: [top], pose: {enabled: true}}
        remove: {markings: [tattoos, scars]}
        reconstruct: {identity: [face, body, skin]}

    Categoria com lista de objetos (accessories/jewelry) vira politica POR OBJETO; categoria com lista de partes
    (clothing: [top]) preserva a categoria e registra as partes como itens nomeados; {enabled: false} ignora."""
    out: dict[str, list[str]] = {"preserve": [], "remove": [], "reconstruct": []}
    for key in out:
        block = (spec or {}).get(key) or {}
        if isinstance(block, list):
            out[key] += [str(x) for x in block]
            continue
        if not isinstance(block, dict):
            raise AttributePolicyError(f"{key}: esperado objeto ou lista")
        for cat, val in block.items():
            if isinstance(val, dict):
                if val.get("enabled", True):
                    out[key].append(str(cat))
                continue
            names = [str(v) for v in (val or [])] if isinstance(val, list) else ([str(cat)] if val else [])
            if cat in ("accessories", "jewelry", "acessorios", "joias"):
                out[key] += names  # cada objeto com a sua politica
            elif cat in ("markings", "identity", "marcas", "identidade"):
                out[key] += names  # grupos: cada nome e um atributo (tattoos, scars / face, body, skin)
            else:
                out[key].append(str(cat))
                if key == "preserve":
                    out[key] += [n for n in names if canonical(n) not in ATTRIBUTES and canonical(n) != canonical(cat)]
    return out["preserve"], out["remove"], out["reconstruct"]


def _check(attr: str, pol: str, where: str) -> None:
    if pol not in POLICIES:
        raise AttributePolicyError(f"politica invalida para {attr}: {pol}")
    allowed = ATTRIBUTES[attr][1]
    if pol not in allowed:
        raise AttributePolicyError(f"{attr}: {pol} nao e suportado no Replacement ({where}); permitido: {', '.join(allowed)}")


def catalog() -> list[dict[str, Any]]:
    return [{"attribute": a, "default": d, "allowed": list(al)} for a, (d, al) in ATTRIBUTES.items()]


__all__ = ["ITEMS", "from_structured", "item_of", "ATTRIBUTES", "IGNORE", "OPTIONAL", "PRESERVE", "RECONSTRUCT", "REMOVE", "SKIN_MARKINGS", "AttributePolicy",
           "AttributePolicyError", "canonical", "catalog", "persona_policy", "resolve"]
