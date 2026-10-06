"""Telemetria por passada do Persona Engine V2 (base, rosto 1-3, corpo 1-2).

Um registro por passada com tudo o que e preciso para reproduzir e comparar:
modelo, LoRA, semente, hashes do prompt e do negativo, mascara, strength,
denoise, notas medidas, tempo, GPU, VRAM, custo e rollback. Nota que nao foi
medida fica None (nunca 0 e nunca copiada de outra passada).
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

BASE = "base"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class PassRecord:
    model: str
    model_version: str
    lora: str
    lora_strength: float
    seed: int
    prompt_hash: str
    negative_hash: str
    pass_type: str  # base | face | body
    pass_number: int  # 0 = base
    mask_type: str | None
    denoise: float | None
    strength: float | None
    face_identity_score: float | None = None
    age_score: float | None = None
    skin_score: float | None = None
    body_score: float | None = None
    pose_score: float | None = None
    anatomy_score: float | None = None
    subject_count: int | None = None
    duration: float | None = None
    gpu: str | None = None
    vram: int | None = None
    cost: float | None = None
    rollback: bool = False
    rollback_reason: str | None = None
    image: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def base_record(*, model: str, model_version: str, lora: str, lora_strength: float, seed: int, prompt: str,
                negative: str) -> PassRecord:
    return PassRecord(model=model, model_version=model_version, lora=lora, lora_strength=lora_strength, seed=seed,
                      prompt_hash=text_hash(prompt), negative_hash=text_hash(negative), pass_type=BASE, pass_number=0,
                      mask_type=None, denoise=1.0, strength=None)


def cost_of(seconds: float | None, price_per_hour: float | None) -> float | None:
    if seconds is None or price_per_hour is None:
        return None
    return round(price_per_hour * seconds / 3600, 5)


__all__ = ["BASE", "PassRecord", "base_record", "cost_of", "text_hash"]
