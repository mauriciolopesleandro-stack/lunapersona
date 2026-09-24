"""Teste A/B da auditoria de prompt da persona.

O que a auditoria achou: a identidade fixa da persona (personas/<id>/persona.json)
esta escrita em portugues e ainda carrega anotacoes de quem a preencheu
("nas referencias", "na maioria das referencias", "aparente nas fotos de corpo
inteiro"). O backend cola esse texto, do jeito que esta, na frente do prompt da
cena - que e em ingles, porque o Chroma1-HD (encoder T5) entende ingles bem
melhor. O teste mede se isso atrapalha a identidade.

  A = caminho de producao: POST /api/generate com persona_id, o proprio backend
      monta "<identidade em PT>, <cena>".
  B = mesma cena e mesma seed, com a identidade reescrita em ingles e sem as
      anotacoes (IDENTITY_EN abaixo), enviada sem persona_id.

Cada par usa a mesma seed, entao a unica diferenca e o texto da identidade.
As chamadas passam pelo backend (e nao direto no ComfyUI) para contarem como
atividade no auto-desligamento do pod.

Uso (de qualquer maquina com acesso ao pod, ou de dentro dele):

    python scripts/ab_prompt_test.py                 # usa RUNPOD_POD_ID do .env
    python scripts/ab_prompt_test.py --api http://127.0.0.1:8000/api   # dentro do pod
    python scripts/ab_prompt_test.py --dry-run       # so mostra os prompts

Saida: outputs/ab-prompt/<data-hora>/ com as imagens, results.json e
index.html (A e B lado a lado).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.persona_manager.manager import PersonaManager  # noqa: E402

PERSONA_ID = "luna"

# Mesmos campos e mesma ordem do persona.json, traduzidos e sem anotacoes.
IDENTITY_EN = ", ".join(
    [
        "oval face, defined cheekbones, slightly tapered chin",
        "symmetrical striking features, direct confident expression",
        "dark brown almond-shaped eyes",
        "thick straight well-defined dark brown eyebrows",
        "straight slim nose with a slightly rounded tip",
        "full lips with a natural nude-pink tone",
        "long loose wavy hair below the chest, parted in the middle",
        "very dark brown almost black hair at the roots with caramel-copper balayage toward the ends",
        "voluminous wavy hair with natural shine",
        "even golden olive tanned skin",
        "slim toned body, medium-tall",
    ]
)

# Cenas no formato que o assistente de chat produz (uma frase, em ingles).
SCENES = [
    "Realistic photography of a woman sitting by a cafe window in the morning, natural light, 85mm, shallow depth of field.",
    "Realistic photography of a woman walking on a beach at sunset, wearing a white linen dress, golden hour light, 50mm.",
    "Realistic close-up portrait of a woman in a photo studio, soft key light, neutral grey background, 85mm, sharp focus on the eyes.",
]

DEFAULT_SEEDS = [1234, 98765]


def read_env_value(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def default_api_base() -> str:
    pod_id = read_env_value("RUNPOD_POD_ID")
    if not pod_id:
        sys.exit("Sem --api e sem RUNPOD_POD_ID no .env: nao sei onde esta o backend.")
    return f"https://{pod_id}-8000.proxy.runpod.net/api"


def post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} em {url}: {detail}") from exc


def download(url: str, dest: Path, timeout: float) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        dest.write_bytes(resp.read())


def build_prompts(persona_manager: PersonaManager, scene: str) -> tuple[str, str]:
    # A: reproduz exatamente o que GenerationService faz com persona_id.
    fragment_pt = persona_manager.get_persona(PERSONA_ID).identity_prompt_fragment()
    prompt_a = f"{fragment_pt}, {scene}" if fragment_pt else scene
    prompt_b = f"{IDENTITY_EN}, {scene}"
    return prompt_a, prompt_b


def write_report(out_dir: Path, rows: list[dict]) -> None:
    cards = []
    for r in rows:
        def cell(side: str) -> str:
            v = r[side]
            img = (
                f'<img src="{html.escape(v["file"])}" alt="">'
                if v.get("file")
                else f'<div class="err">{html.escape(v.get("error", "sem imagem"))}</div>'
            )
            secs = f' · {v["seconds"]:.0f}s' if v.get("seconds") else ""
            return (
                f'<figure>{img}<figcaption><b>{side}</b>{secs}'
                f'<details><summary>prompt</summary><p>{html.escape(v["prompt"])}</p></details>'
                f"</figcaption></figure>"
            )

        cards.append(
            f'<section><h2>Cena {r["scene_index"] + 1} · seed {r["seed"]}</h2>'
            f'<p class="scene">{html.escape(r["scene"])}</p>'
            f'<div class="pair">{cell("A")}{cell("B")}</div></section>'
        )

    page = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Teste A/B de prompt</title>
<style>
  :root {{ --bg:#0d0c16; --panel:#171522; --text:#f1f0f8; --muted:#9a97b0; --line:rgba(255,255,255,.1); }}
  body {{ margin:0; padding:20px 16px 40px; background:var(--bg); color:var(--text); font:14px/1.5 system-ui,sans-serif; }}
  main {{ max-width:1100px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 6px; }}
  .lead {{ color:var(--muted); margin:0 0 20px; }}
  section {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px; margin-bottom:16px; }}
  h2 {{ font-size:15px; margin:0 0 4px; }}
  .scene {{ color:var(--muted); margin:0 0 10px; }}
  .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  figure {{ margin:0; }}
  img {{ width:100%; border-radius:10px; display:block; }}
  figcaption {{ margin-top:6px; }}
  details p {{ color:var(--muted); font-size:12px; margin:4px 0 0; }}
  .err {{ aspect-ratio:1; display:grid; place-items:center; border:1px dashed var(--line); border-radius:10px; color:#ff8a98; padding:12px; text-align:center; }}
  @media (max-width:640px) {{ .pair {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
<h1>Teste A/B · identidade da {PERSONA_ID}</h1>
<p class="lead"><b>A</b> = producao (identidade em portugues, montada pelo backend).
<b>B</b> = identidade em ingles, sem anotacoes. Mesma cena e mesma seed em cada par.</p>
{''.join(cards)}
</main></body></html>"""
    (out_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", help="Base da API do backend (ex: https://<pod>-8000.proxy.runpod.net/api)")
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS)
    parser.add_argument("--scenes", type=int, default=len(SCENES), help="Quantas cenas da lista usar")
    parser.add_argument("--timeout", type=float, default=420.0, help="Segundos por geracao")
    parser.add_argument("--dry-run", action="store_true", help="So imprime os prompts, nao gera nada")
    args = parser.parse_args()

    persona_manager = PersonaManager(REPO_ROOT / "personas")
    scenes = SCENES[: max(1, args.scenes)]

    if args.dry_run:
        for i, scene in enumerate(scenes):
            prompt_a, prompt_b = build_prompts(persona_manager, scene)
            print(f"\n=== Cena {i + 1} ===\nA ({len(prompt_a)} caracteres):\n{prompt_a}\n\nB ({len(prompt_b)} caracteres):\n{prompt_b}")
        return

    api = (args.api or default_api_base()).rstrip("/")
    out_dir = REPO_ROOT / "outputs" / "ab-prompt" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(scenes) * len(args.seeds) * 2
    print(f"Backend: {api}\nSaida: {out_dir}\n{total} imagens (pode levar ~1 min cada na L4)\n")

    rows: list[dict] = []
    done = 0
    for si, scene in enumerate(scenes):
        prompt_a, prompt_b = build_prompts(persona_manager, scene)
        for seed in args.seeds:
            row = {"scene_index": si, "scene": scene, "seed": seed}
            variants = {
                "A": {"prompt": scene, "persona_id": PERSONA_ID},
                "B": {"prompt": prompt_b, "persona_id": None},
            }
            for side, body in variants.items():
                done += 1
                entry = {"prompt": prompt_a if side == "A" else prompt_b}
                print(f"[{done}/{total}] cena {si + 1} seed {seed} {side}...", end=" ", flush=True)
                start = time.monotonic()
                try:
                    result = post_json(f"{api}/generate", {**body, "seed": seed}, args.timeout)
                    images = result.get("images") or []
                    if not images:
                        raise RuntimeError("backend nao devolveu imagem")
                    name = f"cena{si + 1}_seed{seed}_{side}.png"
                    download(images[0]["url"], out_dir / name, args.timeout)
                    entry.update(file=name, seconds=time.monotonic() - start, prompt_id=result.get("prompt_id"))
                    print(f"ok ({entry['seconds']:.0f}s)")
                except Exception as exc:  # um erro nao derruba o resto do teste
                    entry["error"] = str(exc)
                    print(f"ERRO: {exc}")
                row[side] = entry
            rows.append(row)
            (out_dir / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
            write_report(out_dir, rows)

    print(f"\nPronto. Abra {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
