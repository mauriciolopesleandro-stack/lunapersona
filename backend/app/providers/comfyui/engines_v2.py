"""Fabrica das engines V2 no ComfyUI: monta adapter (RealVisXL/Lustify) + segmentador + analisador e
entrega ReplacementEngine / FaceSwapEngine prontos. O adapter e carregado (load = conferencia de nos,
checkpoint, LoRA e ControlNet) uma vez por modelo; faltou algo = erro com a lista, sem fallback.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.clients.comfyui_client import ComfyUIClient
from app.core.engines.face_swap import FaceSwapEngine
from app.core.engines.models import ModelInfo, ModelRegistry
from app.core.engines.replacement import ReplacementEngine
from app.core.generation.v2_config import load_v2_config
from app.providers.comfyui.face import QwenFaceAdapter
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter
from app.providers.comfyui.replacement import ComfyImageStore, ComfySegmenter
from app.providers.comfyui.sdxl_engine import LustifyAdapter, RealVisXLAdapter
from app.providers.comfyui.session import ComfySession
from app.validation_backends.comfyui import ComfyImageAnalyzer
from app.validation_backends.reference import ComfyReferenceReader
from app.validation_backends.skin import _split_locator
from app.workflow_manager.manager import WorkflowManager

ADAPTERS = {"realvisxl": RealVisXLAdapter, "lustify": LustifyAdapter}
CONFIGS = {"v2": "persona_replacement_v2.json", "v2.1": "persona_replacement_v2_1.json", "v3": "persona_replacement_v3.json"}


class EngineSetupError(RuntimeError):
    pass


class ComfyEngineFactory:
    def __init__(self, client: ComfyUIClient, workflows: WorkflowManager, registry: ModelRegistry, config_dir: Path,
                 engines_cfg: dict[str, Any], price_per_hour: float | None = None) -> None:
        self.client = client
        self.session = ComfySession(client, workflows)
        self.store = ComfyImageStore(client)
        self.registry = registry
        self.config_dir = config_dir
        self.cfg = engines_cfg
        self.price = price_per_hour if price_per_hour is not None else engines_cfg.get("price_per_hour_fallback")
        self._adapters: dict[str, Any] = {}

    def url_for(self, locator: str) -> str:
        return self.client.build_image_url(*_split_locator(locator))

    async def upload(self, name: str, content: bytes) -> str:
        return await self.client.upload_image(name, content)

    async def _adapter(self, model: ModelInfo):
        if model.id in self._adapters:
            return self._adapters[model.id]
        cls = ADAPTERS.get(model.id)
        if cls is None:
            raise EngineSetupError(f"sem adapter para o checkpoint {model.id}")
        v2 = load_v2_config(self.config_dir / "persona_engine_v2.json")
        region = ComfyRegionPassAdapter(self.session, v2.model(model.id), v2.lora,
                                        steps=v2.pass_steps, identity_adapters=v2.identity_adapters)
        adapter = cls(self.session, model, self.registry.get("lunavox_sdxl_v1"), self.cfg["controlnet"], region=region,
                      depth_model=self.cfg.get("depth_model", "depth_anything_v2_vits.pth"), price_per_hour=self.price)
        problems = await adapter.load()
        if problems:
            raise EngineSetupError(f"{model.title} nao esta pronto no ComfyUI: {problems}")
        self._adapters[model.id] = adapter
        return adapter

    def _parts(self, adapter, version: str = "v2") -> dict[str, Any]:
        # O checkpoint das passadas fica no grafo da analise para o ComfyUI nao descarrega-lo entre passadas.
        keep = {"ka1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": adapter.model.file}},
                "ka2": {"class_type": "PreviewAny", "inputs": {"source": ["ka1", 0]}}}
        return {"reader": ComfyReferenceReader(self.client), "segmenter": ComfySegmenter(self.session, self.store),
                "analyzer": ComfyImageAnalyzer(self.client, keep_alive=keep), "store": self.store, "adapter": adapter,
                "config": ReplacementEngine.load_config(self.config_dir / CONFIGS.get(version, "persona_replacement_v2.json")),
                "price_per_hour": self.price, "provider": "comfyui"}

    async def replacement(self, model: ModelInfo, qwen: bool | None = None, version: str = "v2") -> ReplacementEngine:
        # spec Master 9: o Face Lock Qwen-Image-Edit 2511 + BFS (o da geracao V1) NAO e etapa padrao do Replacement.
        # So e montado quando pedido (qwen=True) ou ligado na config (qwen.enabled) - e entao fica registrado.
        parts = self._parts(await self._adapter(model), version)
        cfg = parts["config"]
        default = bool((cfg.get("qwen") or {}).get("enabled")) or bool((cfg.get("qwen_identity_refinement") or {}).get("enabled")) \
            or bool((cfg.get("identity_refinement") or {}).get("qwen_allowed"))
        want = bool(qwen) if qwen is not None else default
        if version == "v3":
            from app.core.engines.replacement_v3 import PersonaReplacementV3
            from app.providers.comfyui.replacement import ComfyGarmentDescriber
            eng = PersonaReplacementV3(**parts, face_lock=await self._face_lock() if want else None)
            eng.describer = ComfyGarmentDescriber(self.client)
            return eng
        return ReplacementEngine(**parts, face_lock=await self._face_lock() if want else None)

    async def _face_lock(self):
        lock = QwenFaceAdapter(self.session)
        problems = await lock.validate_configuration()
        if problems:  # sem os modelos da V1 no pod: erro claro (nao troca para outra coisa em silencio)
            raise EngineSetupError(f"Face Lock (Qwen BFS) indisponivel: {problems}")
        return lock

    async def face_swap(self, model: ModelInfo) -> FaceSwapEngine:
        parts = self._parts(await self._adapter(model))
        # head_swap (Qwen + BFS) ainda nao tem porta no backend: pedido qwen_bfs falha claro (sem fallback).
        return FaceSwapEngine(**parts, replacement=ReplacementEngine(**parts), head_swap=None)


__all__ = ["ComfyEngineFactory", "EngineSetupError"]
