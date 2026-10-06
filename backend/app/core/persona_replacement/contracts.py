"""Contratos do Persona Replacement: transformar a pessoa de uma FOTO EXISTENTE na
persona, preservando a fotografia. Independente do Generation Engine (que nao
importa nada daqui); a comunicacao e por estes contratos.

A foto original e a fonte da verdade de cenario, luz, pose, roupa e camera; a
Persona Sheet, de identidade, idade, cabelo e corpo.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

STAGE_MASKS = ("face_full", "face_inner", "face_transition", "body_skin")


class ReplacementConfigError(ValueError):
    """Configuracao do replacement invalida."""


@dataclass(frozen=True)
class StageSpec:
    name: str  # face_pass_1 ... body_pass_2
    kind: str  # "face" | "body"
    mask: str
    strength: float  # opacidade da mascara ao colar (quanto da etapa fica)
    denoise: float  # quanto o recorte e redesenhado
    prompt: str
    lora: bool = True
    identity_adapter: str | None = None
    adapter_weight: float | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplacementConfig:
    stages: tuple[StageSpec, ...]
    hair: dict[str, Any]
    lighting: dict[str, Any]
    texture: dict[str, Any]
    blending: dict[str, Any]
    checks: dict[str, Any]
    negative_extra: tuple[str, ...]
    budget_limit_usd: float
    generation_config: str  # de onde vem modelo, LoRA e InstantID (so leitura)
    version: str = ""

    def stage(self, name: str) -> StageSpec:
        return next(s for s in self.stages if s.name == name)


def _unit(v: Any, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise ReplacementConfigError(f"{what} precisa ser numero.") from exc
    if not 0.0 <= x <= 1.0:
        raise ReplacementConfigError(f"{what} fora de 0-1: {x}.")
    return x


def parse_replacement_config(data: dict[str, Any]) -> ReplacementConfig:
    try:
        stages = []
        for item in data["stages"]:
            if item["mask"] not in STAGE_MASKS:
                raise ReplacementConfigError(f"Mascara '{item['mask']}' invalida ({', '.join(STAGE_MASKS)}).")
            if item["kind"] not in ("face", "body"):
                raise ReplacementConfigError(f"Etapa '{item['name']}' com tipo invalido: {item['kind']}.")
            if item["kind"] == "body" and item["mask"] != "body_skin":
                raise ReplacementConfigError("Etapa de corpo so mexe na pele do corpo (body_skin), nunca na roupa.")
            stages.append(StageSpec(
                name=item["name"], kind=item["kind"], mask=item["mask"],
                strength=_unit(item["strength"], f"{item['name']} strength"),
                denoise=_unit(item["denoise"], f"{item['name']} denoise"), prompt=item["prompt"],
                lora=bool(item.get("lora", True)), identity_adapter=item.get("identity_adapter"),
                adapter_weight=_unit(item["adapter_weight"], f"{item['name']} adapter_weight") if item.get("identity_adapter") else None,
                enabled=bool(item.get("enabled", True))))
        names = [s.name for s in stages]
        if len(set(names)) != len(names):
            raise ReplacementConfigError("Nomes de etapa repetidos.")
        face3 = next((s for s in stages if s.name == "face_pass_3"), None)
        if face3 is not None and (face3.identity_adapter or face3.denoise > 0.25):
            raise ReplacementConfigError("face_pass_3 e INTEGRACAO: sem adaptador de identidade e denoise <= 0,25.")
        for key in ("max_background_changed", "max_clothing_changed", "max_pose_distance", "max_identity_drop",
                    "min_final_face"):
            if key not in data["checks"]:
                raise ReplacementConfigError(f"Criterio '{key}' ausente em checks.")
        return ReplacementConfig(
            stages=tuple(stages), hair=dict(data["hair"]), lighting=dict(data["lighting"]), texture=dict(data["texture"]),
            blending=dict(data["blending"]), checks=dict(data["checks"]), negative_extra=tuple(data.get("negative_extra", [])),
            budget_limit_usd=float(data["budget"]["limit_usd"]), generation_config=data["generation_config"],
            version=data.get("version", ""))
    except KeyError as exc:
        raise ReplacementConfigError(f"Campo obrigatorio ausente: {exc}.") from exc


def load_replacement_config(path: Path) -> ReplacementConfig:
    return parse_replacement_config(json.loads(path.read_text(encoding="utf-8")))


# --- portas (implementadas no provider) ------------------------------------------------


@dataclass
class RawSegments:
    """O que o segmentador mede na foto: pessoa e cabelo (0-1, tamanho da foto) e
    caixas de objetos a proteger (ex.: oculos escuros)."""

    person: np.ndarray
    hair: np.ndarray | None = None
    protect_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)


class Segmenter(Protocol):
    async def segment(self, image: str, sheet: Any) -> RawSegments:
        """Segmenta a pessoa escolhida na ficha da foto."""


@dataclass
class TransformRequest:
    image: str  # locator da imagem de entrada (checkpoint atual)
    mask: np.ndarray  # 0-1, tamanho da foto: so aqui a etapa pode mexer
    prompt: str
    negative: str
    strength: float
    denoise: float
    seed: int
    name: str
    use_lora: bool = True
    identity_adapter: str | None = None
    adapter_weight: float | None = None
    reference: Any = None  # master_face (ReferenceImage) quando ha adaptador


@dataclass
class TransformResult:
    image: str
    seconds: float
    gpu: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ReplacementTransformer(Protocol):
    async def transform(self, request: TransformRequest) -> TransformResult:
        """Transformacao localizada: so dentro da mascara; fora, a imagem de entrada."""


class ImageStore(Protocol):
    async def load(self, image: str) -> np.ndarray:
        """RGB uint8 (H, W, 3)."""

    async def save(self, pixels: np.ndarray, name: str) -> str:
        """Grava e devolve o locator."""


__all__ = ["STAGE_MASKS", "ImageStore", "RawSegments", "ReplacementConfig", "ReplacementConfigError",
           "ReplacementTransformer", "Segmenter", "StageSpec", "TransformRequest", "TransformResult",
           "load_replacement_config", "parse_replacement_config"]
