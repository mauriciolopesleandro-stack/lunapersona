"""Guarda o PersonaProfile no mesmo persona.json do PersonaManager.

Os tracos fixos continuam em identity.fixed, e roupa/expressao/luz/camera em
identity.variable_defaults - o GenerationService atual le de la, entao as
telas antigas e o Persona Engine enxergam a mesma persona. O resto fica no
bloco "engine". Cada alteracao sobe a versao e guarda uma copia em
personas/<id>/engine/versions/.

Remover = desativar (active=false): a pasta tem LoRA, voz, referencias e
historico, que nao voltam se apagados.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.persona.profile import (
    LEGACY_APPEARANCE_FIELDS,
    LEGACY_STYLE_FIELDS,
    AppearanceProfile,
    IdentityProfile,
    PersonaConstraints,
    PersonaProfile,
    PersonaValidationError,
    PersonaValidationSettings,
    StyleProfile,
)
from app.core.storage import SAFE_ID, read_json, utcnow, write_json_atomic
from app.persona_manager.manager import FIXED_IDENTITY_FIELDS, PersonaNotFoundError

_PATCHABLE = {"name", "description", "identity", "appearance", "style", "constraints", "validation"}


class PersonaRepository:
    def __init__(self, personas_dir: Path) -> None:
        self.personas_dir = personas_dir

    # --- caminhos --------------------------------------------------------

    def persona_dir(self, persona_id: str) -> Path:
        if not SAFE_ID.match(persona_id or ""):
            raise PersonaNotFoundError(f"Persona '{persona_id}' nao encontrada.")
        return self.personas_dir / persona_id

    def _json_path(self, persona_id: str) -> Path:
        return self.persona_dir(persona_id) / "persona.json"

    # --- leitura ---------------------------------------------------------

    def list(self, include_inactive: bool = False) -> list[PersonaProfile]:
        if not self.personas_dir.exists():
            return []
        out = []
        for entry in sorted(self.personas_dir.iterdir()):
            if entry.is_dir() and SAFE_ID.match(entry.name) and (entry / "persona.json").exists():
                profile = self.get(entry.name, include_inactive=True)
                if profile.active or include_inactive:
                    out.append(profile)
        return out

    def get(self, persona_id: str, include_inactive: bool = False) -> PersonaProfile:
        path = self._json_path(persona_id)
        data = read_json(path)
        if data is None:
            raise PersonaNotFoundError(f"Persona '{persona_id}' nao encontrada.")
        profile = _from_json(persona_id, data, path)
        if not profile.active and not include_inactive:
            raise PersonaNotFoundError(f"Persona '{persona_id}' nao encontrada.")
        return profile

    def versions(self, persona_id: str) -> list[dict[str, Any]]:
        folder = self.persona_dir(persona_id) / "engine" / "versions"
        files = sorted(folder.glob("v*.json"), key=lambda p: int(p.stem[1:])) if folder.exists() else []
        return [read_json(p) for p in files]

    # --- escrita ---------------------------------------------------------

    def create(self, data: dict[str, Any]) -> PersonaProfile:
        name = str(data.get("name", "")).strip()
        persona_id = self._new_id(name)
        now = utcnow()
        profile = PersonaProfile(id=persona_id, name=name, created_at=now, updated_at=now)
        _apply_patch(profile, {k: v for k, v in data.items() if k != "name"})
        profile.validate()
        self._write(profile, {})
        return profile

    def update(self, persona_id: str, patch: dict[str, Any]) -> PersonaProfile:
        unknown = set(patch) - _PATCHABLE
        if unknown:
            raise PersonaValidationError(f"Campos que nao podem ser alterados: {', '.join(sorted(unknown))}.")
        profile = self.get(persona_id)
        _apply_patch(profile, patch)
        profile.validate()
        profile.version += 1
        profile.updated_at = utcnow()
        self._write(profile, read_json(self._json_path(persona_id), {}))
        return profile

    def deactivate(self, persona_id: str) -> PersonaProfile:
        profile = self.get(persona_id)
        profile.active = False
        profile.version += 1
        profile.updated_at = utcnow()
        self._write(profile, read_json(self._json_path(persona_id), {}))
        return profile

    def _new_id(self, name: str) -> str:
        base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40] or "persona"
        candidate = base
        while (self.personas_dir / candidate).exists():
            candidate = f"{base}-{uuid.uuid4().hex[:6]}"
        return candidate

    def _write(self, profile: PersonaProfile, previous: dict[str, Any]) -> None:
        identity = dict(previous.get("identity", {}))
        variable = dict(identity.get("variable_defaults", {}))
        for key in LEGACY_APPEARANCE_FIELDS:
            variable[key] = profile.appearance.values.get(key, "")
        for key in LEGACY_STYLE_FIELDS:
            variable[key] = profile.style.values.get(key, "")
        identity["fixed"] = {k: profile.identity.traits.get(k, "") for k in FIXED_IDENTITY_FIELDS}
        identity["variable_defaults"] = variable
        payload = {
            **previous,
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "identity": identity,
            "engine": {
                "version": profile.version,
                "active": profile.active,
                "created_at": profile.created_at,
                "updated_at": profile.updated_at,
                "identity": {
                    "apparent_age": profile.identity.apparent_age,
                    "sex": profile.identity.sex,
                    "distinctive_keywords": profile.identity.distinctive_keywords,
                },
                "appearance": {k: v for k, v in profile.appearance.values.items() if k not in LEGACY_APPEARANCE_FIELDS},
                "style": {k: v for k, v in profile.style.values.items() if k not in LEGACY_STYLE_FIELDS},
                "constraints": {"rules": profile.constraints.rules, "negative": profile.constraints.negative},
                "validation": {"threshold": profile.validation.threshold},
            },
        }
        write_json_atomic(self._json_path(profile.id), payload)
        snapshot = self.persona_dir(profile.id) / "engine" / "versions" / f"v{profile.version}.json"
        write_json_atomic(snapshot, profile.to_dict())


def _from_json(persona_id: str, data: dict[str, Any], path: Path) -> PersonaProfile:
    engine = data.get("engine", {})
    identity = data.get("identity", {})
    variable = identity.get("variable_defaults", {})
    appearance = {k: variable[k] for k in LEGACY_APPEARANCE_FIELDS if variable.get(k)}
    appearance.update(engine.get("appearance", {}))
    style = {k: variable[k] for k in LEGACY_STYLE_FIELDS if variable.get(k)}
    style.update(engine.get("style", {}))
    engine_identity = engine.get("identity", {})
    constraints = engine.get("constraints", {})
    # Persona anterior ao Persona Engine: datas pelo arquivo.
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    lora = data.get("lora") or {}
    return PersonaProfile(
        id=data.get("id", persona_id),
        name=data.get("name", persona_id),
        description=data.get("description", ""),
        identity=IdentityProfile(
            traits={k: v for k, v in identity.get("fixed", {}).items() if v},
            apparent_age=engine_identity.get("apparent_age"),
            sex=engine_identity.get("sex", ""),
            distinctive_keywords=list(engine_identity.get("distinctive_keywords", [])),
        ),
        appearance=AppearanceProfile(values=appearance),
        style=StyleProfile(values=style),
        constraints=PersonaConstraints(
            rules=list(constraints.get("rules", [])), negative=list(constraints.get("negative", []))
        ),
        validation=PersonaValidationSettings(threshold=engine.get("validation", {}).get("threshold")),
        active=engine.get("active", True),
        version=int(engine.get("version", 1)),
        created_at=engine.get("created_at") or stamp,
        updated_at=engine.get("updated_at") or stamp,
        identity_assets={"lora": lora} if lora else {},
    )


def _apply_patch(profile: PersonaProfile, patch: dict[str, Any]) -> None:
    """Junta o patch no perfil. Dentro de identidade/aparencia/estilo, so as
    chaves enviadas mudam; texto vazio apaga o campo."""
    if "name" in patch:
        profile.name = str(patch["name"]).strip()
    if "description" in patch:
        profile.description = str(patch["description"] or "")
    identity = patch.get("identity") or {}
    if "traits" in identity:
        profile.identity.traits = _merge(profile.identity.traits, identity["traits"])
    if "apparent_age" in identity:
        profile.identity.apparent_age = identity["apparent_age"]
    if "sex" in identity:
        profile.identity.sex = identity["sex"] or ""
    if "distinctive_keywords" in identity:
        profile.identity.distinctive_keywords = _clean_list(identity["distinctive_keywords"])
    if "appearance" in patch:
        profile.appearance.values = _merge(profile.appearance.values, patch["appearance"] or {})
    if "style" in patch:
        profile.style.values = _merge(profile.style.values, patch["style"] or {})
    constraints = patch.get("constraints") or {}
    if "rules" in constraints:
        profile.constraints.rules = _clean_list(constraints["rules"])
    if "negative" in constraints:
        profile.constraints.negative = _clean_list(constraints["negative"])
    validation = patch.get("validation") or {}
    if "threshold" in validation:
        profile.validation.threshold = validation["threshold"]


def _merge(current: dict[str, str], changes: dict[str, Any]) -> dict[str, str]:
    merged = dict(current)
    for key, value in changes.items():
        text = str(value or "").strip()
        if text:
            merged[key] = text
        else:
            merged.pop(key, None)
    return merged


def _clean_list(values: Any) -> list[str]:
    return [s for v in values or [] if (s := str(v).strip())]
