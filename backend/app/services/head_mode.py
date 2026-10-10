"""Modo "Luna na foto": a pessoa da foto ganha a cabeca (rosto + cabelo) da Luna e TODO o resto fica identico.

Fluxo (validado na fase 0/POC de 10/10, scripts/fase0):
  1. rosto alvo pelo LunaFaces (mulher maior; senao o maior) e os outros rostos (ficam fora da mascara);
  2. referencias: banco de fotos da Luna aprovadas pelo usuario, ordenadas por angulo e sorriso parecidos com o alvo
     (com referencia sorrindo a Luna saia sorrindo em foto seria);
  3. para cada uma das N primeiras: Qwen-Image-Edit 2511 + BFS Head V5 num recorte em volta do rosto (workflow de
     producao qwen-bfs-head-swap), saida crua -> composicao organica (app/core/head/compose.py);
  4. fica a candidata mais parecida com as fotos do usuario (media das 'proxies', LunaFaces/ArcFace).
Sem rosto, sem referencias no pod ou composicao recusada = erro claro (sem fallback para outra engine).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import cv2
import numpy as np

from app.core.head.compose import compor
from app.services.head_matte import ensure_model, matte_rgb
from app.services.head_swap import WORKFLOW, crop_box, faces_in, model_size


class HeadModeError(RuntimeError):
    pass


def load_head_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def teeth(rgb: np.ndarray, kps) -> float | None:
    """Dentes a mostra entre os cantos da boca (kps 3 e 4): pixels claros e pouco saturados numa elipse."""
    if kps is None or len(kps) < 5:
        return None
    k = np.asarray(kps, np.float32)
    c = (k[3] + k[4]) / 2
    w = float(np.linalg.norm(k[4] - k[3]))
    if w < 8:
        return None
    m = np.zeros(rgb.shape[:2], np.uint8)
    cv2.ellipse(m, (int(c[0]), int(c[1])), (int(w * 0.42), int(w * 0.28)), 0, 0, 360, 255, -1)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sel = m > 0
    if sel.sum() < 20:
        return None
    v, s = hsv[..., 2][sel].astype(float), hsv[..., 1][sel].astype(float)
    ref = np.percentile(v, 95)
    return float(((v > 0.75 * ref) & (s < 70) & (v > 120)).mean())


def rank_bank(bank: list[dict[str, Any]], yaw: float | None, tooth: float | None) -> list[dict[str, Any]]:
    def dist(b):
        d = abs((yaw or 0.0) - b["yaw"])
        d += 4.0 * abs(tooth - b["teeth"]) if tooth is not None else 0.3
        return d - 0.8 * (b.get("sim", 0.75) - 0.75)

    return sorted(bank, key=dist)


def pick_target(faces: list[dict[str, Any]]):
    women = [f for f in faces if str(f.get("sex") or "").upper().startswith("F")] or faces
    target = max(women, key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]))
    others = [f["bbox"] for f in faces if f is not target]
    return target, others


class HeadModeService:
    def __init__(self, client, workflows, store, url_for: Callable[[str], str], jobs, cfg: dict[str, Any],
                 matte: Callable[[np.ndarray], Awaitable[np.ndarray]] | None = None) -> None:
        self.client = client
        self.workflows = workflows
        self.store = store
        self.url_for = url_for
        self.jobs = jobs
        self.cfg = cfg
        self._uploaded: dict[str, str] = {}
        self._matte = matte

    async def _person(self, rgb: np.ndarray) -> np.ndarray:
        if self._matte is not None:
            return await self._matte(rgb)
        mm = self.cfg["matte_model"]
        path = await ensure_model(Path(mm["path"]), mm["url"])
        return await asyncio.to_thread(matte_rgb, rgb, path)

    async def _ref(self, name: str) -> str:
        if name not in self._uploaded:
            p = Path(self.cfg["refs_dir"]) / name
            if not p.exists():
                raise HeadModeError(f"referencia da Luna ausente no pod: {p} (banco de fotos do usuario)")
            self._uploaded[name] = await self.client.upload_image(f"luna_head_{name}", p.read_bytes())
        return self._uploaded[name]

    async def _similarity(self, image: str, target_box) -> float | None:
        cx, cy = (target_box[0] + target_box[2]) / 2, (target_box[1] + target_box[3]) / 2
        sims = []
        for proxy in self.cfg.get("proxies", []):
            faces = await faces_in(self.client, image, reference=await self._ref(proxy)) or []
            if not faces:
                continue
            near = min(faces, key=lambda f: ((f["bbox"][0] + f["bbox"][2]) / 2 - cx) ** 2 + ((f["bbox"][1] + f["bbox"][3]) / 2 - cy) ** 2)
            if near.get("sim") is not None:
                sims.append(float(near["sim"]))
        return round(float(np.mean(sims)), 3) if sims else None

    async def _swap(self, image: str, head: str, box, W: int, H: int, seed: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        x, y, w, h = crop_box(tuple(box), W, H)
        mw, mh = model_size(w, h)
        g = self.workflows.render(WORKFLOW, {"BODY_IMAGE": image, "HEAD_IMAGE": head, "WIDTH": mw, "HEIGHT": mh,
                                             "CROP_X": x, "CROP_Y": y, "CROP_W": w, "CROP_H": h,
                                             "FEATHER": int(min(w, h) * 0.08), "SEED": seed, "FILENAME_PREFIX": "luna_head_comp"})
        g["90"] = {"class_type": "SaveImage", "inputs": {"images": ["32", 0], "filename_prefix": "luna_head_raw"}}
        entry = await self.client.wait_for_completion(await self.client.queue_prompt(g))
        raw = next((i for i in self.client.extract_images(entry) if i.filename.startswith("luna_head_raw")), None)
        if raw is None:
            raise HeadModeError("a troca de cabeca nao devolveu imagem")
        loc = f"{raw.subfolder}/{raw.filename}" if raw.subfolder else raw.filename
        return await self.store.load(f"{loc} [output]"), (x, y, w, h)

    async def run(self, image: str) -> dict[str, Any]:
        t0 = time.monotonic()
        rgb = await self.store.load(image)
        H, W = rgb.shape[:2]
        faces = await faces_in(self.client, image) or []
        if not faces:
            raise HeadModeError("nenhum rosto encontrado na foto")
        target, others = pick_target(faces)
        box = target["bbox"]
        tooth = teeth(rgb, target.get("kps"))
        ranked = rank_bank(self.cfg["bank"], target.get("yaw"), tooth)[: int(self.cfg.get("candidates", 2))]
        person = await self._person(rgb)
        bgr_orig = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        cands = []
        for i, ref in enumerate(ranked):
            t = time.monotonic()
            raw, crop = await self._swap(image, await self._ref(ref["file"]), box, W, H, int(self.cfg.get("seed", 1234)) + i)
            comp = await asyncio.to_thread(compor, bgr_orig, cv2.cvtColor(raw, cv2.COLOR_RGB2BGR), crop, box, others, person)
            item: dict[str, Any] = {"ref": ref["file"], "seconds": round(time.monotonic() - t, 1), **comp.info}
            if comp.final is not None:
                loc = await self.store.save(cv2.cvtColor(comp.final, cv2.COLOR_BGR2RGB), "luna_head")
                item.update(image=loc, similarity=await self._similarity(loc, box))
            cands.append(item)
        ok = [c for c in cands if c.get("image")]
        if not ok:
            raise HeadModeError(f"nenhuma candidata pode ser composta: {[c.get('erro') for c in cands]}")
        best = max(ok, key=lambda c: c.get("similarity") or -1)
        sim = best.get("similarity")
        status = "PASS" if sim is not None and sim >= float(self.cfg.get("warn_below", 0.65)) else "WARN"
        checks = {"identity": {"name": "identity", "status": status, "score": sim, "threshold": self.cfg.get("warn_below", 0.65),
                               "reason": "semelhanca com as fotos da Luna aprovadas"}}
        return {"engine": "head", "model": "qwen-image-edit-2511+bfs-head-v5", "mode": "LUNA_NA_FOTO", "status": status,
                "image_url": self.url_for(best["image"]), "validation": {"status": status, "checks": checks},
                "measures": {"similarity": sim, "target_yaw": target.get("yaw"), "target_teeth": tooth},
                "telemetry": {"duration_s": round(time.monotonic() - t0, 1), "candidates": cands, "chosen": best["ref"],
                              "version": self.cfg.get("version")},
                "intermediates": {}}

    def start(self, image: str) -> dict[str, str]:
        return self.jobs.start(lambda: self.run(image))


__all__ = ["HeadModeError", "HeadModeService", "load_head_config", "pick_target", "rank_bank", "teeth"]
