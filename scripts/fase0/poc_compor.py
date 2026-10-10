"""PROVA DE CONCEITO, parte local (CPU): compoe as cabecas cruas do pod com o metodo M4 (mascara organica + cabelo
original + mistura Laplaciana) e mede a semelhanca com a Luna das fotos do USUARIO (luna_usuario_media.npy).

  python poc_compor.py --pasta <saida do pod> --entradas <upload> --media <luna_usuario_media.npy> --saida <pasta>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent / "bench_cabeca"))
from compor_metodos import corrigida_e_mascara, m2h_laplaciano_cabelo, medidas  # noqa: E402
from rostos import ler, rostos  # noqa: E402


def sim_luna(img, media, alvo):
    fs = rostos(img)
    perto = [f for f in fs if f.bbox[0] < alvo[2] and f.bbox[2] > alvo[0] and f.bbox[1] < alvo[3] and f.bbox[3] > alvo[1]]
    fs = perto or fs
    return round(max(float(np.dot(f.normed_embedding, media)) for f in fs), 3) if fs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", required=True)
    ap.add_argument("--entradas", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()
    pasta, ent, out = Path(a.pasta), Path(a.entradas), Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)
    media = np.load(a.media)
    res = json.load(open(pasta / "poc_saida.json"))
    tabela = {}
    for key, r in res.items():
        if not r.get("crua"):
            continue
        original, crua = ler(str(ent / r["imagem"])), ler(str(pasta / r["crua"]))
        meta = {k: r[k] for k in ("recorte", "alvo", "outros", "modelo")}
        meta["alvo"] = [float(v) for v in meta["alvo"]]
        x = corrigida_e_mascara(original, crua, meta)
        linha = {"ref": r["ref"], "segundos": r["segundos"]}
        if x is None:
            linha["erro"] = "alinhamento"
            tabela[key] = linha
            continue
        f1, a1, M_t, corr, alinhada, box, zona = x
        xx, yy, w, h = box
        M = M_t[yy:yy + h, xx:xx + w]
        f4, a4, info, cab = m2h_laplaciano_cabelo(original, crua, meta, box, M, zona, alinhada)
        zona_t = np.zeros(original.shape[:2], bool)
        zona_t[yy:yy + h, xx:xx + w] = zona
        med = medidas(original, f4, a4, zona_t, lambda im: [], media, meta["alvo"])
        linha.update(fora_cabeca_px=med["fora_cabeca_px"], emenda=med["emenda"], sim_luna=sim_luna(f4, media, meta["alvo"]),
                     sim_original=sim_luna(original, media, meta["alvo"]))
        cv2.imwrite(str(out / f"{key}__M4.png"), f4)
        tabela[key] = linha
        print(key, json.dumps(linha), flush=True)
    json.dump(tabela, open(out / "poc_medidas.json", "w"), indent=1)


if __name__ == "__main__":
    main()
