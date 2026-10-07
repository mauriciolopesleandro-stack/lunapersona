#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mock_comfy.py - ComfyUI FALSO para testar o fluxo inteiro SEM ligar o pod.

Responde nas mesmas rotas da API real (/system_stats, /upload/image, /prompt,
/history, /view) e, no lugar do modelo, devolve o recorte com defeitos de proposito:
mais claro/alaranjado, liso e com deriva leve. Se o fluxo estiver certo, o
compor corrige isso e a checagem APROVA.

  python mock_comfy.py --gerar-workflow wf_teste_api.json   # workflow minimo de teste
  python mock_comfy.py                                       # sobe em http://127.0.0.1:8188
  python comfy_pod.py --trabalho foto1/ --workflow wf_teste_api.json \
         --url http://127.0.0.1:8188 --compor                # sem --ligar/--desligar
"""
import argparse, email, email.policy, json, time, urllib.parse, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import cv2
import numpy as np

WF_TESTE = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}},
    "2": {"class_type": "LoadImageMask", "inputs": {"image": "m.png", "channel": "red"}},
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "denoise": 0.65, "steps": 25}},
    "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "ComfyUI"}},
}


def modelo_imperfeito(img, mascara):
    lab = cv2.cvtColor(img.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
    lab = cv2.GaussianBlur(lab, (0, 0), 2.0)                     # pele "de plastico"
    lab[..., 0] += 1.5; lab[..., 2] += 1.0                       # deriva global
    m = cv2.GaussianBlur((mascara > 127).astype(np.float32), (0, 0), 4)
    lab[..., 0] += 6 * m; lab[..., 1] += 2 * m; lab[..., 2] += 4 * m   # mancha clara/alaranjada
    return np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR) * 255 + 0.5, 0, 255).astype(np.uint8)


UP, OUT, HIST = {}, {}, {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/system_stats":
            return self._json({"system": {"os": "mock"}})
        if p.path.startswith("/history/"):
            pid = p.path.split("/")[-1]; h = HIST.get(pid)
            return self._json({pid: h["dados"]} if h and time.time() >= h["pronto"] else {})
        if p.path == "/view":
            b = OUT[urllib.parse.parse_qs(p.query)["filename"][0]]
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
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
            wf = json.loads(corpo)["prompt"]
            for k, v in wf.items():
                if v["class_type"] == "KSampler" and v["inputs"].get("denoise", 0) > 1:
                    return self._json({"error": "invalid", "node_errors": {k: "denoise > 1"}})
            img = [v for v in wf.values() if v["class_type"] == "LoadImage"][0]["inputs"]["image"]
            msk = [v for v in wf.values() if v["class_type"] == "LoadImageMask"][0]["inputs"]["image"]
            sid, sv = [(k, v) for k, v in wf.items() if v["class_type"] == "SaveImage"][0]
            im = cv2.imdecode(np.frombuffer(UP[img], np.uint8), cv2.IMREAD_COLOR)
            mk = cv2.imdecode(np.frombuffer(UP[msk], np.uint8), cv2.IMREAD_GRAYSCALE)
            nome = sv["inputs"]["filename_prefix"] + "_00001_.png"
            OUT[nome] = cv2.imencode(".png", modelo_imperfeito(im, mk))[1].tobytes()
            pid = uuid.uuid4().hex
            HIST[pid] = {"pronto": time.time() + 1.0, "dados": {
                "outputs": {sid: {"images": [{"filename": nome, "subfolder": "", "type": "output"}]}},
                "status": {"status_str": "success", "completed": True}}}
            return self._json({"prompt_id": pid, "number": len(HIST), "node_errors": {}})
        self._json({"erro": "rota"}, 404)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8188)
    ap.add_argument("--gerar-workflow")
    a = ap.parse_args()
    if a.gerar_workflow:
        with open(a.gerar_workflow, "w") as f:
            json.dump(WF_TESTE, f, indent=1)
        print("workflow de teste salvo em", a.gerar_workflow)
    else:
        print(f"ComfyUI falso em http://127.0.0.1:{a.porta}")
        ThreadingHTTPServer(("127.0.0.1", a.porta), H).serve_forever()
