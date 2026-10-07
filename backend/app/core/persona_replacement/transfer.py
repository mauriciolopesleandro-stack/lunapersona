"""Persona Transfer: a persona INTEIRA gerada na pose da pessoa da foto (sem face swap).

  FOTO -> leitura (pessoa, pose, descricao) -> segmentacao (pessoa, cabelo, roupa,
  acessorios) -> PASS 1 transferencia: a regiao da pessoa SEM a roupa e SEM os
  acessorios (+ folga de cabelo em volta da cabeca) e gerada de novo com a LoRA da
  persona e a pose original como condicao -> PASS 2 refino de rosto (so se a
  identidade ficou baixa) -> PASS 3 integracao (denoise baixo, sem LoRA, nas
  transicoes) -> composicao final contra a ORIGINAL -> validacao.

A pessoa original da so pose, enquadramento, roupa, luz e cenario; identidade,
rosto, cabelo, pele e corpo visivel vem da persona. Nada de grao/filtro depois.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from app.core.persona_replacement.contracts import TransformRequest
from app.core.persona_replacement.replacement_orchestrator import ReplacementOrchestrator, ReplacementResult
from app.core.persona_replacement.rollback import Checkpoint, CheckpointStore
from app.core.persona_replacement.segmentation import MaskSet, build_masks, dilate, ellipse
from app.core.persona_replacement.telemetry import StageRecord, cost
from app.core.persona_replacement.validation import background_change, changed_fraction, validate
from app.providers.base import ProviderImage, ReferenceImage

TRANSFER, FACE_REFINE, INTEGRATION = "persona_transfer", "face_refinement", "integration"


@dataclass
class TransferConfig:
    transfer: dict[str, Any]
    face_refinement: dict[str, Any]
    integration: dict[str, Any]
    checks: dict[str, Any]
    negative_extra: tuple[str, ...]
    budget_limit_usd: float
    generation_config: str
    version: str = ""
    post_lighting_match: dict[str, Any] = field(default_factory=dict)


def load_transfer_config(path: Path) -> TransferConfig:
    d = json.loads(path.read_text(encoding="utf-8"))
    t = d["transfer"]
    if not 0.5 <= float(t["denoise"]) <= 1.0:
        raise ValueError("transfer.denoise precisa estar entre 0,5 e 1,0 (a persona e GERADA na regiao, nao colada).")
    if float(d["integration"]["denoise"]) > 0.25:
        raise ValueError("integracao e passada leve: denoise <= 0,25.")
    if d["face_refinement"].get("identity_adapter") and float(d["face_refinement"].get("adapter_weight", 0)) > 0.6:
        raise ValueError("refino de rosto nao usa InstantID forte (spec: identidade vem da persona).")
    return TransferConfig(t, d["face_refinement"], d["integration"], d["checks"], tuple(d.get("negative_extra", [])),
                          float(d["budget"]["limit_usd"]), d["generation_config"], d.get("version", ""),
                          d.get("post_lighting_match", {}))


def transfer_masks(masks: MaskSet, face_bbox, hair_room: float) -> tuple[np.ndarray, np.ndarray]:
    """(regiao gerada, folga de cabelo). A regiao e a pessoa MENOS roupa e acessorios. Com
    hair_room 0 (padrao: fundo TRAVADO) a regiao nunca sai do contorno da pessoa; acima de 0,
    uma folga em volta da cabeca deixa o cabelo da persona passar do contorno original."""
    h, w = masks.person.shape
    region = np.clip(masks.person - masks.clothing - masks.protect, 0, 1)
    if hair_room <= 0:
        return region, np.zeros((h, w), np.float32)
    x1, y1, x2, y2 = face_bbox
    fw, fh = x2 - x1, y2 - y1
    head = ellipse(h, w, (x1 + x2) / 2, (y1 + y2) / 2 + fh * 0.6, fw * (1.0 + 1.6 * hair_room) * 0.9,
                   fh * (1.6 + 2.0 * hair_room) * 0.9)
    room = np.clip(head * (1 - masks.clothing) * (1 - masks.protect) - masks.person, 0, 1)
    return np.clip(region + room, 0, 1), room


def plausible_accessories(boxes, face_bbox, max_face_ratio: float) -> list:
    """Oculos, brinco, pulseira e relogio sao pequenos: uma caixa maior que o rosto e falso
    positivo do grounding (ex.: o top inteiro com as maos) e nao pode travar a regiao."""
    x1, y1, x2, y2 = face_bbox
    face_area = max(1.0, (x2 - x1) * (y2 - y1))
    return [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) <= face_area * max_face_ratio]


def _box_sum(a: np.ndarray, r: int) -> np.ndarray:
    """Soma numa janela (2r+1)^2 por imagem integral (borda: so o que existe)."""
    h, w = a.shape[:2]
    c = np.pad(a, ((1, 0), (1, 0)) + ((0, 0),) * (a.ndim - 2)).cumsum(0).cumsum(1)
    y0, y1 = np.clip(np.arange(h) - r, 0, h), np.clip(np.arange(h) + r + 1, 0, h)
    x0, x1 = np.clip(np.arange(w) - r, 0, w), np.clip(np.arange(w) + r + 1, 0, w)
    return c[y1][:, x1] - c[y0][:, x1] - c[y1][:, x0] + c[y0][:, x0]


def fill_tattoos(rgb: np.ndarray, tattoos: np.ndarray, skin: np.ndarray, radius: int) -> np.ndarray:
    """Entrada da passada 1 sem a tinta: cada pixel de tatuagem recebe a media da pele limpa em
    volta. Nao e filtro no resultado - a geracao parte de pele lisa e cria a textura dela."""
    ink = dilate((tattoos > 0.5).astype(np.float32), 2) > 0.5
    if not ink.any():
        return rgb.copy()
    known = ((skin > 0.5) & ~ink).astype(np.float32)
    num = _box_sum(rgb.astype(np.float32) * known[..., None], radius)
    den = _box_sum(known, radius)[..., None]
    mean = (rgb[known > 0].mean(axis=0) if known.any() else rgb[ink].mean(axis=0)).astype(np.float32)
    fill = np.where(den > 0.5, num / np.maximum(den, 1e-6), mean)
    out = rgb.astype(np.float32)
    out[ink] = fill[ink]
    return out.round().clip(0, 255).astype(np.uint8)


def edge_ring(region: np.ndarray, r: int) -> np.ndarray:
    m = (region > 0.5).astype(np.float32)
    return np.clip(dilate(m, r) - (1.0 - dilate(1.0 - m, r)), 0, 1)


class TransferOrchestrator(ReplacementOrchestrator):
    """Reusa leitura, segmentacao, medida, checkpoints e validacao do replacement."""

    config: TransferConfig  # type: ignore[assignment]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._region = None
        self._room = None
        self._seam = None

    def _pixel_checks(self, original: np.ndarray, px: np.ndarray, masks: MaskSet) -> dict[str, Any]:
        protected_scene = masks.person if self._region is None else np.clip(masks.person + self._region, 0, 1)
        clothing = masks.clothing if self._seam is None else masks.clothing * (1 - self._seam)
        return {"background_changed": background_change(original, px, protected_scene),
                "clothing_changed": changed_fraction(original, px, clothing, threshold=10),
                "hair_room_changed": changed_fraction(original, px, self._room) if self._room is not None else None}

    def _prompt(self, template: str, description: str) -> str:
        return template.replace("{description}", description).replace(", ,", ",")

    async def _stage(self, store: CheckpointStore, name: str, kind: str, mask: np.ndarray, prompt: str, negative: str,
                     denoise: float, strength: float, seed: int, use_lora: bool, original, masks, master, base_pose,
                     records: list[StageRecord], modified: np.ndarray, **extra) -> np.ndarray:
        current = store.current
        req = TransformRequest(image=extra.get("source") or current.image, mask=mask, prompt=prompt, negative=negative, strength=strength,
                               denoise=denoise, seed=seed, name=name, use_lora=use_lora,
                               identity_adapter=extra.get("identity_adapter"), adapter_weight=extra.get("adapter_weight"),
                               reference=master if extra.get("identity_adapter") else None)
        rec = StageRecord(name, kind, mask=extra.get("mask_name"), strength=strength, denoise=denoise, seed=seed,
                          identity_adapter=req.identity_adapter, lora=use_lora)
        records.append(rec)
        res = await self.transformer.transform(req)
        px = await self.store.load(res.image)
        keep = mask > 0.02  # fundo TRAVADO: nem 1 px fora da mascara vem do modelo
        px = np.where(keep[..., None], px, current.pixels)
        modified = await self._try(store, name, kind, px, original, masks, master, base_pose, rec, mask, modified)
        rec.seconds, rec.gpu, rec.cost_usd = res.seconds, res.gpu.get("name"), cost(res.seconds, self.price)
        return modified

    async def run(self, image: str, master: ReferenceImage, negative: str, seed: int) -> ReplacementResult:
        cfg = self.config
        original = await self.store.load(image)
        h, w = original.shape[:2]
        sheet = await self.reader.read(ProviderImage("comfyui", image, "", w, h), master)
        raw = await self.segmenter.segment(image, sheet)
        raw = replace(raw, protect_boxes=plausible_accessories(raw.protect_boxes, sheet.target_face.bbox,
                                                               float(cfg.transfer.get("max_accessory_face_ratio", 1.0))))
        masks = build_masks(raw, sheet.target_face.bbox, sheet.target_face.kps, original)
        region, room = transfer_masks(masks, sheet.target_face.bbox, float(cfg.transfer["hair_room"]))
        # costura regiao/roupa (decote, mangas): a integracao funde alguns px; a roupa e medida fora dela
        seam = edge_ring(region, max(3, int(cfg.integration.get("edge_ring", 6))))
        self._region, self._room, self._seam = region, room, seam
        base_pose = sheet.target_body.keypoints if sheet.target_body is not None else None
        m0 = await self._measure(image, w, h, master, base_pose)
        m0.pose = 0.0 if base_pose else None
        store = CheckpointStore()
        store.add(Checkpoint("original", image, original, m0), accept=True)
        records: list[StageRecord] = []
        modified = np.zeros((h, w), np.float32)
        neg = ", ".join(dict.fromkeys([t for t in negative.split(", ") if t] + list(cfg.negative_extra)))

        # PASS 1: a persona inteira na pose original (identidade + corpo visivel + cabelo + pele);
        # a entrada vai sem a tinta das tatuagens (a geracao nao tem de onde copiar o desenho)
        t = cfg.transfer
        source = None
        if t.get("prefill_tattoos", True) and (masks.tattoos > 0.5).any():
            clean = fill_tattoos(original, masks.tattoos * region, masks.skin,
                                 max(6, int(min(h, w) * float(t.get("prefill_radius_frac", 0.02)))))
            source = await self.store.save(clean, "tattoo_prefill")
        modified = await self._stage(store, TRANSFER, "identity", region, self._prompt(t["prompt"], sheet.description()),
                                     neg, float(t["denoise"]), 1.0, seed % 2**32, True, original, masks, master,
                                     base_pose, records, modified, mask_name="person-clothing-accessories", source=source)

        # sem a persona inteira nao ha o que refinar: refinar so o rosto da original seria face swap
        transferred = store.current.name == TRANSFER
        if not transferred:
            for name, kind in ((FACE_REFINE, "face"), (INTEGRATION, "integration")):
                records.append(StageRecord(name, kind, accepted=False, rollback_reason="transferencia recusada"))

        # PASS 2: refino do rosto SO se a identidade ficou baixa
        fr = cfg.face_refinement
        current = store.current
        if not transferred:
            pass  # registrado acima
        elif fr.get("enabled") and (current.measure.face or 0) < float(fr["only_if_identity_below"]):
            modified = await self._stage(store, FACE_REFINE, "face", masks.face_full, fr["prompt"], neg,
                                         float(fr["denoise"]), float(fr["strength"]), (seed + 101) % 2**32,
                                         bool(fr.get("lora", True)), original, masks, master, base_pose, records,
                                         modified, identity_adapter=fr.get("identity_adapter"),
                                         adapter_weight=fr.get("adapter_weight"), mask_name="face_full")
        else:
            records.append(StageRecord(FACE_REFINE, "face", accepted=False,
                                       rollback_reason="nao necessario (identidade ja acima do limite)"))

        # PASS 3: integracao leve nas transicoes (pele/roupa, cabelo/fundo, pescoco) - sem LoRA
        it = cfg.integration
        if transferred and it.get("enabled"):
            # a regiao + a costura com a roupa; nunca o fundo (o contorno ja nasceu na passada 1)
            mask = np.clip(region + seam * masks.clothing - masks.protect, 0, 1)
            modified = await self._stage(store, INTEGRATION, "integration", mask, it["prompt"], neg,
                                         float(it["denoise"]), float(it["strength"]), (seed + 202) % 2**32,
                                         bool(it.get("lora", False)), original, masks, master, base_pose, records,
                                         modified, mask_name="region+edge_ring")

        final = store.current
        edge_width = max(3, int(min(h, w) * 0.006))
        # a "pessoa" do resultado inclui a folga de cabelo (gerada); a roupa e medida fora da costura
        scored = replace(masks, person=np.clip(masks.person + region, 0, 1), clothing=masks.clothing * (1 - seam))
        report = validate(original, final.pixels, scored, region, final.measure, cfg.checks, edge_width)
        if not transferred:
            report.failures.append("transfer_rejected")
            report.status = "FAIL"
        original_ref = ReferenceImage("original", "original.png", self.encode(original), "")
        mixed = await self.analyzer.analyze(ProviderImage("comfyui", final.image, "", w, h), original_ref)
        pf = mixed.persona_face()
        report.original_similarity = pf.similarity if pf else None
        if report.original_similarity is not None and report.original_similarity > float(cfg.checks["max_original_similarity"]):
            report.failures.append("identity_mixing")
            report.status = "FAIL"
        # PHOTO_INTEGRATION_SCORE (informativo): o pior entre luz, textura, borda e fundo preservado
        bg_score = None if report.background_changed is None else max(0.0, 1.0 - report.background_changed * 20)
        parts = [x for x in (report.lighting.get("score"), report.texture.get("score"), report.edge.get("score"), bg_score)
                 if x is not None]
        report.integration_score = round(min(parts), 3) if parts else None
        result = ReplacementResult(final, report, records, store.names(), masks.areas(), sheet.to_dict())
        result.mask_areas["transfer_region"] = round(float((region > 0.5).mean()), 5)
        result.mask_areas["hair_room"] = round(float((room > 0.5).mean()), 5)
        result.mask_areas["seam"] = round(float((seam > 0.5).mean()), 5)
        return result


__all__ = ["FACE_REFINE", "INTEGRATION", "TRANSFER", "TransferConfig", "TransferOrchestrator", "edge_ring",
           "fill_tattoos", "load_transfer_config", "plausible_accessories", "transfer_masks"]
