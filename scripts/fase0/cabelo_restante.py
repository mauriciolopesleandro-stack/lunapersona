"""FASE 0 (CPU): cabelo COMPRIDO da pessoa original que fica fora do recorte da troca de cabeca (foto da lingerie, 10/10:
fios loiros ate o quadril dos dois lados). Repinta esses fios com a cor do cabelo novo da Luna, mantendo a textura.

  remanescente = cor do cabelo original (amostra acima da testa, Mahalanobis em Lab) E pessoa E textura de fio
                 E fora da cabeca nova E ligado a ela (componentes que encostam na regiao trocada)
  repintura    = L mapeado (media/desvio do cabelo original -> do cabelo novo), a/b do cabelo novo; borda suave

  python cabelo_restante.py --original <img> --resultado <img> --saida <pasta>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "bench_cabeca"))
sys.path.insert(0, str(AQUI.parent.parent / "backend"))
from app.core.head.compose import bgr, disco, lab, mascara_cabeca  # noqa: E402
from rostos import ler, rostos  # noqa: E402


def pessoa(img):
    from rembg import new_session, remove
    m = np.asarray(remove(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), session=new_session("u2net_human_seg"), only_mask=True))
    return m > 127


def modelo_cor(lo, amostra):
    v = lo[amostra].reshape(-1, 3)
    mu, cov = v.mean(0), np.cov(v.T) + np.eye(3) * 4.0
    return mu, np.linalg.inv(cov), v


def mahal(lo, mu, inv):
    d = lo.reshape(-1, 3) - mu
    return np.sqrt(np.einsum("ij,jk,ik->i", d, inv, d)).reshape(lo.shape[:2])


def cabelo_restante(original, resultado, face, pes):
    h, w = original.shape[:2]
    x1, y1, x2, y2 = face
    fw, fh = x2 - x1, y2 - y1
    lo, lr = lab(original), lab(resultado)
    # o que a troca mudou perto da cabeca (cabeca nova)
    dE = np.sqrt(((cv2.GaussianBlur(lr, (0, 0), 2) - cv2.GaussianBlur(lo, (0, 0), 2)) ** 2).sum(2))
    cab = mascara_cabeca(face, (h, w))
    mudou = (dE > 10) & (cv2.dilate(cab.astype(np.uint8), disco(fw * 1.2)) > 0)
    n, lb, _, _ = cv2.connectedComponentsWithStats(mudou.astype(np.uint8), 8)
    perto = cv2.dilate(cab.astype(np.uint8), disco(fw * 0.15)) > 0
    keep = np.zeros(n, bool)
    for i in range(1, n):
        keep[i] = (lb == i)[perto].any()
    nova = keep[lb] | cab
    # cor do cabelo ORIGINAL (acima da testa, na foto original)
    amo = np.zeros((h, w), bool)
    amo[int(max(0, y1 - fh * 0.30)):int(max(1, y1 + fh * 0.02)), int(x1 + fw * 0.15):int(x2 - fw * 0.15)] = True
    amo &= pes
    mu_o, inv_o, v_o = modelo_cor(lo, amo)
    # cor do cabelo NOVO (dentro da cabeca nova, acima da testa, no resultado)
    amo_n = np.zeros((h, w), bool)
    amo_n[int(max(0, y1 - fh * 0.30)):int(max(1, y1 + fh * 0.05)), int(x1 - fw * 0.3):int(x2 + fw * 0.3)] = True
    amo_n &= nova & (lr[..., 0] < np.percentile(lr[..., 0][nova], 60))
    v_n = lr[amo_n].reshape(-1, 3)
    # remanescente no RESULTADO: cor do cabelo antigo, pessoa, textura de fio, fora da cabeca nova
    md = mahal(lr, mu_o, inv_o)
    L = lr[..., 0]
    tex = np.abs(L - cv2.GaussianBlur(L, (0, 0), 1.5))
    tex = cv2.GaussianBlur(tex, (0, 0), 3) > 1.2  # fio tem textura; pele lisa nao
    cand = (md < 3.5) & pes & ~(cv2.dilate(nova.astype(np.uint8), disco(2)) > 0) & tex
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_OPEN, disco(1))
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, disco(max(2, fw * 0.04))) > 0
    n, lb, st, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), 8)
    encosta = cv2.dilate(nova.astype(np.uint8), disco(fw * 0.25)) > 0
    keep = np.zeros(n, bool)
    for i in range(1, n):
        keep[i] = (lb == i)[encosta].any() and st[i, cv2.CC_STAT_AREA] > fw * 2
    rem = keep[lb]
    info = {"remanescente_px": int(rem.sum()), "cabelo_original_Lab": [round(float(t), 1) for t in v_o.mean(0)],
            "cabelo_novo_Lab": [round(float(t), 1) for t in v_n.mean(0)] if len(v_n) else None}
    if rem.sum() == 0 or len(v_n) < 30:
        return resultado.copy(), rem, info
    # repintura: L por media/desvio, a/b do cabelo novo (mantem a textura relativa dos fios)
    src = lr[rem]
    out = lr.copy()
    mu_s, sd_s = src.mean(0), src.std(0) + 1e-3
    mu_t, sd_t = v_n.mean(0), v_n.std(0) + 1e-3
    novo = np.empty_like(src)
    novo[:, 0] = (src[:, 0] - mu_s[0]) / sd_s[0] * min(sd_t[0], sd_s[0] * 1.2) + mu_t[0]
    novo[:, 1:] = (src[:, 1:] - mu_s[1:]) * 0.5 + mu_t[1:]
    out[rem] = novo
    a = cv2.GaussianBlur(rem.astype(np.float32), (0, 0), 1.2)
    a = np.clip(np.maximum(a, rem * 1.0), 0, 1)
    fin = bgr(out * a[..., None] + lr * (1 - a[..., None]))
    return fin, rem, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--resultado", required=True)
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()
    out = Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)
    o, r = ler(a.original), ler(a.resultado)
    f = max(rostos(o), key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
    fin, rem, info = cabelo_restante(o, r, [float(v) for v in f.bbox], pessoa(o))
    cv2.imwrite(str(out / "cabelo_repintado.png"), fin)
    dbg = r.copy()
    dbg[rem] = (0.4 * dbg[rem] + np.array([255, 0, 255]) * 0.6).astype(np.uint8)
    cv2.imwrite(str(out / "cabelo_mascara.jpg"), dbg)
    print(json.dumps(info))


if __name__ == "__main__":
    main()
