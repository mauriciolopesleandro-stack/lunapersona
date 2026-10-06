"""Persona Replacement: a mesma fotografia, com a persona no lugar da pessoa.

  FOTO -> leitura (pessoa, rosto, pose, luz) -> segmentacao (pessoa, cabelo, pele,
  roupa, protegidos) -> cabelo (recolor, sem regenerar) -> rosto 1 (identidade) ->
  rosto 2 (refino) -> rosto 3 (integracao) -> corpo 1 -> corpo 2 (so pele) ->
  luz/cor + grao (so na regiao alterada) -> composicao final contra a ORIGINAL
  (fundo = pixels originais) -> validacao.

Cada etapa parte do melhor checkpoint aceito e e medida; se piorar, rollback.
Nenhuma etapa mexe fora da propria mascara: isso e garantido aqui (os pixels de
fora voltam do checkpoint), nao confiado ao modelo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.generation.multipass import measure
from app.core.persona_replacement.contracts import (
    ImageStore,
    ReplacementConfig,
    ReplacementTransformer,
    Segmenter,
)
from app.core.persona_replacement.face_transform import build_request, stage_kind
from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.lighting import match_grain, match_lighting, needs_hair_recolor, noise_level, recolor_hair
from app.core.persona_replacement.rollback import Checkpoint, CheckpointStore, stage_reasons
from app.core.persona_replacement.segmentation import MaskSet, build_masks, dilate
from app.core.persona_replacement.telemetry import StageRecord, cost
from app.core.persona_replacement.validation import (
    ReplacementReport,
    background_change,
    clothing_change,
    surroundings,
    validate,
)
from app.providers.base import ProviderImage, ReferenceImage


@dataclass
class ReplacementResult:
    final: Checkpoint
    report: ReplacementReport
    records: list[StageRecord] = field(default_factory=list)
    checkpoints: list[str] = field(default_factory=list)
    mask_areas: dict[str, float] = field(default_factory=dict)
    reading: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"final": {"name": self.final.name, "image": self.final.image}, "report": self.report.to_dict(),
                "records": [r.to_dict() for r in self.records], "checkpoints": self.checkpoints,
                "mask_areas": self.mask_areas, "reading": self.reading}


class ReplacementOrchestrator:
    def __init__(self, *, reader, segmenter: Segmenter, transformer: ReplacementTransformer, analyzer,
                 store: ImageStore, config: ReplacementConfig, duplicate_similarity: float,
                 price_per_hour: float | None = None) -> None:
        self.reader = reader
        self.segmenter = segmenter
        self.transformer = transformer
        self.analyzer = analyzer
        self.store = store
        self.config = config
        self.duplicate_similarity = duplicate_similarity
        self.price = price_per_hour

    async def _measure(self, image: str, w: int, h: int, master: ReferenceImage, base_pose):
        analysis = await self.analyzer.analyze(ProviderImage("comfyui", image, "", w, h), master)
        return measure(analysis, None, base_pose, self.duplicate_similarity)

    def _pixel_checks(self, original: np.ndarray, px: np.ndarray, masks: MaskSet) -> dict[str, Any]:
        return {"background_changed": background_change(original, px, masks.person),
                "clothing_changed": clothing_change(original, px, masks.clothing)}

    async def _try(self, store: CheckpointStore, name: str, kind: str, px: np.ndarray, original: np.ndarray,
                   masks: MaskSet, master, base_pose, rec: StageRecord, region: np.ndarray, modified: np.ndarray) -> np.ndarray:
        h, w = original.shape[:2]
        loc = await self.store.save(px, name)
        after = await self._measure(loc, w, h, master, base_pose)
        pixel = self._pixel_checks(original, px, masks)
        reasons = stage_reasons(kind, store.current.measure, after, pixel, self.config.checks)
        rec.image, rec.identity, rec.age, rec.pose, rec.metrics = loc, after.face, after.age, after.pose, pixel
        rec.accepted, rec.rollback_reason = not reasons, "; ".join(reasons) or None
        store.add(Checkpoint(name, loc, px, after, pixel), accept=not reasons)
        return np.maximum(modified, region) if not reasons else modified

    async def run(self, image: str, master: ReferenceImage, negative: str, seed: int) -> ReplacementResult:
        cfg = self.config
        original = await self.store.load(image)
        h, w = original.shape[:2]
        sheet = await self.reader.read(ProviderImage("comfyui", image, "", w, h), master)
        raw = await self.segmenter.segment(image, sheet)
        masks = build_masks(raw, sheet.target_face.bbox, sheet.target_face.kps, original)
        base_pose = sheet.target_body.keypoints if sheet.target_body is not None else None
        m0 = await self._measure(image, w, h, master, base_pose)
        m0.pose = 0.0 if base_pose else None
        store = CheckpointStore()
        store.add(Checkpoint("original", image, original, m0), accept=True)
        records: list[StageRecord] = []
        modified = np.zeros((h, w), np.float32)
        neg = ", ".join(dict.fromkeys([t for t in negative.split(", ") if t] + list(cfg.negative_extra)))

        # 1. cabelo da persona por recolor (fios, luz e volume da foto ficam)
        hair = cfg.hair
        if hair.get("enabled") and needs_hair_recolor(original, masks.hair, float(hair["recolor_if_luma_above"])):
            t0 = time.monotonic()
            grow = max(2, int(min(h, w) * float(hair.get("edge_grow_frac", 0.004))))
            hair_soft = feather(np.clip(dilate(masks.hair, grow) * (1 - masks.skin) * masks.person + masks.hair, 0, 1), grow)
            px = recolor_hair(original, hair_soft, float(hair["target_luma"]), float(hair["target_cb"]),
                              float(hair["target_cr"]), float(hair["strength"]))
            rec = StageRecord("hair_recolor", "hair", mask="hair", strength=float(hair["strength"]))
            modified = await self._try(store, "hair_recolor", "hair", px, original, masks, master, base_pose, rec,
                                       masks.hair, modified)
            rec.seconds = round(time.monotonic() - t0, 2)
            records.append(rec)

        # 2. rosto e corpo: transformacao localizada, nunca fora da mascara
        for n, stage in enumerate(s for s in cfg.stages if s.enabled):
            current = store.current
            req = build_request(stage, masks, current.image, neg, (seed + 101 * (n + 1)) % 2**32, master)
            rec = StageRecord(stage.name, stage.kind, mask=stage.mask, strength=stage.strength, denoise=stage.denoise,
                              seed=req.seed if req else None, identity_adapter=stage.identity_adapter, lora=stage.lora)
            records.append(rec)
            if req is None:
                rec.accepted, rec.rollback_reason = False, f"mascara {stage.mask} vazia (nada visivel para transformar)"
                continue
            res = await self.transformer.transform(req)
            px = await self.store.load(res.image)
            keep = dilate((req.mask > 0.02).astype(np.float32), 3) > 0.5
            px = np.where(keep[..., None], px, current.pixels)  # fora da mascara: o checkpoint, garantido
            modified = await self._try(store, stage.name, stage_kind(stage), px, original, masks, master, base_pose,
                                       rec, req.mask, modified)
            rec.seconds, rec.gpu = res.seconds, res.gpu.get("name")
            rec.cost_usd = cost(res.seconds, self.price)

        # 3. luz/cor e grao so na regiao alterada (sem o cabelo: o recolor e a cor dele);
        #    fundo final = pixels ORIGINAIS
        current = store.current
        region = np.clip(modified - masks.hair - masks.protect, 0, 1) * masks.person
        lit = match_lighting(current.pixels, original, region, float(cfg.lighting["luma"]), float(cfg.lighting["chroma"]))
        # grao de referencia: a pessoa ORIGINAL na mesma regiao (o fundo tem detalhe de cena, nao grao)
        reference_noise = (noise_level(original, region) if cfg.texture.get("reference", "original_region") == "original_region"
                           else noise_level(original, surroundings(masks.person)))
        grained = match_grain(lit, region, reference_noise, seed) if cfg.texture.get("match_grain") else lit
        inside = dilate((masks.person > 0.5).astype(np.float32), 2) > 0.5
        integrated = np.where(inside[..., None], grained, original)
        rec = StageRecord("integration", "integration", mask="modified")
        await self._try(store, "integrated", "integration", integrated, original, masks, master, base_pose, rec,
                        region, modified)
        records.append(rec)

        final = store.current
        edge_width = max(3, int(min(h, w) * float(cfg.blending.get("edge_band_frac", 0.006))))
        report = validate(original, final.pixels, masks, region if region.any() else masks.person, final.measure,
                          cfg.checks, edge_width)
        return ReplacementResult(final, report, records, store.names(), masks.areas(), sheet.to_dict())


__all__ = ["ReplacementOrchestrator", "ReplacementResult"]
