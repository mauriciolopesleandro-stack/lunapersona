"""Teste do caminho rapido (roda no pod, com o Python do ComfyUI - tem o PIL):

  /workspace/runpod-slim/ComfyUI/.venv-cu128/bin/python /workspace/lunapersona/scripts/test_fast_pipeline.py

1. Z-Image Turbo gera as cenas (8 passos) - todas primeiro, para nao trocar de
   modelo na GPU a cada foto (Z-Image ~18 GB e Qwen-Image-Edit ~30 GB nao cabem
   juntos nos 24 GB: cada troca recarrega do volume).
2. Qwen-Image-Edit 2511 + BFS head V5 troca a cabeca pela da Luna (e a do outro
   personagem pelo retrato dele). Com duas pessoas, so o recorte em volta de quem
   troca vai para o modelo e volta colado com borda suave.
3. Mede o tempo de cada etapa e a semelhanca (ArcFace, LunaFaces) com as
   referencias; monta /workspace/fast_test.jpg (linha de cima: cena crua;
   de baixo: com a troca).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[1]
COMFY = "http://127.0.0.1:8188"
ROOT = Path("/workspace/runpod-slim/ComfyUI")
OUT, INP = ROOT / "output", ROOT / "input"
W, H = 864, 1536
RAFA_HEAD = "luna_studio_00627_.png [output]"  # retrato do Rafa do teste de casal
BASELINE = [618, 620, 622, 624, 626]  # ensaio solo v2 feito com Chroma + LoRA

WOMAN = "a 25-year-old Brazilian woman with long dark brown wavy hair, tan skin, slim curvy body"
RAFA = "Rafa, a 28-year-old man with short black hair, short beard and light brown skin"
STYLE = ("candid smartphone photo, natural skin texture with pores, natural light, realistic, "
         "boutique hotel room in Copacabana with floor-to-ceiling windows and ocean view at sunset")
SCENES = [
    ("solo", f"{WOMAN} sitting on the edge of a bed, adjusting her hair with her right hand, looking at the camera, "
             f"wearing black lace lingerie with a wine-colored silk robe over it, {STYLE}"),
    ("solo", f"full body shot of {WOMAN} standing by the window with golden sunset light on her face, wearing black "
             f"lace lingerie with a wine-colored silk robe over it, {STYLE}"),
    ("solo", f"{WOMAN} sitting in a velvet armchair holding a glass of champagne in her right hand, smiling at the "
             f"camera, wearing black lace lingerie with a wine-colored silk robe over it, {STYLE}"),
    ("solo", f"full body shot of {WOMAN} on a hotel balcony leaning on a wooden railing, wearing a white bikini, "
             f"ocean behind her, golden hour, candid smartphone photo, natural skin texture, realistic"),
    ("couple", f"photo of two people: {WOMAN}, wearing black lace lingerie with a wine-colored silk robe, and {RAFA}, "
               f"wearing an open white linen shirt and beige linen trousers. He hugs her from behind by the window, "
               f"his hands resting on her waist, both smiling, {STYLE}"),
    ("couple", f"photo of two people: {WOMAN}, wearing black lace lingerie with a wine-colored silk robe, and {RAFA}, "
               f"wearing an open white linen shirt and beige linen trousers. They sit on the edge of the bed, "
               f"foreheads touching, holding hands, {STYLE}"),
    ("couple", f"photo of two people on a hotel balcony at sunset: {WOMAN}, wearing a white bikini, and {RAFA}, "
               f"wearing a white linen shirt and linen shorts. They clink two glasses of champagne, looking at each "
               f"other and laughing, ocean behind, candid smartphone photo, natural skin texture, realistic"),
]


class WorkflowManager:
    """O mesmo render do backend (placeholders "${NOME}" no valor inteiro)."""

    def render(self, workflow_id: str, params: dict) -> dict:
        data = json.loads((REPO / "workflows" / f"{workflow_id}.json").read_text(encoding="utf-8"))
        merged = {**data["meta"].get("optional_params", {}), **params}

        def sub(node):
            if isinstance(node, dict):
                return {k: sub(v) for k, v in node.items()}
            if isinstance(node, list):
                return [sub(v) for v in node]
            if isinstance(node, str) and node.startswith("${") and node.endswith("}"):
                return merged[node[2:-1]]
            return node

        return sub(data["graph"])


def luna_reference() -> bytes:
    refs = REPO / "personas" / "luna" / "references"
    entries = json.loads((refs / "index.json").read_text(encoding="utf-8"))
    primary = next((e for e in entries if e.get("is_primary")), entries[0])
    return (refs / primary["filename"]).read_bytes()


def run(graph: dict) -> dict:
    req = urllib.request.Request(f"{COMFY}/prompt", json.dumps({"prompt": graph}).encode(),
                                 {"Content-Type": "application/json"})
    pid = json.load(urllib.request.urlopen(req))["prompt_id"]
    while True:
        time.sleep(0.5)
        hist = json.load(urllib.request.urlopen(f"{COMFY}/history/{pid}"))
        if pid in hist:
            entry = hist[pid]
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(entry["status"])[:600])
            return entry


def saved(entry: dict) -> str:
    for out in entry["outputs"].values():
        for img in out.get("images", []):
            if img.get("type") == "output":
                return img["filename"]
    raise RuntimeError("sem imagem de saida")


def faces(image: str, ref: str) -> list[dict]:
    g = {"1": {"class_type": "LoadImage", "inputs": {"image": image}},
         "2": {"class_type": "LoadImage", "inputs": {"image": ref}},
         "lf": {"class_type": "LunaFaces", "inputs": {"image": ["1", 0], "reference": ["2", 0], "det_size": 1024}}}
    return json.loads(run(g)["outputs"]["lf"]["text"][0])


def pick(found: list[dict], sex: str) -> dict | None:
    same = [f for f in found if f.get("sex") == sex]
    area = lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1])  # noqa: E731
    return max(same, key=area) if same else None


def size16(w: float, h: float, mp: float = 1.0) -> tuple[int, int]:
    s = (mp * 1e6 / (w * h)) ** 0.5
    return max(16, int(w * s) // 16 * 16), max(16, int(h * s) // 16 * 16)


def head_swap(wm: WorkflowManager, image: Path, face: dict | None, head: str, crop: bool, seed: int) -> Path:
    """Troca a cabeca. crop=True: so o recorte em volta do rosto vai para o
    modelo (com duas pessoas ele nao sabe qual trocar) e volta colado."""
    src = Image.open(image).convert("RGB")
    box = (0, 0, src.width, src.height)
    if crop and face:
        x1, y1, x2, y2 = face["bbox"]
        side = max(x2 - x1, y2 - y1) * 3.6
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + (y2 - y1) * 0.7
        box = (int(max(0, cx - side / 2)), int(max(0, cy - side / 2)),
               int(min(src.width, cx + side / 2)), int(min(src.height, cy + side / 2)))
    part = src.crop(box)
    name = f"fast_{image.stem}_{seed}.png"
    part.save(INP / name)
    w, h = size16(part.width, part.height, 1.0 if crop else 1.33)
    graph = wm.render("qwen-bfs-head-swap", {"BODY_IMAGE": name, "HEAD_IMAGE": head, "WIDTH": w, "HEIGHT": h,
                                              "SEED": seed, "FILENAME_PREFIX": "fast_swap"})
    swapped = Image.open(OUT / saved(run(graph))).convert("RGB").resize(part.size, Image.LANCZOS)
    if not crop:
        out = swapped
    else:
        mask = Image.new("L", part.size, 0)
        pad = int(min(part.size) * 0.08)
        ImageDraw.Draw(mask).rectangle((pad, pad, part.width - pad, part.height - pad), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(pad / 2))
        out = src.copy()
        out.paste(swapped, box[:2], mask)
    path = OUT / f"fast_final_{image.stem}_{seed}.png"
    out.save(path)
    return path


def main() -> None:
    wm = WorkflowManager()
    (INP / "fast_luna_ref.png").write_bytes(luna_reference())
    luna = "fast_luna_ref.png"
    log: list[str] = []

    base = []
    for n in BASELINE:
        f = pick(faces(f"luna_studio_{n:05d}_.png [output]", luna), "F")
        base.append(f["sim"] if f else None)
    log.append(f"Chroma+LoRA (ensaio v2) semelhanca com a Luna: {base}")

    raw = []
    for i, (_kind, prompt) in enumerate(SCENES):
        t = time.time()
        graph = wm.render("zimage-txt2img", {"PROMPT": prompt, "WIDTH": W, "HEIGHT": H, "SEED": 1000 + i,
                                              "FILENAME_PREFIX": "fast_scene"})
        raw.append(OUT / saved(run(graph)))
        log.append(f"cena {i + 1}: Z-Image {time.time() - t:.1f}s")

    final = []
    for i, ((kind, _prompt), image) in enumerate(zip(SCENES, raw)):
        t = time.time()
        found = faces(f"{image.name} [output]", luna)
        her, him = pick(found, "F"), pick(found, "M")
        out = head_swap(wm, image, her, luna, crop=kind == "couple", seed=2000 + i)
        if kind == "couple" and him:
            him_now = pick(faces(f"{out.name} [output]", RAFA_HEAD), "M")
            out = head_swap(wm, out, him_now, RAFA_HEAD, crop=True, seed=3000 + i)
        final.append(out)
        sim_her = pick(faces(f"{out.name} [output]", luna), "F")
        sim_him = pick(faces(f"{out.name} [output]", RAFA_HEAD), "M") if kind == "couple" else None
        log.append(f"cena {i + 1}: troca {time.time() - t:.1f}s  Luna={sim_her and sim_her['sim']}"
                   f"  Rafa={sim_him and sim_him['sim']}  rostos={[(f['sex'], f['sim']) for f in found]}")

    hgt = 560
    rows = []
    for imgs in (raw, final):
        ims = [Image.open(p).convert("RGB") for p in imgs]
        ims = [im.resize((int(im.width * hgt / im.height), hgt)) for im in ims]
        rows.append(ims)
    sheet = Image.new("RGB", (sum(i.width for i in rows[0]) + 6 * len(raw), hgt * 2 + 6), (20, 20, 20))
    for r, ims in enumerate(rows):
        x = 0
        for im in ims:
            sheet.paste(im, (x, r * (hgt + 6)))
            x += im.width + 6
    sheet.save("/workspace/fast_test.jpg", quality=85)
    print("\n".join(log))


if __name__ == "__main__":
    main()
