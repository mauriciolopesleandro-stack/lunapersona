"""Painel minimo do pod do benchmark (porta 8188, pelo proxy da RunPod): a sessao local le os
marcadores e os logs e pede o desligamento. So a biblioteca padrao.

  GET  /status         -> {"prontos": [...], "falhas": {...}, "comfy": bool, "minutos": x, "disco_livre_gb": y}
  GET  /log/<nome>     -> /root/bench/<nome> (recursos.csv, sessao.log, comfy.log, quedas.log)
  POST /fim            -> grava /root/bench/fim (o sessao_pod.sh desliga o pod)
"""
import json
import os
import shutil
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

B = "/root/bench"
T0 = time.time()


def comfy_ok():
    try:
        urllib.request.urlopen("http://127.0.0.1:8189/system_stats", timeout=3).read()
        return True
    except Exception:
        return False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            prontos = sorted(f[7:] for f in os.listdir(B) if f.startswith("pronto_"))
            falhas = {f[7:]: open(os.path.join(B, f)).read().strip() for f in os.listdir(B) if f.startswith("falhou_")}
            st = {"prontos": prontos, "falhas": falhas, "comfy": comfy_ok(),
                  "minutos": round((time.time() - T0) / 60, 1),
                  "disco_livre_gb": round(shutil.disk_usage("/root").free / 1e9, 1)}
            return self._send(200, json.dumps(st).encode())
        if self.path.startswith("/log/"):
            p = os.path.join(B, os.path.basename(self.path[5:]))
            if os.path.exists(p):
                return self._send(200, open(p, "rb").read(), "text/plain")
        self._send(404, b"{}")

    def do_POST(self):
        if self.path == "/fim":
            open(os.path.join(B, "fim"), "w").write(str(time.time()))
            return self._send(200, b'{"ok": true}')
        self._send(404, b"{}")


if __name__ == "__main__":
    os.makedirs(B, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8188), H).serve_forever()
