"""Contratos entre o Persona Engine e os modelos (Persona Engine V1).

O nucleo (app/core) so conhece estas interfaces; cada provider (hoje o
ComfyUI do estudio, em app/providers/comfyui) implementa as etapas:

  SceneAdapter          cena, corpo e pose (Z-Image + LoRA da persona)
  FaceIdentityAdapter   Face Lock: refaz cabeca/rosto com a master_face (Qwen 2511 BFS)
  PoseControlAdapter    prepara o controle de pose (DWPose + ControlNet) - opcional

Um ProviderSet junta as tres etapas de um provider. Trocar de GPU, de
servidor ou de modelo e escrever outro ProviderSet, sem mexer no nucleo.
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
    width: int | None = None
    height: int | None = None
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReferenceImage:
    reference_id: str
    filename: str
    content: bytes
    sha256: str = ""


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
class NegativeSet:
    """Negativo em camadas (global + persona + cena). O adapter decide se aplica."""

    global_terms: list[str] = field(default_factory=list)
    persona_terms: list[str] = field(default_factory=list)
    scene_terms: list[str] = field(default_factory=list)
    version: str = ""

    def all_terms(self) -> list[str]:
        return list(dict.fromkeys([*self.global_terms, *self.persona_terms, *self.scene_terms]))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PromptSections:
    """Saida do PromptBuilder. O adapter monta o texto final do seu modelo."""

    subject: str  # gatilho interno da LoRA + "a woman" (nunca vem do texto do usuario)
    appearance: str
    scene: str
    style: str
    directives: list[str] = field(default_factory=list)  # ex.: "only one woman in the photo"
    negative: NegativeSet = field(default_factory=NegativeSet)
    version: str = ""
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PoseControl:
    """Pose pedida: imagem de origem do esqueleto (no provider) e forca."""

    source: str
    strength: float


@dataclass
class FaceLockGuidance:
    """V1.1: texto extra para o Face Lock preservar a textura da pele e a idade.
    stage_negative so entra se o adapter suportar negativo (o Qwen BFS roda com CFG 1: nao)."""

    positive: list[str] = field(default_factory=list)
    stage_negative: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkinCorrectionRequest:
    """V1.1: correcao minima de textura so na regiao do rosto."""

    face_bbox: tuple[float, float, float, float]
    prompt: str
    denoise: float
    seed: int
    expand: float = 0.25


@dataclass
class SceneRequest:
    prompt: PromptSections
    parameters: GenerationParameters
    lora: dict[str, Any]  # arquivo, gatilho e forca da LoRA da persona (da ficha + persona.json)
    pose: PoseControl | None = None


@dataclass
class StageOutput:
    image: ProviderImage
    stage: str
    adapter: str
    seconds: float
    seed: int
    model_ids: list[str] = field(default_factory=list)
    provider_job_id: str = ""
    # True = o modelo desta etapa precisou ser carregado (outro estava na GPU).
    model_switch: bool | None = None
    gpu: dict[str, Any] = field(default_factory=dict)
    effective_parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["image"] = self.image.to_dict()
        return data


@dataclass
class AdapterCapabilities:
    name: str
    title: str
    supports_negative_prompt: bool = False
    supports_pose_control: bool = False
    default_size: tuple[int, int] = (832, 1216)
    max_pixels: int = 1_300_000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SceneAdapter(ABC):
    name: str

    @abstractmethod
    async def generate(self, request: SceneRequest) -> StageOutput: ...

    @abstractmethod
    async def validate_configuration(self, lora: dict[str, Any], pose: bool) -> list[str]: ...

    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities: ...

    def model_versions(self, lora: dict[str, Any]) -> dict[str, str]:
        return {}

    def normalize_parameters(self, params: GenerationParameters) -> GenerationParameters:
        caps = self.get_capabilities()
        width, height = params.width or caps.default_size[0], params.height or caps.default_size[1]
        scale = min(1.0, (caps.max_pixels / (width * height)) ** 0.5)
        return GenerationParameters(
            width=max(256, round(width * scale / 16) * 16),
            height=max(256, round(height * scale / 16) * 16),
            seed=params.seed,
        )


class ModelAdapter(SceneAdapter):
    """V2: etapa de cena dirigida por um perfil de modelo (RealVisXL, Lustify...).
    O nucleo escolhe o perfil por dado (generation_model); o adapter traduz
    perfil + LoRA + negativo para o grafo do provider. Um adapter por perfil,
    o mesmo orquestrador para todos."""

    profile: Any  # app.core.generation.v2_config.ModelProfile

    @property
    def model_id(self) -> str:
        return self.profile.id


@dataclass
class RegionPassRequest:
    """V2: uma passada numa regiao (rosto ou corpo). `region` vem do nucleo:
    {"crop": {x,y,w,h}, "shapes": [...], "feather": f} em coordenadas do recorte."""

    region: dict[str, Any]
    prompt: str
    negative: str
    denoise: float
    strength: float
    seed: int
    name: str = ""


class RegionPassAdapter(ABC):
    """V2: redesenha so a regiao pedida, a partir da imagem anterior, e cola de volta
    com a mascara (opacidade = strength). Nunca parte do zero."""

    name: str

    @abstractmethod
    async def refine(self, image: ProviderImage, request: RegionPassRequest) -> StageOutput: ...

    @abstractmethod
    async def validate_configuration(self) -> list[str]: ...


class FaceIdentityAdapter(ABC):
    name: str

    @abstractmethod
    async def lock_face(self, image: ProviderImage, master_face: ReferenceImage, seed: int,
                        guidance: FaceLockGuidance | None = None) -> StageOutput: ...

    @abstractmethod
    async def validate_configuration(self) -> list[str]: ...

    def model_versions(self) -> dict[str, str]:
        return {}


class PoseControlAdapter(ABC):
    name: str

    @abstractmethod
    async def prepare(self, pose_reference: str, strength: float) -> PoseControl: ...

    @abstractmethod
    async def validate_configuration(self) -> list[str]: ...


class SkinCorrectionAdapter(ABC):
    """V1.1: recupera microtextura da pele sem refazer o rosto (opcional)."""

    name: str

    @abstractmethod
    async def correct(self, image: ProviderImage, request: SkinCorrectionRequest) -> StageOutput: ...

    @abstractmethod
    async def validate_configuration(self) -> list[str]: ...

    def model_versions(self) -> dict[str, str]:
        return {}


@dataclass
class ProviderSet:
    name: str
    title: str
    scene: SceneAdapter
    face: FaceIdentityAdapter
    pose: PoseControlAdapter | None = None
    skin: SkinCorrectionAdapter | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name, "title": self.title,
            "scene": self.scene.get_capabilities().to_dict(),
            "face": self.face.name,
            "pose": self.pose.name if self.pose else None,
            "skin": self.skin.name if self.skin else None,
        }


class ProviderRegistry:
    def __init__(self) -> None:
        self._sets: dict[str, ProviderSet] = {}

    def register(self, provider_set: ProviderSet) -> None:
        self._sets[provider_set.name] = provider_set

    def get(self, name: str) -> ProviderSet:
        found = self._sets.get(name)
        if found is None:
            raise UnknownProviderError(
                f"Provider '{name}' nao existe. Disponiveis: {', '.join(sorted(self._sets)) or 'nenhum'}."
            )
        return found

    def names(self) -> list[str]:
        return sorted(self._sets)

    def default(self) -> str:
        if not self._sets:
            raise UnknownProviderError("Nenhum provider registrado.")
        return next(iter(self._sets))
