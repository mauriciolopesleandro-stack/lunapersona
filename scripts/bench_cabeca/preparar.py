"""SEM POD: prepara tudo do benchmark de troca de cabeca (PROMPT_modelo_fase_unica.md).

  - cenas (PNG), alvo escolhido pela SEMELHANCA com a modelo original (nunca a maior/do centro;
    abaixo do limiar -> revisao), outra pessoa = protegida;
  - recorte em volta do alvo (mesmo do site: 3,6x o rosto) e tamanho do modelo (1 MP, multiplo de 16);
  - cabeca da Luna recortada PELA CAIXA DO ROSTO (topo do cabelo ate pouco abaixo do queixo, calibrado
    nas referencias), mascara da cabeca no recorte (variante F);
  - matriz: variantes x cenas x sementes -> execucoes.json (grafos prontos) + referencia media da Luna.

  python preparar.py --saida C:\\Users\\mauri\\lv\\bench
"""
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import workflow_f  # noqa: E402,F401
import workflows_bench as wb  # noqa: E402
from rostos import ler, rostos  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CENAS = REPO / "generated" / "pacote_luna_v2" / "exemplos" / "pack_pexels_cozinha"
REFS = REPO / "personas" / "luna" / "references"
CENAS_BENCH = [5, 9, 14]
SEMENTES = [1234, 5678]
ALVO_CENA, ALVO_INDICE_X = 5, 400  # a modelo original: o rosto da cena 5 que contem x=400 (mulher a esquerda)
LIMIAR_ALVO = 0.35
CROP_SIDE, CROP_DROP = 3.6, 0.7  # = backend/app/services/head_swap.py
REF_SIDE, REF_TOP, REF_BOTTOM = 0.9, 0.75, 0.35  # recorte da cabeca da Luna (calibrado nas 4 referencias)
MODEL_PIXELS = 1024 * 1024


def model_size(w, h):
    s = (MODEL_PIXELS / (w * h)) ** 0.5
    return max(256, round(w * s / 16) * 16), max(256, round(h * s / 16) * 16)


def crop_box(face, W, H):
    x1, y1, x2, y2 = face
    side = max(x2 - x1, y2 - y1) * CROP_SIDE
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + (y2 - y1) * CROP_DROP
    left, top = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    right, bottom = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
    return left, top, right - left, bottom - top


def mascara_cabeca(face, shape):
    """Cabeca + cabelo + pescoco a partir da caixa do rosto (elipse da cabeca e do cabelo,
    elipse do pescoco). Usada na variante F e como semente da mascara M da composicao."""
    H, W = shape
    x1, y1, x2, y2 = face
    fw, fh = x2 - x1, y2 - y1
    m = np.zeros((H, W), np.uint8)
    cx = (x1 + x2) / 2
    cv2.ellipse(m, (int(cx), int(y1 + fh * 0.42)), (int(fw * 0.95), int(fh * 0.95)), 0, 0, 360, 255, -1)
    cv2.ellipse(m, (int(cx), int(y2 + fh * 0.25)), (int(fw * 0.42), int(fh * 0.42)), 0, 0, 360, 255, -1)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()
    out = Path(a.saida)
    (out / "entradas").mkdir(parents=True, exist_ok=True)

    # 1. a modelo original (alvo) e a referencia media da Luna (metrica)
    cena_alvo = ler(str(CENAS / f"{ALVO_CENA:02d}.jpg"))
    alvo = next(f for f in rostos(cena_alvo) if f.bbox[0] <= ALVO_INDICE_X <= f.bbox[2])
    alvo_emb = alvo.normed_embedding
    refs = sorted(REFS.glob("*.png"))
    embs = []
    for r in refs:
        fs = rostos(ler(str(r)))
        embs.append(fs[0].normed_embedding)
    luna = np.mean(embs, axis=0)
    luna /= np.linalg.norm(luna)
    np.save(out / "luna_ref_media.npy", luna)

    # 2. cabeca da Luna (Picture 2): referencia principal recortada pela caixa do rosto
    idx = json.load(open(REFS / "index.json", encoding="utf-8"))
    prim = next(r for r in idx if r.get("is_primary"))
    ref = ler(str(REFS / prim["filename"]))
    rf = rostos(ref)[0]
    x1, y1, x2, y2 = rf.bbox
    fw, fh = x2 - x1, y2 - y1
    Hr, Wr = ref.shape[:2]
    box = (int(max(0, x1 - fw * REF_SIDE)), int(max(0, y1 - fh * REF_TOP)), int(min(Wr, x2 + fw * REF_SIDE)),
           int(min(Hr, y2 + fh * REF_BOTTOM)))
    cv2.imwrite(str(out / "entradas" / "bench_luna_cabeca.png"), ref[box[1]:box[3], box[0]:box[2]])

    # 3. cenas
    cenas = {}
    for n in CENAS_BENCH:
        img = ler(str(CENAS / f"{n:02d}.jpg"))
        H, W = img.shape[:2]
        fs = rostos(img)
        sims = [float(np.dot(alvo_emb, f.normed_embedding)) for f in fs]
        i = int(np.argmax(sims))
        meta = {"cena": n, "largura": W, "altura": H, "sim_alvo": round(sims[i], 3)}
        if sims[i] < LIMIAR_ALVO:
            meta["revisao"] = "nenhum rosto parecido com a modelo original - nao chuto"
            cenas[n] = meta
            continue
        face = [float(v) for v in fs[i].bbox]
        x, y, w, h = crop_box(face, W, H)
        mw, mh = model_size(w, h)
        nome = f"bench_c{n:02d}.png"
        cv2.imwrite(str(out / "entradas" / nome), img)
        # mascara da cabeca no recorte, no tamanho do modelo (variante F)
        mfull = mascara_cabeca(face, (H, W))
        mcrop = cv2.resize(mfull[y:y + h, x:x + w], (mw, mh), interpolation=cv2.INTER_LINEAR)
        cv2.imwrite(str(out / "entradas" / f"bench_c{n:02d}_mascara.png"), mcrop)
        meta.update(imagem=nome, alvo=face, kps=fs[i].kps.round(1).tolist(),
                    outros=[[float(v) for v in f.bbox] for j, f in enumerate(fs) if j != i],
                    recorte=[x, y, w, h], modelo=[mw, mh])
        cenas[n] = meta

    # 4. matriz de execucoes (agrupada por modelo, na ordem da sessao)
    execs = []
    for v in wb.ORDER:
        for n, meta in cenas.items():
            if "revisao" in meta:
                continue
            for s in SEMENTES:
                rid = f"{v}_c{n:02d}_s{s}"
                g = wb.build(v, meta["imagem"], "bench_luna_cabeca.png", meta["modelo"][0], meta["modelo"][1], s,
                             f"bench_{rid}", tuple(meta["recorte"]), mask=f"bench_c{n:02d}_mascara.png")
                execs.append({"id": rid, "variante": v, "cena": n, "semente": s, "grafo": g,
                              "arquivos": [meta["imagem"], "bench_luna_cabeca.png", f"bench_c{n:02d}_mascara.png"]})
    plano = {"cenas": cenas, "sementes": SEMENTES, "ordem": wb.ORDER,
             "downloads": {v: wb.files_of(v) for v in wb.ORDER}, "execucoes": len(execs),
             "referencias_luna": [r.name for r in refs], "ref_principal": prim["filename"], "recorte_ref": box}
    json.dump(plano, open(out / "plano.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    json.dump(execs, open(out / "execucoes.json", "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: v for k, v in plano.items() if k != "downloads"}, indent=1, ensure_ascii=False))
    gb = sum(0 for _ in [])
    print("execucoes:", len(execs))


if __name__ == "__main__":
    main()
