"""ComfyCheckpointAdapter: geracao BASE da V2 (experimental) num checkpoint
completo (RealVisXL, Lustify) + a LoRA SDXL da persona.

- Um adapter por perfil de modelo (config/persona_engine_v2.json); o grafo e o
  workflow do perfil (realvis-base, lustify-base).
- CFG > 1: o negativo E aplicado (comum + persona + extra do modelo) e fica
  registrado em effective_parameters.
- Sem checkpoint ou sem LoRA no pod: erro explicito, sem fallback para outro modelo.
- Pose (DWPose/ControlNet) ainda nao existe na V2: pedido com pose = erro.
"""
from __future__ import annotations

from typing import Any

from app.clients.comfyui_client import ComfyUIError
from app.core.generation.v2_config import LoraSpec, ModelProfile
from app.providers.base import (
    AdapterCapabilities,
    ModelAdapter,
    ProviderConfigurationError,
    ProviderImage,
    SceneRequest,
    StageOutput,
)
from app.providers.comfyui.scene import build_prompt
from app.providers.comfyui.session import ComfySession, new_seed, output_name
from app.workflow_manager.manager import WorkflowNotFoundError


class ComfyCheckpointAdapter(ModelAdapter):
    def __init__(self, session: ComfySession, profile: ModelProfile, lora: LoraSpec, negative: list[str],
                 default_size: tuple[int, int] = (832, 1216)) -> None:
        self.session = session
        self.profile = profile
        self.lora = lora
        self.negative = list(negative)
        self.default_size = default_size
        self.name = profile.id

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(name=self.name, title=self.profile.title, supports_negative_prompt=True,
                                   supports_pose_control=False, default_size=self.default_size, max_pixels=1_100_000)

    def model_versions(self, lora: dict[str, Any] | None = None) -> dict[str, str]:
        return {"checkpoint": self.profile.checkpoint, "checkpoint_sha256": self.profile.sha256,
                "lora": self.lora.file, "lora_sha256": self.lora.sha256, "workflow": self.profile.workflow}

    async def validate_configuration(self, lora: dict[str, Any] | None = None, pose: bool = False) -> list[str]:
        client = self.session.client
        status = await client.check_connection()
        if not status.ok:
            return [f"ComfyUI fora do ar: {status.message}"]
        problems = []
        if pose:
            problems.append("Pose ainda nao existe na V2 (DWPose/ControlNet SDXL nao implementado).")
        try:
            self.session.workflows.get_workflow(self.profile.workflow)
        except WorkflowNotFoundError:
            problems.append(f"Workflow {self.profile.workflow} nao encontrado.")
        try:
            if self.profile.checkpoint not in await client.list_checkpoints():
                problems.append(f"Checkpoint {self.profile.checkpoint} ({self.profile.title}) nao esta no pod.")
            if self.lora.file not in await client.list_loras():
                problems.append(f"LoRA {self.lora.file} nao esta no pod (sem fallback).")
        except ComfyUIError as exc:
            problems.append(f"Nao consegui listar os modelos do ComfyUI: {exc}")
        return problems

    def values(self, request: SceneRequest, width: int, height: int, seed: int) -> dict[str, Any]:
        if request.pose is not None:
            raise ProviderConfigurationError("Pose ainda nao existe na V2.")
        negative = list(dict.fromkeys([*request.prompt.negative.all_terms(), *self.negative]))
        s = self.profile.sampling
        return {
            "PROMPT": build_prompt(request), "NEGATIVE": ", ".join(negative), "WIDTH": width, "HEIGHT": height,
            "SEED": seed, "CKPT": self.profile.checkpoint, "LORA_NAME": self.lora.file,
            "LORA_STRENGTH": self.lora.strength, "STEPS": s.steps, "CFG": s.cfg, "SAMPLER": s.sampler,
            "SCHEDULER": s.scheduler, "FILENAME_PREFIX": f"luna_v2_{self.profile.id}",
        }

    async def generate(self, request: SceneRequest) -> StageOutput:
        params = self.normalize_parameters(request.parameters)
        seed = params.seed if params.seed is not None else new_seed()
        values = self.values(request, params.width, params.height, seed)
        switch = self.session.mark(self.profile.id)
        prompt_id, image, seconds = await self.session.run(self.profile.workflow, values)
        return StageOutput(
            image=ProviderImage("comfyui", output_name(image), image.url, params.width, params.height),
            stage="base", adapter=self.name, seconds=round(seconds, 2), seed=seed,
            model_ids=[self.profile.checkpoint, self.lora.file], provider_job_id=prompt_id, model_switch=switch,
            gpu=await self.session.gpu(),
            effective_parameters={"workflow": self.profile.workflow, "model": self.profile.id,
                                  "prompt": values["PROMPT"], "negative": values["NEGATIVE"], "negative_applied": True,
                                  "lora": self.lora.file, "lora_strength": self.lora.strength,
                                  "width": params.width, "height": params.height, "seed": seed,
                                  "steps": values["STEPS"], "cfg": values["CFG"], "sampler": values["SAMPLER"],
                                  "scheduler": values["SCHEDULER"]},
        )


def v2_model_adapters(session: ComfySession, config) -> dict[str, ComfyCheckpointAdapter]:
    """Um adapter por perfil da configuracao da V2 (mesma sessao, mesmo orquestrador)."""
    size = (config.width, config.height)
    return {mid: ComfyCheckpointAdapter(session, p, config.lora, config.negative_for(p), size)
            for mid, p in config.models.items()}


__all__ = ["ComfyCheckpointAdapter", "v2_model_adapters"]
