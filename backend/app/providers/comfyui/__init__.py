from app.clients.comfyui_client import ComfyUIClient
from app.providers.base import ProviderSet
from app.providers.comfyui.face import QwenFaceAdapter
from app.providers.comfyui.pose import DWPoseControlAdapter
from app.providers.comfyui.scene import ZImageAdapter
from app.providers.comfyui.session import ComfySession
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
    )


__all__ = ["ComfySession", "DWPoseControlAdapter", "QwenFaceAdapter", "ZImageAdapter", "comfyui_provider_set"]
