"""--seco dos workflows: manda o grafo de cada variante a um ComfyUI REAL (v0.37, local na CPU,
com arquivos de modelo vazios de mesmo nome) e confere se ele aceita (nos, entradas, nomes de
arquivo). Nada e gerado de verdade: a fila e limpa logo depois.

  python validar_workflows.py --url http://127.0.0.1:8190
"""
import argparse
import io
import json
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import workflow_f  # noqa: E402,F401  (registra a variante F)
import workflows_bench as wb  # noqa: E402


def post(url, path, obj=None, data=None, headers=None):
    body = json.dumps(obj).encode() if obj is not None else data
    h = {"Content-Type": "application/json"} if obj is not None else (headers or {})
    req = urllib.request.Request(url + path, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}")


def upload_png(url, name):
    import numpy as np
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.zeros((64, 64, 3), np.uint8)).save(buf, "PNG")
    lim = uuid.uuid4().hex
    corpo = (f'--{lim}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
             f"Content-Type: image/png\r\n\r\n").encode() + buf.getvalue() + b"\r\n"
    corpo += f'--{lim}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n--{lim}--\r\n'.encode()
    return post(url, "/upload/image", data=corpo, headers={"Content-Type": f"multipart/form-data; boundary={lim}"})["name"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8190")
    a = ap.parse_args()
    body, head, mask = (upload_png(a.url, n) for n in ("val_body.png", "val_head.png", "val_mask.png"))
    ok = True
    for v in wb.ORDER:
        g = wb.build(v, body, head, 1024, 1024, 1234, f"val_{v}", (0, 0, 64, 64), mask=mask)
        r = post(a.url, "/prompt", {"prompt": g, "client_id": "validar"})
        erros = r.get("node_errors") or ({} if "prompt_id" in r else r.get("error"))
        print(v, "OK" if not erros else f"ERRO {json.dumps(erros)[:600]}")
        ok &= not erros
    post(a.url, "/queue", {"clear": True})
    post(a.url, "/interrupt", {})
    print("TODOS OK" if ok else "HA ERROS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
