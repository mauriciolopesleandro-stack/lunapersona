"""Workflows (formato API do ComfyUI) das variantes do benchmark de troca de cabeca
(PROMPT_modelo_fase_unica.md). Todas recebem o MESMO recorte da cena (Picture 1) e a
MESMA cabeca da Luna (Picture 2) e salvam SO o recorte gerado: quem compoe e o
compor_cabeca.py, fora do pod.

  A  2511 Q3_K_M (GGUF)        B  2511 fp8 (= estudio)       C  2511 NVFP4
  D  FireRed 1.1 + BFS          E  FireRed 1.1 sem BFS         F  Qwen-Image-2.1 (mascara, pesquisa)
  G  2511 fp8 + BFS + LoRA da Luna (luna_qwen_2511_v1)

Parametros do estudio (A,B,C,G): 8 passos, CFG 1, euler/simple, shift 3,1, CFGNorm 1,
index_timestep_zero, Lightning 8 passos + BFS head V5. FireRed (D,E): os mesmos nos do
workflow oficial (firered-image-edit-1.1.json: AuraFlow 3,1, CFGNorm 1, Lightning 8 passos,
CFG 1, euler/simple), com a Lightning da propria FireRed.
"""
from __future__ import annotations

import copy

BFS_PROMPT = (
    "head_swap: start with Picture 1 as the base image, keeping its lighting, environment, and background. "
    "remove the head from Picture 1 completely and replace it with the head from Picture 2, strictly preserving "
    "the hair, eye color, and nose structure of Picture 2. copy the eye direction, head rotation, and "
    "micro-expressions from Picture 1."
)
F_PROMPT = (
    "Replace the head inside the mask with the head of the woman from the reference image: same face, eyes, "
    "nose, lips, jawline, skin tone and long dark brown hair. Keep the head rotation, eye direction and "
    "expression of the original, and the lighting of the scene. Do not change anything outside the mask."
)

ENC, VAE = "qwen_2.5_vl_7b_fp8_scaled.safetensors", "qwen_image_vae.safetensors"
QWEN_LIGHT = "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"
FIRE_LIGHT = "FireRed-Image-Edit-1.1-Lightning-8steps-v1.2.safetensors"
BFS = "bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
LUNA_LORA = "luna_qwen_2511_v1.safetensors"

# arquivos baixados na sessao (destino no disco do pod -> URL)
DOWNLOADS = {
    "A": [("diffusion_models", "qwen-image-edit-2511-Q3_K_M.gguf",
           "https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main/qwen-image-edit-2511-Q3_K_M.gguf")],
    "B": [],
    "C": [("diffusion_models", "qwen_image_edit_2511_nvfp4.safetensors",
           "https://huggingface.co/ANXS1/Qwen-Image-Edit-2511-NVFP4/resolve/main/qwen_image_edit_2511_nvfp4.safetensors")],
    "D": [("diffusion_models", "FireRed-Image-Edit-1.1-transformer.safetensors",
           "https://huggingface.co/FireRedTeam/FireRed-Image-Edit-1.1-ComfyUI/resolve/main/FireRed-Image-Edit-1.1-transformer.safetensors"),
          ("loras", FIRE_LIGHT,
           f"https://huggingface.co/FireRedTeam/FireRed-Image-Edit-1.1-ComfyUI/resolve/main/{FIRE_LIGHT}")],
    "E": [],  # mesmos arquivos do D
    "F": [("diffusion_models", "qwen_image_2.1_int8_convrot.safetensors",
           "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_int8_convrot.safetensors"),
          ("text_encoders", "qwen3vl_8b_int8_convrot.safetensors",
           "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors"),
          ("vae", "qwen_image_2.1_vae_bf16.safetensors",
           "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors")],
    "G": [],
}
NEEDS = {"E": "D"}  # E so roda quando os arquivos do D chegaram
ORDER = ["B", "G", "A", "C", "F", "D", "E"]  # por modelo; B/G ja estao no volume, A e o menor download


def _qwen_edit(loader: dict, loras: list[tuple[str, float]], body: str, head: str, width: int, height: int,
               seed: int, prefix: str, crop: tuple[int, int, int, int]) -> dict:
    x, y, w, h = crop
    g = {
        "1": loader,
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": ENC, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "CFGNorm", "inputs": {"model": ["4", 0], "strength": 1, "pre_cfg": False}},
    }
    prev = ["5", 0]
    for i, (name, strength) in enumerate(loras):
        k = str(6 + i)
        g[k] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": prev, "lora_name": name, "strength_model": strength}}
        prev = [k, 0]
    g.update({
        "10": {"class_type": "LoadImage", "inputs": {"image": body}},
        "11": {"class_type": "LoadImage", "inputs": {"image": head}},
        "14": {"class_type": "ImageCrop", "inputs": {"image": ["10", 0], "width": w, "height": h, "x": x, "y": y}},
        "12": {"class_type": "ImageScale", "inputs": {"image": ["14", 0], "upscale_method": "lanczos", "width": width,
                                                      "height": height, "crop": "disabled"}},
        "13": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["11", 0], "upscale_method": "lanczos",
                                                                   "megapixels": 1.0, "resolution_steps": 16}},
        "20": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "prompt": BFS_PROMPT, "vae": ["3", 0],
                                                                       "image1": ["12", 0], "image2": ["13", 0]}},
        "21": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "prompt": "", "vae": ["3", 0],
                                                                       "image1": ["12", 0], "image2": ["13", 0]}},
        "22": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["20", 0], "reference_latents_method": "index_timestep_zero"}},
        "23": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["21", 0], "reference_latents_method": "index_timestep_zero"}},
        "30": {"class_type": "VAEEncode", "inputs": {"pixels": ["12", 0], "vae": ["3", 0]}},
        "31": {"class_type": "KSampler", "inputs": {"model": prev, "positive": ["22", 0], "negative": ["23", 0],
                                                    "latent_image": ["30", 0], "seed": seed, "steps": 8, "cfg": 1.0,
                                                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "32": {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["3", 0]}},
        "33": {"class_type": "SaveImage", "inputs": {"images": ["32", 0], "filename_prefix": prefix}},
    })
    return g


def _unet(name: str, dtype: str = "default") -> dict:
    return {"class_type": "UNETLoader", "inputs": {"unet_name": name, "weight_dtype": dtype}}


def build(variant: str, body: str, head: str, width: int, height: int, seed: int, prefix: str,
          crop: tuple[int, int, int, int], mask: str | None = None) -> dict:
    if variant == "A":
        return _qwen_edit({"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "qwen-image-edit-2511-Q3_K_M.gguf"}},
                          [(QWEN_LIGHT, 1.0), (BFS, 1.0)], body, head, width, height, seed, prefix, crop)
    if variant == "B":
        return _qwen_edit(_unet("qwen_image_edit_2511_fp8mixed.safetensors"), [(QWEN_LIGHT, 1.0), (BFS, 1.0)],
                          body, head, width, height, seed, prefix, crop)
    if variant == "C":
        return _qwen_edit(_unet("qwen_image_edit_2511_nvfp4.safetensors"), [(QWEN_LIGHT, 1.0), (BFS, 1.0)],
                          body, head, width, height, seed, prefix, crop)
    if variant == "D":
        return _qwen_edit(_unet("FireRed-Image-Edit-1.1-transformer.safetensors", "fp8_e4m3fn"), [(FIRE_LIGHT, 1.0), (BFS, 1.0)],
                          body, head, width, height, seed, prefix, crop)
    if variant == "E":
        return _qwen_edit(_unet("FireRed-Image-Edit-1.1-transformer.safetensors", "fp8_e4m3fn"), [(FIRE_LIGHT, 1.0)],
                          body, head, width, height, seed, prefix, crop)
    if variant == "G":
        return _qwen_edit(_unet("qwen_image_edit_2511_fp8mixed.safetensors"),
                          [(QWEN_LIGHT, 1.0), (BFS, 1.0), (LUNA_LORA, 1.0)], body, head, width, height, seed, prefix, crop)
    if variant == "F":
        if F_TEMPLATE is None:
            raise RuntimeError("workflow do Qwen-Image-2.1 ainda nao montado (F_TEMPLATE)")
        return F_TEMPLATE(body=body, head=head, mask=mask, width=width, height=height, seed=seed, prefix=prefix, crop=crop)
    raise ValueError(variant)


F_TEMPLATE = None  # preenchido por workflow_f.py (montado a partir do template oficial do ComfyUI v0.37)


def files_of(variant: str) -> list[tuple[str, str, str]]:
    return copy.deepcopy(DOWNLOADS.get(NEEDS.get(variant, variant), []))
