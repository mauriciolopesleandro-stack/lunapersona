"""FASE 0 (CPU): CORPO, parte 1 - tom de pele do corpo igual ao da Luna, sem redesenhar nada.

So a cor da pele visivel muda (deslocamento de baixa frequencia em Lab), dentro de uma mascara suave:
  pele = pessoa (rembg u2net_human_seg) E cor de pele E NAO roupa (rembg u2net_cloth_seg) E fora da cabeca nova.
A roupa cor de pele (calca bege da rua) fica de fora pelo detector de ROUPA, nao pela cor - foi a falha da V3.
Alvo do tom: a pele do rosto novo da Luna (bochechas), ja na luz da cena.

  python tom_corpo.py --final <M4.png> --original <foto> --meta <poc_saida.json> --chave <id__ref> --saida <pasta>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "retoque"))
sys.path.insert(0, str(AQUI.parent / "bench_cabeca"))
from compor_cabeca import _disco  # noqa: E402
from retoque_tatuagens import _bgr, _lab  # noqa: E402
from rostos import ler  # noqa: E402

_S = {}


def sessao(nome):
    from rembg import new_session
    if nome not in _S:
        _S[nome] = new_session(nome)
    return _S[nome]


def mascara(img, nome):
    from rembg import remove
    m = np.asarray(remove(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), session=sessao(nome), only_mask=True))
    if m.ndim == 3:
        m = m[..., 0]
    h = img.shape[0]
    if m.shape[0] == 3 * h:  # u2net_cloth_seg: cima / baixo / inteira empilhadas
        m = np.maximum.reduce([m[:h], m[h:2 * h], m[2 * h:]])
    if m.shape[:2] != img.shape[:2]:
        m = cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    return m > 127


def pele_cor(img):
    ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    cr, cb = ycc[..., 1].astype(int), ycc[..., 2].astype(int)
    return (cr > 133) & (cr < 180) & (cb > 77) & (cb < 135) & (ycc[..., 0] > 40)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--chave", required=True)
    ap.add_argument("--saida", required=True)
    ap.add_argument("--forca", type=float, default=0.8)
    a = ap.parse_args()
    out = Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)
    fin, orig = ler(a.final), ler(a.original)
    r = json.load(open(a.meta))[a.chave]
    x1, y1, x2, y2 = r["alvo"]
    fw, fh = x2 - x1, y2 - y1
    h, w = fin.shape[:2]
    pessoa = mascara(orig, "u2net_human_seg")
    roupa = mascara(orig, "u2net_cloth_seg")
    cabeca = np.zeros((h, w), np.uint8)
    cv2.ellipse(cabeca, (int((x1 + x2) / 2), int(y1 + fh * 0.45)), (int(fw * 1.0), int(fh * 1.05)), 0, 0, 360, 255, -1)
    pele = pessoa & pele_cor(orig) & ~(cv2.dilate(roupa.astype(np.uint8), _disco(3)) > 0) & ~(cabeca > 0)
    pele = cv2.morphologyEx(pele.astype(np.uint8), cv2.MORPH_OPEN, _disco(2)) > 0
    # tom alvo: bochechas do rosto novo
    boch = np.zeros((h, w), bool)
    boch[int(y1 + fh * 0.45):int(y1 + fh * 0.7), int(x1 + fw * 0.15):int(x2 - fw * 0.15)] = True
    boch &= pele_cor(fin)
    lf = _lab(fin)
    if boch.sum() < 30 or pele.sum() < 200:
        print(json.dumps({"chave": a.chave, "erro": "pele ou bochecha insuficiente"}))
        return
    alvo = np.median(lf[boch], axis=0)
    corpo = np.median(lf[pele], axis=0)
    delta = (alvo - corpo) * np.array([0.6, 1.0, 1.0]) * a.forca  # luz (L) so em parte: a sombra do corpo e real
    soft = cv2.GaussianBlur(pele.astype(np.float32), (0, 0), max(2.0, fw * 0.02)) * pele
    soft = np.clip(cv2.GaussianBlur(soft, (0, 0), 1.5), 0, 1)
    novo = _bgr(lf + delta[None, None, :] * soft[..., None])
    res = {"chave": a.chave, "pele_px": int(pele.sum()), "tom_rosto_Lab": [round(float(v), 1) for v in alvo],
           "tom_corpo_Lab": [round(float(v), 1) for v in corpo], "delta": [round(float(v), 1) for v in delta]}
    cv2.imwrite(str(out / f"{a.chave}__tom.png"), novo)
    dbg = orig.copy()
    dbg[pele] = (0.5 * dbg[pele] + np.array([0, 0, 255]) * 0.5).astype(np.uint8)
    dbg[roupa] = (0.5 * dbg[roupa] + np.array([255, 0, 0]) * 0.5).astype(np.uint8)
    cv2.imwrite(str(out / f"{a.chave}__mascara_pele.jpg"), dbg)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
