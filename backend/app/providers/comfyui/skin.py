"""ZImageTextureAdapter: correcao de pele da V1.1 (workflows/zimage-face-texture.json).

So o recorte do rosto (caixa do rosto + 25%) passa pelo Z-Image Turbo SEM a
LoRA da persona, com denoise baixo (da ficha), e volta colado com borda suave.
Quem decide se a correcao fica e a guarda do nucleo (identidade, idade, pose,
pessoas); este adapter so executa.
"""
from __future__ import annotations

from app.clients.comfyui_client import ComfyUIError
from app.providers.base import ProviderImage, SkinCorrectionAdapter, SkinCorrectionRequest, StageOutput
from app.providers.comfyui.scene import TEXT_ENCODER, UNET, VAE
from app.providers.comfyui.session import ComfySession, output_name
from app.workflow_manager.manager import WorkflowNotFoundError

WORKFLOW = "zimage-face-texture"
WORK_SIDE = 768  # lado maior do recorte durante a correcao


def crop_box(bbox, expand: float, width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    left, top = max(0, int(x1 - w * expand)), max(0, int(y1 - h * expand))
    right, bottom = min(width, int(x2 + w * expand)), min(height, int(y2 + h * expand))
    return left, top, max(16, right - left), max(16, bottom - top)


def work_size(cw: int, ch: int) -> tuple[int, int]:
    scale = WORK_SIDE / max(cw, ch)
    return max(256, round(cw * scale / 16) * 16), max(256, round(ch * scale / 16) * 16)


class ZImageTextureAdapter(SkinCorrectionAdapter):
    name = "zimage-face-texture"

    def __init__(self, session: ComfySession) -> None:
        self.session = session

    def model_versions(self) -> dict[str, str]:
        return {"unet": UNET, "text_encoder": TEXT_ENCODER, "vae": VAE, "lora": "nenhuma", "workflow": WORKFLOW}

    async def validate_configuration(self) -> list[str]:
        try:
            self.session.workflows.get_workflow(WORKFLOW)
        except WorkflowNotFoundError:
            return [f"Workflow {WORKFLOW} nao encontrado."]
        try:
            if UNET not in await self.session.client.list_diffusion_models():
                return [f"Modelo {UNET} nao esta no pod."]
        except ComfyUIError as exc:
            return [f"Nao consegui listar os modelos do ComfyUI: {exc}"]
        return []

    async def correct(self, image: ProviderImage, request: SkinCorrectionRequest) -> StageOutput:
        width, height = image.width or 832, image.height or 1216
        x, y, cw, ch = crop_box(request.face_bbox, request.expand, width, height)
        ww, wh = work_size(cw, ch)
        values = {
            "IMAGE": image.locator, "PROMPT": request.prompt, "SEED": request.seed, "DENOISE": request.denoise,
            "CROP_X": x, "CROP_Y": y, "CROP_W": cw, "CROP_H": ch, "WORK_W": ww, "WORK_H": wh,
            "FEATHER": max(4, int(min(cw, ch) * 0.12)),
        }
        switch = self.session.mark("zimage")
        prompt_id, out, seconds = await self.session.run(WORKFLOW, values)
        return StageOutput(
            image=ProviderImage("comfyui", output_name(out), out.url, width, height),
            stage="skin_correction", adapter=self.name, seconds=round(seconds, 2), seed=request.seed,
            model_ids=[UNET], provider_job_id=prompt_id, model_switch=switch, gpu=await self.session.gpu(),
            effective_parameters={"workflow": WORKFLOW, "denoise": request.denoise, "crop": [x, y, cw, ch],
                                  "work_size": [ww, wh], "prompt": request.prompt, "lora": None},
        )
