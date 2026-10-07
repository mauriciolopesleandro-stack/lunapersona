"""Persona Transfer: a identidade da persona na foto, sem colagem e sem mexer no resto.

  FOTO ORIGINAL -> mascara do ROSTO + mascara do CABELO -> IDENTIDADE (passada 1: rosto,
  pescoco e cabelo gerados com a LoRA e a pose da foto; refino de rosto com InstantID
  moderado so se a identidade ficar baixa) -> BRACOS / MAOS / ROUPA: geometria original
  (nao sao regenerados) -> REMOCAO DE TATUAGEM so na pele (entrada sem a tinta, denoise
  medio) -> INTEGRACAO DE BORDAS sem tocar no rosto -> RESULTADO (fora das mascaras, os
  pixels da foto; fundo travado).

Nada de grao/filtro depois.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.contracts import TransformRequest
from app.core.persona_replacement.replacement_orchestrator import ReplacementOrchestrator, ReplacementResult
from app.core.persona_replacement.rollback import Checkpoint, CheckpointStore
from app.core.persona_replacement.segmentation import MaskSet, build_masks, dilate, erode
from app.core.persona_replacement.telemetry import StageRecord, cost
from app.core.persona_replacement.validation import background_change, changed_fraction, validate
from app.providers.base import ProviderImage, ReferenceImage

TRANSFER, FACE_REFINE, TATTOO, INTEGRATION = "persona_transfer", "face_refinement", "tattoo_removal", "integration"
EARRING, TONE, RESUMED = "earring_cleanup", "tone_match", "luna_identity"


@dataclass
class TransferConfig:
    transfer: dict[str, Any]
    face_refinement: dict[str, Any]
    integration: dict[str, Any]
    tattoo_removal: dict[str, Any]
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
    if float(d["tattoo_removal"]["denoise"]) > 0.7:
        raise ValueError("remocao de tatuagem preserva a geometria: denoise <= 0,7.")
    return TransferConfig(t, d["face_refinement"], d["integration"], d["tattoo_removal"], d["checks"],
                          tuple(d.get("negative_extra", [])), float(d["budget"]["limit_usd"]), d["generation_config"],
                          d.get("version", ""), d.get("post_lighting_match", {}))


def identity_mask(masks: MaskSet, face_bbox, grow_frac: float) -> np.ndarray:
    """ROSTO + CABELO (+ pescoco): so isso recebe a identidade. Bracos, maos e roupa ficam com a
    geometria da foto. Nunca sai do contorno da pessoa (fundo travado)."""
    grow = max(2, int((face_bbox[3] - face_bbox[1]) * grow_frac))
    head = np.maximum(np.maximum(dilate(masks.face_full, grow), masks.face_transition), masks.hair)
    return np.clip(head * masks.person - masks.clothing - masks.protect, 0, 1)


def skin_tattoo_mask(masks: MaskSet, identity: np.ndarray, clothing_margin: int) -> np.ndarray:
    """Tatuagem SO na pele: longe da borda da roupa (a sombra da borda nao e tinta) e fora do
    que a passada de identidade ja gerou."""
    near_clothing = dilate((masks.clothing > 0.5).astype(np.float32), clothing_margin)
    ink = (masks.tattoos > 0.5).astype(np.float32) * (1 - near_clothing) * (1 - identity) * (1 - masks.protect)
    return np.clip(dilate(ink, 2) * np.clip(masks.skin + masks.tattoos, 0, 1) * (1 - near_clothing), 0, 1)


def luma(rgb: np.ndarray) -> np.ndarray:
    x = rgb.astype(np.float32)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


def local_mean(values: np.ndarray, known: np.ndarray, radius: int) -> np.ndarray:
    """Media local so dos pixels conhecidos (convolucao normalizada); onde nao ha nenhum, a media geral."""
    k = known.astype(np.float32)
    num, den = _box_sum(values * (k[..., None] if values.ndim == 3 else k), radius), _box_sum(k, radius)
    if values.ndim == 3:
        den = den[..., None]
    fallback = values[known].mean(axis=0) if known.any() else values.mean(axis=(0, 1))
    return np.where(den > 0.5, num / np.maximum(den, 1e-6), fallback).astype(np.float32)


def skin_region(masks: MaskSet, clothes: np.ndarray | None, identity: np.ndarray, rgb: np.ndarray, margin: int,
                reach: int, ink_delta: float) -> tuple[np.ndarray, np.ndarray]:
    """(pele visivel de bracos/maos/ombros/colo, tinta). A ROUPA vem do Florence ("clothes"): o
    que nao e roupa, nao e rosto/cabelo e nao e acessorio, mas e pele OU mais escuro que a pele
    vizinha, e pele com tinta. Assim a tatuagem nao some da mascara so porque nao "parece" pele
    (antes ela caia na roupa e ficava protegida). Sem o Florence, so a tinta detectada."""
    if clothes is None:
        ink = skin_tattoo_mask(masks, identity, margin)
        return ink, ink
    person, skin = masks.person > 0.5, masks.skin > 0.5
    covered = dilate(((clothes > 0.5) & person).astype(np.float32), margin) > 0.5
    free = person & ~covered & (identity < 0.5) & (masks.protect < 0.5)
    y = luma(rgb)
    around = local_mean(y, skin & free, reach)  # so a pele do proprio corpo (cabelo claro nao conta)
    darker = y < around - ink_delta
    near_skin = dilate(skin.astype(np.float32), reach) > 0.5
    body = free & (skin | (darker & near_skin))
    body = (dilate(body.astype(np.float32), 1) > 0.5) & free  # fecha furos de 1 px
    ink = body & (~skin | darker | (masks.tattoos > 0.5))  # + o que o Florence ja marcou como tatuagem
    return body.astype(np.float32), ink.astype(np.float32)


def ink_by_color(rgb: np.ndarray, body: np.ndarray, skin: np.ndarray, florence: np.ndarray, reach: int, edge: int,
                 dy: float, dcr: float, dy_florence: float, dcr_florence: float, close: int) -> np.ndarray:
    """Tinta = mais ESCURA e mais FRIA/cinza (menos vermelho) que a pele vizinha. Sombra, mecha de
    cabelo e dobra de dedo sao quentes (mesmo vermelho da pele) e ficam de fora. Dentro do que o
    Florence marcou como tatuagem o limiar e mais baixo (traco fino e claro). O contorno do corpo
    (borda contra o fundo) nao conta. Tracos proximos viram um desenho so (fechamento pequeno)."""
    from app.core.persona_replacement.lighting import to_ycc

    inside = erode((body > 0.5).astype(np.float32), edge) > 0.5
    known = (skin > 0.5) & inside
    if not known.any():
        return np.zeros(body.shape, np.float32)
    ycc = to_ycc(rgb)
    ly, lcr = local_mean(ycc[..., 0], known, reach), local_mean(ycc[..., 2], known, reach)
    dark, cold = ycc[..., 0] - ly, ycc[..., 2] - lcr
    flo = florence > 0.5
    ink = inside & np.where(flo, (dark < -dy_florence) & (cold < -dcr_florence), (dark < -dy) & (cold < -dcr))
    ink = erode(dilate(ink.astype(np.float32), close), close) > 0.5  # tracos proximos viram um desenho so
    ink = dilate(erode(ink.astype(np.float32), 1), 1) > 0.5  # pontinhos isolados (ruido) saem
    return (ink & inside).astype(np.float32)


def dilate_round(mask: np.ndarray, r: int) -> np.ndarray:
    """Dilatacao "redonda" (cruz e quadrado alternados = octogono): sem cantos retos nem blocos."""
    out = (mask > 0.5).astype(np.float32)
    for i in range(max(0, r)):
        if i % 2 == 0:
            p = np.pad(out, 1)
            out = np.maximum.reduce([p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        else:
            out = dilate(out, 1)
    return out


def erode_round(mask: np.ndarray, r: int) -> np.ndarray:
    return 1.0 - dilate_round(1.0 - (mask > 0.5).astype(np.float32), r)


def tattoo_zones(rgb: np.ndarray, body: np.ndarray, skin: np.ndarray, florence: np.ndarray, face_full: np.ndarray,
                 reach: int, edge: int, dy: float, dcr: float, dcr_light: float, dy_florence: float,
                 dcr_florence: float, close: int, margin: int, face_guard: int) -> np.ndarray:
    """Mascara ORGANICA em volta de cada tatuagem (nunca o braco/mao/colo inteiro): tinta escura
    (mais escura E mais fria que a pele vizinha), tinta clara (bem mais fria/acinzentada) e, onde o
    Florence marcou tatuagem, os tracos fracos. Pontinhos isolados saem, tracos vizinhos se juntam
    (fechamento redondo) e entra uma margem pequena redonda. So na pele livre (sem cabelo, top,
    acessorio) e longe do rosto."""
    from app.core.persona_replacement.lighting import to_ycc

    free = (body > 0.5) & ~(dilate(face_full, face_guard) > 0.5)
    inside = erode_round(free.astype(np.float32), edge) > 0.5
    known = (skin > 0.5) & inside
    if not known.any():
        return np.zeros(body.shape, np.float32)
    ycc = to_ycc(rgb)
    dark = ycc[..., 0] - local_mean(ycc[..., 0], known, reach)
    cold = ycc[..., 2] - local_mean(ycc[..., 2], known, reach)
    flo = florence > 0.5
    core = inside & (((dark < -dy) & (cold < -dcr)) | (cold < -dcr_light)
                     | (flo & ((dark < -dy_florence) | (cold < -dcr_florence))))
    core = dilate_round(erode_round(core.astype(np.float32), 1), 1)  # ruido de 1-2 px sai
    zone = erode_round(dilate_round(core, close), close)  # tracos vizinhos = um desenho so
    zone = dilate_round(zone, margin)  # margem pequena (residuo, halo)
    return (zone * free).astype(np.float32)


def soft_tone_match(new: np.ndarray, reference: np.ndarray, weight: np.ndarray, known: np.ndarray, radius: int) -> np.ndarray:
    """Tom/luz da pele refeita = o da pele ORIGINAL ali mesmo (pixels limpos entre os tracos e em
    volta, raio pequeno: segue a sombra do braco), so baixa frequencia, ponderado pela mascara SUAVE
    (transicao gradual, sem borda). Fora da mascara nada muda."""
    wgt = np.clip(weight, 0, 1)
    sel = wgt > 0.02
    if not sel.any() or not known.any():
        return new.copy()
    target = local_mean(reference.astype(np.float32), known, radius)
    current = local_mean(new.astype(np.float32), sel, radius)
    out = new.astype(np.float32) + (target - current) * wgt[..., None]
    return np.where(sel[..., None], out, new.astype(np.float32)).round().clip(0, 255).astype(np.uint8)


def surgical_ink_mask(masks: MaskSet, ink: np.ndarray, identity: np.ndarray, margin: int, face_guard: int,
                      clothing_guard: int, under_hair: np.ndarray | None = None) -> np.ndarray:
    """SO a tinta + uma margem pequena (nunca a regiao do corpo inteira). Inclui a tinta que o
    Florence achou sob as mechas (area do cabelo), mas nunca o rosto, o top ou os acessorios."""
    person_in = erode((masks.person > 0.5).astype(np.float32), 2) > 0.5  # o contorno do corpo nao e tinta
    hair_ink = (under_hair > 0.5) if under_hair is not None else ((masks.tattoos > 0.5) & (identity > 0.5))
    core = ((ink > 0.5) & person_in) | (hair_ink & (identity > 0.5))
    grown = dilate(core.astype(np.float32), margin) > 0.5
    keep_out = ((dilate(masks.face_full, face_guard) > 0.5) | (dilate(masks.clothing, clothing_guard) > 0.5)
                | (masks.protect > 0.5) | (masks.person < 0.5))
    return (grown & ~keep_out).astype(np.float32)


def earring_box(boxes, face_bbox):
    """O brinco original: caixa pequena (bem menor que o rosto) ao lado do rosto, da altura dos olhos
    ate um pouco abaixo do queixo."""
    x1, y1, x2, y2 = face_bbox
    fw, fh = x2 - x1, y2 - y1
    best = None
    for b in boxes:
        bw, bh = b[2] - b[0], b[3] - b[1]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        small = bw * bh <= 0.15 * fw * fh
        beside = abs(cx - (x1 + x2) / 2) >= 0.25 * fw and x1 - 0.6 * fw <= cx <= x2 + 0.6 * fw
        height = y1 + 0.3 * fh <= cy <= y2 + 0.5 * fh
        if small and beside and height and (best is None or bw * bh > (best[2] - best[0]) * (best[3] - best[1])):
            best = b
    return best


def earring_zone(shape, box, face_full: np.ndarray, identity: np.ndarray, face_guard: int) -> np.ndarray:
    """Onde a argola nova aparece: em volta e ABAIXO do brinco original, so na area do cabelo da
    Luna, nunca no rosto nem no proprio brinco (que fica com os pixels da foto)."""
    h, w = shape
    bw, bh = box[2] - box[0], box[3] - box[1]
    zone = np.zeros((h, w), bool)
    zx1, zx2 = int(max(0, box[0] - 1.2 * bw)), int(min(w, box[2] + 1.2 * bw))
    zy1, zy2 = int(max(0, box[1] - 0.5 * bh)), int(min(h, box[3] + 3.0 * bh))
    zone[zy1:zy2, zx1:zx2] = True
    own = np.zeros((h, w), bool)
    own[int(box[1]):int(box[3]) + 1, int(box[0]):int(box[2]) + 1] = True
    return (zone & ~own & (identity > 0.5) & ~(dilate(face_full, face_guard) > 0.5)).astype(np.float32)


def match_local_tone(new: np.ndarray, reference: np.ndarray, mask: np.ndarray, skin: np.ndarray, radius: int) -> np.ndarray:
    """A pele refeita assume a cor/luz da pele ORIGINAL logo em volta (so a baixa frequencia: a
    textura gerada fica). Nada fora da mascara muda."""
    m = mask > 0.5
    if not m.any():
        return new.copy()
    around = (dilate(m.astype(np.float32), radius) > 0.5) & ~m & (skin > 0.5)
    if not around.any():
        return new.copy()
    target = local_mean(reference.astype(np.float32), around, radius * 2)
    current = local_mean(new.astype(np.float32), m, radius)
    out = new.astype(np.float32)
    out[m] = out[m] + (target - current)[m]
    return out.round().clip(0, 255).astype(np.uint8)


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
    """Entrada da passada de tatuagem sem a tinta: cada pixel de tatuagem recebe a media da pele
    limpa em volta. Nao e filtro no resultado - a geracao parte de pele lisa e cria a textura."""
    ink = dilate((tattoos > 0.5).astype(np.float32), 2) > 0.5
    if not ink.any():
        return rgb.copy()
    known = (skin > 0.5) & ~ink
    near = local_mean(rgb.astype(np.float32), known, radius)  # perto: a luz/sombra local
    far = local_mean(rgb.astype(np.float32), known, radius * 4)  # miolo de tatuagem grande
    den = _box_sum(known.astype(np.float32), radius)[..., None]
    fill = np.where(den > 0.5, near, far)
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
        self._seam = None

    def _pixel_checks(self, original: np.ndarray, px: np.ndarray, masks: MaskSet) -> dict[str, Any]:
        clothing = masks.clothing if self._seam is None else masks.clothing * (1 - self._seam)
        return {"background_changed": background_change(original, px, masks.person),
                "clothing_changed": changed_fraction(original, px, clothing, threshold=10)}

    def _prompt(self, template: str, description: str) -> str:
        return template.replace("{description}", description).replace(", ,", ",")

    async def _stage(self, store: CheckpointStore, name: str, kind: str, mask: np.ndarray, prompt: str, negative: str,
                     denoise: float, strength: float, seed: int, use_lora: bool, original, masks, master, base_pose,
                     records: list[StageRecord], modified: np.ndarray, **extra) -> np.ndarray:
        current = store.current
        req = TransformRequest(image=extra.get("source") or current.image, mask=mask, prompt=prompt, negative=negative,
                               strength=strength, denoise=denoise, seed=seed, name=name, use_lora=use_lora,
                               identity_adapter=extra.get("identity_adapter"), adapter_weight=extra.get("adapter_weight"),
                               reference=master if extra.get("identity_adapter") else None, control=extra.get("control"))
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

    async def run(self, image: str, master: ReferenceImage, negative: str, seed: int,
                  start: str | None = None) -> ReplacementResult:
        """`start`: imagem ja com a identidade da Luna (resultado aprovado antes). Com ela, o rosto
        e o cabelo NAO sao gerados de novo - so as etapas locais (brinco, tatuagem) rodam."""
        cfg = self.config
        original = await self.store.load(image)
        h, w = original.shape[:2]
        sheet = await self.reader.read(ProviderImage("comfyui", image, "", w, h), master)
        raw = await self.segmenter.segment(image, sheet)
        raw = replace(raw, protect_boxes=plausible_accessories(raw.protect_boxes, sheet.target_face.bbox,
                                                               float(cfg.transfer.get("max_accessory_face_ratio", 1.0))))
        masks = build_masks(raw, sheet.target_face.bbox, sheet.target_face.kps, original)
        t, fr, tr, it = cfg.transfer, cfg.face_refinement, cfg.tattoo_removal, cfg.integration
        identity = identity_mask(masks, sheet.target_face.bbox, float(t.get("face_grow_frac", 0.12)))
        margin = max(3, int(min(h, w) * float(tr.get("clothing_margin_frac", 0.006))))
        # trava: se o Florence nao cobrir a maior parte da roupa vista na foto, nao confia nele
        # (sem ela o top escuro poderia virar "pele com tinta")
        heuristic = masks.clothing > 0.5
        coverage = (float(((raw.clothes > 0.5) & heuristic).sum()) / max(1.0, float(heuristic.sum()))
                    if raw.clothes is not None else None)
        if coverage is not None and coverage < float(tr.get("min_clothes_coverage", 0.6)):
            raw = replace(raw, clothes=None)
        if tr.get("require_clothes") and raw.clothes is None:
            # sem a roupa segmentada a pele com tatuagem nao pode ser separada do top: PARA antes de gerar
            debug = {"clothes_coverage": None if coverage is None else round(coverage, 3)}
            for name, m in (("heuristic_clothing", masks.clothing), ("tattoos", masks.tattoos)):
                debug[f"mask_{name}"] = await self.store.save((np.clip(m, 0, 1) * 255).astype(np.uint8), f"mask_{name}")
            m0 = await self._measure(image, w, h, master, None)
            report = validate(original, original, masks, masks.person, m0, cfg.checks, max(3, int(min(h, w) * 0.006)))
            report.failures, report.status = ["clothes_segmentation_failed"], "FAIL"
            return ReplacementResult(Checkpoint("original", image, original, m0), report, [], ["original"],
                                     {**masks.areas(), **debug}, sheet.to_dict())
        body, ink = skin_region(masks, raw.clothes, identity, original, margin,
                                max(6, int(min(h, w) * float(tr.get("reach_frac", 0.03)))), float(tr.get("ink_delta", 12)))
        if raw.clothes is not None:  # a roupa PROTEGIDA e a do Florence (a tinta nao conta como roupa)
            masks = replace(masks, clothing=np.clip((raw.clothes > 0.5) * masks.person - identity, 0, 1).astype(np.float32))
        if not (tr.get("enabled") and ink.any()):
            body = np.zeros((h, w), np.float32)  # sem tinta, a pele fica a da foto (nem a integracao mexe)
        ring_r = max(3, int(it.get("edge_ring", 6)))
        region = np.clip(identity + body, 0, 1)
        surgical = tr.get("mode") == "surgical"
        guard = max(4, int((sheet.target_face.bbox[3] - sheet.target_face.bbox[1]) * float(tr.get("face_guard_frac", 0.1))))
        if surgical:
            free_body = body
            ink = ink_by_color(original, free_body, masks.skin, masks.tattoos,
                               max(6, int(min(h, w) * float(tr.get("reach_frac", 0.03)))),
                               max(2, int(min(h, w) * float(tr.get("edge_frac", 0.005)))),
                               float(tr.get("ink_dy", 8)), float(tr.get("ink_dcr", 2)),
                               float(tr.get("ink_dy_florence", 5)), float(tr.get("ink_dcr_florence", 1)),
                               max(1, int(min(h, w) * float(tr.get("ink_close_frac", 0.006)))))
            # tinta sob as mechas: o mesmo teste de cor (cabelo e quente, tinta e fria) fora do rosto
            hair_zone = identity * (1 - dilate(masks.face_full, guard)) * masks.person
            hair_ink = ink_by_color(original, hair_zone, masks.skin, masks.tattoos,
                                    max(6, int(min(h, w) * float(tr.get("reach_frac", 0.03)))), 1,
                                    float(tr.get("ink_dy", 8)), float(tr.get("ink_dcr", 2)),
                                    float(tr.get("ink_dy_florence", 5)), float(tr.get("ink_dcr_florence", 1)),
                                    max(1, int(min(h, w) * float(tr.get("ink_close_frac", 0.006)))))
            # so a CONTINUACAO de uma tatuagem achada na pele (o cabelo em si tambem e "frio" na foto)
            reach_hair = max(4, int(min(h, w) * float(tr.get("hair_ink_reach_frac", 0.02))))
            hair_ink = hair_ink * (dilate(ink, reach_hair) > 0.5)
            ink = np.maximum(ink, hair_ink)
        zones = tr.get("mode") == "zones"
        soft = None
        if zones:
            px_min = min(h, w)
            frac = lambda k, d: max(1, int(px_min * float(tr.get(k, d))))  # noqa: E731
            zone = tattoo_zones(original, body, masks.skin, masks.tattoos, masks.face_full, frac("reach_frac", 0.03),
                                frac("edge_frac", 0.006), float(tr.get("ink_dy", 6)), float(tr.get("ink_dcr", 1.5)),
                                float(tr.get("ink_dcr_light", 5)), float(tr.get("ink_dy_florence", 2)),
                                float(tr.get("ink_dcr_florence", 0.5)), frac("ink_close_frac", 0.01),
                                frac("ink_margin_frac", 0.006), guard)
            ink = zone
            # borda SUAVE (transicao gradual), so dentro da pele livre: nunca no top, cabelo ou rosto
            free = (body > 0.5) & ~(dilate(masks.face_full, guard) > 0.5)
            soft = np.clip(feather(zone, frac("feather_frac", 0.008)) * free, 0, 1).astype(np.float32)
            body = soft
            region = np.clip(identity + zone, 0, 1)
        elif surgical and ink.any():
            body = surgical_ink_mask(masks, ink, identity, max(2, int(min(h, w) * float(tr.get("ink_margin_frac", 0.005)))),
                                     guard, max(2, int(min(h, w) * float(tr.get("clothing_guard_frac", 0.004)))),
                                     under_hair=hair_ink)
            region = np.clip(identity + body, 0, 1)
        ebox = earring_box(raw.protect_boxes, sheet.target_face.bbox) if tr.get("earring_cleanup") else None
        # a argola fica do lado do rosto: a protecao do brinco usa so o MIOLO do rosto (olhos/nariz/boca)
        #    e a PELE do rosto (a argola fica sobre cabelo/pescoco, nunca sobre a bochecha)
        ear_face = np.maximum(dilate(masks.face_inner, guard), masks.face_full * masks.skin) if zones else masks.face_full
        ezone = earring_zone((h, w), ebox, ear_face, identity, 2 if zones else guard) if ebox is not None else None
        saved = {}
        for name, m in (("identity", identity), ("visible_skin", body), ("ink", ink), ("clothing", masks.clothing),
                        ("earring_zone", ezone if ezone is not None else np.zeros((h, w), np.float32))):
            saved[f"mask_{name}"] = await self.store.save((np.clip(m, 0, 1) * 255).astype(np.uint8), f"mask_{name}")
        seam = np.zeros((h, w), np.float32)  # a integracao nao toca na roupa: a roupa inteira e medida
        base_pose = sheet.target_body.keypoints if sheet.target_body is not None else None
        m0 = await self._measure(image, w, h, master, base_pose)
        m0.pose = 0.0 if base_pose else None
        store = CheckpointStore()
        store.add(Checkpoint("original", image, original, m0), accept=True)
        records: list[StageRecord] = []
        modified = np.zeros((h, w), np.float32)
        neg = ", ".join(dict.fromkeys([x for x in negative.split(", ") if x] + list(cfg.negative_extra)))

        if start is not None:
            # identidade APROVADA reaproveitada: rosto e cabelo nao sao gerados de novo
            px = await self.store.load(start)
            mstart = await self._measure(start, w, h, master, base_pose)
            store.add(Checkpoint(RESUMED, start, px, mstart, self._pixel_checks(original, px, masks)), accept=True)
            for name, kind in ((TRANSFER, "identity"), (FACE_REFINE, "face")):
                records.append(StageRecord(name, kind, accepted=False, rollback_reason="reaproveitado (rosto protegido)"))
            modified = np.maximum(modified, identity)
            transferred = True
        else:
            # 1. IDENTIDADE: rosto + cabelo (+ pescoco) gerados com a LoRA e a pose da foto
            modified = await self._stage(store, TRANSFER, "identity", identity,
                                         self._prompt(t["prompt"], sheet.description()), neg, float(t["denoise"]), 1.0,
                                         seed % 2**32, True, original, masks, master, base_pose, records, modified,
                                         mask_name="face+hair")
            # sem a identidade nao ha o que refinar: refinar so o rosto da original seria face swap
            transferred = store.current.name == TRANSFER
        face_before = store.current.pixels.copy()
        if start is not None:
            pass
        elif not transferred:
            for name, kind in ((FACE_REFINE, "face"), (TATTOO, "body"), (INTEGRATION, "integration")):
                records.append(StageRecord(name, kind, accepted=False, rollback_reason="transferencia recusada"))
        elif fr.get("enabled") and (store.current.measure.face or 0) < float(fr["only_if_identity_below"]):
            modified = await self._stage(store, FACE_REFINE, "face", masks.face_full, fr["prompt"], neg,
                                         float(fr["denoise"]), float(fr["strength"]), (seed + 101) % 2**32,
                                         bool(fr.get("lora", True)), original, masks, master, base_pose, records,
                                         modified, identity_adapter=fr.get("identity_adapter"),
                                         adapter_weight=fr.get("adapter_weight"), mask_name="face_full")
        else:
            records.append(StageRecord(FACE_REFINE, "face", accepted=False,
                                       rollback_reason="nao necessario (identidade ja acima do limite)"))

        # 1b. BRINCO: a argola nova (vinda da LoRA) volta a ser cabelo; o brinco original fica intacto
        if transferred and ezone is not None and ezone.any():
            er = cfg.tattoo_removal["earring"]
            modified = await self._stage(store, EARRING, "body", ezone, er["prompt"], neg, float(er["denoise"]), 1.0,
                                         (seed + 131) % 2**32, False, original, masks, master, base_pose, records,
                                         modified, mask_name="around/below the original earring (hair only)")
        elif tr.get("earring_cleanup"):
            records.append(StageRecord(EARRING, "body", accepted=False, rollback_reason="brinco original nao localizado"))

        # 2. BRACOS / MAOS / ROUPA: geometria original (nenhuma passada os regenera)
        # 3. TATUAGEM: toda a pele visivel de bracos/maos/ombros/colo e redesenhada com a ESTRUTURA da
        #    foto original (profundidade: a tinta nao aparece nela); a entrada vai com a tinta coberta
        if transferred and tr.get("enabled") and ink.any():
            clean = fill_tattoos(store.current.pixels, ink, (masks.skin > 0.5) & (ink < 0.5),
                                 max(6, int(min(h, w) * float(tr.get("prefill_radius_frac", 0.012)))))
            source = await self.store.save(clean, "tattoo_prefill")
            modified = await self._stage(store, TATTOO, "body", body, tr["prompt"], neg, float(tr["denoise"]),
                                         float(tr["strength"]), (seed + 151) % 2**32, bool(tr.get("lora", False)),
                                         original, masks, master, base_pose, records, modified,
                                         mask_name="organic tattoo zones (soft edge)" if zones else
                                         "tattoo ink + small margin" if surgical else
                                         "visible skin (arms, hands, shoulders, chest)", source=source, control=image)
            if zones and store.current.name == TATTOO and tr.get("tone_match", True):
                # tom/luz da pele ORIGINAL ali mesmo (entre os tracos e em volta), gradual pela borda suave
                # referencia: pixels de PELE da foto original ali (os tracos de tinta nao sao "pele")
                ref_known = (masks.skin > 0.5) & (dilate(ink, max(6, int(min(h, w) * 0.02))) > 0.5)
                px = soft_tone_match(store.current.pixels, original, soft, ref_known,
                                     max(6, int(min(h, w) * float(tr.get("tone_radius_frac", 0.012)))))
                rec = StageRecord(TONE, "body", mask="organic tattoo zones (soft edge)")
                records.append(rec)
                modified = await self._try(store, TONE, "body", px, original, masks, master, base_pose, rec, soft,
                                           modified)
            elif surgical and store.current.name == TATTOO and tr.get("tone_match", True):
                # a cor/luz da pele refeita = a da pele original logo em volta (so baixa frequencia)
                before = next(c for c in reversed(store.items) if c.name != TATTOO and c is not store.current)
                px = match_local_tone(store.current.pixels, before.pixels, body, masks.skin,
                                      max(6, int(min(h, w) * float(tr.get("tone_radius_frac", 0.015)))))
                rec = StageRecord(TONE, "body", mask="tattoo ink + small margin")
                records.append(rec)
                modified = await self._try(store, TONE, "body", px, original, masks, master, base_pose, rec, body,
                                           modified)
        elif transferred:
            records.append(StageRecord(TATTOO, "body", accepted=False, rollback_reason="sem tatuagem na pele"))

        # 4. INTEGRACAO DE BORDAS sem tocar no rosto - sem LoRA
        if transferred and it.get("enabled"):
            band = edge_ring(region, ring_r) * masks.person * (1 - dilate(masks.clothing, 1))  # o top fica exato
            mask = np.clip(band * (1 - dilate(masks.face_full, 2)) - masks.protect, 0, 1)
            modified = await self._stage(store, INTEGRATION, "integration", mask, it["prompt"], neg,
                                         float(it["denoise"]), float(it["strength"]), (seed + 202) % 2**32,
                                         bool(it.get("lora", False)), original, masks, master, base_pose, records,
                                         modified, mask_name="edges-face")

        final = store.current
        face_zone = dilate(masks.face_full, 1) > 0.5
        if ezone is not None:  # a zona da argola (lado do rosto) e medida a parte
            face_zone &= ~(dilate(ezone, 2) > 0.5)
        face_changed = float((np.abs(final.pixels.astype(int) - face_before.astype(int)).max(axis=2) > 0)[face_zone].mean())
        edge_width = max(3, int(min(h, w) * 0.006))
        scored = replace(masks, clothing=masks.clothing * (1 - seam))  # a roupa e medida fora da costura
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
        result.mask_areas["identity"] = round(float((identity > 0.5).mean()), 5)
        result.mask_areas.update(saved)
        result.mask_areas["clothes_coverage"] = None if coverage is None else round(coverage, 3)
        result.mask_areas["clothes_source"] = "florence" if raw.clothes is not None else "heuristic"
        result.mask_areas["visible_skin"] = round(float((body > 0.5).mean()), 5)
        result.mask_areas["ink"] = round(float((ink > 0.5).mean()), 5)
        result.mask_areas["seam"] = round(float((seam > 0.5).mean()), 5)
        result.mask_areas["face_changed_after_identity"] = round(face_changed, 5)
        result.mask_areas["earring_box"] = list(ebox) if ebox is not None else None
        if face_changed > 0:
            report.failures.append("face_touched_after_identity")
            report.status = "FAIL"
        return result


__all__ = ["dilate_round", "erode_round", "soft_tone_match", "tattoo_zones", "EARRING", "RESUMED", "TONE", "ink_by_color", "earring_box", "earring_zone", "match_local_tone", "surgical_ink_mask",
           "luma", "local_mean", "skin_region", "FACE_REFINE", "INTEGRATION", "TATTOO", "TRANSFER", "TransferConfig", "TransferOrchestrator", "edge_ring",
           "fill_tattoos", "identity_mask", "load_transfer_config", "plausible_accessories", "skin_tattoo_mask"]
