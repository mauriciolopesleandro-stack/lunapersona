"""Copia (do input/output do ComfyUI) as imagens citadas no resultado da sessao V2 para uma pasta so,
para baixar e olhar. Nao apaga nada do ComfyUI (a retencao das engines cuida disso).

  python coletar_v2.py resultado.json saida_v2
"""
import json
import shutil
import sys
from pathlib import Path

COMFY = Path("/workspace/runpod-slim/ComfyUI")


def path_of(locator: str) -> Path:
    name, folder = (locator[: -len(" [output]")], "output") if locator.endswith(" [output]") else (locator, "input")
    return COMFY / folder / name


def main() -> None:
    res = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(exist_ok=True)
    n = 0
    for r in res.get("resultados", []):
        rid = r["id"]
        items = {"final": r.get("image")} if r.get("image") else {}
        items.update({k: v for k, v in (r.get("intermediates") or {}).items()})
        for tag, loc in items.items():
            src = path_of(loc)
            if src.is_file():
                shutil.copy(src, out / f"{rid}__{tag}.png")
                n += 1
    print(f"coletadas {n} imagens em {out}")


if __name__ == "__main__":
    main()
