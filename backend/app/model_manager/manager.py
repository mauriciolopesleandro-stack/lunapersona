"""Camada de gerenciamento de modelos: le /models/registry.json e expoe os
modelos disponiveis para o resto do backend, sem acoplar o nucleo a um
modelo especifico. Nao baixa nem valida pesos em disco - so metadados.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ModelNotFoundError(Exception):
    pass


@dataclass
class ModelDefinition:
    id: str
    name: str
    engine: str
    type: str
    status: str
    files: dict[str, str]
    loader_config: dict[str, Any]
    compatible_workflows: list[str]
    defaults: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


class ModelManager:
    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path

    def _load_all(self) -> list[ModelDefinition]:
        if not self.registry_path.exists():
            return []
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        return [
            ModelDefinition(
                id=m["id"],
                name=m.get("name", m["id"]),
                engine=m.get("engine", ""),
                type=m.get("type", "image-generation"),
                status=m.get("status", "unknown"),
                files=m.get("files", {}),
                loader_config=m.get("loader_config", {}),
                compatible_workflows=m.get("compatible_workflows", []),
                defaults=m.get("defaults", {}),
                notes=m.get("notes", ""),
            )
            for m in data.get("models", [])
        ]

    def list_models(self) -> list[ModelDefinition]:
        return self._load_all()

    def get_model(self, model_id: str) -> ModelDefinition:
        for m in self._load_all():
            if m.id == model_id:
                return m
        raise ModelNotFoundError(f"Modelo '{model_id}' nao encontrado no registry.")

    def loader_params(self, model_id: str) -> dict[str, Any]:
        """Parametros derivados do modelo para injetar no workflow (nomes de arquivo, dtype etc.)."""
        m = self.get_model(model_id)
        return {
            "UNET_NAME": m.files.get("unet_name", ""),
            "CLIP_NAME_1": m.files.get("clip_name_1", ""),
            "CLIP_NAME_2": m.files.get("clip_name_2", ""),
            "VAE_NAME": m.files.get("vae_name", ""),
            "UNET_WEIGHT_DTYPE": m.loader_config.get("unet_weight_dtype", "default"),
            "CLIP_TYPE": m.loader_config.get("clip_type", "flux"),
        }
