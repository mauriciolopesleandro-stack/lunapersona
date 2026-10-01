"""Troca de personagem em video: a pessoa de um video enviado vira a persona
(Wan 2.2 Animate, modo substituir), mantendo movimento, expressao, cenario,
luz e audio do video original. Modelos e nodes: scripts/setup_animate.sh.

Etapas:
1. scripts/prep_video.py (Python do ComfyUI, PyAV): reamostra para 16 fps,
   corta na duracao maxima, mantem o audio e salva o 1o quadro no tamanho
   do grafo;
2. Florence-2 acha a pessoa nesse 1o quadro; os pontos viram a semente do
   recorte (SAM2) - no template oficial isso era clicado a mao;
3. grafo do template video_wan2_2_14B_animate: pose do corpo e do rosto
   (DWPose), fundo sem a pessoa, LoRAs relight + lightx2v, trechos de 77
   quadros emendados com continue_motion.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError
from app.clients.llm_client import OllamaClient
from app.services.prompt_translator import to_english
from app.services.video_service import QUALITY_PIXELS, TEXT_ENCODER, VAE, VideoResponse
from app.workflow_manager.manager import WorkflowParamError

COMFY_ROOT = Path(os.environ.get("COMFYUI_DIR", "/workspace/runpod-slim/ComfyUI"))
COMFY_PYTHON = COMFY_ROOT / ".venv-cu128" / "bin" / "python"
PREP_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "prep_video.py"

ANIMATE_MODEL = "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"
LIGHTX2V_LORA = "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
RELIGHT_LORA = "WanAnimate_relight_lora_fp16.safetensors"
CLIP_VISION = "clip_vision_h.safetensors"
SAM2_MODEL = "sam2_hiera_base_plus.safetensors"
FPS = 16
CHUNK_FRAMES = 77
STEPS = 6
MAX_SECONDS = 15
CHUNK_TIMEOUT = {"480p": 900.0, "720p": 1800.0}

DEFAULT_PROMPT = "a young woman moving naturally, realistic video, consistent face and body, detailed skin"
NEGATIVE_PROMPT = (
    "oversaturated, overexposed, static, blurry details, subtitles, text, watermark, logo, worst quality, "
    "low quality, jpeg artifacts, ugly, deformed, extra fingers, bad hands, bad face, fused fingers, "
    "morphing face, face changing, extra limbs, many people in the background"
)


@dataclass
class SwapRequest:
    video: str  # mp4 enviado, ja no input/ do ComfyUI
    image: str  # foto da persona (referencia)
    image_type: str = "output"
    image_subfolder: str = ""
    prompt: str = ""
    quality: str = "480p"
    max_seconds: int = 10
    seed: int | None = None


def _person_points(data_text: str, width: int, height: int) -> str:
    """Pontos positivos do SAM2 a partir do retangulo do Florence (o maior):
    centro do tronco e centro do rosto aproximado. Sem deteccao, o centro do
    quadro."""
    boxes: list[list[float]] = []
    try:
        data = json.loads(data_text.replace("'", '"'))
        if isinstance(data, list):
            data = data[0] if data else {}
        boxes = data.get("bboxes") or []
        if boxes and isinstance(boxes[0], list) and boxes[0] and isinstance(boxes[0][0], list):
            boxes = boxes[0]
    except (ValueError, AttributeError, TypeError):
        boxes = []
    if not boxes:
        return json.dumps([{"x": width // 2, "y": height // 2}])
    x1, y1, x2, y2 = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    cx = int((x1 + x2) / 2)
    return json.dumps([
        {"x": cx, "y": int(y1 + (y2 - y1) * 0.45)},
        {"x": cx, "y": int(y1 + (y2 - y1) * 0.12)},
    ])


class SwapService:
    def __init__(self, comfyui_client: ComfyUIClient, llm_client: OllamaClient | None = None) -> None:
        self.comfyui_client = comfyui_client
        self.llm_client = llm_client

    async def _prep(self, video: str, quality: str, max_seconds: int) -> tuple[str, str, dict[str, Any]]:
        stem = f"troca_{uuid.uuid4().hex[:10]}"
        src = COMFY_ROOT / "input" / video
        out_video, first = f"{stem}.mp4", f"{stem}_quadro1.png"
        area = QUALITY_PIXELS.get(quality, QUALITY_PIXELS["480p"])
        proc = await asyncio.create_subprocess_exec(
            str(COMFY_PYTHON), str(PREP_SCRIPT), str(src),
            str(COMFY_ROOT / "input" / out_video), str(COMFY_ROOT / "input" / first),
            str(area), str(max_seconds),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode != 0:
            tail = stderr.decode(errors="replace").strip().splitlines()[-2:]
            raise WorkflowParamError("Nao consegui ler esse video: " + " | ".join(tail))
        return out_video, first, json.loads(stdout.decode().strip().splitlines()[-1])

    async def _find_person(self, first: str, width: int, height: int) -> str:
        graph = {
            "1": {"class_type": "LoadImage", "inputs": {"image": first}},
            "2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": "microsoft/Florence-2-large", "precision": "fp16"}},
            "3": {
                "class_type": "Florence2Run",
                "inputs": {
                    "image": ["1", 0], "florence2_model": ["2", 0], "text_input": "person",
                    "task": "caption_to_phrase_grounding", "fill_mask": False, "keep_model_loaded": False,
                    "max_new_tokens": 256, "num_beams": 3, "do_sample": False, "output_mask_select": "", "seed": 1,
                },
            },
            "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 3]}},
        }
        try:
            entry = await self.comfyui_client.wait_for_completion(await self.comfyui_client.queue_prompt(graph))
        except ComfyUIError:
            return _person_points("", width, height)
        text = next((o["text"][0] for o in entry.get("outputs", {}).values() if o.get("text")), "")
        return _person_points(str(text), width, height)

    def build_graph(
        self, req: SwapRequest, video: str, points: str, prompt: str, width: int, height: int,
        frames: int, seed: int,
    ) -> dict[str, Any]:
        image = f"{req.image_subfolder}/{req.image}" if req.image_subfolder else req.image
        g: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": ANIMATE_MODEL, "weight_dtype": "default"}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": LIGHTX2V_LORA, "strength_model": 1.0}},
            "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": RELIGHT_LORA, "strength_model": 1.0}},
            "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": 8.0}},
            "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["5", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE_PROMPT, "clip": ["5", 0]}},
            "8": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
            "9": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": CLIP_VISION}},
            "10": {"class_type": "LoadImage", "inputs": {"image": f"{image} [{req.image_type}]"}},
            "11": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["9", 0], "image": ["10", 0], "crop": "none"}},
            "12": {"class_type": "LoadVideo", "inputs": {"file": video}},
            "13": {"class_type": "GetVideoComponents", "inputs": {"video": ["12", 0]}},
            "14": {
                "class_type": "ImageScale",
                "inputs": {"image": ["13", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "center"},
            },
            "15": {
                "class_type": "PixelPerfectResolution",
                "inputs": {"original_image": ["13", 0], "image_gen_width": width, "image_gen_height": height, "resize_mode": "Just Resize"},
            },
            # Rosto (expressao) e corpo+maos (pose) em dois passes, como no template.
            "16": {
                "class_type": "DWPreprocessor",
                "inputs": {
                    "image": ["14", 0], "resolution": ["15", 0], "detect_hand": "disable", "detect_body": "disable",
                    "detect_face": "enable", "bbox_detector": "yolox_l.onnx",
                    "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable",
                },
            },
            "17": {
                "class_type": "DWPreprocessor",
                "inputs": {
                    "image": ["14", 0], "resolution": ["15", 0], "detect_hand": "enable", "detect_body": "enable",
                    "detect_face": "disable", "bbox_detector": "yolox_l.onnx",
                    "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable",
                },
            },
            "18": {
                "class_type": "DownloadAndLoadSAM2Model",
                "inputs": {"model": SAM2_MODEL, "segmentor": "video", "device": "cuda", "precision": "fp16"},
            },
            "19": {
                "class_type": "Sam2Segmentation",
                "inputs": {
                    "sam2_model": ["18", 0], "image": ["14", 0], "keep_model_loaded": False,
                    "coordinates_positive": points, "individual_objects": False,
                },
            },
            "20": {"class_type": "GrowMask", "inputs": {"mask": ["19", 0], "expand": 10, "tapered_corners": True}},
            "21": {"class_type": "BlockifyMask", "inputs": {"masks": ["20", 0], "block_size": 32}},
            # Fundo do video original com a pessoa apagada: a persona entra ali.
            "22": {"class_type": "DrawMaskOnImage", "inputs": {"image": ["14", 0], "mask": ["21", 0], "color": "0, 0, 0"}},
        }
        next_id = 23

        def add(class_type: str, inputs: dict[str, Any]) -> str:
            nonlocal next_id
            node_id = str(next_id)
            next_id += 1
            g[node_id] = {"class_type": class_type, "inputs": inputs}
            return node_id

        shared = {
            "positive": ["6", 0], "negative": ["7", 0], "vae": ["8", 0], "width": width, "height": height,
            "length": CHUNK_FRAMES, "batch_size": 1, "continue_motion_max_frames": 5,
            "clip_vision_output": ["11", 0], "reference_image": ["10", 0], "face_video": ["16", 0],
            "pose_video": ["17", 0], "background_video": ["22", 0], "character_mask": ["21", 0],
        }
        sampler = {"steps": STEPS, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}
        chunks = max(1, math.ceil(frames / CHUNK_FRAMES))
        images: list[Any] | None = None
        offset: Any = 0
        for k in range(chunks):
            extra = {"video_frame_offset": offset}
            if images is not None:
                extra["continue_motion"] = images
            anim = add("WanAnimateToVideo", {**shared, **extra})
            sampled = add("KSampler", {
                **sampler, "model": ["4", 0], "seed": seed + k, "positive": [anim, 0], "negative": [anim, 1],
                "latent_image": [anim, 2],
            })
            trimmed = add("TrimVideoLatent", {"samples": [sampled, 0], "trim_amount": [anim, 3]})
            decoded = add("VAEDecode", {"samples": [trimmed, 0], "vae": ["8", 0]})
            new = add("ImageFromBatch", {"image": [decoded, 0], "batch_index": [anim, 4], "length": 4096})
            images = [new, 0] if images is None else [add("ImageBatch", {"image1": images, "image2": [new, 0]}), 0]
            offset = [anim, 5]
        final = add("ImageFromBatch", {"image": images, "batch_index": 0, "length": frames})
        video_out = add("CreateVideo", {"images": [final, 0], "fps": float(FPS), "audio": ["13", 1]})
        add("SaveVideo", {"video": [video_out, 0], "filename_prefix": "video/luna_troca", "format": "mp4", "codec": "auto"})
        return g

    async def generate(self, req: SwapRequest) -> VideoResponse:
        if not COMFY_PYTHON.exists():
            raise WorkflowParamError("O ComfyUI deste servidor nao esta no lugar esperado.")
        start = time.monotonic()
        seconds = max(2, min(MAX_SECONDS, req.max_seconds))
        video, first, info = await self._prep(req.video, req.quality, seconds)
        width, height, frames = info["width"], info["height"], info["frames"]
        points = await self._find_person(first, width, height)
        extra = await to_english(self.llm_client, req.prompt) if req.prompt.strip() else ""
        prompt = f"{extra}, {DEFAULT_PROMPT}" if extra else DEFAULT_PROMPT
        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)
        graph = self.build_graph(req, video, points, prompt, width, height, frames, seed)
        prompt_id = await self.comfyui_client.queue_prompt(graph)
        chunks = max(1, math.ceil(frames / CHUNK_FRAMES))
        entry = await self.comfyui_client.wait_for_completion(
            prompt_id, timeout=CHUNK_TIMEOUT.get(req.quality, 900.0) * chunks
        )
        outputs = self.comfyui_client.extract_images(entry)
        return VideoResponse(
            prompt_id=prompt_id,
            seconds=round(frames / FPS),
            width=width,
            height=height,
            videos=[o for o in outputs if o.filename.endswith(".mp4")],
            duration_seconds=time.monotonic() - start,
        )
