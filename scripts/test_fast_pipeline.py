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

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
COMFY = "http://127.0.0.1:8188"
ROOT = Path("/workspace/runpod-slim/ComfyUI")
OUT, INP = ROOT / "output", ROOT / "input"
W, H = 864, 1536
RAFA_HEAD = "luna_studio_00627_.png [output]"  # retrato do Rafa do teste de casal
# Modo "bfs": fotos ja feitas com Chroma + LoRA (ensaio solo v2 e ensaio de
# casal, este com o rosto do Rafa pelo InstantID).
EXISTING = [("solo", n) for n in (618, 620, 622, 624, 626)] + [("couple", n) for n in (632, 635, 638, 641, 644)]

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


# Modo "comida": encontro no calcadao - objetos na mao e na boca (o que mais
# falhava no pack antigo).
BOARDWALK = ("on the Copacabana boardwalk at sunset, black and white wavy sidewalk, beach and kiosks behind, "
             "candid smartphone photo, natural skin texture, realistic")
FOOD_SCENES = [
    ("couple", f"photo of two people: {WOMAN}, wearing a white summer dress, and {RAFA}, wearing a light blue linen "
               f"shirt. He feeds her a bite of a hot dog, holding it in his right hand close to her mouth, she laughs "
               f"while taking a small bite, {BOARDWALK}"),
    ("solo", f"medium shot of {WOMAN}, wearing a white summer dress, biting a churro covered in sugar and cinnamon, "
             f"holding it in her right hand, sugar on her lips, laughing and looking at the camera, {BOARDWALK}"),
    ("couple", f"photo of two people: {WOMAN}, wearing a white summer dress, and {RAFA}, wearing a light blue linen "
               f"shirt. He holds out a chocolate ice cream cone and she takes a bite of the ice cream from his hand, "
               f"both laughing, {BOARDWALK}"),
    ("solo", f"close-up of {WOMAN}, wearing a white summer dress, licking a strawberry ice cream cone she holds in "
             f"her left hand, playful smile, {BOARDWALK}"),
    ("couple", f"photo of two people sitting at a beach kiosk table: {WOMAN}, wearing a white summer dress, and "
               f"{RAFA}, wearing a light blue linen shirt. He feeds her a spoonful of acai from a bowl, she smiles "
               f"with the spoon at her lips, {BOARDWALK}"),
]


CLOSE_SCENES = [
    "close-up photo of lunavox, a 25-year-old Brazilian woman, taking a bite of a hot dog with mustard at a beach "
    "kiosk, laughing, a little mustard on the corner of her mouth, wearing a white summer dress, Copacabana boardwalk "
    "softly blurred behind, candid smartphone photo, natural skin texture, realistic",
    "close-up photo of lunavox, a 25-year-old Brazilian woman, holding a hot dog with both hands and smiling at the "
    "camera after taking a bite, cheeks full, playful, beach kiosk at sunset behind, candid smartphone photo, natural "
    "skin texture, realistic",
    "close-up photo of lunavox, a 25-year-old Brazilian woman, biting a churro covered in sugar, sugar on her lips, "
    "laughing, Copacabana boardwalk softly blurred behind, candid smartphone photo, natural skin texture, realistic",
]


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
    workflow = "klein-bfs-head-swap" if "klein" in sys.argv else "qwen-bfs-head-swap"
    """Troca a cabeca. crop=True: so o recorte em volta do rosto vai para o
    modelo (com duas pessoas ele nao sabe qual trocar) e volta colado."""
    src = Image.open(image)
    x, y, w, h, feather = 0, 0, src.width, src.height, 0
    if crop and face:  # mesma conta de backend/app/services/head_swap.py
        x1, y1, x2, y2 = face["bbox"]
        side = max(x2 - x1, y2 - y1) * 3.6
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + (y2 - y1) * 0.7
        x, y = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        w, h = int(min(src.width, cx + side / 2)) - x, int(min(src.height, cy + side / 2)) - y
        feather = int(min(w, h) * 0.08)
    mw, mh = size16(w, h, 1.0)
    graph = wm.render(workflow, {
        "BODY_IMAGE": f"{image.name} [output]", "HEAD_IMAGE": head, "WIDTH": mw, "HEIGHT": mh,
        "CROP_X": x, "CROP_Y": y, "CROP_W": w, "CROP_H": h, "FEATHER": feather,
        "SEED": seed, "FILENAME_PREFIX": "fast_klein" if "klein" in sys.argv else "fast_swap"})
    return OUT / saved(run(graph))


def main() -> None:
    started = time.time()
    wm = WorkflowManager()
    (INP / "fast_luna_ref.png").write_bytes(luna_reference())
    luna = "fast_luna_ref.png"
    log: list[str] = []

    def note(message: str) -> None:
        log.append(message)
        print(message, flush=True)

    raw: list[Path] = []
    if "ckpt" in sys.argv:
        # Compara etapas do treino (cada LoRA passada depois de "ckpt"): mesmas
        # 3 cenas e sementes, semelhanca com a Luna. Treino demais copia as
        # fotos de treino e endurece a imagem - fica a etapa mais equilibrada.
        loras = [a for a in sys.argv[sys.argv.index("ckpt") + 1:] if a.endswith(".safetensors")]
        prompts = [
            "lunavox, a 25-year-old Brazilian woman, close-up portrait looking at the camera, soft daylight, "
            "natural skin texture, candid smartphone photo",
            "lunavox, a 25-year-old Brazilian woman, full body photo walking on a street in Sao Paulo wearing jeans "
            "and a white t-shirt, candid smartphone photo, realistic",
            "lunavox, a 25-year-old Brazilian woman, medium shot laughing at a bar table at night holding a glass of "
            "caipirinha, candid smartphone photo, realistic",
        ]
        rows = []
        for lora in loras:
            # Todas as fotos da etapa em sequencia (sem outro grafo no meio, a
            # ComfyUI nao recarrega o modelo): mede o tempo real por foto.
            row, times = [], []
            for i, prompt in enumerate(prompts):
                t = time.time()
                graph = wm.render("zimage-txt2img-lora", {
                    "PROMPT": prompt, "WIDTH": W, "HEIGHT": H, "SEED": 7000 + i, "LORA_NAME": lora,
                    "FILENAME_PREFIX": "fast_ckpt"})
                row.append(OUT / saved(run(graph)))
                times.append(round(time.time() - t, 1))
            rows.append(row)
            sims = [(lambda f: f and f["sim"])(pick(faces(f"{img.name} [output]", luna), "F")) for img in row]
            valid = [s for s in sims if s is not None]
            note(f"{lora}: tempos={times} Luna={sims} media={sum(valid) / len(valid) if valid else 0:.3f}")
        hgt = 520
        ims_rows = [[Image.open(p).convert("RGB") for p in r] for r in rows]
        ims_rows = [[im.resize((int(im.width * hgt / im.height), hgt)) for im in r] for r in ims_rows]
        sheet = Image.new("RGB", (sum(i.width for i in ims_rows[0]) + 12, (hgt + 6) * len(ims_rows)), (20, 20, 20))
        for r, ims in enumerate(ims_rows):
            x = 0
            for im in ims:
                sheet.paste(im, (x, r * (hgt + 6)))
                x += im.width + 6
        sheet.save("/workspace/fast_ckpt.jpg", quality=85)
        note(f"total {time.time() - started:.0f}s -> /workspace/fast_ckpt.jpg")
        return
    if "lora" in sys.argv:
        # LoRA da Luna no Z-Image (scripts/train_zimage_lora.sh): o rosto dela
        # sai direto na cena, sem troca. Mesmas cenas do calcadao, lado a lado
        # com as do Z-Image puro (fast_scene_00001..5) quando existirem.
        out: list[Path] = []
        for i, (_kind, prompt) in enumerate(FOOD_SCENES):
            t = time.time()
            graph = wm.render("zimage-txt2img-lora", {
                "PROMPT": prompt.replace(WOMAN, "lunavox, a 25-year-old Brazilian woman"), "WIDTH": W, "HEIGHT": H,
                "SEED": 1000 + i, "LORA_NAME": "luna_zimage_v1.safetensors", "FILENAME_PREFIX": "fast_lora"})
            out.append(OUT / saved(run(graph)))
            her = pick(faces(f"{out[-1].name} [output]", luna), "F")
            note(f"cena {i + 1} com LoRA: {time.time() - t:.1f}s  Luna={her and her['sim']}")
        before = [OUT / f"fast_scene_{n:05d}_.png" for n in range(1, len(out) + 1)]
        rows = [before, out] if all(p.exists() for p in before) else [out]
        hgt = 560
        ims_rows = [[Image.open(p).convert("RGB") for p in r] for r in rows]
        ims_rows = [[im.resize((int(im.width * hgt / im.height), hgt)) for im in r] for r in ims_rows]
        sheet = Image.new("RGB", (sum(i.width for i in ims_rows[0]) + 6 * len(out), (hgt + 6) * len(ims_rows)),
                          (20, 20, 20))
        for r, ims in enumerate(ims_rows):
            x = 0
            for im in ims:
                sheet.paste(im, (x, r * (hgt + 6)))
                x += im.width + 6
        sheet.save("/workspace/fast_lora.jpg", quality=85)
        # Closes pedidos pelo usuario (comendo no calcadao, natural).
        close = []
        for i, prompt in enumerate(CLOSE_SCENES):
            t = time.time()
            graph = wm.render("zimage-txt2img-lora", {
                "PROMPT": prompt, "WIDTH": W, "HEIGHT": H, "SEED": 5000 + i,
                "LORA_NAME": "luna_zimage_v1.safetensors", "FILENAME_PREFIX": "fast_lora_close"})
            close.append(OUT / saved(run(graph)))
            her = pick(faces(f"{close[-1].name} [output]", luna), "F")
            note(f"close {i + 1} com LoRA: {time.time() - t:.1f}s  Luna={her and her['sim']}")
        ims = [Image.open(p).convert("RGB") for p in close]
        ims = [im.resize((int(im.width * 700 / im.height), 700)) for im in ims]
        sheet = Image.new("RGB", (sum(i.width for i in ims) + 6 * len(ims), 700), (20, 20, 20))
        x = 0
        for im in ims:
            sheet.paste(im, (x, 0))
            x += im.width + 6
        sheet.save("/workspace/fast_lora_close.jpg", quality=86)
        note(f"total {time.time() - started:.0f}s -> /workspace/fast_lora.jpg, /workspace/fast_lora_close.jpg")
        return
    if "bfs" in sys.argv:
        # So a troca de cabeca, nas fotos ja feitas com o Chroma + LoRA (o
        # casal com o rosto do Rafa pelo InstantID): compara com o caminho atual.
        kinds = [kind for kind, _n in EXISTING]
        raw = [OUT / f"luna_studio_{n:05d}_.png" for _kind, n in EXISTING]
    else:
        scenes = FOOD_SCENES if "comida" in sys.argv else SCENES
        kinds = [kind for kind, _prompt in scenes]
        for i, (_kind, prompt) in enumerate(scenes):
            t = time.time()
            graph = wm.render("zimage-txt2img", {"PROMPT": prompt, "WIDTH": W, "HEIGHT": H, "SEED": 1000 + i,
                                                  "FILENAME_PREFIX": "fast_scene"})
            raw.append(OUT / saved(run(graph)))
            note(f"cena {i + 1}: Z-Image {time.time() - t:.1f}s")

    final = []
    for i, (kind, image) in enumerate(zip(kinds, raw)):
        t = time.time()
        found = faces(f"{image.name} [output]", luna)
        her, him = pick(found, "F"), pick(found, "M")
        before_him = pick(faces(f"{image.name} [output]", RAFA_HEAD), "M") if kind == "couple" else None
        out = head_swap(wm, image, her, luna, crop=kind == "couple", seed=2000 + i)
        if kind == "couple" and him:
            him_now = pick(faces(f"{out.name} [output]", RAFA_HEAD), "M")
            out = head_swap(wm, out, him_now, RAFA_HEAD, crop=True, seed=3000 + i)
        final.append(out)
        sim_her = pick(faces(f"{out.name} [output]", luna), "F")
        sim_him = pick(faces(f"{out.name} [output]", RAFA_HEAD), "M") if kind == "couple" else None
        note(f"foto {i + 1} ({image.name}): troca {time.time() - t:.1f}s"
                   f"  Luna antes={her and her['sim']} depois={sim_her and sim_her['sim']}"
                   f"  Rafa antes={before_him and before_him['sim']} depois={sim_him and sim_him['sim']}")

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
    name = ("bfs" if "bfs" in sys.argv else "comida" if "comida" in sys.argv else "zimage") + ("_klein" if "klein" in sys.argv else "_qwen")
    sheet.save(f"/workspace/fast_{name}.jpg", quality=85)
    note(f"total {time.time() - started:.0f}s -> /workspace/fast_{name}.jpg")


if __name__ == "__main__":
    main()
