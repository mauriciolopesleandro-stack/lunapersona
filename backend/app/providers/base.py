"""ModelAdapter: o unico ponto de contato entre o Persona Engine e um modelo
de geracao (ComfyUI hoje; Flux API, GPT Image etc. depois, cada um na sua
pasta em app/providers/).

O nucleo (core/) fala so em termos genericos - prompt em secoes, fotos de
referencia, "forca da identidade" de 0 a 1 - e cada adapter traduz para o
seu modelo (forca da LoRA, peso do IP-Adapter, etc.).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


class ProviderError(Exception):
    pass


class UnknownProviderError(ProviderError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


@dataclass
class GenerationParameters:
    """Parametros independentes de modelo. identity_strength 0-1: quanto a
    identidade da persona pesa (0.5 = o padrao do modelo)."""

    width: int | None = None
    height: int | None = None
    seed: int | None = None
    identity_strength: float = 0.5
    # Retoque do rosto pela foto da persona depois da geracao (se o modelo tiver).
    face_restore: bool = False
    steps: int | None = None
    guidance: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderCapabilities:
    name: str
    title: str
    # Como o modelo segura a identidade: "lora", "reference_image", "text"...
    identity_mechanisms: list[str] = field(default_factory=list)
    supports_face_restore: bool = False
    supports_negative_prompt: bool = False
    supports_seed: bool = True
    default_size: tuple[int, int] = (1024, 1024)
    max_pixels: int = 2_000_000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReferenceImage:
    reference_id: str
    filename: str
    content: bytes
    type: str
    weight: float


@dataclass
class PromptSections:
    """Saida do PromptBuilder. Cada adapter monta o texto final do jeito que
    o seu modelo entende (ordem, idioma, se usa negativo)."""

    identity_directive: str
    identity_traits: str
    appearance: str
    style: str
    scene: str
    constraints: list[str]
    negative: list[str]
    # Reforcos pedidos pelo RetryManager (ex.: "apparent age 26").
    emphasis: list[str] = field(default_factory=list)
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderInput:
    persona_id: str
    prompt: PromptSections
    parameters: GenerationParameters
    references: list[ReferenceImage] = field(default_factory=list)
    # Recursos de identidade da persona que um modelo especifico usa (LoRA...).
    identity_assets: dict[str, Any] = field(default_factory=dict)
    # Sexo da persona ("F"/"M"/""), para o modelo escrever "a woman" etc.
    sex: str = ""


@dataclass
class ProviderImage:
    provider: str
    # Nome da imagem para o proprio provider (ComfyUI: "arquivo.png [output]").
    locator: str
    url: str
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderOutput:
    images: list[ProviderImage]
    provider_job_id: str
    duration_seconds: float
    # O que o modelo realmente usou (workflow, forca da LoRA, semente...).
    effective_parameters: dict[str, Any] = field(default_factory=dict)


class ModelAdapter(ABC):
    name: str

    @abstractmethod
    async def generate(self, request: ProviderInput) -> ProviderOutput: ...

    @abstractmethod
    async def validate_configuration(self) -> list[str]:
        """Problemas que impedem gerar agora (lista vazia = pronto)."""

    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities: ...

    def normalize_parameters(self, params: GenerationParameters) -> GenerationParameters:
        """Encaixa os parametros no que o modelo aceita (tamanho multiplo de
        16 e ate max_pixels, identidade entre 0 e 1)."""
        caps = self.get_capabilities()
        width, height = params.width or caps.default_size[0], params.height or caps.default_size[1]
        scale = min(1.0, (caps.max_pixels / (width * height)) ** 0.5)
        width, height = max(256, round(width * scale / 16) * 16), max(256, round(height * scale / 16) * 16)
        return GenerationParameters(
            width=width,
            height=height,
            seed=params.seed if caps.supports_seed else None,
            identity_strength=min(1.0, max(0.0, params.identity_strength)),
            face_restore=params.face_restore and caps.supports_face_restore,
            steps=params.steps,
            guidance=params.guidance,
        )


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ModelAdapter] = {}

    def register(self, adapter: ModelAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> ModelAdapter:
        adapter = self._adapters.get(name)
        if adapter is None:
            raise UnknownProviderError(
                f"Provider '{name}' nao existe. Disponiveis: {', '.join(sorted(self._adapters)) or 'nenhum'}."
            )
        return adapter

    def names(self) -> list[str]:
        return sorted(self._adapters)

    def default(self) -> str:
        if not self._adapters:
            raise UnknownProviderError("Nenhum provider registrado.")
        return next(iter(self._adapters))
