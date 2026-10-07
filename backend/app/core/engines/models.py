"""Registro de modelos da V2 (config/model_registry_v2.json) e escolha do checkpoint.

O core so conhece IDs logicos ("realvisxl", "lustify"); nome de arquivo, hash, origem e
LICENCA ficam no registro. Escolha explicita de um modelo indisponivel e ERRO - nunca ha
fallback silencioso para outro checkpoint.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AVAILABLE, MISSING, LICENSE_REVIEW = "AVAILABLE", "MISSING", "LICENSE_REVIEW_REQUIRED"
AUTO = "auto"


class ModelUnavailableError(Exception):
    """O modelo pedido nao pode ser usado (faltando, sem licenca conferida...)."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    kind: str
    title: str
    file: str
    version: str
    source: str
    license: str
    commercial_use: str
    restrictions: str
    sha256: str
    size_bytes: int | None
    status: str
    license_status: str
    provisioning: str
    sampling: dict[str, Any] = field(default_factory=dict)
    vram_gb: float | None = None
    default: bool = False
    download_auth: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {"model": self.id, "title": self.title, "file": self.file, "version": self.version, "source": self.source,
                "license": self.license, "commercial_use": self.commercial_use, "restrictions": self.restrictions,
                "hash": self.sha256, "status": self.status, "license_status": self.license_status}


class ModelRegistry:
    def __init__(self, data: dict[str, Any]) -> None:
        self.version = data.get("version", "")
        self._models: dict[str, ModelInfo] = {}
        for kind in ("checkpoints", "loras", "head_swap", "controls", "generation_v1"):
            for mid, d in data.get(kind, {}).items():
                self._models[mid] = ModelInfo(
                    id=mid, kind=kind, title=d.get("title", mid), file=d.get("file") or ", ".join(d.get("files", [])),
                    version=d.get("version", ""), source=d.get("source", ""), license=d.get("license", "unknown"),
                    commercial_use=d.get("commercial_use", "unknown"), restrictions=d.get("restrictions", ""),
                    sha256=d.get("sha256", ""), size_bytes=d.get("size_bytes"), status=d.get("status", MISSING),
                    license_status=d.get("license_status", "OK" if d.get("commercial_use") in ("allowed", "owner", "allowed_with_use_restrictions") else LICENSE_REVIEW),
                    provisioning=d.get("provisioning", ""), sampling=dict(d.get("sampling", {})), vram_gb=d.get("vram_gb"),
                    default=bool(d.get("default")), download_auth=d.get("download_auth"))

    @classmethod
    def load(cls, path: Path) -> "ModelRegistry":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def get(self, model_id: str) -> ModelInfo:
        if model_id not in self._models:
            raise ModelUnavailableError(f"modelo desconhecido: {model_id}")
        return self._models[model_id]

    def checkpoints(self) -> list[ModelInfo]:
        return [m for m in self._models.values() if m.kind == "checkpoints"]

    def select_checkpoint(self, choice: str = AUTO, commercial: bool = False) -> ModelInfo:
        """'auto' = o checkpoint padrao do registro. Escolha explicita indisponivel = erro (sem fallback)."""
        if choice == AUTO:
            m = next((c for c in self.checkpoints() if c.default), None)
            if m is None:
                raise ModelUnavailableError("nenhum checkpoint padrao no registro")
        else:
            m = self.get(choice)
            if m.kind != "checkpoints":
                raise ModelUnavailableError(f"{choice} nao e um checkpoint")
        if m.status != AVAILABLE:
            raise ModelUnavailableError(f"{m.title} indisponivel ({m.status}). {m.download_auth or ''}".strip())
        if commercial and m.license_status == LICENSE_REVIEW:
            raise ModelUnavailableError(f"{m.title}: {LICENSE_REVIEW} - uso comercial nao confirmado ({m.license})")
        return m

    def license_report(self) -> list[dict[str, Any]]:
        return [m.metadata() | {"kind": m.kind} for m in self._models.values()]


__all__ = ["AUTO", "AVAILABLE", "LICENSE_REVIEW", "MISSING", "ModelInfo", "ModelRegistry", "ModelUnavailableError"]
