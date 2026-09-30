"""Persona falando no video: o texto vira audio com a voz dela (VoiceService,
VoxCPM2) e o Wan 2.2 S2V anima uma foto dela com a boca sincronizada a esse
audio. Modelos em scripts/setup_wan.sh.

Mesmo grafo do template oficial do ComfyUI (video_wan2_2_14B_s2v): trechos de
77 quadros (~4,8 s a 16 fps), o 1o com WanSoundImageToVideo e os seguintes com
WanSoundImageToVideoExtend, emendados no latente; LoRA LightX2V de 4 passos.
O numero de trechos sai da duracao do audio.
"""
from __future__ import annotations

import math
import time
import uuid
import wave
from dataclasses import dataclass
from typing import Any

from app.clients.comfyui_client import ComfyUIClient
from app.clients.llm_client import OllamaClient
from app.services.prompt_translator import to_english
from app.services.video_service import FPS, TEXT_ENCODER, VAE, VideoResponse, video_size
from app.services.voice_service import COMFY_OUTPUT, SUBFOLDER, VoiceService
from app.workflow_manager.manager import WorkflowParamError

S2V_MODEL = "wan2.2_s2v_14B_fp8_scaled.safetensors"
S2V_LORA = "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"
AUDIO_ENCODER = "wav2vec2_large_english_fp16.safetensors"
CHUNK_FRAMES = 77  # o S2V pede pelo menos 73
STEPS = 4
CFG = 1.0
MAX_CHUNKS = 6  # ~29 s de fala
CHUNK_TIMEOUT = {"480p": 900.0, "720p": 1800.0}

TALK_PROMPT = (
    "a young woman talking directly to the camera, natural lip movement synchronized with her speech, "
    "expressive face, subtle natural head and shoulder movements, realistic video, consistent face and body"
)
NEGATIVE_PROMPT = (
    "oversaturated, overexposed, static, blurry details, subtitles, text, watermark, logo, worst quality, "
    "low quality, jpeg artifacts, ugly, deformed, extra fingers, bad hands, bad face, distorted mouth, "
    "fused fingers, frozen image, cluttered background, three legs, many people in the background"
)


@dataclass
class TalkRequest:
    persona_id: str
    image: str
    image_type: str
    text: str
    image_subfolder: str = ""
    extra_prompt: str = ""  # opcional: gesto/expressao ("sorrindo, mexendo no cabelo")
    quality: str = "480p"
    source_width: int | None = None
    source_height: int | None = None
    seed: int | None = None


def _wav_seconds(path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


class TalkService:
    def __init__(
        self, comfyui_client: ComfyUIClient, voice_service: VoiceService, llm_client: OllamaClient | None = None
    ) -> None:
        self.comfyui_client = comfyui_client
        self.voice_service = voice_service
        self.llm_client = llm_client

    def build_graph(
        self, image: str, audio: str, prompt: str, width: int, height: int, chunks: int, seed: int
    ) -> dict[str, Any]:
        g: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": S2V_MODEL, "weight_dtype": "default"}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": S2V_LORA, "strength_model": 1.0}},
            "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
            "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE_PROMPT, "clip": ["4", 0]}},
            "7": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
            "8": {"class_type": "AudioEncoderLoader", "inputs": {"audio_encoder_name": AUDIO_ENCODER}},
            # "nome [output]": o audio da fala fica em output/voice, a foto em output/.
            "9": {"class_type": "LoadAudio", "inputs": {"audio": audio}},
            "10": {"class_type": "AudioEncoderEncode", "inputs": {"audio_encoder": ["8", 0], "audio": ["9", 0]}},
            "11": {"class_type": "LoadImage", "inputs": {"image": image}},
            "12": {
                "class_type": "ImageScale",
                "inputs": {"image": ["11", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "center"},
            },
            "13": {
                "class_type": "WanSoundImageToVideo",
                "inputs": {
                    "positive": ["5", 0], "negative": ["6", 0], "vae": ["7", 0], "width": width, "height": height,
                    "length": CHUNK_FRAMES, "batch_size": 1, "audio_encoder_output": ["10", 0], "ref_image": ["12", 0],
                },
            },
        }
        next_id = 14

        def add(class_type: str, inputs: dict[str, Any]) -> str:
            nonlocal next_id
            node_id = str(next_id)
            next_id += 1
            g[node_id] = {"class_type": class_type, "inputs": inputs}
            return node_id

        sampler = {"steps": STEPS, "cfg": CFG, "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}
        latent = [
            add("KSampler", {
                **sampler, "model": ["3", 0], "seed": seed, "positive": ["13", 0], "negative": ["13", 1],
                "latent_image": ["13", 2],
            }),
            0,
        ]
        for k in range(1, chunks):
            ext = add("WanSoundImageToVideoExtend", {
                "positive": ["5", 0], "negative": ["6", 0], "vae": ["7", 0], "video_latent": latent,
                "length": CHUNK_FRAMES, "audio_encoder_output": ["10", 0], "ref_image": ["12", 0],
            })
            sampled = add("KSampler", {
                **sampler, "model": ["3", 0], "seed": seed + k, "positive": [ext, 0], "negative": [ext, 1],
                "latent_image": [ext, 2],
            })
            latent = [add("LatentConcat", {"samples1": latent, "samples2": [sampled, 0], "dim": "t"}), 0]
        # Truque do template: o 1o quadro sai "queimado" do VAE; duplica o 1o
        # latente e descarta o comeco depois de decodificar.
        first = add("LatentCut", {"samples": latent, "dim": "t", "index": 0, "amount": 1})
        doubled = add("LatentConcat", {"samples1": [first, 0], "samples2": latent, "dim": "t"})
        decoded = add("VAEDecode", {"samples": [doubled, 0], "vae": ["7", 0]})
        frames = add("ImageFromBatch", {"image": [decoded, 0], "batch_index": chunks, "length": 4096})
        video = add("CreateVideo", {"images": [frames, 0], "fps": float(FPS), "audio": ["9", 0]})
        add("SaveVideo", {"video": [video, 0], "filename_prefix": "video/luna_fala", "format": "mp4", "codec": "auto"})
        return g

    async def generate(self, req: TalkRequest) -> VideoResponse:
        start = time.monotonic()
        speech = await self.voice_service.speak(req.persona_id, req.text)
        audio_file = speech["audios"][0]["filename"]
        seconds = _wav_seconds(COMFY_OUTPUT / SUBFOLDER / audio_file)
        chunks = max(1, math.ceil(seconds * FPS / CHUNK_FRAMES))
        if chunks > MAX_CHUNKS:
            raise WorkflowParamError(
                f"A fala ficou com {seconds:.0f} s; o limite e ~{MAX_CHUNKS * CHUNK_FRAMES // FPS} s. Encurte o texto."
            )

        extra = await to_english(self.llm_client, req.extra_prompt) if req.extra_prompt.strip() else ""
        prompt = f"{TALK_PROMPT}, {extra}" if extra else TALK_PROMPT
        width, height = video_size(req.quality, req.source_width, req.source_height)
        image = f"{req.image_subfolder}/{req.image}" if req.image_subfolder else req.image
        seed = req.seed if req.seed is not None else uuid.uuid4().int % (2**32)
        graph = self.build_graph(
            f"{image} [{req.image_type}]", f"{SUBFOLDER}/{audio_file} [output]", prompt, width, height, chunks, seed
        )
        prompt_id = await self.comfyui_client.queue_prompt(graph)
        entry = await self.comfyui_client.wait_for_completion(
            prompt_id, timeout=CHUNK_TIMEOUT.get(req.quality, 900.0) * chunks
        )
        return VideoResponse(
            prompt_id=prompt_id,
            seconds=round(seconds),
            width=width,
            height=height,
            videos=self.comfyui_client.extract_images(entry),
            duration_seconds=time.monotonic() - start,
        )
