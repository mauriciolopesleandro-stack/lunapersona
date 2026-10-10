"""PROVA DE CONCEITO (roda NO POD, com o codigo de producao em /workspace/lunapersona): troca de cabeca Qwen 2511 + BFS
(workflow qwen-bfs-head-swap, o mesmo de producao) com referencias diferentes. Salva a saida CRUA do modelo (recorte) e
os metadados para a composicao M4 ser feita depois, na CPU (scripts/fase0/compor_metodos.py).

  python poc_cabeca_pod.py plano.json saida.json [teto_minutos]

plano.json: {"fotos": [{"id", "imagem" (nome no input/), "alvo"?: [x1,y1,x2,y2]}], "refs": {"A": "nome.png", ...},
             "rodadas": [["A","B"], ["C"]], "parciais": {"C": ["id", ...]}}
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/workspace/lunapersona/backend")
from PIL import Image  # noqa: E402

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.services.head_swap import WORKFLOW, crop_box, faces_in, model_size  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

INPUT = Path("/workspace/runpod-slim/ComfyUI/input")
SEED = 1234


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


async def main():
    plano = json.load(open(sys.argv[1]))
    saida = Path(sys.argv[2])
    teto = float(sys.argv[3]) * 60 if len(sys.argv) > 3 else 1e9
    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=90.0, generation_timeout=900.0)
    wm = WorkflowManager(Path("/workspace/lunapersona/workflows"))
    t0 = time.monotonic()
    res = json.loads(saida.read_text()) if saida.exists() else {}
    metas = {}
    for f in plano["fotos"]:
        W, H = Image.open(INPUT / f["imagem"]).size
        faces = await faces_in(client, f["imagem"])
        boxes = [tuple(x["bbox"]) for x in (faces or [])]
        if f.get("alvo"):
            alvo = tuple(f["alvo"])
        else:
            mulheres = [x for x in (faces or []) if str(x.get("sex", "")).upper().startswith("F")] or (faces or [])
            alvo = tuple(max(mulheres, key=lambda x: area(x["bbox"]))["bbox"])
        outros = [b for b in boxes if abs(b[0] - alvo[0]) + abs(b[1] - alvo[1]) > 20]
        x, y, w, h = crop_box(alvo, W, H)
        metas[f["id"]] = {"imagem": f["imagem"], "largura": W, "altura": H, "alvo": list(alvo), "outros": [list(b) for b in outros],
                          "recorte": [x, y, w, h], "modelo": list(model_size(w, h))}
        print("META", f["id"], json.dumps(metas[f["id"]]), flush=True)
    for rodada in plano["rodadas"]:
        for ref in rodada:
            ids = plano.get("parciais", {}).get(ref)
            for f in plano["fotos"]:
                if ids and f["id"] not in ids:
                    continue
                key = f"{f['id']}__{ref}"
                if key in res and res[key].get("crua"):
                    continue
                if time.monotonic() - t0 > teto:
                    print("TETO de tempo: parando antes de", key, flush=True)
                    saida.write_text(json.dumps(res, indent=1))
                    return
                m = metas[f["id"]]
                x, y, w, h = m["recorte"]
                mw, mh = m["modelo"]
                g = wm.render(WORKFLOW, {"BODY_IMAGE": f["imagem"], "HEAD_IMAGE": plano["refs"][ref], "WIDTH": mw, "HEIGHT": mh,
                                         "CROP_X": x, "CROP_Y": y, "CROP_W": w, "CROP_H": h, "FEATHER": int(min(w, h) * 0.08),
                                         "SEED": SEED, "FILENAME_PREFIX": f"poc_comp_{key}"})
                g["90"] = {"class_type": "SaveImage", "inputs": {"images": ["32", 0], "filename_prefix": f"poc_crua_{key}"}}
                t = time.monotonic()
                entry = await client.wait_for_completion(await client.queue_prompt(g))
                imgs = client.extract_images(entry)
                crua = next((i.filename for i in imgs if i.filename.startswith("poc_crua_")), None)
                comp = next((i.filename for i in imgs if i.filename.startswith("poc_comp_")), None)
                res[key] = {**m, "ref": ref, "crua": crua, "comp": comp, "segundos": round(time.monotonic() - t, 1)}
                saida.write_text(json.dumps(res, indent=1))
                print("OK", key, res[key]["segundos"], crua, flush=True)
    print("FIM", round(time.monotonic() - t0), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
