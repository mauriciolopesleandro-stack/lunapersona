"""Benchmark de consistencia da persona (roda no pod, Python do ComfyUI - tem PIL).

  /workspace/runpod-slim/ComfyUI/.venv-cu128/bin/python /workspace/bench/benchmark_persona.py [gen|measure|sheet] [configs...]

Configuracoes (10 imagens cada, sementes 100-109, 832x1216):
  A    Z-Image Turbo + LoRA luna_zimage_v1 1.0, prompt simples de corpo inteiro
  A13  igual ao A com a LoRA 1.3 (o teto do RetryManager)
  B    Qwen-Image-Edit 2511 gerando do zero com 2 referencias (rosto + corpo inteiro), sem LoRA
  C    A + pose (DWPose da foto de corpo inteiro da persona + ControlNet Union do Z-Image)
  D    C + cena (calcada em frente a um cafe)

Medidas (todas automaticas, sem nota inventada):
  - LunaFaces (InsightFace antelopev2): rostos, semelhanca ArcFace com a foto principal, idade, sexo
  - DWPose: quantos corpos, e proporcoes do corpo principal divididas pelo tronco
  - Florence-2 large: caixas de "person"
"""
from __future__ import annotations

import ast
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

COMFY = "http://127.0.0.1:8188"
ROOT = Path("/workspace/runpod-slim/ComfyUI")
OUT = ROOT / "output"
BENCH = Path("/workspace/bench")
RESULTS = BENCH / "results.json"
W, H = 832, 1216
SEEDS = list(range(100, 110))
FACE_REF = "bench_face_ref.png"   # foto principal (luna_15_v1)
BODY_REF = "bench_body_ref.png"   # corpo inteiro (pose_01_v1)
DS23 = "bench_ds23.png"           # foto 23 do dataset (corpo inteiro, ChatGPT)

ZUNET, ZTE, ZVAE = "z_image_turbo_int8_convrot.safetensors", "qwen_3_4b_fp4_mixed.safetensors", "ae.safetensors"
LORA = "luna_zimage_v1.safetensors"
PATCH = "Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors"
SIMPLE = ("lunavox, a woman, full body photo, standing, facing the camera, plain light gray studio background, "
          "wearing a black tank top and blue jeans, white sneakers, soft even light")
SCENE = ("lunavox, a woman, full body photo, standing on a sidewalk in front of a cafe in Sao Paulo, "
         "wearing a black tank top and blue jeans, white sneakers, daylight")
QWEN_PROMPT = ("Create a new full body photo of the same woman: Picture 1 shows her face, Picture 2 shows her "
               "whole body. Keep exactly the same face, hair, skin tone, body shape and body proportions. "
               "She is standing, facing the camera, in a plain light gray studio, wearing a black tank top and "
               "blue jeans and white sneakers, soft even light. Only one person in the photo.")

CONFIGS = {
    "A": {"kind": "zimage", "strength": 1.0, "prompt": SIMPLE, "pose": False},
    "A13": {"kind": "zimage", "strength": 1.3, "prompt": SIMPLE, "pose": False},
    "C": {"kind": "zimage", "strength": 1.0, "prompt": SIMPLE, "pose": True},
    "D": {"kind": "zimage", "strength": 1.0, "prompt": SCENE, "pose": True},
    "B": {"kind": "qwen", "prompt": QWEN_PROMPT},
}


def call(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(COMFY + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def run(graph: dict, timeout: float = 900) -> dict:
    pid = call("/prompt", {"prompt": graph})["prompt_id"]
    start = time.time()
    while time.time() - start < timeout:
        hist = call(f"/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed"):
            return hist
        if hist and hist.get("status", {}).get("status_str") == "error":
            raise RuntimeError(json.dumps(hist["status"].get("messages"))[:2000])
        time.sleep(1.0)
    raise TimeoutError(pid)


def saved(entry: dict) -> str:
    for out in entry["outputs"].values():
        for img in out.get("images", []):
            return f"{img['filename']} [output]"
    raise RuntimeError("sem imagem")


def texts(entry: dict) -> dict[str, str]:
    return {k: str(v["text"][0]) for k, v in entry["outputs"].items() if v.get("text")}


def zimage_graph(cfg: dict, seed: int, prefix: str) -> dict:
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": ZUNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": ZTE, "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": ZVAE}},
        "11": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": LORA, "strength_model": cfg["strength"]}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["11", 0], "shift": 3}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": cfg["prompt"]}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                                                     "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    if cfg["pose"]:
        g["20"] = {"class_type": "LoadImage", "inputs": {"image": BODY_REF}}
        g["21"] = {"class_type": "ImageScale", "inputs": {"image": ["20", 0], "upscale_method": "lanczos", "width": W, "height": H, "crop": "center"}}
        g["22"] = {"class_type": "DWPreprocessor", "inputs": {"image": ["21", 0], "detect_hand": "enable", "detect_body": "enable", "detect_face": "enable",
                                                              "resolution": 1024, "bbox_detector": "yolox_l.onnx",
                                                              "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable"}}
        g["23"] = {"class_type": "ImageScale", "inputs": {"image": ["22", 0], "upscale_method": "bilinear", "width": W, "height": H, "crop": "disabled"}}
        g["50"] = {"class_type": "ModelPatchLoader", "inputs": {"name": PATCH}}
        g["30"] = {"class_type": "ZImageFunControlnet", "inputs": {"model": ["11", 0], "model_patch": ["50", 0], "vae": ["3", 0], "image": ["23", 0], "strength": 0.6}}
        g["4"]["inputs"]["model"] = ["30", 0]
    return g


def qwen_graph(seed: int, prefix: str) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_edit_2511_fp8mixed.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "CFGNorm", "inputs": {"model": ["4", 0], "strength": 1}},
        "6": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["5", 0], "lora_name": "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors", "strength_model": 1.0}},
        "10": {"class_type": "LoadImage", "inputs": {"image": FACE_REF}},
        "11": {"class_type": "LoadImage", "inputs": {"image": BODY_REF}},
        "12": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["10", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 16}},
        "13": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["11", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 16}},
        "20": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["12", 0], "image2": ["13", 0], "prompt": QWEN_PROMPT}},
        "21": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["12", 0], "image2": ["13", 0], "prompt": ""}},
        "22": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["20", 0], "reference_latents_method": "index_timestep_zero"}},
        "23": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["21", 0], "reference_latents_method": "index_timestep_zero"}},
        "30": {"class_type": "EmptySD3LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
        "31": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["22", 0], "negative": ["23", 0], "latent_image": ["30", 0],
                                                      "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1}},
        "32": {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["3", 0]}},
        "33": {"class_type": "SaveImage", "inputs": {"images": ["32", 0], "filename_prefix": prefix}},
    }


def load() -> dict:
    return json.loads(RESULTS.read_text()) if RESULTS.exists() else {}


def save(results: dict) -> None:
    RESULTS.write_text(json.dumps(results, indent=1))


def generate(names: list[str]) -> None:
    results = load()
    for name in names:
        cfg = CONFIGS[name]
        for seed in SEEDS:
            key = f"{name}_{seed}"
            if key in results and results[key].get("image"):
                continue
            t0 = time.time()
            graph = qwen_graph(seed, f"bench_{key}") if cfg["kind"] == "qwen" else zimage_graph(cfg, seed, f"bench_{key}")
            try:
                image = saved(run(graph))
                results[key] = {"config": name, "seed": seed, "image": image, "seconds": round(time.time() - t0, 1)}
            except Exception as exc:  # registra e segue
                results[key] = {"config": name, "seed": seed, "error": str(exc)[:500]}
            print(key, results[key].get("seconds"), results[key].get("error", "")[:200], flush=True)
            save(results)


KP = {"nose": 0, "neck": 1, "rsho": 2, "relb": 3, "rwri": 4, "lsho": 5, "lelb": 6, "lwri": 7,
      "rhip": 8, "rkne": 9, "rank": 10, "lhip": 11, "lkne": 12, "lank": 13}


def parse_pose(text: str) -> list[list[tuple[float, float, float]]]:
    try:
        data = json.loads(text)
    except ValueError:
        data = ast.literal_eval(text)
    frame = data[0] if isinstance(data, list) else data
    cw, ch = frame.get("canvas_width", 1), frame.get("canvas_height", 1)
    people = []
    for person in frame.get("people", []):
        pts = person.get("pose_keypoints_2d") or []
        triples = [tuple(pts[i:i + 3]) for i in range(0, len(pts), 3)]
        if triples and max(max(t[0], t[1]) for t in triples) <= 1.0:
            triples = [(x * cw, y * ch, c) for x, y, c in triples]
        people.append(triples)
    return people


def body_ratios(kp: list[tuple[float, float, float]]) -> dict:
    def p(name):
        x, y, c = kp[KP[name]] if KP[name] < len(kp) else (0, 0, 0)
        return (x, y) if c and c > 0.3 else None

    def d(a, b):
        pa, pb = p(a), p(b)
        return math.dist(pa, pb) if pa and pb else None

    mid_hip = None
    if p("rhip") and p("lhip"):
        mid_hip = ((p("rhip")[0] + p("lhip")[0]) / 2, (p("rhip")[1] + p("lhip")[1]) / 2)
    torso = math.dist(p("neck"), mid_hip) if p("neck") and mid_hip else None
    visible = sum(1 for k in KP if p(k))
    out = {"visible_keypoints": visible, "torso_px": round(torso, 1) if torso else None}
    if not torso:
        return out
    for label, (a, b) in {"shoulders": ("rsho", "lsho"), "hips": ("rhip", "lhip"), "r_thigh": ("rhip", "rkne"),
                          "l_thigh": ("lhip", "lkne"), "r_shin": ("rkne", "rank"), "l_shin": ("lkne", "lank"),
                          "r_upper_arm": ("rsho", "relb"), "l_upper_arm": ("lsho", "lelb")}.items():
        v = d(a, b)
        out[label] = round(v / torso, 3) if v else None
    return out


def measure_graph(image: str) -> dict:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "LoadImage", "inputs": {"image": FACE_REF}},
        "lf": {"class_type": "LunaFaces", "inputs": {"image": ["1", 0], "det_size": 1024, "reference": ["2", 0]}},
        "dw": {"class_type": "DWPreprocessor", "inputs": {"image": ["1", 0], "detect_hand": "enable", "detect_body": "enable", "detect_face": "disable",
                                                          "resolution": 1024, "bbox_detector": "yolox_l.onnx",
                                                          "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable"}},
        "dwp": {"class_type": "PreviewAny", "inputs": {"source": ["dw", 1]}},
        "fm": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {"model": "microsoft/Florence-2-large", "precision": "fp16"}},
        "fr": {"class_type": "Florence2Run", "inputs": {"image": ["1", 0], "florence2_model": ["fm", 0], "text_input": "person",
                                                        "task": "caption_to_phrase_grounding", "fill_mask": False, "keep_model_loaded": True,
                                                        "max_new_tokens": 512, "num_beams": 3, "do_sample": False, "output_mask_select": "", "seed": 1}},
        "frp": {"class_type": "PreviewAny", "inputs": {"source": ["fr", 3]}},
    }


def florence_boxes(text: str) -> int | None:
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None
    if isinstance(data, list):
        data = data[0] if data else {}
    boxes = (data or {}).get("bboxes") or []
    if boxes and isinstance(boxes[0], list) and boxes[0] and isinstance(boxes[0][0], list):
        boxes = boxes[0]
    return len(boxes)


def measure_one(image: str) -> dict:
    t = texts(run(measure_graph(image)))
    faces = json.loads(t.get("lf", "[]"))
    people = parse_pose(t["dwp"]) if t.get("dwp") else []
    main = max(people, key=lambda kp: sum(1 for x in kp[:14] if x[2] > 0.3)) if people else None
    return {
        "faces": [{k: f.get(k) for k in ("sim", "age", "sex", "score", "yaw", "bbox")} for f in faces],
        "dwpose_people": sum(1 for kp in people if sum(1 for x in kp[:14] if x[2] > 0.3) >= 4),
        "body": body_ratios(main) if main else None,
        "florence_person_boxes": florence_boxes(t.get("frp", "")),
    }


def measure(names: list[str]) -> None:
    results = load()
    refs = results.setdefault("_refs", {})
    for ref in (FACE_REF, BODY_REF, DS23):
        if ref not in refs:
            refs[ref] = measure_one(ref)
            print(ref, json.dumps(refs[ref])[:300], flush=True)
            save(results)
    for key, row in sorted(results.items()):
        if key.startswith("_") or row.get("config") not in names or not row.get("image") or "measure" in row:
            continue
        try:
            row["measure"] = measure_one(row["image"])
        except Exception as exc:
            row["measure_error"] = str(exc)[:500]
        print(key, json.dumps(row.get("measure") or row.get("measure_error"))[:300], flush=True)
        save(results)


def sheet(names: list[str]) -> None:
    results = load()
    for name in names:
        rows = [results[f"{name}_{s}"] for s in SEEDS if results.get(f"{name}_{s}", {}).get("image")]
        if not rows:
            continue
        tw, th = 300, 438
        canvas = Image.new("RGB", (tw * 5, (th + 34) * 2), "black")
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(rows[:10]):
            img = Image.open(OUT / row["image"].replace(" [output]", "")).convert("RGB").resize((tw, th))
            x, y = (i % 5) * tw, (i // 5) * (th + 34)
            canvas.paste(img, (x, y))
            m = row.get("measure") or {}
            faces = m.get("faces") or []
            sim = max((f.get("sim") or 0 for f in faces), default=0)
            label = f"s{row['seed']} sim {sim:.2f} rostos {len(faces)} corpos {m.get('dwpose_people')} fl {m.get('florence_person_boxes')}"
            draw.text((x + 4, y + th + 4), label, fill="white")
        canvas.save(BENCH / f"sheet_{name}.jpg", quality=85)
        print("sheet", name, flush=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    names = sys.argv[2:] or list(CONFIGS)
    {"gen": generate, "measure": measure, "sheet": sheet}[mode](names)
