"""PoseControlAdapter do ComfyUI: confere que DWPose e ControlNet Union existem
e entrega o controle de pose que o ZImageAdapter coloca no grafo. A imagem de
pose ja esta no input/ do ComfyUI (enviada por /api/generate/reference)."""
from __future__ import annotations

from app.clients.comfyui_client import ComfyUIError
from app.providers.base import PoseControl, PoseControlAdapter
from app.providers.comfyui.scene import CONTROL_PATCH
from app.providers.comfyui.session import ComfySession


class DWPoseControlAdapter(PoseControlAdapter):
    name = "dwpose-controlnet-union"

    def __init__(self, session: ComfySession) -> None:
        self.session = session

    async def validate_configuration(self) -> list[str]:
        try:
            info = await self.session.client.get_object_info()
            patches = await self.session.client.list_model_patches()
        except ComfyUIError as exc:
            return [f"Nao consegui consultar o ComfyUI: {exc}"]
        problems = [f"No {n} nao esta no pod." for n in ("DWPreprocessor", "ZImageFunControlnet") if n not in info]
        if CONTROL_PATCH not in patches:
            problems.append(f"ControlNet {CONTROL_PATCH} nao esta no pod.")
        return problems

    async def prepare(self, pose_reference: str, strength: float) -> PoseControl:
        return PoseControl(source=pose_reference, strength=strength)
