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
class PersonaLora:
    """LoRA treinada da persona. Quando o arquivo existe no ComfyUI, a
    geracao usa o gatilho + a LoRA em vez do texto de identidade."""

    file: str
    trigger: str
    strength: float = 1.0
    workflow_id: str = ""
    # Guidance usado com a LoRA (mais baixo = foto mais natural). None = o do pedido.
    guidance: float | None = None
    # LoRA da persona para o Qwen-Image-Edit 2511 (pack com a persona,
    # scripts/train_qwen_lora.sh). Sem o arquivo no pod, o pack segue sem ela.
    qwen_file: str = ""
    qwen_strength: float = 1.0
    # LoRA da persona no Z-Image Turbo (scripts/train_zimage_lora.sh): com o
    # arquivo no pod, foto sem foto de referencia sai pelo Z-Image (8 passos,
    # ~17 s) em vez do Chroma (~3-4 min com ampliacao e retoque).
    zimage_file: str = ""
    zimage_strength: float = 1.0


@dataclass
class PersonaVoice:
    """Voz oficial da persona: um audio de referencia (escolhido entre as
    opcoes criadas por descricao) + o texto falado nele. Toda fala nova clona
    essa referencia, entao a voz nao muda de uma geracao para outra."""

    file: str  # relativo a pasta da persona, ex: voice/reference.flac
    text: str
    description: str = ""


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
    lora: PersonaLora | None = None
    voice: PersonaVoice | None = None
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

    def reference_prompt_fragment(self) -> str:
        """Cabelo, olhos, pele e corpo da persona. Com foto de referencia, as
        cores e o formato do corpo da pessoa da foto vencem a LoRA; dizer os da
        persona no prompt e o que faz a troca acontecer."""
        fields = ("cor_cabelo", "olhos", "tom_pele", "caracteristicas_corporais")
        parts = [self.identity.fixed.get(f, "").strip() for f in fields]
        return ", ".join(p for p in parts if p)

    def attitude_prompt_fragment(self) -> str:
        """Expressao padrao da persona (variable_defaults.expressao). Sem ela a
        LoRA copia a expressao da foto de referencia (boca aberta, surpresa)
        e a persona perde a personalidade."""
        return self.identity.variable_defaults.get("expressao", "").strip()

    def body_prompt_fragment(self) -> str:
        """Corpo da persona. Vai em toda geracao com LoRA: so com a LoRA o corpo
        saia mais magro que o padrao em fotos de corpo inteiro."""
        return self.identity.fixed.get("caracteristicas_corporais", "").strip()


def _reference(entry: dict[str, Any]) -> PersonaReference:
    # O index.json tambem guarda tipo, peso e ativo (ReferenceManager do
    # Persona Engine); aqui so entram os campos que este manager conhece.
    known = PersonaReference.__dataclass_fields__
    return PersonaReference(**{k: v for k, v in entry.items() if k in known})


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
            path = entry / "persona.json"
            if entry.is_dir() and path.exists():
                # Persona removida pelo Persona Engine (DELETE e so desativar).
                if json.loads(path.read_text(encoding="utf-8")).get("engine", {}).get("active") is False:
                    continue
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
        lora_data = data.get("lora")
        lora = None
        if lora_data and lora_data.get("file") and lora_data.get("trigger"):
            lora = PersonaLora(
                file=lora_data["file"],
                trigger=lora_data["trigger"],
                strength=float(lora_data.get("strength", 1.0)),
                workflow_id=lora_data.get("workflow_id", ""),
                guidance=float(lora_data["guidance"]) if lora_data.get("guidance") is not None else None,
                qwen_file=lora_data.get("qwen_file", ""),
                qwen_strength=float(lora_data.get("qwen_strength", 1.0)),
                zimage_file=lora_data.get("zimage_file", ""),
                zimage_strength=float(lora_data.get("zimage_strength", 1.0)),
            )
        voice_data = data.get("voice")
        voice = None
        if voice_data and voice_data.get("file"):
            voice = PersonaVoice(
                file=voice_data["file"],
                text=voice_data.get("text", ""),
                description=voice_data.get("description", ""),
            )
        persona = Persona(
            id=data.get("id", persona_id),
            name=data.get("name", persona_id),
            description=data.get("description", ""),
            identity=identity,
            generation=generation,
            lora=lora,
            voice=voice,
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
        # Chaves que este manager nao conhece (o bloco "engine" do Persona
        # Engine, app/core/persona) ficam como estavam.
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        payload = {
            **previous,
            "id": persona.id,
            "name": persona.name,
            "description": persona.description,
            "identity": asdict(persona.identity),
            "generation": asdict(persona.generation),
            "identity_methods": persona.identity_methods,
        }
        payload.pop("lora", None)
        payload.pop("voice", None)
        if persona.lora:
            payload["lora"] = asdict(persona.lora)
        if persona.voice:
            payload["voice"] = asdict(persona.voice)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def set_voice(self, persona_id: str, ext: str, content: bytes, text: str, description: str) -> Persona:
        """Grava o audio escolhido como a voz da persona (substitui a anterior)."""
        persona = self.get_persona(persona_id)
        voice_dir = self._persona_dir(persona_id) / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        for old in voice_dir.glob("reference.*"):
            old.unlink()
        filename = f"reference{ext}"
        (voice_dir / filename).write_bytes(content)
        persona.voice = PersonaVoice(file=f"voice/{filename}", text=text, description=description)
        self._save_persona(persona)
        return persona

    def get_voice_path(self, persona_id: str) -> Path | None:
        voice = self.get_persona(persona_id).voice
        if not voice:
            return None
        path = self._persona_dir(persona_id) / voice.file
        return path if path.exists() else None

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
        return [_reference(entry) for entry in entries]

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
        return _reference(entry)

    def get_reference_path(self, persona_id: str, reference_id: str) -> Path:
        entries = self._load_reference_index(persona_id)
        for entry in entries:
            if entry["id"] == reference_id:
                return self._references_dir(persona_id) / entry["filename"]
        raise ReferenceNotFoundError(f"Referencia '{reference_id}' nao encontrada.")

    def get_primary_reference_bytes(self, persona_id: str) -> tuple[str, bytes] | None:
        """Retorna (nome_do_arquivo, conteudo) da referencia principal da
        persona, ou None se ela nao tiver nenhuma - usado pelo
        GenerationService para ancorar a identidade numa imagem real via
        FLUX Kontext em vez de so descricao em texto."""
        entries = self._load_reference_index(persona_id)
        if not entries:
            return None
        primary = next((e for e in entries if e.get("is_primary")), entries[0])
        path = self._references_dir(persona_id) / primary["filename"]
        if not path.exists():
            return None
        return primary["filename"], path.read_bytes()

    def set_primary_reference(self, persona_id: str, reference_id: str) -> list[PersonaReference]:
        """Marca uma referencia como a principal (foto do card da persona)."""
        entries = self._load_reference_index(persona_id)
        if not any(e["id"] == reference_id for e in entries):
            raise ReferenceNotFoundError(f"Referencia '{reference_id}' nao encontrada.")
        for entry in entries:
            entry["is_primary"] = entry["id"] == reference_id
        self._save_reference_index(persona_id, entries)
        return [_reference(e) for e in entries]

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
