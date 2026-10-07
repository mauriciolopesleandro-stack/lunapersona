"""FaceSwapEngine V2: troca facial dedicada, separada do Replacement completo.

  FACE_ONLY        so o rosto (miolo + contorno), com referencia facial e LoRA; cabelo/pescoco da foto.
  FACE_NECK        rosto + pescoco (juncao mandibula/pescoco integrada).
  FACE_INTEGRATED  rosto + cabelo proximo + pele: passe de identidade (LoRA + pose/profundidade) e
                   refino com referencia facial. Com head_backend="qwen_bfs" usa a troca de cabeca
                   (Qwen-Image-Edit 2511 + BFS, vencedora do benchmark de 2026-10-07) e so a cabeca volta.
  FULL_PERSON      delega ao ReplacementEngine.

Nunca sobe de modo sozinho: pedido FACE_ONLY roda FACE_ONLY (ou falha), nunca FULL_PERSON.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.engines.adapter import ControlSpec, IdentitySpec, InpaintRequest, ModelAdapter
from app.core.engines.integration import integrate
from app.core.engines.replacement import ReplacementEngine, ReplacementRequest, _png
from app.core.engines.telemetry import JobTelemetry
from app.core.engines.validation import changed_fraction, validate_v2
from app.core.persona_replacement.segmentation import build_masks, dilate, ellipse
from app.core.persona_replacement.transfer import identity_mask
from app.providers.base import ProviderImage, ReferenceImage

FACE_ONLY, FACE_NECK, FACE_INTEGRATED, FULL_PERSON = "FACE_ONLY", "FACE_NECK", "FACE_INTEGRATED", "FULL_PERSON"
MODES = (FACE_ONLY, FACE_NECK, FACE_INTEGRATED, FULL_PERSON)
ENGINE_VERSION = "faceswap-v2.0"


class FaceSwapError(ValueError):
    pass


@dataclass
class FaceSwapRequest:
    image: str
    persona_id: str
    master: ReferenceImage
    mode: str = FACE_INTEGRATED
    seed: int = 7901
    head_backend: str = "sdxl"  # "sdxl" | "qwen_bfs"
    reference_strength: float = 0.55
    negative: str = ""
    replacement: ReplacementRequest | None = None  # so para FULL_PERSON


@dataclass
class FaceSwapOutcome:
    image: str
    pixels: np.ndarray
    status: str
    report: Any
    measures: dict[str, Any]
    telemetry: JobTelemetry
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return {"image": self.image, "status": self.status, "mode": self.mode, "validation": self.report.to_dict(),
                "measures": self.measures, "telemetry": self.telemetry.to_dict()}


class FaceSwapEngine:
    def __init__(self, *, reader, segmenter, analyzer, store, adapter: ModelAdapter, config: dict[str, Any],
                 replacement: ReplacementEngine | None = None, head_swap=None, price_per_hour: float | None = None,
                 provider: str = "") -> None:
        self.reader, self.segmenter, self.analyzer, self.store = reader, segmenter, analyzer, store
        self.adapter, self.cfg = adapter, config
        self.replacement = replacement
        self.head_swap = head_swap  # porta opcional: async swap(image, face_bbox, master, seed) -> locator
        self.price = price_per_hour
        self.provider = provider

    def _mask(self, mode: str, masks, face_bbox, shape) -> np.ndarray:
        h, w = shape
        if masks is None:
            x1, y1, x2, y2 = face_bbox
            fw, fh = x2 - x1, y2 - y1
            face = ellipse(h, w, (x1 + x2) / 2, (y1 + y2) / 2 + fh * 0.06, fw * 0.55, fh * 0.62)
            neck = ellipse(h, w, (x1 + x2) / 2, y2 + fh * 0.25, fw * 0.42, fh * 0.42)
            head = ellipse(h, w, (x1 + x2) / 2, y1 + fh * 0.42, fw * 0.95, fh * 0.95)
            return {FACE_ONLY: face, FACE_NECK: np.clip(face + neck, 0, 1), FACE_INTEGRATED: np.clip(head + neck, 0, 1)}[mode]
        if mode == FACE_ONLY:
            return np.clip(masks.face_full - masks.protect, 0, 1)
        if mode == FACE_NECK:
            return np.clip(masks.face_full + masks.face_transition - masks.protect, 0, 1)
        return identity_mask(masks, face_bbox, float(self.cfg["segmentation"]["face_grow_frac"]))

    async def run(self, req: FaceSwapRequest):
        if req.mode not in MODES:
            raise FaceSwapError(f"modo invalido: {req.mode}")
        if req.mode == FULL_PERSON:
            if self.replacement is None or req.replacement is None:
                raise FaceSwapError("FULL_PERSON precisa do ReplacementEngine e do pedido de Replacement")
            return await self.replacement.run(req.replacement)
        start = time.monotonic()
        meta = self.adapter.metadata()
        tel = JobTelemetry(uuid.uuid4().hex[:12], req.persona_id, "face_swap", req.mode, model=meta.get("model", ""),
                           checkpoint_hash=meta.get("hash", ""), lora_hash=meta.get("lora_hash", ""), seed=req.seed,
                           reference_strength=req.reference_strength, provider=self.provider, engine_version=ENGINE_VERSION)
        original = await self.store.load(req.image)
        h, w = original.shape[:2]
        sheet = await self.reader.read(ProviderImage("comfyui", req.image, "", w, h), req.master)
        bbox = sheet.target_face.bbox
        masks = None
        try:
            raw = await self.segmenter.segment(req.image, sheet)
            masks = build_masks(raw, bbox, sheet.target_face.kps, original)
        except Exception:  # sem segmentacao o modo continua, com mascara geometrica (registrado)
            tel.failure_reason = "segmentacao indisponivel: mascara geometrica"
        mask = self._mask(req.mode, masks, bbox, (h, w))
        P, neg = self.cfg["prompts"], ", ".join(dict.fromkeys([t for t in req.negative.split(", ") if t] + list(self.cfg["negative"])))
        cur, loc = original, req.image
        if req.mode == FACE_INTEGRATED and req.head_backend == "qwen_bfs":
            if self.head_swap is None:
                raise FaceSwapError("head_backend qwen_bfs pedido mas nao configurado (sem fallback)")
            t0 = time.monotonic()
            out_loc = await self.head_swap.swap(req.image, bbox, req.master, req.seed)
            px = await self.store.load(out_loc)
            if px.shape != original.shape:
                raise FaceSwapError("troca de cabeca devolveu outro tamanho")
            region = dilate(mask, 6)
            cur = np.where((region > 0.5)[..., None], px, original)
            loc = await self.store.save(cur, "headswap")
            tel.add_pass("qwen_bfs_head", round(time.monotonic() - t0, 2), {"backend": "qwen_bfs"}, True)
        else:
            if req.mode == FACE_INTEGRATED:
                ctrl = ControlSpec(pose_strength=0.8, depth_strength=0.5, end_percent=0.8, structure=req.image)
                prompt = P["identity"].replace("{description}", sheet.description())
                r = await self.adapter.inpaint(InpaintRequest(loc, mask, prompt, neg, 0.85, req.seed, "fs_identity", controls=ctrl))
                px = await self.store.load(r.image)
                cur = np.where((mask > 0.02)[..., None], px, cur)
                loc = await self.store.save(cur, "fs_identity")
                tel.add_pass("fs_identity", r.seconds, r.parameters, True)
            face_mask = mask if req.mode != FACE_INTEGRATED else (masks.face_full if masks is not None else mask)
            denoise = 0.6 if req.mode != FACE_INTEGRATED else 0.4
            r = await self.adapter.inpaint(InpaintRequest(loc, face_mask, P["face"], neg, denoise, req.seed + 101, "face_swap",
                                                          identity=IdentitySpec(True, req.master, req.reference_strength)))
            px = await self.store.load(r.image)
            cur = np.where((face_mask > 0.02)[..., None], px, cur)
            loc = await self.store.save(cur, "face_swap")
            tel.add_pass("face_swap", r.seconds, r.parameters, True)
        body_skin = None if masks is None else np.clip(masks.skin * masks.person - mask, 0, 1)
        cur, integ = integrate(original, cur, mask, face=mask if req.mode == FACE_ONLY else None, body_skin=body_skin,
                               seed=req.seed, tone_harmony=0.5)
        final = np.where((dilate(mask, 2) > 0.5)[..., None], cur, original)
        loc = await self.store.save(final, "faceswap_final")
        an = await self.analyzer.analyze(ProviderImage("comfyui", loc, "", w, h), req.master)
        f = an.persona_face()
        x1, y1, x2, y2 = (int(v) for v in bbox)
        oref = ReferenceImage("original", "original.png", _png(original[max(0, y1 - 20):y2 + 20, max(0, x1 - 20):x2 + 20]), "")
        fo = (await self.analyzer.analyze(ProviderImage("comfyui", loc, "", w, h), oref)).persona_face()
        dup = float(self.cfg.get("duplicate_similarity", 0.45))
        protect = masks.person if masks is not None else mask
        m = {"identity": f.similarity if f else None, "original_sim": fo.similarity if fo else None,
             "background": changed_fraction(original, final, 1 - dilate((protect > 0.02).astype(np.float32), 3)),
             "persona_instances": len([x for x in an.faces if x.similarity is not None and x.similarity >= dup]),
             "faces": len(an.faces), "person_found": f is not None}
        report = validate_v2(m)
        tel.duration_s = round(time.monotonic() - start, 2)
        tel.estimated_cost_usd = self.adapter.estimate_cost(tel.gpu_seconds, self.price)
        tel.validation_result = report.status
        return FaceSwapOutcome(loc, final, report.status, report, {**m, "integration": integ}, tel, req.mode)


__all__ = ["FACE_INTEGRATED", "FACE_NECK", "FACE_ONLY", "FULL_PERSON", "FaceSwapEngine", "FaceSwapError", "FaceSwapRequest"]
