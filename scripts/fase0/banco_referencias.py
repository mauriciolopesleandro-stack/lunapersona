"""FASE 0/POC (CPU): banco de referencias de cabeca a partir das fotos da Luna aprovadas pelo usuario, e escolha da
referencia por ANGULO e EXPRESSAO de cada foto de entrada.

POC de 10/10: com uma referencia sorrindo, a Luna saiu sorrindo em todas (a expressao da referencia vai junto no BFS).
Aqui cada foto recebe a referencia mais parecida em giro da cabeca (yaw) e abertura da boca (sorriso com dentes x boca
fechada), entre as fotos com olhos abertos e rosto grande; desempate pela semelhanca com a media da Luna.

  python banco_referencias.py --usuario <pasta> --media <npy> --banco 06,07,... --alvos <upload> --plano <poc_plano.json>
         --saida <pasta>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "bench_cabeca"))
from rostos import ler  # noqa: E402

_A = None


def app():
    global _A
    if _A is None:
        _A = FaceAnalysis(name="antelopev2", root=r"C:\Users\mauri\lv\insightface", providers=["CPUExecutionProvider"],
                          allowed_modules=["detection", "recognition", "landmark_2d_106"])
        _A.prepare(ctx_id=-1, det_size=(1024, 1024), det_thresh=0.35)
    return _A


def faces(img):
    p = int(max(img.shape[:2]) * 0.25)
    pad = cv2.copyMakeBorder(img, p, p, p, p, cv2.BORDER_REPLICATE)
    fs = app().get(pad)
    for f in fs:
        f.bbox = f.bbox - np.array([p, p, p, p])
        f.kps = f.kps - p
        if getattr(f, "landmark_2d_106", None) is not None:
            f.landmark_2d_106 = f.landmark_2d_106 - p
    return fs


def yaw(f):
    k = f.kps
    olhos = (k[0] + k[1]) / 2
    return float((k[2][0] - olhos[0]) / (np.linalg.norm(k[1] - k[0]) + 1e-6))


def boca(f, img=None):
    """Dentes a mostra (sorriso aberto x boca fechada): fracao de pixels claros e pouco saturados numa elipse entre os
    cantos da boca (kps 3 e 4). O detector de 106 pontos nao esta no ambiente local."""
    if img is None:
        return None
    k = f.kps
    c = (k[3] + k[4]) / 2
    w = float(np.linalg.norm(k[4] - k[3]))
    if w < 8:
        return None
    m = np.zeros(img.shape[:2], np.uint8)
    cv2.ellipse(m, (int(c[0]), int(c[1])), (int(w * 0.42), int(w * 0.28)), 0, 0, 360, 255, -1)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sel = m > 0
    if sel.sum() < 20:
        return None
    v, sat = hsv[..., 2][sel].astype(float), hsv[..., 1][sel].astype(float)
    ref = np.percentile(hsv[..., 2][sel], 95)
    dentes = (v > 0.75 * ref) & (sat < 70) & (v > 120)
    return float(dentes.mean())


def recorte_cabeca(img, f, lado_rel=2.6, desce=0.15):
    x1, y1, x2, y2 = f.bbox
    fw, fh = x2 - x1, y2 - y1
    s = max(fw, fh) * lado_rel
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + fh * desce
    X1, Y1 = int(max(0, cx - s / 2)), int(max(0, cy - s / 2))
    X2, Y2 = int(min(img.shape[1], cx + s / 2)), int(min(img.shape[0], cy + s / 2))
    return img[Y1:Y2, X1:X2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usuario", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--banco", required=True, help="prefixos das fotos do banco (olhos abertos, rosto grande)")
    ap.add_argument("--alvos", required=True)
    ap.add_argument("--plano", required=True)
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()
    out = Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)
    media = np.load(a.media)
    banco = []
    for pre in a.banco.split(","):
        p = next(Path(a.usuario).glob(f"{pre}_*.png"))
        img = ler(str(p))
        for i, f in enumerate(sorted(faces(img), key=lambda r: -float(np.dot(r.normed_embedding, media)))):
            sim = float(np.dot(f.normed_embedding, media))
            if sim < 0.6 or (f.bbox[2] - f.bbox[0]) < 120:
                continue  # outra pessoa (o homem do casal) ou rosto pequeno
            for esp in (False, True):
                c = recorte_cabeca(img, f)
                nome = f"ref_{pre}{'_' + str(i) if i else ''}{'_esp' if esp else ''}.png"
                if esp:
                    c = cv2.flip(c, 1)
                cv2.imwrite(str(out / nome), c)
                banco.append({"nome": nome, "foto": p.name, "sim": round(sim, 3), "yaw": round(-yaw(f) if esp else yaw(f), 2),
                              "boca": round(boca(f, img), 3) if boca(f, img) is not None else None, "espelhada": esp})
    plano = json.load(open(a.plano))
    escolha = {}
    for ft in plano["fotos"]:
        img = ler(str(Path(a.alvos) / ft["imagem"]))
        fs = faces(img)
        if ft.get("alvo"):
            ax = ft["alvo"]
            f = min(fs, key=lambda r: abs(r.bbox[0] - ax[0]) + abs(r.bbox[1] - ax[1]))
        else:
            f = max(fs, key=lambda r: (r.bbox[2] - r.bbox[0]) * (r.bbox[3] - r.bbox[1]))
        ty, tb = yaw(f), boca(f, img)
        cands = []
        for b in banco:
            d = abs(ty - b["yaw"]) + (4.0 * abs(tb - b["boca"]) if tb is not None and b["boca"] is not None else 0.3)
            d += 0.15 * b["espelhada"]  # preferir a foto como ela e
            cands.append((round(d - 0.8 * (b["sim"] - 0.75), 3), b["nome"]))
        cands.sort()
        escolha[ft["id"]] = {"yaw": round(ty, 2), "boca": round(tb, 3) if tb is not None else None,
                             "top": cands[:3]}
        print(ft["id"], escolha[ft["id"]], flush=True)
    json.dump({"banco": banco, "escolha": escolha}, open(out / "banco.json", "w"), indent=1)


if __name__ == "__main__":
    main()
