"""Metrica de semelhanca do estudio (InsightFace antelopev2, cosseno do embedding normalizado), offline/CPU."""
import sys

import cv2
import numpy as np
from insightface.app import FaceAnalysis

_APP = {}


def app(det=1024):
    if det not in _APP:
        a = FaceAnalysis(name="antelopev2", root=r"C:\Users\mauri\lv\insightface", providers=["CPUExecutionProvider"],
                         allowed_modules=["detection", "recognition"])
        a.prepare(ctx_id=-1, det_size=(det, det), det_thresh=0.35)
        _APP[det] = a
    return _APP[det]


def ler(caminho):
    return cv2.imdecode(np.fromfile(caminho, np.uint8), cv2.IMREAD_COLOR)


def rostos(img, det=1024):
    return sorted(app(det).get(img), key=lambda f: -(f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def referencia_media(caminhos):
    embs = []
    for c in caminhos:
        fs = rostos(ler(c))
        if fs:
            embs.append(fs[0].normed_embedding)
    m = np.mean(embs, axis=0)
    return m / np.linalg.norm(m), len(embs)


if __name__ == "__main__":
    ref, n = referencia_media(sys.argv[2:])
    img = ler(sys.argv[1])
    for f in rostos(img):
        print([round(float(x)) for x in f.bbox], "sim", round(float(np.dot(ref, f.normed_embedding)), 3), "det", round(float(f.det_score), 2))
