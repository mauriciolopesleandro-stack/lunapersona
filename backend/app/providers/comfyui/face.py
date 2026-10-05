"""QwenFaceAdapter: Face Lock do Persona Engine V1 (workflows/qwen-bfs-head-swap.json).

Qwen-Image-Edit 2511 + Lightning 8 passos + BFS head V5 trocam a cabeca da
imagem pela da master_face, na imagem inteira (benchmark E1: rosto 0.44 ->
0.65, pose/roupa/maos/cenario preservados). So recebe a master_face: a
master de corpo NUNCA entra aqui (no benchmark ela trocou roupa e cenario).
"""
from __future__ import annotations

from pathlib import Path

from app.clients.comfyui_client import ComfyUIError
from app.providers.base import FaceIdentityAdapter, FaceLockGuidance, ProviderImage, ReferenceImage, StageOutput
from app.providers.comfyui.session import ComfySession, output_name
from app.workflow_manager.manager import WorkflowNotFoundError

WORKFLOW = "qwen-bfs-head-swap"
UNET = "qwen_image_edit_2511_fp8mixed.safetensors"
LIGHTNING = "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"
BFS = "bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
MODEL_PIXELS = 1024 * 1024


def model_size(width: int, height: int, pixels: int = MODEL_PIXELS) -> tuple[int, int]:
    scale = (pixels / (width * height)) ** 0.5
    return max(256, round(width * scale / 16) * 16), max(256, round(height * scale / 16) * 16)


class QwenFaceAdapter(FaceIdentityAdapter):
    name = "qwen-bfs-head-swap"

    def __init__(self, session: ComfySession) -> None:
        self.session = session
        self._uploaded: dict[str, str] = {}

    def model_versions(self) -> dict[str, str]:
        return {"unet": UNET, "lightning_lora": LIGHTNING, "bfs_lora": BFS, "workflow": WORKFLOW}

    async def validate_configuration(self) -> list[str]:
        problems = []
        try:
            self.session.workflows.get_workflow(WORKFLOW)
        except WorkflowNotFoundError:
            problems.append(f"Workflow {WORKFLOW} nao encontrado.")
        try:
            loras = await self.session.client.list_loras()
            for name in (LIGHTNING, BFS):
                if name not in loras:
                    problems.append(f"LoRA {name} nao esta no pod.")
            if UNET not in await self.session.client.list_diffusion_models():
                problems.append(f"Modelo {UNET} nao esta no pod.")
        except ComfyUIError as exc:
            problems.append(f"Nao consegui listar os modelos do ComfyUI: {exc}")
        return problems

    async def _master_name(self, master: ReferenceImage) -> str:
        key = master.sha256 or master.reference_id
        if key not in self._uploaded:
            self._uploaded[key] = await self.session.client.upload_image(
                f"master_{key[:16]}{Path(master.filename).suffix or '.png'}", master.content
            )
        return self._uploaded[key]

    def base_prompt(self) -> str:
        return self.session.workflows.get_workflow(WORKFLOW).optional_params["PROMPT"]

    async def lock_face(self, image: ProviderImage, master_face: ReferenceImage, seed: int,
                        guidance: FaceLockGuidance | None = None) -> StageOutput:
        width, height = image.width or 832, image.height or 1216
        # Benchmark E1: a imagem entrou no Qwen no proprio tamanho (832x1216).
        # So reduz quando ela passa bem de 1 MP.
        mw, mh = (width, height) if width * height <= 1_300_000 else model_size(width, height)
        head = await self._master_name(master_face)
        values = {
            "BODY_IMAGE": image.locator, "HEAD_IMAGE": head, "WIDTH": mw, "HEIGHT": mh,
            "CROP_X": 0, "CROP_Y": 0, "CROP_W": width, "CROP_H": height, "FEATHER": 0,
            "SEED": seed, "FILENAME_PREFIX": "luna_engine_face",
        }
        # V1.1: texto de textura/idade somado a instrucao do BFS. Sem guidance
        # o grafo e identico ao da V1. O negativo da etapa NAO entra: o BFS
        # roda com CFG 1 (o condicionamento negativo nao tem efeito).
        if guidance and guidance.positive:
            values["PROMPT"] = f"{self.base_prompt()} {' '.join(guidance.positive)}"
        switch = self.session.mark("qwen")
        prompt_id, out, seconds = await self.session.run(WORKFLOW, values)
        return StageOutput(
            image=ProviderImage("comfyui", output_name(out), out.url, width, height),
            stage="face_lock", adapter=self.name, seconds=round(seconds, 2), seed=seed,
            model_ids=[UNET, LIGHTNING, BFS], provider_job_id=prompt_id, model_switch=switch,
            gpu=await self.session.gpu(),
            effective_parameters={"workflow": WORKFLOW, "model_size": [mw, mh], "seed": seed,
                                  "master_face": master_face.reference_id, "master_sha256": master_face.sha256,
                                  "crop": "imagem inteira",
                                  "texture_guidance": guidance.positive if guidance else None,
                                  "stage_negative": guidance.stage_negative if guidance else None,
                                  "stage_negative_applied": False},
        )
