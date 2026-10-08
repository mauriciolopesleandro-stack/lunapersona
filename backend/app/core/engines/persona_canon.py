"""Persona Canon e Physical Identity Profile (spec V2.1, secoes 4-9, 39-41).

O Canon e a identidade PERMANENTE da Persona (rosto, corpo, altura, peso, proporcoes, pele, cabelo, idade, tracos
persistentes). Ele e lido da Persona Sheet - NUNCA de uma foto de entrada nem de uma imagem gerada - e e imutavel:
dataclass congelada, com versao e hash do conteudo.

SceneState e o estado TEMPORARIO de uma cena (lugar, pose, roupa, acessorios, luz, clima, hora, expressao).
`apply_scene` junta os dois e RECUSA qualquer tentativa de uma cena mudar um atributo do Canon - e a trava
programatica pedida para o Context Engine (que ainda nao existe no codigo: a API fica pronta para ele).

Valor que a ficha nao tem (altura e peso hoje estao "UNKNOWN") fica None com o motivo: nada e inventado nem
inferido de uma foto.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any

# niveis da spec 6: 1-2 pertencem a Persona; 3-4 podem mudar por cena
LEVEL_CANONICAL = ("face", "body", "height", "weight", "body_type", "proportions", "bust", "waist", "hip", "shoulders",
                   "torso", "legs", "arms", "skin", "hair_identity", "age", "persistent_traits")
LEVEL_VARIABLE = ("hairstyle", "makeup", "clothing", "accessories", "expression", "pose")
LEVEL_SCENE = ("location", "lighting", "weather", "time", "camera", "background", "composition", "perspective")


class CanonViolation(ValueError):
    """Uma cena/foto/imagem gerada tentou mudar a identidade permanente da Persona."""


def _known(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper().startswith("UNKNOWN"):
        return None
    return value


@dataclass(frozen=True)
class Measure:
    value: Any
    unit: str = ""
    source: str = ""
    note: str = ""


@dataclass(frozen=True)
class PhysicalIdentityProfile:
    persona_id: str
    persona_version: str
    height: Measure
    weight: Measure
    body_type: str
    silhouette: str
    proportions: Any  # MappingProxy: razoes DWPose do master_body (segmento / tronco)
    proportions_pose_class: str | None
    bust: str | None
    waist: str | None
    hip: str | None
    skin: Any  # MappingProxy: tom, subtom, textura, idade aparente, caracteristicas
    hair: Any  # MappingProxy: cor, comprimento, textura
    age_target: int | None
    age_range: tuple[int, int] | None
    persistent_traits: tuple[str, ...]
    master_body_sha256: str | None
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k in self.__dataclass_fields__:  # asdict nao copia MappingProxy (imutavel)
            v = getattr(self, k)
            d[k] = dict(v) if isinstance(v, MappingProxyType) else asdict(v) if isinstance(v, Measure) else                 list(v) if isinstance(v, tuple) else v
        return d

    def body_text_en(self) -> str:
        """Corpo da Persona em termos que o SDXL entende (so o que a ficha diz)."""
        words = {"curvilinea": "curvy", "ampulheta": "hourglass figure", "busto cheio": "full bust",
                 "cintura fina": "slim waist", "quadril arredondado": "rounded hips", "bracos tonificados": "toned arms",
                 "longilinea nas pernas": "long legs"}
        text = f"{self.body_type} {self.silhouette}".lower()
        out = [en for pt, en in words.items() if pt in text]
        return ", ".join(dict.fromkeys(out)) or "curvy hourglass figure"

    def skin_text_en(self) -> str:
        tone = (self.skin.get("tone") or "").lower()
        out = []
        if "bronze" in tone:
            out.append("sun-kissed tan skin")
        if "oliva" in tone or "olive" in tone:
            out.append("olive undertone")
        if "dourad" in tone or "golden" in tone:
            out.append("golden undertone")
        return ", ".join(out) or "natural skin tone"


@dataclass(frozen=True)
class PersonaCanon:
    persona_id: str
    persona_version: str
    face: str
    physical: PhysicalIdentityProfile
    master_face_sha256: str | None
    exclusions: Any  # MappingProxy: o que a Persona NAO tem (tatuagens, cicatrizes...)
    canon_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"persona_id": self.persona_id, "persona_version": self.persona_version, "face": self.face,
                "physical": self.physical.to_dict(), "master_face_sha256": self.master_face_sha256,
                "exclusions": {k: list(v) for k, v in self.exclusions.items()}, "canon_hash": self.canon_hash}


@dataclass
class SceneState:
    """Estado temporario da cena: muda a cada foto/cena, nunca toca o Canon."""
    location: str | None = None
    pose: str | None = None
    clothing: str | None = None
    accessories: list[str] = field(default_factory=list)
    lighting: str | None = None
    weather: str | None = None
    time: str | None = None
    expression: str | None = None
    hairstyle: str | None = None
    makeup: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash(d: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def load_canon(sheet: dict[str, Any]) -> PersonaCanon:
    """Persona Sheet -> Canon imutavel. So le; nunca escreve na ficha."""
    if not sheet:
        raise CanonViolation("sem Persona Sheet: o Canon nao pode ser montado (nada e inventado)")
    ia = sheet.get("identity_attributes") or {}
    body = sheet.get("body") or {}
    masters = sheet.get("master_references") or {}
    mb = masters.get("master_body") or {}
    skin = ia.get("skin") or {}
    hair = ia.get("hair") or {}
    age = ia.get("age") or {}
    traits = tuple(f"{k}: {v.get('value')}" for k, v in (ia.get("distinctive_features") or {}).items()
                   if isinstance(v, dict) and v.get("lock") in ("HARD", "SOFT"))

    def m(key: str, unit: str) -> Measure:
        raw = (body.get(key) or {}).get("value") if isinstance(body.get(key), dict) else body.get(key)
        val = _known(raw)
        return Measure(val, unit if val is not None else "", f"body.{key}",
                       "" if val is not None else "nao definido na Persona Sheet (nao inventado)")

    struct = (body.get("structure") or {}).get("value") or (ia.get("body") or {}).get("value") or ""
    text = struct.lower()
    phys_raw = {
        "height": m("height", "cm"), "weight": m("weight", "kg"), "body_type": struct,
        "silhouette": (body.get("silhouette") or {}).get("value", ""),
        "proportions": dict(mb.get("ratios") or {}), "proportions_pose_class": mb.get("pose_class"),
        "bust": "busto cheio" if "busto cheio" in text else None,
        "waist": "cintura fina" if "cintura fina" in text else None,
        "hip": "quadril arredondado" if "quadril arredondado" in text else None,
        "skin": {"tone": skin.get("tone"), "undertone": "oliva dourado" if "oliva" in (skin.get("tone") or "") else None,
                 "texture": skin.get("texture"), "freckles": skin.get("freckles"), "note": skin.get("note")},
        "hair": {"value": hair.get("value")},
        "age_target": age.get("target"), "age_range": tuple(age["accepted_range"]) if age.get("accepted_range") else None,
        "persistent_traits": traits, "master_body_sha256": mb.get("sha256"),
    }
    version = f"{sheet.get('persona_version', '?')}+{_hash(phys_raw)}"
    phys = PhysicalIdentityProfile(
        persona_id=sheet.get("persona_id", ""), persona_version=str(sheet.get("persona_version", "")),
        height=phys_raw["height"], weight=phys_raw["weight"], body_type=phys_raw["body_type"],
        silhouette=phys_raw["silhouette"], proportions=MappingProxyType(phys_raw["proportions"]),
        proportions_pose_class=phys_raw["proportions_pose_class"], bust=phys_raw["bust"], waist=phys_raw["waist"],
        hip=phys_raw["hip"], skin=MappingProxyType(phys_raw["skin"]), hair=MappingProxyType(phys_raw["hair"]),
        age_target=phys_raw["age_target"], age_range=phys_raw["age_range"], persistent_traits=traits,
        master_body_sha256=phys_raw["master_body_sha256"], version=version)
    excl = MappingProxyType({k: tuple(v) for k, v in (sheet.get("identity_exclusions") or {}).items() if isinstance(v, list)})
    face = (ia.get("face") or {}).get("value", "")
    mf = masters.get("master_face") or {}
    canon_hash = _hash({"face": face, "physical": phys.to_dict(), "exclusions": {k: list(v) for k, v in excl.items()},
                        "master_face": mf.get("sha256")})
    return PersonaCanon(sheet.get("persona_id", ""), str(sheet.get("persona_version", "")), face, phys, mf.get("sha256"),
                        excl, canon_hash)


def apply_scene(canon: PersonaCanon, changes: dict[str, Any]) -> SceneState:
    """Uma cena so pode mexer no que e de cena/variavel. Atributo do Canon na lista = CanonViolation."""
    bad = [k for k in changes if k in LEVEL_CANONICAL]
    if bad:
        raise CanonViolation(f"a cena tentou mudar a identidade permanente de '{canon.persona_id}': {bad}")
    unknown = [k for k in changes if k not in SceneState.__dataclass_fields__]
    if unknown:
        raise CanonViolation(f"campos de cena desconhecidos: {unknown}")
    return SceneState(**changes)


def guard_master_promotion(source: str, target: str) -> None:
    """Imagem gerada/foto de entrada nunca vira master (spec 9/39). So acao explicita do usuario, fora daqui."""
    if target.startswith("master") or target in ("body_reference", "new_body_reference"):
        raise CanonViolation(f"proibido promover '{source}' para '{target}' automaticamente")


__all__ = ["CanonViolation", "LEVEL_CANONICAL", "LEVEL_SCENE", "LEVEL_VARIABLE", "Measure", "PersonaCanon",
           "PhysicalIdentityProfile", "SceneState", "apply_scene", "guard_master_promotion", "load_canon"]
