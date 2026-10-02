"""No do ComfyUI do estudio: rostos de uma imagem pelo InsightFace (antelopev2).

Para o pack com a persona (backend/app/services/person_swap.py). O Florence
(modelo de descrever imagem) errava qual rosto era dela com o casal colado; o
InsightFace e feito para rosto: acha de perfil, diz homem/mulher e compara a
identidade de dois rostos.

Saida: texto JSON com uma lista de rostos, do maior para o menor:
[{"bbox": [x1, y1, x2, y2], "score": 0.83, "sex": "F", "age": 27, "sim": 0.41}]
"sim" = semelhanca (cosseno do ArcFace) com o maior rosto da imagem de
referencia; so existe com referencia. Acima de ~0.35 costuma ser a mesma pessoa.

Instalado por scripts/setup_instantid.sh (link em custom_nodes).
"""
from __future__ import annotations

import json
import os

import numpy as np

try:
    import folder_paths

    _ROOT = os.path.join(folder_paths.models_dir, "insightface")
except ImportError:  # fora do ComfyUI (teste)
    _ROOT = "/workspace/runpod-slim/ComfyUI/models/insightface"

_APPS: dict[int, object] = {}


def _app(det_size: int):
    if det_size not in _APPS:
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(name="antelopev2", root=_ROOT, providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(det_size, det_size), det_thresh=0.35)
        _APPS[det_size] = app
    return _APPS[det_size]


def _bgr(image) -> np.ndarray:
    arr = (image.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
    return np.ascontiguousarray(arr[:, :, ::-1])


def _faces(image, det_size: int) -> list:
    faces = _app(det_size).get(_bgr(image))
    return sorted(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)


class LunaFaces:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "det_size": ("INT", {"default": 1024, "min": 320, "max": 2048, "step": 32}),
            },
            "optional": {"reference": ("IMAGE",)},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("faces",)
    FUNCTION = "run"
    CATEGORY = "luna"
    OUTPUT_NODE = True

    def run(self, image, det_size, reference=None):
        ref = None
        if reference is not None:
            ref_faces = _faces(reference[0], 640)
            if ref_faces:
                ref = ref_faces[0].normed_embedding
        out = []
        for f in _faces(image[0], det_size):
            item = {
                "bbox": [round(float(v), 1) for v in f.bbox],
                "score": round(float(f.det_score), 3),
                "sex": getattr(f, "sex", None),
                "age": int(getattr(f, "age", 0) or 0),
            }
            if ref is not None:
                item["sim"] = round(float(np.dot(ref, f.normed_embedding)), 3)
            out.append(item)
        text = json.dumps(out)
        return {"ui": {"text": [text]}, "result": (text,)}


NODE_CLASS_MAPPINGS = {"LunaFaces": LunaFaces}
NODE_DISPLAY_NAME_MAPPINGS = {"LunaFaces": "Luna - rostos (InsightFace)"}
