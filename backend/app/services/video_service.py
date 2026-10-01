"""Anima uma imagem ja gerada da persona (Wan 2.2 Image-to-Video 14B com as
LoRAs LightX2V de 4 passos). Os modelos sao instalados por
scripts/setup_wan.sh.

O Wan gera ~5 s por vez (81 quadros a 16 fps). Para videos mais longos o
grafo encadeia trechos: o ultimo quadro de um trecho e a foto inicial do
proximo, e os quadros de todos viram um unico mp4. Como tudo parte da foto
da persona, rosto e corpo continuam os dela sem LoRA de video.

O grafo e montado aqui (e nao num JSON de workflows/) porque o numero de
trechos varia com a duracao pedida.

Continuar um video (historias maiores que 20 s): o novo trecho parte do
ultimo quadro salvo em PNG (sem a compressao do mp4) e os quadros do video
anterior entram antes dos novos, num mp4 unico.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, GenerationOutputImage
from app.clients.llm_client import OllamaClient
from app.services.prompt_translator import to_english
from app.services.scene_describer import SceneDescriber
from app.workflow_manager.manager import WorkflowParamError

HIGH_NOISE_MODEL = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
LOW_NOISE_MODEL = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
HIGH_NOISE_LORA = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
LOW_NOISE_LORA = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE = "wan_2.1_vae.safetensors"
VIDEO_SUBFOLDER = "video"

FPS = 16
SEGMENT_FRAMES = 81  # ~5 s; o Wan pede 4n+1 quadros
SEGMENT_SECONDS = 5
MAX_SECONDS = 20
STEPS = 4  # LightX2V: 2 passos no modelo de ruido alto, 2 no de ruido baixo
# Area alvo de cada qualidade (a proporcao vem da foto).
QUALITY_PIXELS = {"480p": 480 * 832, "720p": 720 * 1280}
# Tempo maximo por trecho de 5 s antes de desistir.
SEGMENT_TIMEOUT = {"480p": 600.0, "720p": 1200.0}

REALISM_SUFFIX = "realistic video, natural smooth motion, consistent face and body, detailed skin"
NEGATIVE_PROMPT = (
    "blurry, low quality, static image, frozen, distorted face, morphing face, deformed, bad anatomy, "
    "extra limbs, extra legs, extra arms, extra fingers, fused limbs, flickering, jittery motion, "
    "watermark, text, subtitles, logo, cartoon, cgi, 3d render, oversaturated, "
    # Objetos na mao (bafometro, sorvete, celular) deformavam no meio do video.
    "morphing objects, melting objects, object changing shape, object disappearing, duplicate objects, deformed hands"
)


@dataclass
class VideoRequest:
    image: str  # nome do arquivo no ComfyUI
    image_type: str  # "output" (imagem gerada) ou "input" (enviada)
    prompt: str
    seconds: int = 5
    quality: str = "480p"
    source_width: int | None = None
    source_height: int | None = None
    image_subfolder: str = ""
    seed: int | None = None
    # Continuar: mp4 e ultimo quadro (PNG) do video anterior, em output/video.
    continue_video: str = ""
    continue_last_frame: str = ""
    continue_width: int | None = None
    continue_height: int | None = None
    continue_seconds: int = 0


@dataclass
class VideoResponse:
    prompt_id: str
    seconds: int
    width: int
    height: int
    videos: list[GenerationOutputImage]
    duration_seconds: float
    # Ultimo quadro em PNG: ponto de partida de um "Continuar".
    last_frame: GenerationOutputImage | None = None
    # Movimento usado (o digitado, traduzido, ou o criado pela IA).
    motion: str = ""


def video_size(quality: str, width: int | None, height: int | None) -> tuple[int, int]:
    """Tamanho do video: a proporcao da foto na area da qualidade, multiplo de 16."""
    area = QUALITY_PIXELS.get(quality, QUALITY_PIXELS["480p"])
    if not width or not height:
        width, height = 9, 16
    scale = (area / (width * height)) ** 0.5
    return max(256, round(width * scale / 16) * 16), max(256, round(height * scale / 16) * 16)


class VideoService:
    def __init__(
        self,
        comfyui_client: ComfyUIClient,
        llm_client: OllamaClient | None = None,
        scene_describer: SceneDescriber | None = None,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.llm_client = llm_client
        self.scene_describer = scene_describer

    def build_graph(
        self,
        req: VideoRequest,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        segments: int,
        previous_video: str = "",
    ) -> dict[str, Any]:
        if req.continue_last_frame:
            image, image_type = f"{VIDEO_SUBFOLDER}/{req.continue_last_frame}", "output"
        else:
            image = f"{req.image_subfolder}/{req.image}" if req.image_subfolder else req.image
            image_type = req.image_type
        g: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": HIGH_NOISE_MODEL, "weight_dtype": "default"}},
            "2": {"class_type": "UNETLoader", "inputs": {"unet_name": LOW_NOISE_MODEL, "weight_dtype": "default"}},
            "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": HIGH_NOISE_LORA, "strength_model": 1.0}},
            "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": LOW_NOISE_LORA, "strength_model": 1.0}},
            "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": 5.0}},
            "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": 5.0}},
            "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"}},
            "8": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["7", 0]}},
            "9": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE_PROMPT, "clip": ["7", 0]}},
            "10": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
            # "nome [output]" faz o LoadImage ler da pasta output/ (imagem gerada).
            "11": {"class_type": "LoadImage", "inputs": {"image": f"{image} [{image_type}]"}},
            "12": {
                "class_type": "ImageScale",
                "inputs": {"image": ["11", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "center"},
            },
        }
        next_id = 13

        def add(class_type: str, inputs: dict[str, Any]) -> str:
            nonlocal next_id
            node_id = str(next_id)
            next_id += 1
            g[node_id] = {"class_type": class_type, "inputs": inputs}
            return node_id

        start_image = ["12", 0]
        frames: list[Any] | None = None
        for k in range(segments):
            i2v = add(
                "WanImageToVideo",
                {
                    "positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0], "start_image": start_image,
                    "width": width, "height": height, "length": SEGMENT_FRAMES, "batch_size": 1,
                },
            )
            common = {
                "steps": STEPS, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                "positive": [i2v, 0], "negative": [i2v, 1],
            }
            high = add(
                "KSamplerAdvanced",
                {
                    **common, "model": ["5", 0], "add_noise": "enable", "noise_seed": seed + k,
                    "latent_image": [i2v, 2], "start_at_step": 0, "end_at_step": STEPS // 2,
                    "return_with_leftover_noise": "enable",
                },
            )
            low = add(
                "KSamplerAdvanced",
                {
                    **common, "model": ["6", 0], "add_noise": "disable", "noise_seed": 0,
                    "latent_image": [high, 0], "start_at_step": STEPS // 2, "end_at_step": 10000,
                    "return_with_leftover_noise": "disable",
                },
            )
            decoded = add("VAEDecode", {"samples": [low, 0], "vae": ["10", 0]})
            if frames is None:
                frames = [decoded, 0]
            else:
                # O 1o quadro deste trecho repete o ultimo do anterior.
                trimmed = add("ImageFromBatch", {"image": [decoded, 0], "batch_index": 1, "length": SEGMENT_FRAMES - 1})
                frames = [add("ImageBatch", {"image1": frames, "image2": [trimmed, 0]}), 0]
            last = add("ImageFromBatch", {"image": [decoded, 0], "batch_index": SEGMENT_FRAMES - 1, "length": 1})
            start_image = [last, 0]
        # PNG do ultimo quadro para um proximo "Continuar".
        add("SaveImage", {"images": start_image, "filename_prefix": f"{VIDEO_SUBFOLDER}/luna_video_fim"})

        if previous_video:
            # Video anterior + os novos quadros (o 1o novo repete o ultimo antigo).
            loaded = add("LoadVideo", {"file": previous_video})
            parts = add("GetVideoComponents", {"video": [loaded, 0]})
            new = add("ImageFromBatch", {"image": frames, "batch_index": 1, "length": 100000})
            frames = [add("ImageBatch", {"image1": [parts, 0], "image2": [new, 0]}), 0]

        video = add("CreateVideo", {"images": frames, "fps": float(FPS)})
        add("SaveVideo", {"video": [video, 0], "filename_prefix": f"{VIDEO_SUBFOLDER}/luna_video", "format": "mp4", "codec": "auto"})
        return g

    async def generate(self, req: VideoRequest) -> VideoResponse:
        seconds = max(SEGMENT_SECONDS, min(MAX_SECONDS, req.seconds // SEGMENT_SECONDS * SEGMENT_SECONDS))
        width, height = video_size(req.quality, req.source_width, req.source_height)
        previous_video = ""
        if req.continue_video:
            if not req.continue_last_frame:
                raise WorkflowParamError("Esse video nao tem o ultimo quadro salvo; gere um video novo para poder continuar.")
            # Mesmo tamanho do anterior (os quadros sao emendados).
            width = req.continue_width or width
            height = req.continue_height or height
            # O LoadVideo so le da pasta input/: copia o mp4 anterior para la.
            content = await self.comfyui_client.download_file(req.continue_video, VIDEO_SUBFOLDER, "output")
            previous_video = await self.comfyui_client.upload_image(f"continuar_{uuid.uuid4().hex[:10]}.mp4", content)
        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)
        # A IA olha a foto de onde o video parte (ou o ultimo quadro, ao
        # continuar): a cena descrita mantem objetos e pessoas no lugar, e sem
        # movimento digitado ela propoe um natural para a cena.
        if req.continue_last_frame:
            start_image = f"{VIDEO_SUBFOLDER}/{req.continue_last_frame} [output]"
        else:
            name = f"{req.image_subfolder}/{req.image}" if req.image_subfolder else req.image
            start_image = f"{name} [{req.image_type}]"
        scene = await self.scene_describer.describe(start_image) if self.scene_describer else ""
        motion = await to_english(self.llm_client, req.prompt)
        if not motion and self.scene_describer:
            motion = await self.scene_describer.suggest_motion(scene)
        motion = motion or "she moves naturally and smiles"
        prompt = f"{motion}. Scene: {scene}, {REALISM_SUFFIX}" if scene else f"{motion}, {REALISM_SUFFIX}"

        graph = self.build_graph(req, prompt, width, height, seed, seconds // SEGMENT_SECONDS, previous_video)
        start = time.monotonic()
        prompt_id = await self.comfyui_client.queue_prompt(graph)
        timeout = SEGMENT_TIMEOUT.get(req.quality, 600.0) * (seconds // SEGMENT_SECONDS)
        entry = await self.comfyui_client.wait_for_completion(prompt_id, timeout=timeout)
        outputs = self.comfyui_client.extract_images(entry)
        return VideoResponse(
            prompt_id=prompt_id,
            seconds=req.continue_seconds + seconds if previous_video else seconds,
            width=width,
            height=height,
            videos=[o for o in outputs if o.filename.endswith(".mp4")],
            duration_seconds=time.monotonic() - start,
            last_frame=next((o for o in outputs if o.filename.endswith(".png")), None),
            motion=motion,
        )
