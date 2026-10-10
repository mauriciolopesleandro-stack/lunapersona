"""FASE 0 (CPU): "isso parece a Luna?" com medida. A Luna de referencia e o conjunto de fotos que o USUARIO aprovou
(generated/v2/fase0/luna_usuario). Mede com ArcFace (antelopev2, o mesmo da validacao):
  1. coerencia interna do conjunto do usuario (cada foto x media das outras);
  2. as 4 referencias que o sistema usa hoje (personas/luna/references) x media do usuario;
  3. os resultados das trocas ja feitas (chave da folha de notas) x media do usuario;
  4. ranking das fotos do usuario como REFERENCIA DE CABECA (frontal, olhos abertos, rosto grande, perto da media).

  python identidade_luna.py --usuario <pasta> --refs <pasta> --chave <chave.json> --saida <pasta>
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
from rostos import ler, rostos  # noqa: E402


def maior_rosto(img):
    fs = rostos(img)
    if not fs:
        return None
    return max(fs, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def yaw_aprox(f):
    """Giro da cabeca pelos 5 pontos: nariz em relacao ao meio dos olhos (0 = frontal)."""
    k = f.kps
    olhos = (k[0] + k[1]) / 2
    dist = np.linalg.norm(k[1] - k[0]) + 1e-6
    return float((k[2][0] - olhos[0]) / dist)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usuario", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--chave", required=True)
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()
    out = Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)

    # 1a passada: so fotos com UM rosto formam a media provisoria; 2a: em foto de casal fica o rosto mais parecido
    imgs = {p.name: ler(str(p)) for p in sorted(Path(a.usuario).glob("*.png"))}
    todos = {k: rostos(v) for k, v in imgs.items()}
    prov = np.stack([fs[0].normed_embedding for fs in todos.values() if len(fs) == 1]).mean(0)
    prov /= np.linalg.norm(prov)
    usu = {}
    for nome, img in imgs.items():
        fs = todos[nome]
        f = max(fs, key=lambda r: float(np.dot(r.normed_embedding, prov))) if fs else None
        p = Path(nome)
        if f is None:
            usu[p.name] = None
            continue
        x1, y1, x2, y2 = f.bbox
        usu[p.name] = {"emb": f.normed_embedding, "det": float(f.det_score), "lado": float(max(x2 - x1, y2 - y1)),
                       "frac": float((x2 - x1) * (y2 - y1) / (img.shape[0] * img.shape[1])), "yaw": yaw_aprox(f)}
    ok = {k: v for k, v in usu.items() if v is not None}
    E = np.stack([v["emb"] for v in ok.values()])
    media = E.mean(0)
    media /= np.linalg.norm(media)
    rel = {"usuario": {}, "sem_rosto": [k for k, v in usu.items() if v is None]}
    for i, (k, v) in enumerate(ok.items()):
        outros = np.delete(E, i, 0).mean(0)
        outros /= np.linalg.norm(outros)
        rel["usuario"][k] = {"x_media_dos_outros": round(float(np.dot(v["emb"], outros)), 3), "det": round(v["det"], 2),
                             "rosto_px": round(v["lado"]), "yaw": round(v["yaw"], 2)}
    sims = [r["x_media_dos_outros"] for r in rel["usuario"].values()]
    rel["coerencia_usuario"] = {"media": round(float(np.mean(sims)), 3), "min": round(float(np.min(sims)), 3),
                                "max": round(float(np.max(sims)), 3), "n": len(sims)}

    rel["referencias_atuais"] = {}
    for p in sorted(Path(a.refs).glob("*.png")):
        fs = rostos(ler(str(p)))
        f = max(fs, key=lambda r: float(np.dot(r.normed_embedding, media))) if fs else None
        rel["referencias_atuais"][p.name] = round(float(np.dot(f.normed_embedding, media)), 3) if f is not None else None

    chave = json.load(open(a.chave, encoding="utf-8"))
    rel["resultados"] = {}
    for n, c in chave.items():
        fs = rostos(ler(c["resultado"]))  # a Luna trocada = o rosto mais parecido com ela (cenas com 2 pessoas)
        f = max(fs, key=lambda r: float(np.dot(r.normed_embedding, media))) if fs else None
        rel["resultados"][c["id"]] = {"pipeline": c["pipeline"],
                                      "x_luna_usuario": round(float(np.dot(f.normed_embedding, media)), 3) if f is not None else None}

    # ranking como referencia de cabeca: perto da media, frontal, rosto grande, deteccao firme
    cand = []
    for k, r in rel["usuario"].items():
        nota = r["x_media_dos_outros"] - 0.15 * min(1.0, abs(r["yaw"])) + 0.05 * min(1.0, r["rosto_px"] / 400) + 0.05 * (r["det"] - 0.7)
        cand.append((round(nota, 3), k))
    rel["ranking_referencia_cabeca"] = sorted(cand, reverse=True)
    np.save(out / "luna_usuario_media.npy", media)
    json.dump(rel, open(out / "identidade_luna.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({k: rel[k] for k in ("coerencia_usuario", "sem_rosto", "referencias_atuais")}, ensure_ascii=False))
    for k, v in sorted(rel["resultados"].items(), key=lambda kv: -(kv[1]["x_luna_usuario"] or 0)):
        print(f"{v['x_luna_usuario']}\t{k}\t{v['pipeline']}")
    print("ranking:", rel["ranking_referencia_cabeca"][:8])


if __name__ == "__main__":
    main()
