#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comfy_pod.py - roda SO a etapa do modelo no ComfyUI do pod, em lote, ligando e
desligando o pod uma unica vez.

Regra de economia
-----------------
  1. Tudo que nao precisa de GPU roda antes, sem pod:
       python retoque_tatuagens.py preparar ...   (mascaras, recortes, pre-preenchimento)
       python comfy_pod.py ... --seco             (valida workflow, arquivos e plano)
  2. O pod liga UMA vez, recebe TODOS os recortes, processa, devolve e DESLIGA.
     Desliga mesmo se der erro, timeout ou Ctrl+C (bloco finally).
  3. Composicao e checagem rodam depois, com o pod ja desligado (--compor).
  Regioes que ja tem regiao_XX_saida.png sao puladas: se cair no meio, rodar de
  novo so processa o que faltou.

Uso
---
  python comfy_pod.py --trabalho foto1/ foto2/ foto3/ --workflow inpaint_regiao_api.json \
      --url https://SEU_POD-8188.proxy.runpod.net \
      --ligar "runpodctl start pod SEU_POD" --desligar "runpodctl stop pod SEU_POD" --compor

  Varias pastas = varias fotos na MESMA sessao do pod (liga uma vez so).

  --seco             valida tudo e mostra o plano; NAO liga o pod
  --denoise 0.65     ajusta o denoise do KSampler do workflow
  --seed 1234        semente fixa (resultado reproduzivel)
  --timeout-min 20   desliga o pod de qualquer jeito depois disso

O workflow precisa estar no formato API do ComfyUI (Export (API)) e ter:
  LoadImage      -> recebe o recorte pre-preenchido (regiao_XX_entrada.png)
  LoadImageMask  -> recebe a mascara (regiao_XX_mascara.png), canal "red"
  SaveImage      -> resultado (vira regiao_XX_saida.png)
Se houver mais de um de cada, informe os IDs com --no-imagem/--no-mascara/--no-saida.
So usa a biblioteca padrao do Python.
"""
import argparse
import copy
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid


# ----------------------------------------------------------------------------- HTTP
def _http(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(base, caminho, hdr, timeout=30):
    return json.loads(_http(base + caminho, headers=hdr, timeout=timeout))


def _post_json(base, caminho, obj, hdr):
    h = dict(hdr, **{"Content-Type": "application/json"})
    return json.loads(_http(base + caminho, data=json.dumps(obj).encode(), headers=h))


def _upload(base, arquivo, nome, hdr):
    """POST /upload/image (multipart). Devolve o nome que o LoadImage espera."""
    limite = uuid.uuid4().hex
    with open(arquivo, "rb") as f:
        dados = f.read()
    corpo = (
        f'--{limite}\r\nContent-Disposition: form-data; name="image"; filename="{nome}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + dados + b"\r\n"
    for k, v in (("overwrite", "true"), ("type", "input")):
        corpo += f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    corpo += f"--{limite}--\r\n".encode()
    h = dict(hdr, **{"Content-Type": f"multipart/form-data; boundary={limite}"})
    r = json.loads(_http(base + "/upload/image", data=corpo, headers=h, timeout=180))
    return (r["subfolder"] + "/" if r.get("subfolder") else "") + r["name"]


def _esperar_comfy(base, hdr, minutos):
    fim = time.time() + minutos * 60
    while time.time() < fim:
        try:
            _get_json(base, "/system_stats", hdr, timeout=10)
            return
        except Exception:
            time.sleep(5)
    raise TimeoutError(f"ComfyUI nao respondeu em {minutos} min")


# ----------------------------------------------------------------------------- workflow
def _achar(wf, classe, forcado, rotulo):
    if forcado:
        if forcado not in wf:
            raise SystemExit(f"no {forcado} ({rotulo}) nao existe no workflow")
        return forcado
    ids = [k for k, v in wf.items() if v.get("class_type") == classe]
    if len(ids) != 1:
        raise SystemExit(f"achei {len(ids)} nos {classe} ({rotulo}); informe o ID com --no-...")
    return ids[0]


def _carregar_workflow(caminho):
    with open(caminho) as f:
        wf = json.load(f)
    if isinstance(wf.get("prompt"), dict):
        wf = wf["prompt"]
    if "nodes" in wf or not all(isinstance(v, dict) and "class_type" in v for v in wf.values()):
        raise SystemExit("o workflow nao esta no formato API: no ComfyUI use Workflow > Export (API)")
    return wf


def _rodar(cmd, rotulo):
    print(f"[{rotulo}] {cmd}")
    r = subprocess.run(cmd, shell=True)
    if r.returncode != 0:
        print(f"[{rotulo}] retornou codigo {r.returncode}")
    return r.returncode == 0


# ----------------------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trabalho", required=True, nargs="+", help="pasta(s) criada(s) por retoque_tatuagens.py preparar")
    ap.add_argument("--workflow", required=True, help="workflow do ComfyUI em formato API (.json)")
    ap.add_argument("--url", help="URL do ComfyUI no pod, ex. https://ID-8188.proxy.runpod.net")
    ap.add_argument("--ligar", help="comando que liga o pod")
    ap.add_argument("--desligar", help="comando que desliga o pod (roda SEMPRE no final)")
    ap.add_argument("--cabecalho", action="append", default=[], help='ex. "Authorization: Bearer XYZ"')
    ap.add_argument("--no-imagem")
    ap.add_argument("--no-mascara")
    ap.add_argument("--no-saida")
    ap.add_argument("--denoise", type=float)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--espera-min", type=float, default=8, help="tempo maximo para o ComfyUI subir")
    ap.add_argument("--timeout-min", type=float, default=20, help="tempo maximo com o pod ligado")
    ap.add_argument("--seco", action="store_true", help="so valida; nao liga o pod")
    ap.add_argument("--compor", action="store_true", help="depois de desligar, compoe cada pasta em <pasta>/final.png")
    a = ap.parse_args()

    # ---------- tudo offline, antes de gastar pod ----------
    regs = []
    for t in a.trabalho:
        with open(os.path.join(t, "plano.json")) as f:
            plano = json.load(f)
        regs += [dict(r, pasta=t, chave=f"{os.path.basename(os.path.normpath(t))}_r{r['id']:02d}")
                 for r in plano["regioes"] if r["modo"] == "modelo"]

    def arq(r, tipo):
        return os.path.join(r["pasta"], f"regiao_{r['id']:02d}_{tipo}.png")

    pendentes = [r for r in regs if not os.path.exists(arq(r, "saida"))]
    for r in pendentes:
        for tipo in ("entrada", "mascara"):
            if not os.path.exists(arq(r, tipo)):
                raise SystemExit(f"falta {arq(r, tipo)}; rode retoque_tatuagens.py preparar de novo")
    wf = _carregar_workflow(a.workflow)
    n_img = _achar(wf, "LoadImage", a.no_imagem, "recorte")
    n_msk = _achar(wf, "LoadImageMask", a.no_mascara, "mascara")
    n_out = _achar(wf, "SaveImage", a.no_saida, "saida")
    samplers = [k for k, v in wf.items() if v.get("class_type") in ("KSampler", "KSamplerAdvanced")]
    hdr = dict(h.split(":", 1) for h in a.cabecalho)
    hdr = {k.strip(): v.strip() for k, v in hdr.items()}

    print(f"regioes para o modelo: {len(regs)} | pendentes: {len(pendentes)} | ja prontas: {len(regs) - len(pendentes)}")
    print(f"nos: imagem={n_img} mascara={n_msk} saida={n_out} samplers={samplers}")
    if not pendentes:
        print("nada para rodar no modelo: o pod NAO precisa ser ligado")
    elif a.seco:
        print("--seco: tudo validado; o pod nao foi ligado")
        return
    elif not a.url:
        raise SystemExit("falta --url do ComfyUI")

    # ---------- pod ligado: so o essencial ----------
    if pendentes:
        inicio = time.time()
        try:
            if a.ligar and not _rodar(a.ligar, "ligar"):
                raise RuntimeError("falha ao ligar o pod")
            _esperar_comfy(a.url.rstrip("/"), hdr, a.espera_min)
            base = a.url.rstrip("/")
            print(f"ComfyUI respondeu em {time.time() - inicio:.0f}s; enviando {len(pendentes)} regioes")
            cliente, filas = uuid.uuid4().hex, {}
            for r in pendentes:
                w = copy.deepcopy(wf)
                w[n_img]["inputs"]["image"] = _upload(base, arq(r, "entrada"), f"luna_{r['chave']}_entrada.png", hdr)
                w[n_msk]["inputs"]["image"] = _upload(base, arq(r, "mascara"), f"luna_{r['chave']}_mascara.png", hdr)
                w[n_msk]["inputs"]["channel"] = "red"
                w[n_out]["inputs"]["filename_prefix"] = f"luna_{r['chave']}"
                for k in samplers:
                    ins = w[k]["inputs"]
                    if a.denoise is not None and "denoise" in ins:
                        ins["denoise"] = a.denoise
                    if a.seed is not None:
                        ins["seed" if "seed" in ins else "noise_seed"] = a.seed
                resp = _post_json(base, "/prompt", {"prompt": w, "client_id": cliente}, hdr)
                if resp.get("node_errors"):
                    raise RuntimeError(f"{r['chave']}: erro no workflow: {json.dumps(resp['node_errors'])[:500]}")
                filas[r["chave"]] = (resp["prompt_id"], r)
            while filas:
                if time.time() - inicio > a.timeout_min * 60:
                    raise TimeoutError(f"passou de {a.timeout_min} min com o pod ligado")
                for rid, (pid, r) in list(filas.items()):
                    hist = _get_json(base, f"/history/{pid}", hdr)
                    if pid not in hist:
                        continue
                    st = hist[pid].get("status", {})
                    if st.get("status_str") == "error":
                        raise RuntimeError(f"regiao {rid}: ComfyUI devolveu erro: {json.dumps(st.get('messages'))[:500]}")
                    imgs = hist[pid].get("outputs", {}).get(n_out, {}).get("images", [])
                    if not imgs:
                        if st.get("completed"):
                            raise RuntimeError(f"regiao {rid}: terminou sem imagem no no {n_out}")
                        continue
                    q = urllib.parse.urlencode({"filename": imgs[0]["filename"],
                                                "subfolder": imgs[0].get("subfolder", ""),
                                                "type": imgs[0].get("type", "output")})
                    with open(arq(r, "saida"), "wb") as f:
                        f.write(_http(f"{base}/view?{q}", headers=hdr, timeout=180))
                    print(f"  regiao {rid} pronta")
                    del filas[rid]
                time.sleep(2)
        finally:
            if a.desligar:
                _rodar(a.desligar, "desligar")
            print(f"tempo com o pod ligado: {(time.time() - inicio) / 60:.1f} min")

    # ---------- depois, sem pod ----------
    if a.compor:
        aqui = os.path.dirname(os.path.abspath(__file__))
        falhou = False
        for t in a.trabalho:
            print(f"--- compondo {t}")
            r = subprocess.run([sys.executable, os.path.join(aqui, "retoque_tatuagens.py"), "compor",
                                "--trabalho", t, "--saida", os.path.join(t, "final.png")])
            falhou |= r.returncode != 0
        sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
