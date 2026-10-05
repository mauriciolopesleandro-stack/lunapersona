"""Persona Sheet: a fonte de verdade da persona (personas/<id>/persona_sheet.json).

A ficha diz quem a persona e (identidade, idade, corpo, travas), quais sao as
masters (com sha256 - imutaveis), como gerar e como validar. O Persona Engine
so le a ficha: nada aqui grava nela. Uma mudanca de identidade e uma nova
`persona_version`, feita a mao.

As masters sao conferidas pelo hash a cada uso: se o arquivo mudou, a geracao
para (MasterIntegrityError) em vez de seguir com outra pessoa.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.storage import SAFE_ID, read_json

SHEET_FILE = "persona_sheet.json"
SUPPORTED_SCHEMA = 1
_VERSION = re.compile(r"^\d+\.\d+$")
REQUIRED_SECTIONS = (
    "identity", "body", "locks", "master_references", "generation_profile",
    "negative_profile", "validation_profile", "pose_coverage", "known_limitations", "versioning",
)


class PersonaSheetError(ValueError):
    pass


class PersonaSheetMissingError(PersonaSheetError):
    pass


class MasterIntegrityError(PersonaSheetError):
    pass


@dataclass(frozen=True)
class MasterReference:
    role: str
    reference_id: str
    file: str
    sha256: str
    purpose: str = ""


@dataclass
class PersonaSheet:
    persona_id: str
    persona_version: str
    status: str
    data: dict[str, Any]
    path: Path
    masters: dict[str, MasterReference] = field(default_factory=dict)

    # --- atalhos usados pelo engine --------------------------------------

    @property
    def pipeline_version(self) -> str:
        return self.data["generation_profile"]["pipeline_version"]

    @property
    def generation(self) -> dict[str, Any]:
        return self.data["generation_profile"]

    @property
    def validation(self) -> dict[str, Any]:
        return self.data["validation_profile"]

    @property
    def negative(self) -> dict[str, Any]:
        return self.data["negative_profile"]

    @property
    def age(self) -> dict[str, Any]:
        return self.data["identity"].get("age", {})

    @property
    def master_reference_versions(self) -> dict[str, str]:
        return {role: m.sha256 for role, m in self.masters.items()}

    def master(self, role: str) -> MasterReference:
        if role not in self.masters:
            raise PersonaSheetError(f"A ficha de '{self.persona_id}' nao tem a master '{role}'.")
        return self.masters[role]

    def master_ids(self) -> set[str]:
        return {m.reference_id for m in self.masters.values()}

    def master_path(self, role: str) -> Path:
        return self.path.parent / self.master(role).file

    def read_master(self, role: str) -> bytes:
        """Bytes da master, conferidos pelo sha256 da ficha."""
        path = self.master_path(role)
        if not path.exists():
            raise MasterIntegrityError(f"Master '{role}' nao encontrada no volume ({self.master(role).file}).")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != self.master(role).sha256:
            raise MasterIntegrityError(
                f"Master '{role}' foi alterada (sha256 {digest[:12]}... diferente da ficha). "
                "Masters sao imutaveis: a geracao parou. Uma troca de master exige nova versao da ficha."
            )
        return content

    def summary(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "persona_version": self.persona_version,
            "status": self.status,
            "pipeline_version": self.pipeline_version,
            "masters": {r: {"reference_id": m.reference_id, "sha256": m.sha256, "purpose": m.purpose} for r, m in self.masters.items()},
            "age": self.age,
            "locks": self.data["locks"],
            "pose_coverage": self.data["pose_coverage"],
            "known_limitations": self.data["known_limitations"],
        }


def parse_sheet(data: dict[str, Any], path: Path) -> PersonaSheet:
    if data.get("schema_version") != SUPPORTED_SCHEMA:
        raise PersonaSheetError(f"schema_version {data.get('schema_version')} nao suportado (esperado {SUPPORTED_SCHEMA}).")
    persona_id = str(data.get("persona_id", ""))
    if not SAFE_ID.match(persona_id):
        raise PersonaSheetError("persona_id invalido.")
    version = str(data.get("persona_version", ""))
    if not _VERSION.match(version):
        raise PersonaSheetError("persona_version deve ser como 1.0, 1.1, 2.0.")
    missing = [s for s in REQUIRED_SECTIONS if s not in data]
    if missing:
        raise PersonaSheetError(f"Secoes faltando na ficha: {', '.join(missing)}.")
    if not any(v.get("version") == version for v in data["versioning"]):
        raise PersonaSheetError(f"A versao {version} nao esta registrada em 'versioning'.")
    masters: dict[str, MasterReference] = {}
    for role, ref in data["master_references"].items():
        if not isinstance(ref, dict) or "sha256" not in ref:
            continue  # set_version, same_as, MISSING
        if not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) or ".." in ref["file"]:
            raise PersonaSheetError(f"Master '{role}' com sha256 ou caminho invalido.")
        masters[role] = MasterReference(role, ref["reference_id"], ref["file"], ref["sha256"], ref.get("purpose", ""))
    if "master_face" not in masters:
        raise PersonaSheetError("A ficha precisa de master_face.")
    threshold = data["validation_profile"].get("face_identity", {}).get("threshold")
    if not isinstance(threshold, (int, float)) or not 0 < threshold < 1:
        raise PersonaSheetError("validation_profile.face_identity.threshold deve ficar entre 0 e 1.")
    return PersonaSheet(persona_id, version, str(data.get("status", "")), data, path, masters)


class PersonaSheetRepository:
    """Le as fichas. Nao existe metodo de escrita: a ficha muda so por revisao manual."""

    def __init__(self, personas_dir: Path) -> None:
        self.personas_dir = personas_dir

    def path(self, persona_id: str) -> Path:
        if not SAFE_ID.match(persona_id or ""):
            raise PersonaSheetMissingError(f"Persona '{persona_id}' sem Persona Sheet.")
        return self.personas_dir / persona_id / SHEET_FILE

    def exists(self, persona_id: str) -> bool:
        try:
            return self.path(persona_id).exists()
        except PersonaSheetMissingError:
            return False

    def get(self, persona_id: str) -> PersonaSheet:
        path = self.path(persona_id)
        data = read_json(path)
        if data is None:
            raise PersonaSheetMissingError(
                f"A persona '{persona_id}' nao tem Persona Sheet calibrada: o Persona Engine V1 nao gera sem ela."
            )
        sheet = parse_sheet(data, path)
        if sheet.persona_id != persona_id:
            raise PersonaSheetError(f"A ficha em {persona_id}/ e da persona '{sheet.persona_id}'.")
        return sheet

    def protected_reference_ids(self, persona_id: str) -> set[str]:
        """Ids das masters (nao podem ser removidas, trocadas ou desativadas)."""
        return self.get(persona_id).master_ids() if self.exists(persona_id) else set()
