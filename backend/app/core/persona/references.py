"""ReferenceManager: as fotos de referencia da persona, com tipo, peso,
ativa/inativa e historico.

Usa o mesmo references/index.json do PersonaManager (a aba Referencias atual
continua igual) e acrescenta os campos novos em cada entrada. A principal
continua sendo a marcada com is_primary: o tipo PRIMARY e sempre o dela, e
as outras tem o proprio tipo (FACE, FULL_BODY...).

Remover nao apaga a foto: ela vai para references/removed/ e o historico
(references/history.json) guarda o que aconteceu.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.persona.images import image_info
from app.core.storage import new_id, read_json, utcnow, write_json_atomic
from app.persona_manager.manager import PersonaManager, ReferenceNotFoundError

REFERENCE_TYPES = ["PRIMARY", "FACE", "FULL_BODY", "PROFILE", "STYLE", "OTHER"]
# Tipos que mostram o rosto: servem de base para conferir a identidade.
IDENTITY_TYPES = {"PRIMARY", "FACE", "PROFILE"}
MIN_SIDE = 256
MAX_BYTES = 20 * 1024 * 1024


class InvalidReferenceError(ValueError):
    pass


@dataclass
class Reference:
    id: str
    persona_id: str
    type: str
    weight: float
    active: bool
    is_primary: bool
    filename: str
    original_filename: str
    label: str
    width: int | None
    height: int | None
    created_at: str
    image_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_image(content: bytes) -> tuple[str, int, int]:
    if not content:
        raise InvalidReferenceError("Arquivo vazio.")
    if len(content) > MAX_BYTES:
        raise InvalidReferenceError("Imagem maior que 20 MB.")
    info = image_info(content)
    if info is None:
        raise InvalidReferenceError("Envie uma imagem PNG, JPG ou WEBP.")
    _, width, height = info
    if min(width, height) < MIN_SIDE:
        raise InvalidReferenceError(f"Imagem pequena demais ({width}x{height}); o minimo e {MIN_SIDE}px de lado.")
    return info


class ReferenceManager:
    def __init__(self, persona_manager: PersonaManager) -> None:
        self.personas = persona_manager

    # --- caminhos --------------------------------------------------------

    def _dir(self, persona_id: str) -> Path:
        return self.personas.personas_dir / persona_id / "references"

    def _index(self, persona_id: str) -> list[dict[str, Any]]:
        return read_json(self._dir(persona_id) / "index.json", [])

    def _save(self, persona_id: str, entries: list[dict[str, Any]]) -> None:
        write_json_atomic(self._dir(persona_id) / "index.json", entries)

    def _log(self, persona_id: str, reference_id: str, action: str, changes: dict[str, Any] | None = None) -> None:
        path = self._dir(persona_id) / "history.json"
        history = read_json(path, [])
        history.append({
            "id": new_id(), "reference_id": reference_id, "action": action,
            "changes": changes or {}, "created_at": utcnow(),
        })
        write_json_atomic(path, history)

    def _find(self, entries: list[dict[str, Any]], reference_id: str) -> dict[str, Any]:
        for entry in entries:
            if entry["id"] == reference_id:
                return entry
        raise ReferenceNotFoundError(f"Referencia '{reference_id}' nao encontrada.")

    def _to_reference(self, persona_id: str, entry: dict[str, Any]) -> Reference:
        primary = bool(entry.get("is_primary"))
        stored = entry.get("type", "OTHER")
        return Reference(
            id=entry["id"],
            persona_id=persona_id,
            # A principal e sempre PRIMARY; uma que deixou de ser principal vira FACE.
            type="PRIMARY" if primary else ("FACE" if stored == "PRIMARY" else stored),
            weight=float(entry.get("weight", 1.0)),
            active=bool(entry.get("active", True)),
            is_primary=primary,
            filename=entry["filename"],
            original_filename=entry.get("original_filename", ""),
            label=entry.get("label", ""),
            width=entry.get("width"),
            height=entry.get("height"),
            created_at=entry.get("uploaded_at", ""),
            image_url=f"/personas/{persona_id}/references/{entry['id']}/file",
        )

    # --- leitura ---------------------------------------------------------

    def list(self, persona_id: str, include_inactive: bool = True) -> list[Reference]:
        self.personas.get_persona(persona_id)
        refs = [self._to_reference(persona_id, e) for e in self._index(persona_id)]
        return [r for r in refs if r.active or include_inactive]

    def primary(self, persona_id: str) -> Reference | None:
        refs = self.list(persona_id, include_inactive=False)
        return next((r for r in refs if r.is_primary), refs[0] if refs else None)

    def identity_references(self, persona_id: str) -> list[Reference]:
        """Fotos ativas que mostram o rosto, a principal primeiro e depois pelo peso."""
        refs = [r for r in self.list(persona_id, include_inactive=False) if r.type in IDENTITY_TYPES]
        return sorted(refs, key=lambda r: (not r.is_primary, -r.weight))

    def path(self, persona_id: str, reference_id: str) -> Path:
        return self._dir(persona_id) / self._find(self._index(persona_id), reference_id)["filename"]

    def read_bytes(self, reference: Reference) -> bytes:
        return (self._dir(reference.persona_id) / reference.filename).read_bytes()

    def history(self, persona_id: str) -> list[dict[str, Any]]:
        self.personas.get_persona(persona_id)
        return read_json(self._dir(persona_id) / "history.json", [])

    # --- escrita ---------------------------------------------------------

    def add(
        self, persona_id: str, original_filename: str, content: bytes,
        ref_type: str = "OTHER", weight: float = 1.0, label: str = "",
    ) -> Reference:
        _check_type_weight(ref_type, weight)
        fmt, width, height = validate_image(content)
        # O PersonaManager grava o arquivo e a entrada (a 1a vira principal).
        # A extensao vem do conteudo, nao do nome enviado.
        stem = Path(original_filename or "referencia").stem or "referencia"
        created = self.personas.add_reference(persona_id, f"{stem}.{'jpg' if fmt == 'jpeg' else fmt}", content, label)
        entries = self._index(persona_id)
        entry = self._find(entries, created.id)
        entry.update({"type": "OTHER" if ref_type == "PRIMARY" else ref_type, "weight": weight,
                      "active": True, "width": width, "height": height,
                      "original_filename": original_filename or entry["original_filename"]})
        self._save(persona_id, entries)
        self._log(persona_id, created.id, "added", {"type": ref_type, "weight": weight})
        if ref_type == "PRIMARY" and not entry["is_primary"]:
            self.set_primary(persona_id, created.id)
        return self._to_reference(persona_id, self._find(self._index(persona_id), created.id))

    def update(self, persona_id: str, reference_id: str, changes: dict[str, Any]) -> Reference:
        self.personas.get_persona(persona_id)
        entries = self._index(persona_id)
        entry = self._find(entries, reference_id)
        ref_type = changes.get("type", entry.get("type", "OTHER"))
        weight = float(changes.get("weight", entry.get("weight", 1.0)))
        _check_type_weight(ref_type, weight)
        if changes.get("active") is False and entry.get("is_primary"):
            raise InvalidReferenceError("A foto principal nao pode ser desativada: defina outra como principal antes.")
        if ref_type == "PRIMARY":
            self.set_primary(persona_id, reference_id)
            entries = self._index(persona_id)
            entry = self._find(entries, reference_id)
        else:
            entry["type"] = ref_type
        entry["weight"] = weight
        for key in ("active", "label"):
            if key in changes:
                entry[key] = changes[key]
        self._save(persona_id, entries)
        self._log(persona_id, reference_id, "updated", changes)
        return self._to_reference(persona_id, entry)

    def set_primary(self, persona_id: str, reference_id: str) -> Reference:
        entries = self._index(persona_id)
        if self._find(entries, reference_id).get("active", True) is False:
            raise InvalidReferenceError("Ative a foto antes de torna-la principal.")
        self.personas.set_primary_reference(persona_id, reference_id)
        self._log(persona_id, reference_id, "primary_set")
        return self._to_reference(persona_id, self._find(self._index(persona_id), reference_id))

    def remove(self, persona_id: str, reference_id: str) -> None:
        """Tira da lista e guarda a foto em references/removed/."""
        self.personas.get_persona(persona_id)
        entries = self._index(persona_id)
        entry = self._find(entries, reference_id)
        remaining = [e for e in entries if e["id"] != reference_id]
        if entry.get("is_primary") and remaining:
            # Promove a proxima ativa (o PersonaManager pegaria a primeira, ativa ou nao).
            nxt = next((e for e in remaining if e.get("active", True)), remaining[0])
            nxt["is_primary"] = True
        source = self._dir(persona_id) / entry["filename"]
        if source.exists():
            removed = self._dir(persona_id) / "removed"
            removed.mkdir(parents=True, exist_ok=True)
            os.replace(source, removed / entry["filename"])
        self._save(persona_id, remaining)
        self._log(persona_id, reference_id, "removed", {"filename": entry["filename"]})

    def replace(self, persona_id: str, reference_id: str, original_filename: str, content: bytes) -> Reference:
        """Foto nova no lugar de uma antiga: mesmo tipo, peso e, se era a
        principal, vira a principal."""
        old = self._to_reference(persona_id, self._find(self._index(persona_id), reference_id))
        new = self.add(persona_id, original_filename, content, "OTHER" if old.is_primary else old.type, old.weight, old.label)
        if old.is_primary:
            self.set_primary(persona_id, new.id)
        self.remove(persona_id, reference_id)
        self._log(persona_id, new.id, "replaced", {"previous": reference_id})
        return self._to_reference(persona_id, self._find(self._index(persona_id), new.id))


def _check_type_weight(ref_type: str, weight: float) -> None:
    if ref_type not in REFERENCE_TYPES:
        raise InvalidReferenceError(f"Tipo '{ref_type}' invalido. Use: {', '.join(REFERENCE_TYPES)}.")
    if not 0.0 <= weight <= 1.0:
        raise InvalidReferenceError("O peso deve ficar entre 0 e 1.")
