"""Variante F: Qwen-Image-2.1 (licenca de PESQUISA - so comparacao, nunca producao).

Montado do template oficial do ComfyUI v0.37 (image_qwen_image_2_1_image_edit.json):
UNETLoader int8 -> QwenImage21Cache -> KSampler (25 passos, CFG 1, euler/simple);
TextEncodeQwenImage21 com image_1 = recorte da cena e image_2 = cabeca da Luna.
Diferenca pedida no prompt: a edicao e LIMITADA PELA MASCARA DA CABECA (no lugar do
retangulo vermelho): o latente do proprio recorte (VAEEncode) com SetLatentNoiseMask.
"""
from __future__ import annotations

import workflows_bench as wb


def build_f(body: str, head: str, mask: str | None, width: int, height: int, seed: int, prefix: str,
            crop: tuple[int, int, int, int]) -> dict:
    if not mask:
        raise ValueError("a variante F precisa da mascara da cabeca (recorte, tamanho do modelo)")
    x, y, w, h = crop
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2.1_int8_convrot.safetensors",
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                                                     "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "4": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
        "10": {"class_type": "LoadImage", "inputs": {"image": body}},
        "11": {"class_type": "LoadImage", "inputs": {"image": head}},
        "14": {"class_type": "ImageCrop", "inputs": {"image": ["10", 0], "width": w, "height": h, "x": x, "y": y}},
        "12": {"class_type": "ImageScale", "inputs": {"image": ["14", 0], "upscale_method": "lanczos", "width": width,
                                                      "height": height, "crop": "disabled"}},
        "15": {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}},
        "20": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "prompt": wb.F_PROMPT,
                                                                 "negative_prompt": "", "resolution": 1024,
                                                                 "images.image_1": ["12", 0], "images.image_2": ["11", 0],
                                                                 "vae": ["3", 0]}},
        "30": {"class_type": "VAEEncode", "inputs": {"pixels": ["12", 0], "vae": ["3", 0]}},
        "35": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["30", 0], "mask": ["15", 0]}},
        "31": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["20", 0], "negative": ["20", 1],
                                                    "latent_image": ["35", 0], "seed": seed, "steps": 25, "cfg": 1.0,
                                                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "32": {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["3", 0]}},
        "33": {"class_type": "SaveImage", "inputs": {"images": ["32", 0], "filename_prefix": prefix}},
    }


wb.F_TEMPLATE = build_f
