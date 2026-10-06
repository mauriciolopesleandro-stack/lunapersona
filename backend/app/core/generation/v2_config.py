"""Configuracao do Persona Engine V2 (experimental): perfis de modelo e passadas.

O nucleo so enxerga dados: qual perfil usar, que LoRA, que amostragem, que
passadas (mascara, strength, denoise). Quem traduz isso para um grafo e o
adapter do provider (ModelAdapter). Trocar RealVisXL por Lustify = trocar
`generation_model`, sem mexer no nucleo.

Nada aqui e hardcoded: tudo vem de config/persona_engine_v2.json e e validado
ao carregar (erro explicito, nunca valor padrao escondido).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

FACE_MASKS = ("face_full", "face_inner", "face_skin")
BODY_MASKS = ("body_full", "body_regions")
PREP_MASKS = ("hair", "arms")  # modo replicar: cabelo da persona e bracos sem tatuagem


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
    lora: bool = True  # False = passada sem a LoRA (so textura); peso da LoRA nunca muda
    identity_adapter: str | None = None  # ex.: "instantid" (so onde a config pedir)
    adapter_weight: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StyleProfile:
    """Alvo de realismo fotografico (ex.: selfie de celular crua). Vai no prompt
    da base e das passadas; o negativo soma ao negativo comum."""

    id: str
    positive: str
    negative: tuple[str, ...] = ()
    reference: str = ""
    reference_sha256: str = ""


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
    style: StyleProfile | None = None
    identity_adapters: dict[str, Any] = field(default_factory=dict)
    identity_lock: dict[str, Any] = field(default_factory=dict)
    replicate: dict[str, Any] = field(default_factory=dict)
    pre_passes: tuple[PassSpec, ...] = ()

    def model(self, model_id: str | None = None) -> ModelProfile:
        key = model_id or self.generation_model
        if key not in self.models:
            raise V2ConfigError(f"Modelo '{key}' nao existe na configuracao da V2 ({', '.join(self.models)}).")
        return self.models[key]

    def negative_for(self, profile: ModelProfile) -> list[str]:
        style = list(self.style.negative) if self.style else []
        return list(dict.fromkeys([*self.common_negative, *profile.extra_negative, *style]))

    def styled(self, text: str) -> str:
        """Texto com o estilo: `{style}` e substituido; sem o marcador, o estilo vai no fim."""
        style = self.style.positive if self.style else ""
        if "{style}" in text:
            return text.replace("{style}", style).strip().rstrip(",").strip()
        return f"{text}, {style}" if style else text


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
                            _unit(item.get("denoise"), f"{kind} {n} denoise"), bool(item.get("enabled", False)),
                            bool(item.get("lora", True)), item.get("identity_adapter"),
                            _unit(item["adapter_weight"], f"{kind} {n} adapter_weight") if item.get("identity_adapter") else None))
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
            style=StyleProfile(data["style"]["id"], data["style"]["positive"], tuple(data["style"].get("negative", [])),
                               data["style"].get("reference", ""), data["style"].get("reference_sha256", ""))
            if data.get("style") else None,
            identity_adapters=dict(data.get("identity_adapters", {})),
            replicate=dict(data.get("replicate", {})),
            pre_passes=_passes((data.get("replicate") or {}).get("pre_passes", []), "prep", PREP_MASKS, 2),
        )
        for spec in cfg.face_passes:
            if spec.identity_adapter and spec.identity_adapter not in cfg.identity_adapters:
                raise V2ConfigError(f"Adaptador de identidade '{spec.identity_adapter}' sem configuracao em identity_adapters.")
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
    lock = data.get("identity_lock") or {}
    if lock.get("locked"):
        actual = identity_fingerprint(data)
        if actual != lock.get("fingerprint"):
            raise V2ConfigError(
                "A receita do rosto da persona esta TRAVADA e foi alterada (modelo, LoRA, passadas ou InstantID). "
                f"Impressao esperada {str(lock.get('fingerprint'))[:12]}, atual {actual[:12]}. "
                "Para mudar, destrave com nova versao (identity_lock) de proposito.")
        cfg = replace(cfg, identity_lock=dict(lock, fingerprint=actual))
    return cfg


# O que define o rosto da persona na V2. Mudar qualquer um destes campos muda a impressao.
LOCKED_FIELDS = ("generation_model", "lora", "face_passes", "body_passes", "identity_adapters", "pass_steps")


def identity_fingerprint(data: dict[str, Any]) -> str:
    """sha256 da receita do rosto: modelo e checkpoint, LoRA, passadas (mascara, strength,
    denoise, LoRA, adaptador), InstantID e passos. Estilo e cenas ficam de fora."""
    model = data["models"][data["generation_model"]]
    payload = {k: data.get(k) for k in LOCKED_FIELDS}
    payload["checkpoint"] = {"file": model["checkpoint"], "sha256": model.get("sha256"), "sampling": model["sampling"]}
    payload["prompts"] = {k: v for k, v in (data.get("pass_prompts") or {}).items() if k != "body"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def load_v2_config(path: Path) -> V2Config:
    return parse_v2_config(json.loads(path.read_text(encoding="utf-8")))


__all__ = ["BODY_MASKS", "FACE_MASKS", "PREP_MASKS", "LOCKED_FIELDS", "identity_fingerprint", "LoraSpec", "ModelProfile", "PassSpec", "Sampling", "StyleProfile", "V2Config",
           "V2ConfigError", "load_v2_config", "parse_v2_config"]
