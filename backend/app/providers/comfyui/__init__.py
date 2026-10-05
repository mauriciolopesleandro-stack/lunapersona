from app.clients.comfyui_client import ComfyUIClient
from app.providers.base import ProviderSet
from app.providers.comfyui.face import QwenFaceAdapter
from app.providers.comfyui.pose import DWPoseControlAdapter
from app.providers.comfyui.scene import ZImageAdapter
from app.providers.comfyui.session import ComfySession
from app.providers.comfyui.skin import ZImageTextureAdapter
from app.workflow_manager.manager import WorkflowManager


def comfyui_provider_set(client: ComfyUIClient, workflows: WorkflowManager) -> ProviderSet:
    """Pipeline vencedor do benchmark: Z-Image + LoRA -> Qwen 2511 BFS (master_face),
    com DWPose + ControlNet opcional."""
    session = ComfySession(client, workflows)
    return ProviderSet(
        name="comfyui",
        title="ComfyUI - Z-Image + LoRA -> Qwen 2511 BFS (rosto master)",
        scene=ZImageAdapter(session),
        face=QwenFaceAdapter(session),
        pose=DWPoseControlAdapter(session),
        # V1.1: so roda se a Persona Sheet ligar a correcao de pele.
        skin=ZImageTextureAdapter(session),
    )


__all__ = ["ComfySession", "DWPoseControlAdapter", "QwenFaceAdapter", "ZImageAdapter", "ZImageTextureAdapter", "comfyui_provider_set"]
