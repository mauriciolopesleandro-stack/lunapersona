"""ModelAdapter da V2: a unica porta entre as engines e um backend de geracao.

As engines (Replacement, FaceSwap) falam so em mascaras, controles e referencias; o adapter
traduz para o provider (hoje ComfyUI). Nenhum nome de arquivo, workflow ou API aparece no core.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ControlSpec:
    """Condicionamento estrutural. structure = imagem de onde sai a pose/profundidade."""
    pose_strength: float = 0.0
    depth_strength: float = 0.0
    end_percent: float = 0.8
    structure: str | None = None


@dataclass
class IdentitySpec:
    """Identidade: LoRA (forca FIXA do registro) + referencia facial opcional."""
    use_lora: bool = True
    reference: Any = None  # ReferenceImage da master
    reference_strength: float = 0.0  # 0 = sem adaptador de referencia


@dataclass
class InpaintRequest:
    image: str  # locator da imagem de entrada
    mask: np.ndarray  # 0-1 do tamanho da imagem; so aqui o modelo mexe
    prompt: str
    negative: str
    denoise: float
    seed: int
    stage: str
    controls: ControlSpec = field(default_factory=ControlSpec)
    identity: IdentitySpec = field(default_factory=IdentitySpec)
    steps: int | None = None
    cfg: float | None = None
    work_side: int = 1024  # lado maior do recorte durante a passada (hi-res: 1536)
    strength: float = 1.0  # opacidade da colagem dentro da mascara


@dataclass
class AdapterResult:
    image: str
    seconds: float
    gpu: dict[str, Any]
    parameters: dict[str, Any]


class ModelAdapter(ABC):
    """Interface generica (RealVisXL, Lustify, futuros)."""

    model_id: str = ""

    @abstractmethod
    async def load(self) -> list[str]:
        """Confere/carrega o que o backend precisa; devolve problemas (vazio = ok)."""

    async def unload(self) -> None:
        return None

    @abstractmethod
    async def generate(self, prompt: str, negative: str, width: int, height: int, seed: int, **kw) -> AdapterResult: ...

    @abstractmethod
    async def inpaint(self, request: InpaintRequest) -> AdapterResult: ...

    async def refine(self, request: InpaintRequest) -> AdapterResult:
        """Refinamento = inpaint com denoise baixo na mesma regiao (o adapter pode especializar)."""
        return await self.inpaint(request)

    def supports_reference(self) -> bool:
        return False

    def supports_controlnet(self) -> bool:
        return False

    def supports_lora(self) -> bool:
        return False

    def supports_inpainting(self) -> bool:
        return True

    def estimate_vram(self) -> float | None:
        return None

    def estimate_cost(self, seconds: float, price_per_hour: float | None) -> float | None:
        return None if price_per_hour is None else round(seconds / 3600 * price_per_hour, 4)

    @abstractmethod
    def metadata(self) -> dict[str, Any]: ...


__all__ = ["AdapterResult", "ControlSpec", "IdentitySpec", "InpaintRequest", "ModelAdapter"]
