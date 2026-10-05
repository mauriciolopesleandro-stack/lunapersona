"""Benchmark E + C2 da persona (roda no pod, Python do ComfyUI - tem PIL).

  PY=/workspace/runpod-slim/ComfyUI/.venv-cu128/bin/python
  $PY benchmark_persona2.py gen <estagios...>   # gera (pula o que ja existe)
  $PY benchmark_persona2.py measure             # mede tudo que falta
  $PY benchmark_persona2.py sheet <estagios...> # folhas de contato

Estagios (todos 832x1216; provider, modelo, LoRA, workflow, semente e tempo gravados por imagem):
  E0   BASE: Z-Image Turbo int8 + LoRA luna_zimage_v1 1.0, 5 cenarios x 2 sementes
  E1   E0 -> Qwen-Image-Edit 2511 + BFS head V5 (workflow de producao qwen-bfs-head-swap), cabeca = rosto MASTER
  E2   E0 -> Qwen-Image-Edit 2511 com 3 imagens: base + rosto MASTER + corpo inteiro MASTER (instrucao de edicao)
  C2A  Z-Image + LoRA + pose (DWPose + ControlNet Union 0.8) de uma pose simples (em pe, de frente), texto coerente
  C2B  idem, 5 poses diferentes x 2 sementes, fundo de estudio
  C2C  idem C2B, com cena (cafe)
  CQ   C2C -> o mesmo do E2 (ControlNet + Qwen rosto+corpo)
  ST   teste de estresse da persona (definido depois do veredito; ver STRESS)
"""
from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

COMFY = "http://127.0.0.1:8188"
ROOT = Path("/workspace/runpod-slim/ComfyUI")
OUT, INP = ROOT / "output", ROOT / "input"
BENCH = Path("/workspace/bench2")
RESULTS = BENCH / "results.json"
W, H = 832, 1216
FACE_MASTER = "bench_face_ref.png"   # references/f7e81b9a... luna_15_v1 (PRIMARY)
BODY_MASTER = "bench_body_ref.png"   # references/db5b998d... pose_01_v1 (corpo inteiro)
POSE_04 = "bench_pose04.png"         # references/f4b1a7e6... pose_04_v1
DS23 = "bench_ds23.png"              # dataset 23 (andando)

ZUNET, ZTE, ZVAE = "z_image_turbo_int8_convrot.safetensors", "qwen_3_4b_fp4_mixed.safetensors", "ae.safetensors"
LORA = "luna_zimage_v1.safetensors"
PATCH = "Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors"
QUNET, QTE, QVAE = "qwen_image_edit_2511_fp8mixed.safetensors", "qwen_2.5_vl_7b_fp8_scaled.safetensors", "qwen_image_vae.safetensors"
QLIGHT = "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"
BFS = "bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
POSE_STRENGTH = 0.8

SCENARIOS = {
    "studio": "full body photo, standing, facing the camera, plain light gray studio background, wearing a black tank top and blue jeans, white sneakers, soft even light",
    "cafe": "full body photo, standing inside a cozy cafe next to the counter, wearing a beige sweater and dark trousers, ankle boots, warm indoor light",
    "walkway": "full body photo, standing on an outdoor park walkway with trees, wearing a white summer dress and sandals, daylight",
    "walking": "full body photo, walking towards the camera on a city sidewalk, wearing a denim jacket, black t-shirt and black jeans, sneakers, daylight",
    "profile": "full body photo, standing in slight side profile, body turned three quarters to the left, head turned towards the camera, plain studio background, wearing a red dress and heels",
}
BASE_SEEDS = [200, 201]
# Poses do C2B/C2C: (fonte do esqueleto, descricao coerente com a pose)
POSES = {
    "frontal": ("E0_studio_200", "standing, facing the camera, arms relaxed at her sides"),
    "walk34": (BODY_MASTER, "walking with a long stride, body in three quarter view, arms swinging"),
    "walk23": (DS23, "walking, looking to her side, holding a phone in one hand"),
    "side": ("E0_profile_200", "standing in slight side profile, body turned three quarters, head towards the camera"),
    "pose04": (POSE_04, "posing as in the reference pose"),
}
STUDIO = "plain light gray studio background, wearing a black tank top and blue jeans, white sneakers, soft even light"
CAFE = "inside a cozy cafe with tables and a counter, wearing a beige sweater and dark trousers, ankle boots, warm indoor light"

E1_PROMPT = ("head_swap: start with Picture 1 as the base image, keeping its lighting, environment, and background. "
             "remove the head from Picture 1 completely and replace it with the head from Picture 2, strictly preserving "
             "the hair, eye color, and nose structure of Picture 2. copy the eye direction, head rotation, and "
             "micro-expressions from Picture 1.")
E2_PROMPT = ("Edit Picture 1. Make the woman in Picture 1 the same person as in Picture 2 and Picture 3: the same face, "
             "hair and skin tone as Picture 2, and the same body shape and body proportions as Picture 3. Keep the pose, "
             "the clothes, the hands, the background, the lighting and the framing of Picture 1 exactly the same. "
             "Only one person in the photo.")


# --- ComfyUI -----------------------------------------------------------------

def call(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(COMFY + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def run(graph: dict, timeout: float = 1200) -> dict:
    pid = call("/prompt", {"prompt": graph})["prompt_id"]
    start = time.time()
    while time.time() - start < timeout:
        hist = call(f"/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed"):
            return hist
        if hist and hist.get("status", {}).get("status_str") == "error":
            raise RuntimeError(json.dumps(hist["status"].get("messages"))[-1500:])
        time.sleep(0.5)
    raise TimeoutError(pid)


def saved(entry: dict) -> str:
    """A imagem gerada (o mapa de pose tambem e salvo, com _pose no nome)."""
    for out in entry["outputs"].values():
        for img in out.get("images", []):
            if "_pose_" not in img["filename"]:
                return f"{img['filename']} [output]"
    raise RuntimeError("sem imagem")


def texts(entry: dict) -> dict[str, str]:
    return {k: str(v["text"][0]) for k, v in entry["outputs"].items() if v.get("text")}


def path_of(name: str) -> Path:
    return OUT / name.replace(" [output]", "") if name.endswith(" [output]") else INP / name


# --- grafos ------------------------------------------------------------------

DW = {"detect_hand": "enable", "detect_body": "enable", "detect_face": "enable", "resolution": 1024,
      "bbox_detector": "yolox_l.onnx", "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt", "scale_stick_for_xinsr_cn": "disable"}


def zimage(prompt: str, seed: int, prefix: str, pose_source: str | None = None) -> dict:
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": ZUNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": ZTE, "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": ZVAE}},
        "11": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": LORA, "strength_model": 1.0}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["11", 0], "shift": 3}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                                                     "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    if pose_source:
        g["20"] = {"class_type": "LoadImage", "inputs": {"image": pose_source}}
        g["21"] = {"class_type": "ImageScale", "inputs": {"image": ["20", 0], "upscale_method": "lanczos", "width": W, "height": H, "crop": "center"}}
        g["22"] = {"class_type": "DWPreprocessor", "inputs": {"image": ["21", 0], **DW}}
        g["23"] = {"class_type": "ImageScale", "inputs": {"image": ["22", 0], "upscale_method": "bilinear", "width": W, "height": H, "crop": "disabled"}}
        g["50"] = {"class_type": "ModelPatchLoader", "inputs": {"name": PATCH}}
        g["30"] = {"class_type": "ZImageFunControlnet", "inputs": {"model": ["11", 0], "model_patch": ["50", 0], "vae": ["3", 0], "image": ["23", 0], "strength": POSE_STRENGTH}}
        g["4"]["inputs"]["model"] = ["30", 0]
        g["24"] = {"class_type": "SaveImage", "inputs": {"images": ["23", 0], "filename_prefix": prefix + "_pose"}}
    return g


def qwen_base(prefix: str, seed: int, prompt: str, refs: list[str], loras: list[tuple[str, float]], base: str) -> dict:
    """Edicao no Qwen 2511: image1 = base (no tamanho W x H), image2.. = referencias (1 MP)."""
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": QUNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": QTE, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": QVAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "CFGNorm", "inputs": {"model": ["4", 0], "strength": 1}},
        "10": {"class_type": "LoadImage", "inputs": {"image": base}},
        "12": {"class_type": "ImageScale", "inputs": {"image": ["10", 0], "upscale_method": "lanczos", "width": W, "height": H, "crop": "disabled"}},
        "30": {"class_type": "VAEEncode", "inputs": {"pixels": ["12", 0], "vae": ["3", 0]}},
    }
    model = ["5", 0]
    for i, (name, strength) in enumerate(loras):
        g[f"l{i}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": name, "strength_model": strength}}
        model = [f"l{i}", 0]
    images = {"image1": ["12", 0]}
    for i, ref in enumerate(refs, start=2):
        g[f"r{i}"] = {"class_type": "LoadImage", "inputs": {"image": ref}}
        g[f"s{i}"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": [f"r{i}", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 16}}
        images[f"image{i}"] = [f"s{i}", 0]
    g["20"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], **images, "prompt": prompt}}
    g["21"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], **images, "prompt": ""}}
    g["22"] = {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["20", 0], "reference_latents_method": "index_timestep_zero"}}
    g["23"] = {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["21", 0], "reference_latents_method": "index_timestep_zero"}}
    g["31"] = {"class_type": "KSampler", "inputs": {"model": model, "positive": ["22", 0], "negative": ["23", 0], "latent_image": ["30", 0],
                                                      "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1}}
    g["32"] = {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["3", 0]}}
    g["33"] = {"class_type": "SaveImage", "inputs": {"images": ["32", 0], "filename_prefix": prefix}}
    return g


# --- plano -------------------------------------------------------------------

def plan() -> list[dict]:
    jobs = []
    for scen, text in SCENARIOS.items():
        for seed in BASE_SEEDS:
            key = f"E0_{scen}_{seed}"
            jobs.append({"key": key, "stage": "E0", "scenario": scen, "seed": seed, "provider": "comfyui", "model": ZUNET,
                         "lora": f"{LORA}@1.0", "workflow": "zimage-txt2img-lora (grafo igual)", "prompt": f"lunavox, a woman, {text}"})
    for scen in SCENARIOS:
        for seed in BASE_SEEDS:
            base = f"E0_{scen}_{seed}"
            jobs.append({"key": f"E1_{scen}_{seed}", "stage": "E1", "scenario": scen, "seed": seed, "base": base, "provider": "comfyui",
                         "model": QUNET, "lora": f"{QLIGHT}@1.0 + {BFS}@1.0", "workflow": "qwen-bfs-head-swap (imagem inteira)",
                         "prompt": E1_PROMPT, "refs": [FACE_MASTER]})
            jobs.append({"key": f"E2_{scen}_{seed}", "stage": "E2", "scenario": scen, "seed": seed, "base": base, "provider": "comfyui",
                         "model": QUNET, "lora": f"{QLIGHT}@1.0", "workflow": "qwen edit 3 imagens (base + rosto + corpo)",
                         "prompt": E2_PROMPT, "refs": [FACE_MASTER, BODY_MASTER]})
    for seed in range(300, 310):
        jobs.append({"key": f"C2A_frontal_{seed}", "stage": "C2A", "scenario": "frontal", "seed": seed, "provider": "comfyui", "model": ZUNET,
                     "lora": f"{LORA}@1.0", "workflow": f"zimage + DWPose + ControlNet Union {POSE_STRENGTH}",
                     "prompt": f"lunavox, a woman, full body photo, {POSES['frontal'][1]}, {STUDIO}", "pose_source": POSES["frontal"][0]})
    for stage, scene in (("C2B", STUDIO), ("C2C", CAFE)):
        for pose, (source, desc) in POSES.items():
            for seed in (400, 401):
                jobs.append({"key": f"{stage}_{pose}_{seed}", "stage": stage, "scenario": pose, "seed": seed, "provider": "comfyui",
                             "model": ZUNET, "lora": f"{LORA}@1.0", "workflow": f"zimage + DWPose + ControlNet Union {POSE_STRENGTH}",
                             "prompt": f"lunavox, a woman, full body photo, {desc}, {scene}", "pose_source": source})
    for pose in POSES:
        for seed in (400, 401):
            jobs.append({"key": f"CQ_{pose}_{seed}", "stage": "CQ", "scenario": pose, "seed": seed, "base": f"C2C_{pose}_{seed}",
                         "provider": "comfyui", "model": QUNET, "lora": f"{QLIGHT}@1.0", "workflow": "qwen edit 3 imagens (base + rosto + corpo)",
                         "prompt": E2_PROMPT, "refs": [FACE_MASTER, BODY_MASTER]})
    return jobs


def load() -> dict:
    return json.loads(RESULTS.read_text()) if RESULTS.exists() else {}


def save(results: dict) -> None:
    tmp = RESULTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, indent=1))
    tmp.replace(RESULTS)


def resolve(results: dict, name: str) -> str:
    """Nome no ComfyUI: fonte do input/ ou a imagem salva de outro job."""
    return results[name]["image"] if name in results else name


def generate(stages: list[str]) -> None:
    results = load()
    for job in plan() + stress_jobs():
        if job["stage"] not in stages or results.get(job["key"], {}).get("image"):
            continue
        prefix = f"b2_{job['key']}"
        if job["stage"] in ("E0", "C2A", "C2B", "C2C") or (job["stage"] == "ST" and job.get("kind") == "zimage"):
            graph = zimage(job["prompt"], job["seed"], prefix, resolve(results, job["pose_source"]) if job.get("pose_source") else None)
        else:
            base = resolve(results, job["base"])
            if job["stage"] == "E1" or job.get("bfs"):
                graph = qwen_base(prefix, job["seed"], job["prompt"], job["refs"], [(QLIGHT, 1.0), (BFS, 1.0)], base)
            else:
                graph = qwen_base(prefix, job["seed"], job["prompt"], job["refs"], [(QLIGHT, 1.0)], base)
        t0 = time.time()
        row = {k: v for k, v in job.items()}
        row.update({"width": W, "height": H, "started": t0})
        try:
            entry = run(graph)
            row["image"] = saved(entry)
            if job.get("pose_source"):
                poses = [f"{i['filename']} [output]" for o in entry["outputs"].values() for i in o.get("images", []) if "_pose" in i["filename"]]
                row["pose_map"] = poses[0] if poses else None
        except Exception as exc:
            row["error"] = str(exc)[:800]
        row["seconds"] = round(time.time() - t0, 1)
        results[job["key"]] = row
        save(results)
        print(job["key"], row["seconds"], row.get("error", "")[:200], flush=True)


# --- medidas -----------------------------------------------------------------

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


def good(kp, name):
    i = KP[name]
    if i >= len(kp):
        return None
    x, y, c = kp[i]
    return (x, y) if c and c > 0.3 else None


def torso(kp):
    neck, rh, lh = good(kp, "neck"), good(kp, "rhip"), good(kp, "lhip")
    if not (neck and rh and lh):
        return None, None
    mid = ((rh[0] + lh[0]) / 2, (rh[1] + lh[1]) / 2)
    return math.dist(neck, mid), neck


def body_ratios(kp) -> dict:
    t, _ = torso(kp)
    out = {"visible_keypoints": sum(1 for k in KP if good(kp, k)), "torso_px": round(t, 1) if t else None}
    if not t:
        return out
    for label, (a, b) in {"shoulders": ("rsho", "lsho"), "hips": ("rhip", "lhip"), "r_thigh": ("rhip", "rkne"),
                          "l_thigh": ("lhip", "lkne"), "r_shin": ("rkne", "rank"), "l_shin": ("lkne", "lank"),
                          "r_upper_arm": ("rsho", "relb"), "l_upper_arm": ("lsho", "lelb"),
                          "r_forearm": ("relb", "rwri"), "l_forearm": ("lelb", "lwri")}.items():
        pa, pb = good(kp, a), good(kp, b)
        out[label] = round(math.dist(pa, pb) / t, 3) if pa and pb else None
    return out


def bbox(kp, w, h):
    pts = [(x, y) for x, y, c in kp[:18] if c and c > 0.3]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def pose_distance(a, b) -> float | None:
    """Distancia media entre os pontos do corpo (normalizada: pescoco na origem,
    dividida pelo tronco). 0 = mesma pose."""
    ta, na = torso(a)
    tb, nb = torso(b)
    if not (ta and tb):
        return None
    ds = []
    for k in KP:
        pa, pb = good(a, k), good(b, k)
        if pa and pb:
            ds.append(math.dist(((pa[0] - na[0]) / ta, (pa[1] - na[1]) / ta), ((pb[0] - nb[0]) / tb, (pb[1] - nb[1]) / tb)))
    return round(sum(ds) / len(ds), 3) if len(ds) >= 6 else None


def measure_graph(image: str) -> dict:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "LoadImage", "inputs": {"image": FACE_MASTER}},
        "lf": {"class_type": "LunaFaces", "inputs": {"image": ["1", 0], "det_size": 1024, "reference": ["2", 0]}},
        "dw": {"class_type": "DWPreprocessor", "inputs": {"image": ["1", 0], **{**DW, "detect_face": "disable"}}},
        "dwp": {"class_type": "PreviewAny", "inputs": {"source": ["dw", 1]}},
    }


def measure_one(image: str) -> dict:
    t0 = time.time()
    t = texts(run(measure_graph(image)))
    faces = json.loads(t.get("lf", "[]"))
    people = parse_pose(t["dwp"]) if t.get("dwp") else []
    with Image.open(path_of(image)) as im:
        w, h = im.size
    bodies = []
    for kp in people:
        n = sum(1 for x in kp[:14] if x[2] > 0.3)
        if n >= 4:
            b = bbox(kp, w, h)
            bodies.append({"n": n, "bbox": [round(v, 1) for v in b], "height_frac": round((b[3] - b[1]) / h, 3)})
    main = max(people, key=lambda kp: sum(1 for x in kp[:14] if x[2] > 0.3)) if people else None
    return {
        "faces": [{k: f.get(k) for k in ("sim", "age", "sex", "score", "yaw", "bbox")} for f in faces],
        "bodies": bodies,
        "main_kp": [list(x) for x in main] if main else None,
        "body": body_ratios(main) if main else None,
        "measure_seconds": round(time.time() - t0, 1),
    }


def diff_outside(a: Path, b: Path, box) -> dict:
    """Quanto a imagem b mudou em relacao a a: geral, fora do corpo principal
    (fundo) e no tronco (roupa). Media da diferenca absoluta 0-255 em 256 px."""
    with Image.open(a) as ia, Image.open(b) as ib:
        ia, ib = ia.convert("RGB").resize((W, H)), ib.convert("RGB").resize((W, H))
        d = ImageChops.difference(ia, ib).convert("L")
        whole = ImageStat.Stat(d).mean[0]
        out = {"whole": round(whole, 2)}
        if box:
            x1, y1, x2, y2 = box
            pad = 0.1 * (x2 - x1)
            mask = Image.new("L", (W, H), 255)
            ImageDraw.Draw(mask).rectangle([x1 - pad, y1 - pad, x2 + pad, y2 + pad], fill=0)
            out["background"] = round(ImageStat.Stat(d, mask).mean[0], 2)
        return out


def measure() -> None:
    results = load()
    refs = results.setdefault("_refs", {})
    for ref in (FACE_MASTER, BODY_MASTER, POSE_04, DS23):
        if ref not in refs:
            refs[ref] = measure_one(ref)
            save(results)
    for key, row in sorted(results.items()):
        if key.startswith("_") or not row.get("image") or row.get("measure"):
            continue
        try:
            row["measure"] = measure_one(row["image"])
        except Exception as exc:
            row["measure_error"] = str(exc)[:500]
            save(results)
            continue
        m = row["measure"]
        if row.get("base") and results.get(row["base"], {}).get("measure"):
            bm = results[row["base"]]["measure"]
            main_box = (bm.get("bodies") or [{}])[0].get("bbox") if bm.get("bodies") else None
            m["vs_base"] = diff_outside(path_of(results[row["base"]]["image"]), path_of(row["image"]), main_box)
            if bm.get("main_kp") and m.get("main_kp"):
                m["vs_base"]["pose_distance"] = pose_distance(bm["main_kp"], m["main_kp"])
        if row.get("pose_map"):
            src = row["pose_source"]
            src_kp = (results.get(src, {}).get("measure") or refs.get(src) or {}).get("main_kp")
            if src_kp and m.get("main_kp"):
                m["pose_adherence"] = pose_distance(src_kp, m["main_kp"])
        save(results)
        print(key, json.dumps({k: m.get(k) for k in ("faces", "bodies", "vs_base", "pose_adherence")})[:260], flush=True)


# --- teste de estresse (definido depois do veredito) -------------------------

STRESS_PIPELINE = "zimage"  # trocado na chamada: zimage | zimage+qwen
STRESS = [
    "medium shot sitting at a wooden table in a bakery in Sao Paulo, holding a coffee cup, wearing a green blouse, morning light",
    "full body photo, standing at a bus stop on Avenida Paulista, wearing a black leather jacket and jeans, overcast day",
    "half body photo, laughing on a balcony with plants, wearing a white linen shirt, golden hour",
    "full body photo, walking on Copacabana boardwalk, wearing a yellow sundress and sandals, sunny afternoon",
    "close-up portrait, looking at the camera, soft window light, wearing a grey knit sweater",
    "full body photo, sitting on a park bench reading a book, wearing a denim jacket and a long skirt, autumn daylight",
    "half body photo, cooking in a home kitchen, stirring a pot, wearing a red apron over a white t-shirt, warm light",
    "full body photo, standing in a bookstore aisle holding an open book, wearing a long beige coat, soft light",
    "half body photo, at a supermarket picking fruit, wearing a striped t-shirt, fluorescent light",
    "full body photo, stretching on a yoga mat in a bright living room, wearing black leggings and a sports top",
]


def stress_jobs() -> list[dict]:
    mode = (BENCH / "stress_mode.txt").read_text().strip() if (BENCH / "stress_mode.txt").exists() else "zimage"
    jobs = []
    for i, scene in enumerate(STRESS):
        seed = 500 + i
        jobs.append({"key": f"ST0_{i}", "stage": "ST", "kind": "zimage", "scenario": f"stress{i}", "seed": seed, "provider": "comfyui",
                     "model": ZUNET, "lora": f"{LORA}@1.0", "workflow": "zimage-txt2img-lora", "prompt": f"lunavox, a woman, {scene}"})
        if mode == "zimage+qwen":
            jobs.append({"key": f"STQ_{i}", "stage": "ST", "kind": "qwen", "scenario": f"stress{i}", "seed": seed, "base": f"ST0_{i}",
                         "provider": "comfyui", "model": QUNET, "lora": f"{QLIGHT}@1.0", "workflow": "qwen edit 3 imagens (base + rosto + corpo)",
                         "prompt": E2_PROMPT, "refs": [FACE_MASTER, BODY_MASTER]})
        if mode == "zimage+bfs":
            # Vencedor do benchmark E: Z-Image + LoRA -> Qwen 2511 + BFS (so a cabeca, rosto MASTER).
            jobs.append({"key": f"STQ_{i}", "stage": "ST", "kind": "qwen", "bfs": True, "scenario": f"stress{i}", "seed": seed,
                         "base": f"ST0_{i}", "provider": "comfyui", "model": QUNET, "lora": f"{QLIGHT}@1.0 + {BFS}@1.0",
                         "workflow": "qwen-bfs-head-swap (imagem inteira)", "prompt": E1_PROMPT, "refs": [FACE_MASTER]})
    return jobs


# --- folhas ------------------------------------------------------------------

def sheet(prefixes: list[str]) -> None:
    results = load()
    for prefix in prefixes:
        rows = [r for k, r in sorted(results.items()) if k.startswith(prefix + "_") and r.get("image")]
        if not rows:
            continue
        tw, th, cols = 260, 380, 5
        nrows = math.ceil(len(rows) / cols)
        canvas = Image.new("RGB", (tw * cols, (th + 30) * nrows), "black")
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(rows):
            img = Image.open(path_of(row["image"])).convert("RGB").resize((tw, th))
            x, y = (i % cols) * tw, (i // cols) * (th + 30)
            canvas.paste(img, (x, y))
            m = row.get("measure") or {}
            sim = max((f.get("sim") or 0 for f in m.get("faces") or []), default=0)
            label = f"{row['scenario']} s{row['seed']} sim {sim:.2f} R{len(m.get('faces') or [])} C{len(m.get('bodies') or [])}"
            draw.text((x + 3, y + th + 3), label, fill="white")
        canvas.save(BENCH / f"sheet_{prefix}.jpg", quality=85)
        print("sheet", prefix, flush=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "gen":
        generate(sys.argv[2:])
    elif mode == "measure":
        measure()
    elif mode == "sheet":
        sheet(sys.argv[2:])
