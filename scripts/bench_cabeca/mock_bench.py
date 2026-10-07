"""ComfyUI FALSO para ensaiar a sessao do benchmark SEM pod. Executa o grafo de verdade so nas partes
de imagem (LoadImage -> ImageCrop -> ImageScale) e, no lugar do modelo, cola a cabeca da Luna sobre o
rosto do recorte com defeitos de proposito: deslocamento de 3 px, deriva de exposicao/branco no
recorte INTEIRO (como o Qwen faz). Se o compor_cabeca estiver certo, alinha, corrige e aprova.

  python mock_bench.py --porta 8191 --entradas C:\\Users\\mauri\\lv\\bench\\entradas
"""
import argparse
import email
import email.policy
import json
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

UP, OUT, HIST = {}, {}, {}
ENTRADAS = None


def _img(nome):
    return cv2.imdecode(np.frombuffer(UP[nome], np.uint8), cv2.IMREAD_COLOR)


def executar(g):
    body = _img(g["10"]["inputs"]["image"])
    head = _img(g["11"]["inputs"]["image"])
    c = g["14"]["inputs"]
    crop = body[c["y"]:c["y"] + c["height"], c["x"]:c["x"] + c["width"]]
    s = g["12"]["inputs"]
    W, H = s["width"], s["height"]
    out = cv2.resize(crop, (W, H), interpolation=cv2.INTER_LANCZOS4)
    # rosto do alvo no recorte: a mascara da cena (mesmo numero), no tamanho do modelo
    cena = g["10"]["inputs"]["image"].replace(".png", "")
    mk = cv2.imread(str(Path(ENTRADAS) / f"{cena}_mascara.png"), 0)
    mk = cv2.resize(mk, (W, H)) > 127
    ys, xs = np.nonzero(mk)
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), int(ys.min() + (ys.max() - ys.min()) * 0.72)
    hh = cv2.resize(head, (x2 - x1, y2 - y1))
    oval = np.zeros((y2 - y1, x2 - x1), np.float32)
    cv2.ellipse(oval, ((x2 - x1) // 2, (y2 - y1) // 2), ((x2 - x1) // 2 - 2, (y2 - y1) // 2 - 2), 0, 0, 360, 1, -1)
    oval = cv2.GaussianBlur(oval, (0, 0), 6)[..., None]
    reg = out[y1:y2, x1:x2].astype(np.float32)
    out[y1:y2, x1:x2] = (hh * oval + reg * (1 - oval)).astype(np.uint8)
    # deriva global (exposicao/branco) + deslocamento de 3 px
    out = np.clip(out.astype(np.float32) * np.array([0.95, 1.0, 1.06]) + 6, 0, 255).astype(np.uint8)
    out = cv2.warpAffine(out, np.float32([[1, 0, 3], [0, 1, -2]]), (W, H), borderMode=cv2.BORDER_REPLICATE)
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/system_stats":
            return self._json({"system": {"os": "mock", "comfyui_version": "0.37.0"},
                               "devices": [{"vram_total": 34e9, "vram_free": 20e9}]})
        if p.path.startswith("/history/"):
            pid = p.path.split("/")[-1]
            h = HIST.get(pid)
            return self._json({pid: h["dados"]} if h and time.time() >= h["pronto"] else {})
        if p.path == "/view":
            b = OUT[urllib.parse.parse_qs(p.query)["filename"][0]]
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        self._json({"erro": "rota"}, 404)

    def do_POST(self):
        corpo = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/upload/image":
            msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + corpo)
            for parte in msg.iter_parts():
                if parte.get_filename():
                    UP[parte.get_filename()] = parte.get_payload(decode=True)
                    return self._json({"name": parte.get_filename(), "subfolder": "", "type": "input"})
        if self.path == "/prompt":
            g = json.loads(corpo)["prompt"]
            sid = next(k for k, v in g.items() if v["class_type"] == "SaveImage")
            nome = g[sid]["inputs"]["filename_prefix"] + "_00001_.png"
            OUT[nome] = cv2.imencode(".png", executar(g))[1].tobytes()
            pid = uuid.uuid4().hex
            t0 = int(time.time() * 1000)
            HIST[pid] = {"pronto": time.time() + 0.3, "dados": {
                "outputs": {sid: {"images": [{"filename": nome, "subfolder": "", "type": "output"}]}},
                "status": {"status_str": "success", "completed": True,
                           "messages": [["execution_start", {"timestamp": t0}], ["execution_success", {"timestamp": t0 + 1500}]]}}}
            return self._json({"prompt_id": pid, "number": len(HIST), "node_errors": {}})
        self._json({"erro": "rota"}, 404)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8191)
    ap.add_argument("--entradas", required=True)
    a = ap.parse_args()
    ENTRADAS = a.entradas
    print(f"ComfyUI falso do benchmark em http://127.0.0.1:{a.porta}")
    ThreadingHTTPServer(("127.0.0.1", a.porta), H).serve_forever()
