"""Gerencia personas (personagens): perfil, Identity Profile e referencias
visuais. Cada persona vive em /personas/<id>/, com um persona.json (perfil)
e uma pasta references/ (imagens + index.json de metadados).

Nao implementa nenhum metodo de preservacao de identidade (LoRA, IP-Adapter,
FaceID) - apenas guarda os dados de forma organizada para que esses metodos
possam ser plugados depois. O unico mecanismo ativo hoje e "image prompting"
por texto: o GenerationService concatena as caracteristicas fixas da persona
ao prompt do usuario antes de renderizar o workflow.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Campos deliberadamente em portugues, espelhando o vocabulario do
# Identity Profile definido para o projeto (formato do rosto, olhos, etc.).
FIXED_IDENTITY_FIELDS = [
    "formato_rosto",
    "caracteristicas_faciais",
    "olhos",
    "sobrancelhas",
    "nariz",
    "boca",
    "formato_cabelo",
    "cor_cabelo",
    "textura_cabelo",
    "tom_pele",
    "caracteristicas_corporais",
    "caracteristicas_visuais_permanentes",
]

VARIABLE_DEFAULT_FIELDS = [
    "roupa",
    "cenario",
    "iluminacao",
    "pose",
    "expressao",
    "camera",
]

REFERENCE_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class PersonaError(Exception):
    pass


class PersonaNotFoundError(PersonaError):
    pass


class ReferenceNotFoundError(PersonaError):
    pass


class InvalidReferenceFileError(PersonaError):
    pass


@dataclass
class PersonaIdentity:
    fixed: dict[str, str] = field(default_factory=dict)
    variable_defaults: dict[str, str] = field(default_factory=dict)


@dataclass
class PersonaGeneration:
    model_id: str = ""
    workflow_id: str = ""


@dataclass
class PersonaReference:
    id: str
    filename: str
    original_filename: str
    uploaded_at: str
    label: str = ""
    is_primary: bool = False


@dataclass
class Persona:
    id: str
    name: str
    description: str = ""
    identity: PersonaIdentity = field(default_factory=PersonaIdentity)
    generation: PersonaGeneration = field(default_factory=PersonaGeneration)
    references: list[PersonaReference] = field(default_factory=list)
    identity_methods: dict[str, list[str]] = field(
        default_factory=lambda: {
            "planned": ["image_prompting", "ip_adapter", "faceid", "lora"],
            "active": ["image_prompting"],
        }
    )

    def identity_prompt_fragment(self) -> str:
        """Monta um fragmento de texto com as caracteristicas fixas nao vazias.

        E o unico mecanismo de identidade ativo nesta fase: pura composicao
        de prompt, sem nenhuma alteracao no grafo do ComfyUI.
        """
        parts = [v.strip() for v in self.identity.fixed.values() if v and v.strip()]
        return ", ".join(parts)


class PersonaManager:
    def __init__(self, personas_dir: Path) -> None:
        self.personas_dir = personas_dir

    def _persona_dir(self, persona_id: str) -> Path:
        return self.personas_dir / persona_id

    def _persona_json_path(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / "persona.json"

    def _references_dir(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / "references"

    def _references_index_path(self, persona_id: str) -> Path:
        return self._references_dir(persona_id) / "index.json"

    def list_personas(self) -> list[Persona]:
        if not self.personas_dir.exists():
            return []
        personas = []
        for entry in sorted(self.personas_dir.iterdir()):
            if entry.is_dir() and (entry / "persona.json").exists():
                personas.append(self.get_persona(entry.name))
        return personas

    def get_persona(self, persona_id: str) -> Persona:
        path = self._persona_json_path(persona_id)
        if not path.exists():
            raise PersonaNotFoundError(f"Persona '{persona_id}' nao encontrada.")
        data = json.loads(path.read_text(encoding="utf-8"))

        identity_data = data.get("identity", {})
        identity = PersonaIdentity(
            fixed=identity_data.get("fixed", {}),
            variable_defaults=identity_data.get("variable_defaults", {}),
        )
        generation_data = data.get("generation", {})
        generation = PersonaGeneration(
            model_id=generation_data.get("model_id", ""),
            workflow_id=generation_data.get("workflow_id", ""),
        )
        persona = Persona(
            id=data.get("id", persona_id),
            name=data.get("name", persona_id),
            description=data.get("description", ""),
            identity=identity,
            generation=generation,
            identity_methods=data.get(
                "identity_methods",
                {"planned": ["image_prompting", "ip_adapter", "faceid", "lora"], "active": ["image_prompting"]},
            ),
        )
        persona.references = self.list_references(persona_id)
        return persona

    def _save_persona(self, persona: Persona) -> None:
        path = self._persona_json_path(persona.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": persona.id,
            "name": persona.name,
            "description": persona.description,
            "identity": asdict(persona.identity),
            "generation": asdict(persona.generation),
            "identity_methods": persona.identity_methods,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def update_identity(
        self,
        persona_id: str,
        fixed: dict[str, str] | None,
        variable_defaults: dict[str, str] | None,
    ) -> Persona:
        persona = self.get_persona(persona_id)
        if fixed is not None:
            persona.identity.fixed = {k: v for k, v in fixed.items() if k in FIXED_IDENTITY_FIELDS}
        if variable_defaults is not None:
            persona.identity.variable_defaults = {
                k: v for k, v in variable_defaults.items() if k in VARIABLE_DEFAULT_FIELDS
            }
        self._save_persona(persona)
        return persona

    def update_generation_defaults(
        self, persona_id: str, model_id: str | None, workflow_id: str | None
    ) -> Persona:
        persona = self.get_persona(persona_id)
        if model_id is not None:
            persona.generation.model_id = model_id
        if workflow_id is not None:
            persona.generation.workflow_id = workflow_id
        self._save_persona(persona)
        return persona

    # --- Referencias -----------------------------------------------------

    def _load_reference_index(self, persona_id: str) -> list[dict[str, Any]]:
        index_path = self._references_index_path(persona_id)
        if not index_path.exists():
            return []
        return json.loads(index_path.read_text(encoding="utf-8"))

    def _save_reference_index(self, persona_id: str, entries: list[dict[str, Any]]) -> None:
        index_path = self._references_index_path(persona_id)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_references(self, persona_id: str) -> list[PersonaReference]:
        entries = self._load_reference_index(persona_id)
        return [PersonaReference(**entry) for entry in entries]

    def add_reference(
        self, persona_id: str, original_filename: str, content: bytes, label: str = ""
    ) -> PersonaReference:
        # Garante que a persona existe antes de aceitar o upload.
        self.get_persona(persona_id)

        ext = Path(original_filename).suffix.lower()
        if ext not in REFERENCE_ALLOWED_EXTENSIONS:
            raise InvalidReferenceFileError(
                f"Extensao '{ext}' nao suportada. Use: {', '.join(sorted(REFERENCE_ALLOWED_EXTENSIONS))}."
            )

        reference_id = uuid.uuid4().hex
        filename = f"{reference_id}{ext}"
        references_dir = self._references_dir(persona_id)
        references_dir.mkdir(parents=True, exist_ok=True)
        (references_dir / filename).write_bytes(content)

        entries = self._load_reference_index(persona_id)
        is_primary = len(entries) == 0  # a primeira referencia vira principal por padrao
        entry = {
            "id": reference_id,
            "filename": filename,
            "original_filename": original_filename,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "is_primary": is_primary,
        }
        entries.append(entry)
        self._save_reference_index(persona_id, entries)
        return PersonaReference(**entry)

    def get_reference_path(self, persona_id: str, reference_id: str) -> Path:
        entries = self._load_reference_index(persona_id)
        for entry in entries:
            if entry["id"] == reference_id:
                return self._references_dir(persona_id) / entry["filename"]
        raise ReferenceNotFoundError(f"Referencia '{reference_id}' nao encontrada.")

    def delete_reference(self, persona_id: str, reference_id: str) -> None:
        entries = self._load_reference_index(persona_id)
        remaining = [e for e in entries if e["id"] != reference_id]
        if len(remaining) == len(entries):
            raise ReferenceNotFoundError(f"Referencia '{reference_id}' nao encontrada.")

        file_path = self._references_dir(persona_id) / next(
            e["filename"] for e in entries if e["id"] == reference_id
        )
        file_path.unlink(missing_ok=True)

        # Se a referencia principal foi removida, promove a proxima.
        if remaining and not any(e["is_primary"] for e in remaining):
            remaining[0]["is_primary"] = True

        self._save_reference_index(persona_id, remaining)
