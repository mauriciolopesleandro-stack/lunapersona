"""Configuracao do Persona Engine V2 (experimental): perfis de modelo e passadas.

O nucleo so enxerga dados: qual perfil usar, que LoRA, que amostragem, que
passadas (mascara, strength, denoise). Quem traduz isso para um grafo e o
adapter do provider (ModelAdapter). Trocar RealVisXL por Lustify = trocar
`generation_model`, sem mexer no nucleo.

Nada aqui e hardcoded: tudo vem de config/persona_engine_v2.json e e validado
ao carregar (erro explicito, nunca valor padrao escondido).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FACE_MASKS = ("face_full", "face_inner", "face_skin")
BODY_MASKS = ("body_full", "body_regions")


class V2ConfigError(ValueError):
    """Configuracao da V2 invalida ou incompleta."""


@dataclass(frozen=True)
class Sampling:
    steps: int
    cfg: float
    sampler: str
    scheduler: str


@dataclass(frozen=True)
class ModelProfile:
    id: str
    title: str
    family: str
    workflow: str
    checkpoint: str
    sha256: str
    size_bytes: int
    source: str
    sampling: Sampling
    download_auth: str | None = None
    extra_negative: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoraSpec:
    file: str
    strength: float
    sha256: str = ""


@dataclass(frozen=True)
class PassSpec:
    kind: str  # "face" | "body"
    number: int  # 1..3 rosto, 1..2 corpo
    name: str
    mask: str
    strength: float
    denoise: float
    enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class V2Config:
    generation_model: str
    models: dict[str, ModelProfile]
    lora: LoraSpec
    common_negative: tuple[str, ...]
    width: int
    height: int
    face_passes: tuple[PassSpec, ...]
    body_passes: tuple[PassSpec, ...]
    age_target: int
    benchmark_limit_usd: float
    status: str = "EXPERIMENTAL"
    pass_steps: int = 20
    pass_prompts: dict[str, str] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)

    def model(self, model_id: str | None = None) -> ModelProfile:
        key = model_id or self.generation_model
        if key not in self.models:
            raise V2ConfigError(f"Modelo '{key}' nao existe na configuracao da V2 ({', '.join(self.models)}).")
        return self.models[key]

    def negative_for(self, profile: ModelProfile) -> list[str]:
        return list(dict.fromkeys([*self.common_negative, *profile.extra_negative]))


def _unit(value: Any, what: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise V2ConfigError(f"{what} precisa ser numero.") from exc
    if not 0.0 <= v <= 1.0:
        raise V2ConfigError(f"{what} fora de 0-1: {v}.")
    return v


def _passes(items: list[dict[str, Any]], kind: str, masks: tuple[str, ...], limit: int) -> tuple[PassSpec, ...]:
    if len(items) > limit:
        raise V2ConfigError(f"No maximo {limit} passadas de {kind}.")
    out = []
    for n, item in enumerate(items, start=1):
        if item.get("mask") not in masks:
            raise V2ConfigError(f"Mascara '{item.get('mask')}' invalida para passada de {kind} ({', '.join(masks)}).")
        out.append(PassSpec(kind, n, str(item["name"]), item["mask"], _unit(item.get("strength"), f"{kind} {n} strength"),
                            _unit(item.get("denoise"), f"{kind} {n} denoise"), bool(item.get("enabled", False))))
    return tuple(out)


def parse_v2_config(data: dict[str, Any]) -> V2Config:
    try:
        models = {}
        for mid, m in data["models"].items():
            s = m["sampling"]
            if not m.get("checkpoint") or not m.get("workflow"):
                raise V2ConfigError(f"Modelo '{mid}' sem checkpoint ou workflow.")
            models[mid] = ModelProfile(
                id=mid, title=m["title"], family=m["family"], workflow=m["workflow"], checkpoint=m["checkpoint"],
                sha256=m.get("sha256", ""), size_bytes=int(m.get("size_bytes", 0)), source=m.get("source", ""),
                sampling=Sampling(int(s["steps"]), float(s["cfg"]), s["sampler"], s["scheduler"]),
                download_auth=m.get("download_auth"), extra_negative=tuple(m.get("extra_negative", [])),
            )
        lora = data["lora"]
        if not lora.get("file"):
            raise V2ConfigError("A V2 precisa da LoRA da persona (lora.file).")
        strength = float(lora["strength"])
        if not 0.0 < strength <= 2.0:
            raise V2ConfigError(f"lora.strength fora de 0-2: {strength}.")
        cfg = V2Config(
            generation_model=data["generation_model"], models=models,
            lora=LoraSpec(lora["file"], strength, lora.get("sha256", "")),
            common_negative=tuple(data.get("common_negative", [])),
            width=int(data["size"]["width"]), height=int(data["size"]["height"]),
            face_passes=_passes(data.get("face_passes", []), "face", FACE_MASKS, 3),
            body_passes=_passes(data.get("body_passes", []), "body", BODY_MASKS, 2),
            age_target=int(data["age_target"]),
            benchmark_limit_usd=float(data["benchmark"]["limit_usd"]),
            status=data.get("status", "EXPERIMENTAL"),
            pass_steps=int(data.get("pass_steps", 20)),
            pass_prompts=dict(data.get("pass_prompts", {})),
            acceptance=dict(data.get("acceptance", {})),
        )
        enabled = [p for p in (*cfg.face_passes, *cfg.body_passes) if p.enabled]
        if enabled:
            needed = {p.name for p in cfg.face_passes if p.enabled} | ({"body"} if any(p.kind == "body" for p in enabled) else set())
            missing = sorted(needed - set(cfg.pass_prompts))
            if missing:
                raise V2ConfigError(f"Passadas ligadas sem prompt em pass_prompts: {', '.join(missing)}.")
            for key in ("max_identity_drop", "max_age_worsening", "max_pose_distance"):
                if key not in cfg.acceptance:
                    raise V2ConfigError(f"Passadas ligadas sem o criterio de aceite '{key}'.")
    except KeyError as exc:
        raise V2ConfigError(f"Campo obrigatorio ausente na configuracao da V2: {exc}.") from exc
    cfg.model()  # generation_model precisa existir
    return cfg


def load_v2_config(path: Path) -> V2Config:
    return parse_v2_config(json.loads(path.read_text(encoding="utf-8")))


__all__ = ["BODY_MASKS", "FACE_MASKS", "LoraSpec", "ModelProfile", "PassSpec", "Sampling", "V2Config",
           "V2ConfigError", "load_v2_config", "parse_v2_config"]
