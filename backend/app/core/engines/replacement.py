"""ReplacementEngine V2: troca a pessoa da foto pela Luna mantendo a FOTOGRAFIA (enquadramento,
pose, perspectiva, roupa, cenario, luz) e eliminando o que e da pessoa original (rosto, cabelo,
tatuagens).

  ORIGINAL -> analise da cena -> segmentacao (pessoa, roupa, acessorios, outras pessoas)
  -> PASSE 1 reconstrucao da identidade (rosto+cabelo+pescoco; LoRA + pose + profundidade da foto)
  -> PASSE 2 refino de rosto (referencia facial, so se precisar; MAX_QUALITY sempre)
  -> hi-res do rosto -> refino de corpo (transicoes) -> limpeza de tatuagem (entrada sem a tinta,
     profundidade da imagem LIMPA) -> integracao fotografica -> fora das mascaras = foto original
  -> Validation V2 por dimensao -> ACCEPT / RETRY especifico / REJECT

Cada etapa e ligada pelo StagePlan (politica FAST/QUALITY/MAX_QUALITY ou degrau A..H do benchmark).
Nada aqui conhece ComfyUI, RunPod ou nome de checkpoint: tudo passa pelo ModelAdapter e pelas portas.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from app.core.engines.adapter import ControlSpec, IdentitySpec, InpaintRequest, ModelAdapter
from app.core.engines.attributes import (
    EXCLUSION_NEGATIVE,
    PRESERVE,
    RECONSTRUCT,
    REMOVE,
    AttributePolicy,
    AttributePolicyError,
    resolve,
)
from app.core.engines.accessories import AccessoryLayer, build_layer, composite_layers, preserved_mask
from app.core.engines.attributes import ITEMS, from_structured
from app.core.engines.markings import clean_skin_reference, complete_markings, keep_inked_regions
from app.core.engines.skin import drop_small_blobs, structure_preserving_fill, tone_match
from app.core.engines.hair import clean_hair_mask
from app.core.engines.integration import integrate
from app.core.engines.policies import POLICIES, StagePlan, plan_for
from app.core.engines.quality_gate import QualityGate, measure_accessories, source_pixel_residual
from app.core.engines.scene_analysis import analyze_scene, check_hierarchy, render_pose_map, semantic_masks
from app.core.engines.retry import PRIORITY, RetryPolicyV2
from app.core.engines.telemetry import JobTelemetry
from app.core.engines.validation import (
    DEFAULT_THRESHOLDS,
    PASS,
    REJECT,
    ValidationReportV2,
    changed_fraction,
    lab_mean,
    luma,
    seam_excess,
    straight_edges,
    texture_energy,
    validate_v2,
)
from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.segmentation import MaskSet, build_masks, dilate, ellipse, erode, skin_pixels
from app.core.persona_replacement.transfer import (
    fill_tattoos,
    identity_mask,
    plausible_accessories,
    skin_region,
    soft_tone_match,
    dilate_round,
    erode_round,
    tattoo_zones,
)
from app.core.validation.geometry import point
from app.providers.base import ProviderImage, ReferenceImage

ENGINE_VERSION = "replacement-v2.2-gate"
QUALITY_PROFILES = {"fast": "FAST", "balanced": "QUALITY", "hyperrealistic": "MAX_QUALITY"}


class ReplacementRequestError(ValueError):
    pass


@dataclass
class ReplacementRequest:
    image: str  # locator da foto no provider
    persona_id: str
    master: ReferenceImage
    mode: str = "QUALITY"
    model: str = "auto"
    seed: int = 7801
    options: dict[str, bool] = field(default_factory=dict)
    advanced: dict[str, Any] = field(default_factory=dict)  # seed/steps/cfg/denoise/... (so por pedido explicito)
    negative: str = ""
    keep_intermediates: bool = False
    # spec 45.9: instrucoes explicitas por atributo (prioridade sobre a Persona Sheet e o padrao)
    preserve_attributes: list[str] = field(default_factory=list)
    remove_attributes: list[str] = field(default_factory=list)
    reconstruct_attributes: list[str] = field(default_factory=list)
    persona_sheet: dict[str, Any] | None = None  # dados da Persona Sheet (identity_exclusions, replacement_policy)
    # spec 46.10: a mesma politica em formato ESTRUTURADO (preserve: {accessories: [...]}, remove: {markings: [...]})
    structured_policy: dict[str, Any] | None = None
    # spec Master 29: atalhos do pedido (None = nao pedido; vale a Persona Sheet/padrao) e forcas por pedido
    replacement_version: str = "v2"
    pose_required: bool | None = None
    clothing_required: bool | None = None
    accessories_required: bool | None = None
    remove_tattoos: bool | None = None
    quality_profile: str | None = None  # fast | balanced | hyperrealistic (substitui `mode` quando vem)
    identity_strength: float | None = None  # InstantID
    pose_strength: float | None = None
    depth_strength: float | None = None
    debug: bool = False  # REPLACEMENT_DEBUG: mascaras, mapa de pose, cada passe e o relatorio
    qwen_face_lock: bool | None = None  # Qwen BFS: so experimental, so por pedido explicito (ou config qwen.enabled)

    OPTIONS =("preserve_pose", "preserve_clothes", "preserve_background", "preserve_lighting", "remove_original_tattoos",
               "identity_lock", "body_lock", "skin_realism", "photographic_integration")
    ADVANCED = ("steps", "cfg", "identity_denoise", "face_denoise", "face_reference_strength", "identity_strength",
                "pose_strength", "depth_strength", "tattoo_denoise", "body_denoise", "max_retries")

    def validate(self) -> None:
        bad = [k for k in self.options if k not in self.OPTIONS]
        if bad:
            raise ReplacementRequestError(f"opcoes desconhecidas: {bad}")
        bad = [k for k in self.advanced if k not in self.ADVANCED]
        if bad:
            raise ReplacementRequestError(f"parametros avancados desconhecidos: {bad}")
        if not self.image or not self.persona_id:
            raise ReplacementRequestError("imagem e persona sao obrigatorias")
        if self.replacement_version != "v2":
            raise ReplacementRequestError(f"esta engine e a V2; replacement_version '{self.replacement_version}' roda em "
                                          "outro orquestrador (V1: core/persona_replacement)")
        if self.quality_profile is not None and self.quality_profile not in QUALITY_PROFILES:
            raise ReplacementRequestError(f"quality_profile invalido: {self.quality_profile} (use {sorted(QUALITY_PROFILES)})")
        for nome, val, top in (("identity_strength", self.identity_strength, 0.8), ("pose_strength", self.pose_strength, 1.0),
                               ("depth_strength", self.depth_strength, 1.0)):
            if val is not None and not 0.0 <= float(val) <= top:
                raise ReplacementRequestError(f"{nome} fora de 0..{top}")

    @property
    def effective_mode(self) -> str:
        return QUALITY_PROFILES[self.quality_profile] if self.quality_profile else self.mode

    def attributes(self) -> AttributePolicy:
        """Politica de atributos resolvida: padrao < Persona Sheet < pedido. As opcoes antigas viram atributos."""
        preserve, remove = list(self.preserve_attributes), list(self.remove_attributes)
        reconstruct = list(self.reconstruct_attributes)
        # atalhos (spec Master 29): so o que foi pedido de verdade entra (None = fica a ficha/padrao)
        for flag, attrs_ in ((self.pose_required, ["pose"]), (self.clothing_required, ["clothing"]),
                             (self.accessories_required, ["accessories", "jewelry"])):
            if flag is True:
                preserve += [a for a in attrs_ if a not in preserve]
        if self.clothing_required is False and "clothing" not in preserve:
            reconstruct.append("clothing")
        if self.accessories_required is False:
            remove += [a for a in ("accessories", "jewelry") if a not in preserve]
        if self.remove_tattoos is True and "tattoos" not in remove:
            remove.append("tattoos")
        elif self.remove_tattoos is False and "tattoos" not in preserve:
            preserve.append("tattoos")
        if self.structured_policy:
            try:
                sp, sr, sc = from_structured(self.structured_policy)
            except AttributePolicyError as exc:
                raise ReplacementRequestError(str(exc)) from exc
            preserve, remove, reconstruct = preserve + sp, remove + sr, reconstruct + sc
        o = {k: bool(v) for k, v in self.options.items()}
        if o.get("remove_original_tattoos") is False and "tattoos" not in remove:
            preserve.append("tattoos")
        try:
            return resolve(self.persona_sheet, preserve, remove, reconstruct)
        except AttributePolicyError as exc:
            raise ReplacementRequestError(str(exc)) from exc

    def plan(self, attrs: AttributePolicy | None = None) -> StagePlan:
        o = {k: bool(v) for k, v in self.options.items()}
        attrs = attrs or self.attributes()
        over: dict[str, Any] = dict(self.advanced)
        if "identity_strength" in over:  # nome da spec para a forca do InstantID
            over["face_reference_strength"] = over.pop("identity_strength")
        for key, val in (("face_reference_strength", self.identity_strength), ("pose_strength", self.pose_strength),
                         ("depth_strength", self.depth_strength)):
            if val is not None:
                over[key] = float(val)
        mode = self.effective_mode
        if mode in ("FAST", "QUALITY", "MAX_QUALITY"):  # a escada A..H do benchmark fica como esta
            over.setdefault("tattoo_cleanup", attrs.removes_skin_markings())
            if attrs.is_("body", RECONSTRUCT):
                # corpo da Persona: roupa RECONSTRUCT = corpo+roupa redesenhados; roupa PRESERVE (spec 46) = so a pele
                # visivel (bracos, pernas, barriga, pescoco) com a anatomia da Persona, roupa pixel a pixel da foto
                over.setdefault("body_identity", True)
                over.setdefault("body_refinement", False)
            if attrs.is_("hands", RECONSTRUCT):
                over.setdefault("hand_pose_lock", True)
        if not attrs.removes_skin_markings():  # tudo PRESERVE: nada de limpeza, em qualquer modo
            over["tattoo_cleanup"] = False
        if "preserve_pose" in o:
            over["pose"] = o["preserve_pose"]
        if "remove_original_tattoos" in o:
            over["tattoo_cleanup"] = o["remove_original_tattoos"]
        if "photographic_integration" in o:
            over["photographic_integration"] = o["photographic_integration"]
        if "identity_lock" in o:
            over["face_reference"] = o["identity_lock"]
        if "body_lock" in o:
            over["body_refinement"] = o["body_lock"]
        if o.get("preserve_background") is False or o.get("preserve_clothes") is False:
            raise ReplacementRequestError("o Replacement sempre preserva fundo e roupa (nao ha modo que os regenere)")
        return plan_for(mode, over)

    def explicit(self, key: str) -> bool:
        """O pedido fixou este parametro do plano? (entao a config dedicada nao o sobrescreve)"""
        alias = {"face_reference_strength": ("identity_strength",)}
        own = {"face_reference_strength": self.identity_strength, "pose_strength": self.pose_strength,
               "depth_strength": self.depth_strength}
        return key in self.advanced or any(a in self.advanced for a in alias.get(key, ())) or own.get(key) is not None


@dataclass
class ReplacementOutcome:
    image: str
    pixels: np.ndarray
    status: str
    report: ValidationReportV2
    measures: dict[str, Any]
    telemetry: JobTelemetry
    intermediates: dict[str, str] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    mask_areas: dict[str, float] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)  # REPLACEMENT_DEBUG: analise, relatorio, registro

    def to_dict(self) -> dict[str, Any]:
        return {"image": self.image, "status": self.status, "validation": self.report.to_dict(), "measures": self.measures,
                "telemetry": self.telemetry.to_dict(), "intermediates": self.intermediates, "attempts": self.attempts,
                "mask_areas": self.mask_areas, "gate": self.telemetry.gate, "debug": self.debug}


@dataclass
class _Scene:
    original: np.ndarray
    sheet: Any
    masks: MaskSet | None
    identity: np.ndarray
    face_full: np.ndarray
    body: np.ndarray | None
    ink_zone: np.ndarray | None
    raw_tattoos: np.ndarray | None
    base_pose: Any
    clothes_ok: bool
    # oculos/acessorios do rosto: pixels ORIGINAIS colados por cima depois de cada passe (nao viram buraco
    # na mascara - no reteste da varanda o buraco partiu o rosto em dois e o modelo gerou um rosto escuro)
    accessory: np.ndarray | None = None
    # spec 45.4: mascaras da pessoa ORIGINAL (a de marcas tem prioridade de remocao)
    markings: np.ndarray | None = None
    source_masks: dict[str, np.ndarray] = field(default_factory=dict)
    box_policy: list[dict[str, Any]] = field(default_factory=list)  # cada caixa detectada -> classe e politica
    jewelry_body: np.ndarray | None = None  # joias REMOVE fora da cabeca (preenchimento cheio na reconstrucao)
    layers: list[AccessoryLayer] = field(default_factory=list)  # spec 46.2: um objeto = uma camada (mascara/ordem/politica)
    # maos do DWPose na foto ORIGINAL (21 pontos cada): definem o tamanho da mascara da mao e se o gesto e medivel
    hands: list = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)  # sanidade das mascaras (cabelo refeito, roupa nao segmentada)


JEWELRY_WORDS = ("earring", "bracelet", "necklace", "ring", "jewel", "brinco", "pulseira", "colar")


def _drop_below_ankles(mask: np.ndarray, pose) -> np.ndarray:
    """Abaixo dos dois tornozelos (DWPose) e calcado: nao entra como marca. Sem tornozelo detectado: nada muda."""
    from app.core.validation.geometry import point

    if not pose:
        return mask
    ankles = [p for p in (point(pose, "rank"), point(pose, "lank")) if p]
    if not ankles:
        return mask
    y_cut = int(max(a[1] for a in ankles))
    out = mask.copy()
    out[y_cut:, :] = 0
    return out


def _jewelry_plausible(label: str, box, face_bbox, pose) -> bool:
    """Joia so onde joia fica: pulseira/anel perto de um pulso (DWPose), brinco/colar perto da cabeca/pescoco.
    Sem esqueleto: aceita (nao inventa regra sem medida)."""
    from app.core.validation.geometry import point

    bx1, by1, bx2, by2 = box
    cx, cy = (bx1 + bx2) / 2, (by1 + by2) / 2
    fx1, fy1, fx2, fy2 = face_bbox
    fh = max(1.0, fy2 - fy1)
    lab = (label or "").lower()
    if any(w in lab for w in ("earring", "necklace", "brinco", "colar")):
        return fy1 - fh * 0.5 <= cy <= fy2 + fh * 1.8 and fx1 - fh * 1.5 <= cx <= fx2 + fh * 1.5
    if not pose:
        return True
    wrists = [p for p in (point(pose, "rwri"), point(pose, "lwri")) if p]
    if not wrists:
        return True
    reach = max(bx2 - bx1, by2 - by1, fh * 0.6)
    return any(abs(cx - wx) <= reach and abs(cy - wy) <= reach for wx, wy in wrists)


def box_class(label: str) -> str:
    """Rotulo do detector -> atributo. Sem rotulo (detector antigo) = acessorio (conservador: mantido)."""
    lab = (label or "").lower()
    return "jewelry" if any(w in lab for w in JEWELRY_WORDS) else "accessories"


class ReplacementEngine:
    def __init__(self, *, reader, segmenter, analyzer, store, adapter: ModelAdapter, config: dict[str, Any],
                 lora_hash: str = "", price_per_hour: float | None = None, provider: str = "", face_lock=None) -> None:
        self.reader, self.segmenter, self.analyzer, self.store = reader, segmenter, analyzer, store
        self.adapter = adapter
        # Face Lock da master (o mesmo da geracao V1: Qwen-Image-Edit 2511 + BFS head). Porta opcional:
        # async lock_face(ProviderImage, ReferenceImage, seed) -> StageOutput. Sem ela, o passo nao roda.
        self.face_lock = face_lock
        self.cfg = config
        self.lora_hash = lora_hash
        self.price = price_per_hour
        self.provider = provider

    @classmethod
    def load_config(cls, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    # --- cena e mascaras -----------------------------------------------------------------------
    async def _scene(self, image: str, master: ReferenceImage, plan: StagePlan, attrs: AttributePolicy | None = None) -> _Scene:
        attrs = attrs or resolve()
        original = await self.store.load(image)
        h, w = original.shape[:2]
        sheet = await self.reader.read(ProviderImage("comfyui", image, "", w, h), master)
        if sheet.target_face is None:
            raise ReplacementRequestError("nenhuma pessoa/rosto encontrado na foto")
        bbox = sheet.target_face.bbox
        base_pose = sheet.target_body.keypoints if sheet.target_body is not None else None
        sg, tt = self.cfg["segmentation"], self.cfg["tattoo"]
        if not plan.segmentation:
            # sem segmentacao: elipse grosseira de cabeca+cabelo (degraus A..D do benchmark)
            x1, y1, x2, y2 = bbox
            fw, fh = x2 - x1, y2 - y1
            head = ellipse(h, w, (x1 + x2) / 2, y1 + fh * 0.42, fw * 0.95, fh * 0.95)
            neck = ellipse(h, w, (x1 + x2) / 2, y2 + fh * 0.25, fw * 0.42, fh * 0.42)
            ident = np.clip(head + neck, 0, 1)
            face_full = ellipse(h, w, (x1 + x2) / 2, (y1 + y2) / 2 + fh * 0.06, fw * 0.55, fh * 0.62)
            return _Scene(original, sheet, None, ident, face_full, None, None, None, base_pose, False,
                          source_masks={"source_identity_mask": ident, "source_face_mask": face_full})
        raw = await self.segmenter.segment(image, sheet)
        # cada caixa (oculos, brinco...) segue a politica do SEU atributo: PRESERVE = protegida e colada de volta;
        # REMOVE = nao e protegida (no rosto: regenerada; no corpo: entra na mascara de marcas)
        labels = list(getattr(raw, "protect_labels", []) or [])
        labels += [""] * (len(raw.protect_boxes) - len(labels))
        plausible = set(plausible_accessories(raw.protect_boxes, bbox, float(sg["max_accessory_face_ratio"])))
        keep_boxes, drop_boxes, box_policy = [], [], []
        preserved = {b for b, lab in zip(raw.protect_boxes, labels) if attrs.item_policy(lab)[1] == PRESERVE}
        kept_labels: dict[tuple, str] = {}
        for b, lab in zip(raw.protect_boxes, labels):
            if b not in plausible:
                continue
            item, pol = attrs.item_policy(lab)
            cls = ITEMS[item] if item else box_class(lab)
            note = ""
            if cls == "jewelry" and b not in {x for x, y in zip(raw.protect_boxes, labels) if box_class(y) != "jewelry"} \
                    and not _jewelry_plausible(lab, b, bbox, base_pose):
                pol, note = "IGNORE", "rotulo de joia longe do pulso/cabeca no esqueleto: ignorado"
            elif pol != PRESERVE and b in preserved:  # mesma caixa com rotulo de manter (espelho: mao+celular = brinco E relogio)
                pol, note = PRESERVE, "mesma caixa tambem rotulada para manter: manter vence"
            elif pol != PRESERVE and cls == "jewelry" and not _jewelry_plausible(lab, b, bbox, base_pose):
                # espelho: os chinelos viraram "pulseira". Pulseira so perto do PULSO, brinco/colar perto da cabeca/pescoco
                pol, note = "IGNORE", "rotulo de joia longe do pulso/cabeca no esqueleto: ignorado"
            box_policy.append({"box": [round(v, 1) for v in b], "label": lab, "item": item, "attribute": cls, "policy": pol,
                               "note": note})
            if pol == PRESERVE:
                keep_boxes.append(b)
                kept_labels.setdefault(b, lab)
            elif pol == REMOVE:
                drop_boxes.append(b)
        raw = replace(raw, protect_boxes=keep_boxes, protect_labels=[])
        masks = build_masks(raw, bbox, sheet.target_face.kps, original)
        hair_clean, hair_info = clean_hair_mask(original, masks.hair, bbox, masks.face_full,
                                                float(sg.get("max_hair_face_ratio", 3.0)))
        if hair_info.get("cleaned"):
            masks = replace(masks, hair=hair_clean)
        ident = identity_mask(masks, bbox, float(sg["face_grow_frac"]))
        fx1, fy1, fx2, fy2 = bbox
        near_head = np.zeros((h, w), np.float32)
        gy, gx = (fy2 - fy1) * 0.8, (fx2 - fx1) * 0.6
        near_head[max(0, int(fy1 - gy)):int(fy2 + gy) + 1, max(0, int(fx1 - gx)):int(fx2 + gx) + 1] = 1
        head_jewelry = np.zeros((h, w), np.float32)
        for bx1, by1, bx2, by2 in drop_boxes:
            jb = np.zeros((h, w), np.float32)
            jb[max(0, int(by1)):int(by2) + 1, max(0, int(bx1)):int(bx2) + 1] = 1
            if (jb * near_head).sum() > 0.5 * jb.sum():  # brinco/colar junto da cabeca: o passe de identidade refaz
                head_jewelry = np.maximum(head_jewelry, dilate(jb, 2))
        if head_jewelry.any():
            ident = np.clip(ident + head_jewelry * (1 - masks.clothing) * (1 - masks.protect), 0, 1)
        if attrs.is_("hair", RECONSTRUCT) and masks.hair.any():
            # porta (2026-10-07): o cacheado original passava do cabelo novo da Luna e sobrava atras do ombro
            band = max(3, int(min(h, w) * float(sg.get("hair_band_frac", 0.02))))
            ident = np.clip(ident + dilate((masks.hair > 0.5).astype(np.float32), band) * (1 - masks.clothing)
                            * (1 - masks.protect), 0, 1)
        if attrs.is_("hair", PRESERVE):  # cabelo da foto fica: so rosto/pescoco recebem a identidade
            ident = np.clip(ident * (1 - dilate((masks.hair > 0.5).astype(np.float32), 2)) + masks.face_full * masks.person, 0, 1)
        accessory = None
        layers: list[AccessoryLayer] = []
        if raw.protect_boxes:
            # spec 46.2/46.3: cada objeto mantido vira camada (oculos de lente clara: so a armacao)
            for b in raw.protect_boxes:
                lab = kept_labels.get(b, "")
                layers.append(build_layer(original, b, lab, attrs.item_policy(lab)[0], PRESERVE))
            layers = [x for x in layers if (x.mask > 0.5).sum() > 20 or x.lens is not None]
            accessory = preserved_mask(layers)
            # a mascara de geracao cobre o acessorio (rosto inteiro e coerente); ele volta colado depois
            fx1, fy1, fx2, fy2 = bbox
            gx, gy = (fx2 - fx1) * 0.25, (fy2 - fy1) * 0.25
            head_box = np.zeros((h, w), np.float32)
            head_box[max(0, int(fy1 - gy)):int(fy2 + gy) + 1, max(0, int(fx1 - gx)):int(fx2 + gx) + 1] = 1
            on_head = masks.protect * masks.person * np.maximum(head_box, dilate(np.maximum(masks.face_full, masks.hair), 6))
            ident = np.clip(ident + on_head, 0, 1)
            masks = replace(masks, face_full=np.clip(masks.face_full + on_head, 0, 1))
        heuristic = masks.clothing > 0.5
        cov = (float(((raw.clothes > 0.5) & heuristic).sum()) / max(1.0, float(heuristic.sum()))
               if getattr(raw, "clothes", None) is not None else 0.0)
        clothes_ok = cov >= float(sg["min_clothes_coverage"])
        px_min = min(h, w)
        body = ink = None
        if clothes_ok:
            body, _ = skin_region(masks, raw.clothes, ident, original, max(3, int(px_min * 0.006)),
                                  max(6, int(px_min * float(tt["reach_frac"]))), 12.0)
            masks = replace(masks, clothing=np.clip((raw.clothes > 0.5) * masks.person - ident, 0, 1).astype(np.float32))
            guard = max(4, int((bbox[3] - bbox[1]) * float(tt["face_guard_frac"])))
            f = lambda k: max(1, int(px_min * float(tt[k])))  # noqa: E731
            ink = tattoo_zones(original, body, masks.skin, masks.tattoos, masks.face_full, f("reach_frac"), f("edge_frac"),
                               float(tt["ink_dy"]), float(tt["ink_dcr"]), float(tt["ink_dcr_light"]),
                               float(tt["ink_dy_florence"]), float(tt["ink_dcr_florence"]), f("close_frac"), f("margin_frac"), guard)
            if ink is not None and ink.any():  # so regiao com MIOLO de tinta (borda do braco contra madeira nao e tatuagem)
                ink = keep_inked_regions(ink, original, clean_skin_reference(original, ink, masks.person))
                ink = _drop_below_ankles(ink, base_pose)  # calcado (espelho: chinelos cinza) nao e tatuagem
        # mascara de MARCAS da pessoa original (spec 45.4/45.6): tinta/marcas na pele + joias REMOVE fora do rosto;
        # buracos fechados e margem em volta (evita contorno fantasma); nunca roupa, nem acessorio mantido
        markings = None
        jewelry_body = np.zeros((h, w), np.float32)
        for bx1, by1, bx2, by2 in drop_boxes:
            jb = np.zeros((h, w), np.float32)
            jb[max(0, int(by1)):int(by2) + 1, max(0, int(bx1)):int(bx2) + 1] = 1
            jewelry_body = np.maximum(jewelry_body, jb * (1 - (ident > 0.5)))
        if attrs.removes_skin_markings() or drop_boxes:
            m = np.zeros((h, w), np.float32) if ink is None or not attrs.removes_skin_markings() else ink.astype(np.float32)
            for bx1, by1, bx2, by2 in drop_boxes:
                jb = np.zeros((h, w), np.float32)
                jb[max(0, int(by1)):int(by2) + 1, max(0, int(bx1)):int(bx2) + 1] = 1
                m = np.maximum(m, jb * (1 - (ident > 0.5)))  # no rosto/cabelo a passada de identidade ja regenera
            if m.any():
                close = max(2, int(px_min * float(tt.get("close_frac", 0.01))))
                margin = max(2, int(px_min * float(tt.get("mask_margin_frac", 0.008))))
                # pintinha/poro isolado nao e marca (viravam remendos quadrados no teste de 2026-10-07)
                m = drop_small_blobs(m, max(2, int(px_min * float(tt.get("min_blob_frac", 0.003)))))
                # completa o pedaco da tatuagem que encosta no top/borda (o detector so ve "buraco na pele"):
                # cresce so por vizinho com cara de tinta (cinza quente, menos saturado e mais escuro que a pele)
                if m.any() and attrs.removes_skin_markings():
                    allowed = masks.person * (1 - masks.clothing) * (1 - masks.protect) * (1 - (ident > 0.5))                         * (1 - (dilate_round(masks.hair, 2) > 0.5))
                    m = complete_markings(original, m, clean_skin_reference(original, m, masks.person), allowed,
                                          max_grow=max(4, int(px_min * float(tt.get("complete_grow_frac", 0.03)))))
                # dilatacao REDONDA (a quadrada fazia cantos retos que apareciam na pele)
                m = erode_round(dilate_round(m, close), close)  # fecha buracos (tinta vista pelos furos no reteste)
                m = dilate_round(m, margin)
                m = m * masks.person * (1 - masks.clothing) * (1 - masks.protect)
                markings = np.clip(m, 0, 1) if m.any() else None
        source = {"source_identity_mask": ident, "source_face_mask": masks.face_full, "source_hair_mask": masks.hair,
                  "source_body_mask": np.clip(masks.person * (1 - masks.clothing) * (1 - ident), 0, 1)}
        if markings is not None:
            source["source_markings_mask"] = markings
        notes = {"hair": hair_info}
        if not clothes_ok:
            notes["clothing"] = ("roupa nao segmentada pelo detector: com a roupa PRESERVE o corpo/roupa da foto ficam "
                                 "(sem como separar pele de roupa)")
        return _Scene(original, sheet, masks, ident, masks.face_full, body, ink, raw.tattoos, base_pose, clothes_ok, accessory,
                      markings, source, box_policy, jewelry_body if jewelry_body.any() else None, layers, notes=notes)

    # --- uma passada ---------------------------------------------------------------------------
    async def _pass(self, name: str, cur: dict, mask: np.ndarray, prompt: str, negative: str, denoise: float, seed: int,
                    plan: StagePlan, tel: JobTelemetry, *, controls: ControlSpec | None = None, identity: IdentitySpec | None = None,
                    image: str | None = None, work_side: int | None = None,
                    accessory=None) -> tuple[np.ndarray, str, float]:
        req = InpaintRequest(image=image or cur["image"], mask=mask, prompt=prompt, negative=negative, denoise=denoise,
                             seed=seed % 2**32, stage=name, controls=controls or ControlSpec(),
                             identity=identity or IdentitySpec(), steps=plan.steps, cfg=plan.cfg,
                             work_side=work_side or plan.work_side)
        res = await self.adapter.inpaint(req)
        px = await self.store.load(res.image)
        keep = mask > 0.02  # fora da mascara: nada vem do modelo
        px = np.where(keep[..., None], px, cur["pixels"])
        if accessory is not None:  # acessorios mantidos por cima, na ordem de oclusao (so onde a passada mexeu)
            px = accessory(px, keep)
        loc = await self.store.save(px, name)
        tel.add_pass(name, res.seconds, res.parameters, True)
        return px, loc, res.seconds

    def _conditioning(self, attrs: AttributePolicy, base_negative: str) -> tuple[Any, str]:
        """Spec 45.3/45.9: a politica entra no PROMPT (camada positiva de exclusao + o que preservar) e no NEGATIVO
        (marcas da pessoa original). Atributo PRESERVE tira do negativo o que o contradiz (ex.: maquiagem pedida)."""
        drop = {t for a, ts in EXCLUSION_NEGATIVE.items() if attrs.is_(a, PRESERVE) for t in ts}
        if attrs.is_("makeup", PRESERVE):
            drop |= {"heavy makeup", "contour makeup", "false eyelashes", "glossy lipstick"}
        terms = [t for t in base_negative.split(", ") if t] + list(self.cfg["negative"]) + attrs.negative_conditioning()
        negative = ", ".join(t for t in dict.fromkeys(terms) if t not in drop)
        positive = attrs.positive_conditioning()
        face_terms = [t for t in positive if t.startswith(("same facial", "same makeup", "no makeup", "keep the"))]
        skin_terms = [t for t in positive if t not in face_terms]
        if attrs.is_("expression", RECONSTRUCT):
            face_terms.append("confident look, subtle closed-lip half smile")

        def prompt(base: str, kind: str) -> str:
            text = base
            if attrs.is_("makeup", PRESERVE):
                text = text.replace(", no makeup", "").replace("no makeup, ", "")
            extra = face_terms if kind == "face" else skin_terms if kind == "skin" else face_terms + skin_terms[:3]
            return ", ".join(dict.fromkeys([text] + [t for t in extra if t not in text]))

        return prompt, negative

    async def _identity_of(self, locator: str, w: int, h: int, master: ReferenceImage):
        return await self.analyzer.analyze(ProviderImage("comfyui", locator, "", w, h), master)

    # --- execucao de um plano ------------------------------------------------------------------
    async def _attempt(self, req: ReplacementRequest, scene: _Scene, plan: StagePlan, tel: JobTelemetry,
                       attrs: AttributePolicy | None = None) -> tuple[np.ndarray, str, dict, dict]:
        orig, h, w = scene.original, scene.original.shape[0], scene.original.shape[1]
        P = self.cfg["prompts"]
        attrs = attrs or resolve()
        cond, negative = self._conditioning(attrs, req.negative)
        cur = {"pixels": orig.copy(), "image": req.image}
        modified = np.zeros((h, w), np.float32)
        inter: dict[str, str] = {}
        drop = float(self.cfg["acceptance"]["max_identity_drop"])
        structure = ControlSpec(pose_strength=plan.pose_strength if plan.pose else 0.0,
                                depth_strength=plan.depth_strength if plan.depth else 0.0,
                                end_percent=plan.control_end, structure=req.image)
        ident = scene.identity
        shrink = int(plan.extra.get("mask_shrink", 0))
        if shrink:
            ident = erode(ident, 4 * shrink)
        grow_n = int(plan.extra.get("identity_grow", 0))
        if grow_n and scene.masks is not None:  # retry de residuo da pessoa original: mascara da identidade maior
            g = max(3, int(min(h, w) * 0.012)) * grow_n
            ident = np.clip(dilate(ident, g) * (scene.masks.person > 0.5) * (1 - (scene.masks.clothing > 0.5))
                            * (1 - (scene.masks.protect > 0.5)) + ident, 0, 1)
        # PASSE 1: reconstrucao da identidade
        # sem cabelo/rosto da pessoa ORIGINAL e sem o CENARIO no texto: o cenario ja esta nos pixels da foto. Varanda
        # 2026-10-08: "Copacabana" no texto fez o modelo pintar um morro onde ficava o coque loiro da pessoa original
        description = person_text(scrub_identity(scene.sheet.description()))
        # CORPO DA PERSONA (antes do rosto): corpo e roupa redesenhados na pose da foto, com a LoRA e o corpo da
        # Persona Sheet. Sem profundidade (ela prende o formato do corpo ORIGINAL). Maos e calcado ficam da foto.
        body_region = None
        if plan.body_identity and scene.masks is not None and not scene.clothes_ok and not attrs.is_("clothing", RECONSTRUCT):
            # espelho 2026-10-08: sem a roupa segmentada o top branco foi redesenhado preto. Roupa PRESERVE sem mascara
            # de roupa = corpo nao e refeito (fica registrado; a validacao avisa que a roupa nao foi conferida)
            tel.add_pass("body_identity", 0.0, {}, False, scene.notes.get("clothing"))
        elif plan.body_identity and scene.masks is not None:
            body_region = self._body_region(scene, ident, plan, keep_clothing=not attrs.is_("clothing", RECONSTRUCT),
                                            keep_hands=True)
            if body_region is not None and body_region.sum() > 200:
                body_txt = ((req.persona_sheet or {}).get("identity_attributes") or {}).get("body", {}).get("value", "")
                bprompt = cond(P.get("body_identity", P["identity"]).replace("{description}", description)
                               .replace("{body}", body_text_en(body_txt)), "skin")
                bctrl = ControlSpec(pose_strength=plan.pose_strength if plan.pose else 0.0, depth_strength=0.0,
                                    end_percent=plan.control_end, structure=req.image)
                bneg = negative
                if not attrs.is_("clothing", RECONSTRUCT):  # porta 2026-10-08: alca, calcinha e marca inventadas na pele
                    bneg = ", ".join(dict.fromkeys([t for t in negative.split(", ") if t] + list(self.cfg.get("body_skin_negative", []))))
                pxb, locb, _ = await self._pass("body_identity", cur, body_region, bprompt, bneg, plan.body_identity_denoise,
                                                req.seed + 41, plan, tel, controls=bctrl, accessory=None)
                pxb2 = restore_background(pxb, orig, scene.masks.person, body_region)
                band = skin_band(scene.masks, body_region, ident)
                if band is not None:  # espelho 2026-10-08: faixa de pele clara (a ORIGINAL) na barra do short
                    known = ((body_region > 0.5) & (skin_pixels(pxb2) > 0.5)).astype(np.float32)
                    pxb2 = tone_match(pxb2, pxb2, band, known, radius=max(3, int(min(h, w) * 0.01)), max_shift=45.0)
                    body_region = np.maximum(body_region, band)
                if pxb2 is not pxb:
                    pxb, locb = pxb2, await self.store.save(pxb2, "body_identity_fix")
                cur, modified = {"pixels": pxb, "image": locb}, np.maximum(modified, body_region)
                inter["body_identity"] = locb
                tel.passes[-1]["params"]["body_text"] = body_text_en(body_txt)
            else:
                body_region = None
        prompt = cond(P["identity"].replace("{description}", description), "identity")
        layers = scene.layers
        if plan.extra.get("accessory_grow"):  # retry de acessorio: objeto um pouco maior ao recolocar
            g = int(plan.extra["accessory_grow"])
            layers = [replace(x, mask=dilate(x.mask, g)) for x in scene.layers]
        paste = None if not layers else (lambda px_, within=None: composite_layers(px_, orig, layers, within))
        # MAO com POSE TRAVADA (spec 46.4): gesto/posicao/relacao com objetos da foto, anatomia e pele da Persona.
        # Entrada sem tatuagem (fechamento/push-pull) quando houver tinta na mao; estrutura da mao original.
        if plan.hand_pose_lock and scene.masks is not None:
            # quarto 2026-10-08: punho fechado segurando o top (o DWPose nao ve os dedos) foi refeito sem medida e virou
            # uma mao branca "fantasma"; a limpeza de tatuagem em cima dela fez uma mancha laranja. Mao sem gesto medivel
            # fica da foto (a tinta sai na limpeza de pele, que preserva os dedos).
            hmask = self._hand_mask(scene, measurable_only=True)
            if hmask is None and self._hand_mask(scene) is not None:
                tel.add_pass("hand_gesture_lock", 0.0, {}, False,
                             "gesto da mao nao medivel na foto (DWPose sem dedos): mao mantida, tinta sai na limpeza de pele")
            if hmask is not None:
                hmask = hmask * dilate((scene.masks.person > 0.5).astype(np.float32), 4)
                hand = await self._hand_stage(req, scene, plan, tel, cur, hmask, negative, cond, paste, inter, w, h)
                if hand is not None:
                    cur, modified = hand, np.maximum(modified, hmask)
        # rosto/cabelo: SO a pose da foto (posicao da cabeca). A profundidade da foto trazia o formato do rosto e dos
        # cachos da pessoa ORIGINAL (2026-10-07: "o cabelo ficou diferente da Luna"). Profundidade fica no corpo.
        ident_ctrl = ControlSpec(pose_strength=structure.pose_strength,
                                 depth_strength=structure.depth_strength if plan.extra.get("identity_depth", False) else 0.0,
                                 end_percent=structure.end_percent, structure=structure.structure)
        px, loc, _ = await self._pass("identity", cur, ident, prompt, negative, plan.identity_denoise, req.seed, plan, tel,
                                      controls=ident_ctrl, accessory=paste)
        if scene.masks is not None:  # espelho 2026-10-08: halo claro na parede em volta do cabelo original
            px2 = restore_background(px, orig, scene.masks.person, ident)
            if px2 is not px:
                px, loc = px2, await self.store.save(px2, "identity_bg")
        cur, modified = {"pixels": px, "image": loc}, np.maximum(modified, ident)
        inter["identity"] = loc
        an = await self._identity_of(loc, w, h, req.master)
        face = an.persona_face()
        score = face.similarity if face else None
        tel.passes[-1].update(identity=score)
        acc = self.cfg["acceptance"]
        seam_tol, edge_tol = float(acc.get("stage_seam_tolerance", 0.8)), float(acc.get("stage_edges_tolerance", 1.5))

        # borda contra o fundo nao e emenda (o cabelo reconstruido muda de cor contra o ceu/parede de proposito)
        bg_edge = None if scene.masks is None else (dilate((scene.masks.person < 0.5).astype(np.float32), 3) > 0.5)

        # bloco = remendo em PELE reconstruida fora da identidade; rosto/cabelo/pescoco novos tem tracos e cachos novos
        new_features = dilate(np.maximum(scene.identity, scene.face_full if scene.masks is None else
                                         np.maximum(scene.face_full, scene.masks.hair)).astype(np.float32), 4) > 0.5
        if body_region is not None:  # roupa/corpo redesenhados tem linhas novas (barra, cordao) - nao sao remendos
            new_features = new_features | (body_region > 0.5)

        def visual(px, region):
            return seam_excess(orig, px, region, ignore=bg_edge), straight_edges(orig, px, region, ignore=new_features)

        thr = {**DEFAULT_THRESHOLDS, **(self.cfg.get("thresholds") or {})}
        seam_ok, edge_ok = float(thr["seam_excess"]["pass"]), float(thr["straight_edges"]["pass"])

        def visual_ok(before, after):
            """Etapa so fica se nao levar emenda/blocos para ALEM do aceitavel. Piora pequena abaixo do limite
            nao derruba a etapa: no reteste o refino que levou a identidade de 0,33 a 0,78 foi descartado por
            uma emenda 5,0 -> 6,1 (ainda PASS) - a identidade e o objetivo principal."""
            (s0, e0), (s1, e1) = before, after
            bad = []
            if s0 is not None and s1 is not None and s1 > max(s0 + seam_tol, seam_ok):
                bad.append(f"emenda {s0:.1f}->{s1:.1f}")
            if e0 is not None and e1 is not None and e1 > max(e0 + edge_tol, edge_ok):
                bad.append(f"blocos {e0:.1f}->{e1:.1f}")
            return not bad, "; ".join(bad)

        async def try_stage(name, mask, prompt, denoise, seed_off, min_gain=None, **kw):
            nonlocal cur, modified, score
            px2, loc2, secs = await self._pass(name, cur, mask, prompt, negative, denoise, req.seed + seed_off, plan, tel,
                                               accessory=paste, **kw)
            an2 = await self._identity_of(loc2, w, h, req.master)
            f2 = an2.persona_face()
            s2 = f2.similarity if f2 else None
            ok = s2 is not None and (score is None or score - s2 <= drop)
            reason = None if ok else f"identidade caiu ({score} -> {s2}): mantido o anterior"
            # refino com referencia so fica com GANHO real: a referencia tambem traz maquiagem/bronzeado
            if ok and min_gain is not None and score is not None and s2 < score + min_gain:
                ok, reason = False, f"ganho de identidade pequeno ({score} -> {s2}): mantido o anterior (mais natural)"
            if ok:
                region = np.maximum(modified, mask)
                vok, why = visual_ok(visual(cur["pixels"], region), visual(px2, region))
                if not vok:
                    ok, reason = False, f"etapa criou defeito visivel ({why}): mantido o anterior"
            tel.passes[-1].update(accepted=ok, identity=s2, reason=reason)
            inter[name] = loc2
            if ok:
                cur, modified, score = {"pixels": px2, "image": loc2}, np.maximum(modified, mask), s2

        # PASSE 2: refino de rosto (referencia facial)
        if plan.refinement and (score is None or score < plan.face_refine_if_identity_below):
            idsp = IdentitySpec(use_lora=True, reference=req.master if plan.face_reference else None,
                                reference_strength=plan.face_reference_strength if plan.face_reference else 0.0)
            gain = None if plan.name == "MAX_QUALITY" else float(acc.get("face_refine_min_gain", 0.02))
            await try_stage("face_refine", scene.face_full, cond(P["face"], "face"), plan.face_denoise, 101, min_gain=gain, identity=idsp,
                            controls=ControlSpec(structure=req.image))
        # FACE LOCK com a master (2026-10-07: tres trocas com 0,73-0,81 pareciam tres mulheres diferentes - o
        # rosto herdava o formato do rosto ORIGINAL pela profundidade e a referencia so entrava num refino leve).
        # A cabeca da master entra pela troca de cabeca da geracao V1; so a regiao da identidade volta para a foto.
        # Spec Master 9: o Qwen NAO e etapa padrao - so roda pedido explicitamente (experimental) e fica registrado.
        if plan.extra.get("qwen_face_lock", False) and plan.face_reference:
            if self.face_lock is None:
                raise ReplacementRequestError("Face Lock Qwen pedido, mas a porta do Qwen nao foi montada (sem troca silenciosa)")
            if "qwen_face_lock" not in tel.experimental_stages:
                tel.experimental_stages.append("qwen_face_lock")
            out = await self.face_lock.lock_face(ProviderImage("comfyui", cur["image"], "", w, h), req.master, req.seed + 211)
            px2 = await self.store.load(out.image.locator)
            if px2.shape[:2] != (h, w):  # o Qwen trabalha em ~1 MP: volta ao tamanho da foto
                from PIL import Image as _Img
                px2 = np.asarray(_Img.fromarray(px2).resize((w, h), _Img.LANCZOS))
            region = np.clip(feather(dilate(ident, 3), 3), 0, 1) * (dilate(ident, 3) > 0.5)
            px2 = (px2.astype(np.float32) * region[..., None] + cur["pixels"].astype(np.float32) * (1 - region[..., None])
                   + 0.5).astype(np.uint8)
            if paste is not None:  # acessorios mantidos voltam por cima (oculos na frente do rosto)
                px2 = paste(px2)
            loc2 = await self.store.save(px2, "face_lock")
            inter["face_lock"] = loc2
            an2 = await self._identity_of(loc2, w, h, req.master)
            f2 = an2.persona_face()
            s2 = f2.similarity if f2 else None
            # o Face Lock (cabeca da MASTER) e a autoridade do rosto. Teste do espelho (2026-10-07): o ArcFace deu
            # 0,74 ao Face Lock (era a Luna no olho) e 0,79 ao refino (outra mulher) - o numero escolheu errado.
            # So sai se a identidade cair abaixo de um piso absoluto (deu errado de verdade) ou criar defeito visivel.
            floor = float(acc.get("face_lock_floor", 0.65))
            if any(x.kind == "glasses_dark" and x.policy == PRESERVE for x in scene.layers):
                floor = float(acc.get("face_lock_floor_eyes_covered", 0.5))  # olhos cobertos: o ArcFace nao ve os olhos
            ok = s2 is not None and s2 >= floor
            reason = None if ok else f"Face Lock abaixo do piso de identidade ({s2} < {floor}): mantido o anterior"
            if ok:
                vok, why = visual_ok(visual(cur["pixels"], np.maximum(modified, region)), visual(px2, np.maximum(modified, region)))
                if not vok:
                    ok, reason = False, f"Face Lock criou defeito visivel ({why}): mantido o anterior"
            tel.add_pass("face_lock", out.seconds or 0.0, {**(out.effective_parameters or {}), "identity_before": score}, ok, reason)
            tel.passes[-1]["identity"] = s2
            if ok:
                cur, modified, score = {"pixels": px2, "image": loc2}, np.maximum(modified, region), s2
        # hi-res do rosto (detalhe de pele/olhos, sem referencia)
        if plan.hires:
            await try_stage("face_hires", scene.face_full, P["hires"], 0.25, 131, work_side=plan.hires_side,
                            controls=ControlSpec(structure=req.image))
        # refino de corpo: transicoes pescoco/ombro (pose + profundidade, LoRA)
        if plan.body_refinement and scene.masks is not None:
            band = np.clip(dilate(ident, 14) - erode(ident, 4), 0, 1) * scene.masks.person * (1 - scene.masks.clothing)
            band *= 1 - dilate(scene.face_full, 2)
            if band.sum() > 50:
                await try_stage("body_refine", band, cond(P["body"], "skin"), plan.body_denoise, 151, controls=structure)
        # RECONSTRUCAO DA PELE nas marcas da pessoa original (spec 45.5/45.6): mascara expandida -> entrada sem a
        # marca (push-pull, sem blocos) -> RealVisXL + LoRA da Persona com denoise alto (profundidade da pele LIMPA +
        # pose seguram a anatomia) -> refino local leve -> integracao de textura na borda -> residuo medido.
        # Nada de borrar, pintar cor ou clonar vizinho como resultado.
        # a integracao final so cuida da regiao da IDENTIDADE (rosto/cabelo/pescoco/corpo): as zonas de marcas ja tem
        # o proprio casamento de cor - no teste final de 2026-10-07 o degrau (+13) aplicado na borda delas clareou o
        # vinco da axila com borda dura
        ident_region = modified.copy()
        if plan.tattoo_cleanup and scene.markings is not None and scene.masks is not None:
            tt = self.cfg["tattoo"]
            px_min = min(h, w)
            boost = int(plan.extra.get("tattoo_margin_boost", 0))
            zone = scene.markings
            if boost:
                zone = dilate_round(zone, max(2, int(px_min * float(tt.get("mask_margin_frac", 0.008)))) * boost)
                zone = zone * scene.masks.person * (1 - scene.masks.clothing) * (1 - scene.masks.protect)
            # cabelo e a regiao da identidade ficam fora (no smoke test a pele pintou os fios do ombro)
            keep_out = np.maximum(dilate((scene.masks.hair > 0.5).astype(np.float32), 4), dilate((ident > 0.5).astype(np.float32), 3))
            zone = np.clip(zone * (1 - keep_out), 0, 1)
            if body_region is not None:  # o corpo ja foi redesenhado sem marcas: so sobra o que ficou da foto (maos)
                zone = np.clip(zone * (1 - (body_region > 0.5)), 0, 1)
            if zone.sum() > 30:
                soft = np.clip(feather(zone, max(2, int(px_min * float(tt["feather_frac"])))), 0, 1) * (zone > 0.02)
                known = ((scene.masks.skin > 0.5) & (scene.masks.person > 0.5) & ~(dilate_round(zone, 2) > 0.5)).astype(np.float32)
                # entrada que tira a marca SEM apagar dedos/juntas (traco fino: fechamento; tinta cheia: push-pull)
                clean, solid = structure_preserving_fill(cur["pixels"], dilate_round(zone, 2), known,
                                                         line_radius=max(2, int(px_min * float(tt.get("line_radius_frac", 0.004)))),
                                                         force_solid=scene.jewelry_body)
                clean_loc = await self.store.save(clean, "tattoo_prefill")
                skin_desc = ((attrs.persona_features and "") or "") + cond(P["tattoo"], "skin")
                ctrl = ControlSpec(pose_strength=plan.tattoo_pose_strength if plan.pose else 0.0,
                                   depth_strength=plan.tattoo_depth_strength, end_percent=0.9, structure=clean_loc)
                px2, loc2, _ = await self._pass("tattoo_cleanup", {"pixels": cur["pixels"], "image": clean_loc}, soft, skin_desc,
                                                negative, plan.tattoo_denoise, req.seed + 171, plan, tel, controls=ctrl,
                                                identity=IdentitySpec(use_lora=True))
                inter["tattoo_raw"] = loc2
                tidx = len(tel.passes) - 1
                tel.passes[tidx]["params"]["solid_ink_fraction"] = round(float(solid.sum()) / max(1.0, float((zone > 0.5).sum())), 3)
                tel.passes[tidx]["params"]["attributes"] = [a for a in ("tattoos", "scars", "birthmarks", "original_person_marks",
                                                                      "jewelry") if attrs.is_(a, REMOVE)]
                # cor de baixa frequencia casada com a pele limpa em volta (sem refino em anel: no teste de 2026-10-07
                # o refino 0,3 + integracao deixavam manchas claras redondas); zona enorme fica como o modelo fez
                ring = np.clip(feather(dilate_round(zone, 1), 2), 0, 1)
                stage_known = (known > 0.5) & ~(dilate_round(zone, 2) > 0.5)
                px3 = tone_match(px2, cur["pixels"], dilate_round(zone, 1), stage_known,
                                 radius=max(3, int(px_min * float(tt.get("tone_radius_frac", 0.008)))))
                px3 = np.where((dilate_round(zone, 1) > 0.5)[..., None], px3, cur["pixels"])
                info_t = {"tone_match": True}
                if plan.skin_refine_denoise > 0:  # refino leve opcional (desligado por padrao)
                    stage = {"pixels": px3, "image": await self.store.save(px3, "tattoo_tone")}
                    px3, _, _ = await self._pass("skin_refine", stage, ring, cond(P["tattoo"], "skin"), negative,
                                                 plan.skin_refine_denoise, req.seed + 181, plan, tel,
                                                 controls=ControlSpec(depth_strength=0.5, end_percent=0.8, structure=stage["image"]),
                                                 identity=IdentitySpec(use_lora=True))
                loc3 = await self.store.save(px3, "tattoo_skin")
                inter["tattoo_cleanup"] = loc3
                region = np.maximum(modified, ring)
                residual = self._markings_residual(scene, px3)
                vok, why = visual_ok(visual(cur["pixels"], region), visual(px3, region))
                smear = zone_texture_ratio(px3, zone, known)
                if vok and smear is not None and smear < float(self.cfg["acceptance"].get("min_zone_texture_ratio", 0.45)):
                    vok, why = False, f"pele refeita borrada/lisa (textura {smear:.2f} da pele em volta)"
                tel.passes[tidx]["texture_ratio"] = smear
                why_txt = None if vok else f"reconstrucao criou defeito visivel ({why}): marcas mantidas"
                tel.passes[tidx].update(accepted=vok, residual=residual, integration=info_t, reason=why_txt)
                if tel.passes[-1]["pass"] == "skin_refine":
                    tel.passes[-1].update(accepted=vok, reason=why_txt)  # o refino local faz parte da mesma etapa
                if vok:
                    cur, modified = {"pixels": px3, "image": loc3}, region
        # integracao fotografica (CPU)
        integ: dict[str, Any] = {}
        if plan.photographic_integration:
            body_skin = None
            if scene.masks is not None:
                body_skin = np.clip(scene.masks.skin * scene.masks.person - modified, 0, 1)
            t0 = time.monotonic()
            px2, integ = integrate(orig, cur["pixels"], ident_region, face=scene.face_full, body_skin=body_skin, seed=req.seed)
            vok, why = visual_ok(visual(cur["pixels"], modified), visual(px2, modified))
            loc2 = await self.store.save(px2, "integrated")
            inter["integrated"] = loc2
            if vok:
                cur = {"pixels": px2, "image": loc2}
            integ["aceita"] = vok
            tel.add_pass("photographic_integration", 0.0, {"cpu_seconds": round(time.monotonic() - t0, 2), **integ}, vok,
                         None if vok else f"integracao criou defeito visivel ({why}): mantida a imagem anterior")
        # fora do que foi alterado: a FOTO ORIGINAL (garantido aqui, nao confiado ao modelo)
        keep = dilate((modified > 0.02).astype(np.float32), 2) > 0.5
        final = np.where(keep[..., None], cur["pixels"], orig)
        if scene.layers:  # integracao (grao/tom) tambem nao mexe nos acessorios; ordem de oclusao no fim
            final = composite_layers(final, orig, scene.layers, within=keep.astype(np.float32))
        final_loc = await self.store.save(final, "final")
        if req.keep_intermediates:
            masks_out = [("mask_identity", ident), ("mask_modified", modified), ("mask_face", scene.face_full),
                         ("mask_ink", scene.ink_zone), ("mask_hair", None if scene.masks is None else scene.masks.hair)]
            masks_out += [(k, v) for k, v in scene.source_masks.items()]
            if req.debug:  # spec Master 36: todas as mascaras com nome + mapa de pose + apelidos de cada passe
                masks_out += list(semantic_masks(scene, self._hand_mask(scene) if scene.masks is not None else None).items())
                body = scene.sheet.target_body
                inter["pose_map"] = await self.store.save(render_pose_map(h, w, scene.base_pose,
                                                                          getattr(body, "hands", None) if body else None),
                                                          "pose_map")
                inter["original"] = req.image
                for alias, names in (("initial_generation", ("body_identity", "identity")),
                                     ("face_pass", ("face_lock", "face_refine", "identity")), ("skin_pass", ("body_identity",)),
                                     ("tattoo_pass", ("tattoo_cleanup",)), ("integration_pass", ("integrated",))):
                    hit = next((inter[n] for n in names if n in inter), None)
                    if hit is not None:
                        inter[alias] = hit
            for nm, mk in masks_out:
                if mk is not None:
                    inter[nm] = await self.store.save(np.repeat((np.clip(mk, 0, 1) * 255).astype(np.uint8)[..., None], 3, 2), nm)
            if req.debug:
                inter["final"] = final_loc
        return final, final_loc, inter, {"modified_area": round(float((modified > 0.5).mean()), 4), "integration": integ,
                                         "modified_mask": modified, "body_region": body_region}

    async def _hand_stage(self, req, scene, plan, tel, cur, hmask, negative, cond, paste, inter, w, h) -> dict | None:
        tt = self.cfg["tattoo"]
        src = cur["pixels"]
        if scene.ink_zone is not None and (scene.ink_zone * hmask).any():
            known = ((scene.masks.skin > 0.5) & ~(dilate(scene.ink_zone, 2) > 0.5)).astype(np.float32)
            src, _ = structure_preserving_fill(src, scene.ink_zone * (hmask > 0.5), known,
                                               line_radius=max(2, int(min(h, w) * float(tt.get("line_radius_frac", 0.004)))))
        src_loc = await self.store.save(src, "hand_input")
        seed = req.seed + 61 + 97 * int(plan.extra.get("hand_retry", 0))
        prompt = cond(self.cfg["prompts"].get("hand", "natural hand, same gesture as the photo, correct anatomy, five "
                                              "fingers, natural nails"), "skin")
        ctrl = ControlSpec(pose_strength=1.0, depth_strength=plan.hand_depth_strength, end_percent=0.9, structure=src_loc)
        soft = np.clip(feather(hmask, 4), 0, 1)
        px, loc, _ = await self._pass("hand_gesture_lock", {"pixels": cur["pixels"], "image": src_loc}, soft, prompt, negative,
                                      plan.hand_denoise, seed, plan, tel, controls=ctrl, identity=IdentitySpec(use_lora=True),
                                      accessory=paste)
        inter["hand_gesture_lock"] = loc
        before = await self._hand_counts(req.image, w, h, req.master)
        after = await self._hand_counts(loc, w, h, req.master)
        ratio = hand_ratio(before, after)
        ok = ratio is not None and ratio >= float(self.cfg["acceptance"].get("hand_min_ratio", 0.85))
        smear = zone_texture_ratio(px, hmask, scene.masks.skin * scene.masks.person if scene.masks is not None else None)
        why = None
        if ratio is None:
            why = "dedos nao medidos na etapa: mantida a mao da foto (sem prova de gesto)"
        elif not ok:
            why = f"mao perdeu dedos no detector ({ratio}): mantida a anterior"
        if ok and smear is not None and smear < float(self.cfg["acceptance"].get("min_zone_texture_ratio", 0.45)):
            ok, why = False, f"mao borrada/lisa (textura {smear:.2f} da pele em volta): mantida a anterior"
        tel.passes[-1].update(accepted=ok, hand_points={"original": before, "novo": after, "razao": ratio},
                              texture_ratio=smear, reason=why)
        return {"pixels": px, "image": loc} if ok else None

    async def _source_hands(self, locator: str, w: int, h: int, master) -> list:
        try:
            body = (await self.analyzer.analyze(ProviderImage("comfyui", locator, "", w, h), master)).main_body()
        except Exception:  # noqa: BLE001 - sem maos medidas: mascara pelo antebraco
            return []
        return list(getattr(body, "hands", None) or []) if body is not None else []

    async def _faces_in(self, locator: str, w: int, h: int, master) -> int | None:
        cache = self.__dict__.setdefault("_faces_cache", {})
        if locator not in cache:
            try:
                cache[locator] = len((await self._identity_of(locator, w, h, master)).faces)
            except Exception:  # noqa: BLE001 - contagem e medida auxiliar: sem ela o check fica UNKNOWN
                cache[locator] = None
        return cache[locator]

    async def _hand_counts(self, locator: str, w: int, h: int, master) -> list[int]:
        cache = self.__dict__.setdefault("_hand_cache", {})
        if locator not in cache:
            an = await self.analyzer.analyze(ProviderImage("comfyui", locator, "", w, h), master)
            body = an.main_body()
            cache[locator] = [] if body is None else [sum(1 for p in hand if p[2] and p[2] > 0.3) for hand in (body.hands or [])]
        return cache[locator]

    def _hand_mask(self, scene: _Scene, measurable_only: bool = False) -> np.ndarray | None:
        """Mao = a caixa dos pontos da mao do DWPose (com folga) quando a foto mostra os dedos; senao uma elipse
        alem do pulso com o tamanho do ANTEBRACO. Quarto 2026-10-08: o raio era 0,8 x altura do rosto - num close-up
        a "mao" cobria antebraco e colo inteiros (o corpo da Persona pulava o braco e a tatuagem ficava).
        measurable_only: so maos com >= 10 pontos de dedo (gesto que da para travar e conferir)."""
        from app.core.validation.geometry import point

        if not scene.base_pose:
            return None
        h, w = scene.original.shape[:2]
        fx1, fy1, fx2, fy2 = scene.sheet.target_face.bbox
        fh = max(1.0, fy2 - fy1)
        out = np.zeros((h, w), np.float32)
        hands = [[q for q in hd if q[2] and q[2] > 0.3] for hd in (scene.hands or [])]
        for wri, elb in (("rwri", "relb"), ("lwri", "lelb")):
            p, e = point(scene.base_pose, wri), point(scene.base_pose, elb)
            if not p:
                continue
            fore = float(np.hypot(p[0] - e[0], p[1] - e[1])) if e else fh * 0.9
            near = [hd for hd in hands if hd and np.hypot(np.mean([q[0] for q in hd]) - p[0],
                                                          np.mean([q[1] for q in hd]) - p[1]) < max(fore, fh * 0.5)]
            pts = max(near, key=len) if near else []
            if len(pts) >= 10:
                xs, ys = [q[0] for q in pts] + [p[0]], [q[1] for q in pts] + [p[1]]
                cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                rx = (max(xs) - min(xs)) / 2 * 1.3 + fore * 0.08
                ry = (max(ys) - min(ys)) / 2 * 1.3 + fore * 0.08
                out = np.maximum(out, ellipse(h, w, cx, cy, rx, ry))
                continue
            if measurable_only:
                continue
            dx, dy = (p[0] - e[0], p[1] - e[1]) if e else (0.0, 0.0)
            n = max(1.0, (dx * dx + dy * dy) ** 0.5)
            r = float(np.clip(fore * 0.45, fh * 0.2, fh * 0.6))
            cx, cy = p[0] + dx / n * r * 0.6, p[1] + dy / n * r * 0.6
            out = np.maximum(out, ellipse(h, w, cx, cy, r, r))
        return out if out.any() else None

    def _body_region(self, scene: _Scene, ident: np.ndarray, plan: StagePlan, keep_clothing: bool = False,
                     keep_hands: bool = True) -> np.ndarray | None:
        """Pessoa (corpo + roupa) sem a cabeca, sem as maos (pulsos do DWPose) e sem o calcado (abaixo dos
        tornozelos), com uma folga em volta do contorno para a silhueta da Persona caber."""
        from app.core.validation.geometry import point

        m = scene.masks
        h, w = m.person.shape
        fx1, fy1, fx2, fy2 = scene.sheet.target_face.bbox
        fh = max(1.0, fy2 - fy1)
        grow = max(3, int(min(h, w) * plan.body_identity_grow_frac))
        if keep_clothing:  # MESMA roupa: so a pele visivel da pessoa, com folga so para fora da pele
            skin_vis = (m.person > 0.5).astype(np.float32) * (1 - dilate((m.clothing > 0.5).astype(np.float32), 2))
            region = dilate(skin_vis, grow) * (1 - dilate((m.clothing > 0.5).astype(np.float32), max(3, grow // 2)))
        else:
            region = dilate((m.person > 0.5).astype(np.float32), grow)
        region *= 1 - dilate((ident > 0.5).astype(np.float32), 2)
        region *= 1 - (m.protect > 0.5)
        keep = np.zeros((h, w), np.float32)
        pose = scene.base_pose
        if pose:
            hm = self._hand_mask(scene) if keep_hands else None  # maos: etapa propria (pose lock) ou pixels da foto
            if hm is not None:
                keep = np.maximum(keep, hm)
            ankles = [p for p in (point(pose, "rank"), point(pose, "lank")) if p]
            if ankles:
                keep[int(max(a[1] for a in ankles)):, :] = 1
        region *= 1 - keep
        return np.clip(region, 0, 1) if region.sum() > 0 else None

    def _markings_residual(self, scene: _Scene, img: np.ndarray, skip: np.ndarray | None = None) -> float | None:
        """Fracao da marca original (miolo da zona) ainda detectada como tinta/marca na imagem."""
        if scene.ink_zone is None or scene.masks is None or scene.body is None:
            return None
        h, w = img.shape[:2]
        core = erode(scene.ink_zone, max(2, int(min(h, w) * 0.006)))
        if skip is not None:  # corpo redesenhado: a marca da pessoa original nao existe mais ali (e o corpo mudou de lugar)
            core = core * (1 - (dilate((skip > 0.5).astype(np.float32), 3) > 0.5))
        if not (core > 0.5).any():
            return 0.0
        tt = self.cfg["tattoo"]
        f = lambda k: max(1, int(min(h, w) * float(tt[k])))  # noqa: E731
        found = tattoo_zones(img, scene.body, skin_pixels(img) * scene.masks.person, np.zeros((h, w), np.float32),
                             scene.face_full, f("reach_frac"), f("edge_frac"), float(tt["ink_dy"]), float(tt["ink_dcr"]),
                             float(tt["ink_dcr_light"]), 99.0, 99.0, 1, 0, 4)
        return round(float(((found > 0.5) & (core > 0.5)).sum()) / max(1.0, float((core > 0.5).sum())), 4)

    # --- medidas para a validacao --------------------------------------------------------------
    def _clothing_color_delta(self, scene: _Scene, final: np.ndarray) -> float | None:
        if scene.masks is None or not (scene.masks.clothing > 0.5).any():
            return None
        core = erode((scene.masks.clothing > 0.5).astype(np.float32), 6)
        a, b = lab_mean(scene.original, core), lab_mean(final, core)
        return None if a is None or b is None else round(float(np.linalg.norm(a - b)), 2)

    def _preserved_change(self, scene: _Scene, final: np.ndarray, attr: str, modified_mask: np.ndarray | None) -> float | None:
        """Atributo PRESERVE: fracao alterada dentro dele (miolo, sem a borda de transicao)."""
        if scene.masks is None:
            return None
        if attr == "clothing":
            region = scene.masks.clothing
            if not scene.clothes_ok:  # sem roupa segmentada: o que nao e pele, cabelo nem rosto dentro da pessoa
                region = (scene.masks.person > 0.5) & ~(skin_pixels(scene.original) > 0.5) & ~(scene.masks.hair > 0.5) \
                    & ~(scene.face_full > 0.5) & ~(scene.masks.protect > 0.5)
                region = region.astype(np.float32)
        elif attr == "hair":
            region = scene.masks.hair
        else:
            region = scene.accessory if scene.accessory is not None else None
        if region is None or not (region > 0.5).any():
            return None
        core = erode((region > 0.5).astype(np.float32), 3)
        if attr == "clothing" and modified_mask is not None:
            # so a BORDA do que foi refeito nao conta (transicao); roupa repintada por dentro da regiao conta sim
            # (espelho 2026-10-08: o top inteiro estava dentro da regiao e a medida ficava vazia)
            mm = (modified_mask > 0.5).astype(np.float32)
            border = np.clip(dilate(mm, 4) - erode(mm, 4), 0, 1)
            core = core * (1 - border) * (1 - (scene.face_full > 0.5)) * (1 - dilate((scene.masks.hair > 0.5).astype(np.float32), 3))
        return changed_fraction(scene.original, final, core, threshold=10)

    async def _measure(self, req: ReplacementRequest, scene: _Scene, final: np.ndarray, final_loc: str, modified_area: float,
                       modified_mask: np.ndarray | None = None, attrs: AttributePolicy | None = None,
                       body_region: np.ndarray | None = None) -> dict[str, Any]:
        attrs_hair_preserved = attrs is not None and attrs.is_("hair", PRESERVE)
        orig = scene.original
        h, w = orig.shape[:2]
        an = await self._identity_of(final_loc, w, h, req.master)
        face = an.persona_face()
        dup = float(self.cfg.get("duplicate_similarity", 0.45))
        persona_n = len([f for f in an.faces if f.similarity is not None and f.similarity >= dup])
        x1, y1, x2, y2 = (int(v) for v in scene.sheet.target_face.bbox)
        # recorte LARGO: com o rosto colado na borda o detector nao acha o rosto e a medida some (smoke test)
        mg = float(self.cfg["acceptance"].get("original_crop_margin", 0.6))
        mx, my = int((x2 - x1) * mg), int((y2 - y1) * mg)
        crop = orig[max(0, y1 - my):y2 + my, max(0, x1 - mx):x2 + mx]
        orig_ref = ReferenceImage("original", "original.png", _png(crop), "")
        an_o = await self.analyzer.analyze(ProviderImage("comfyui", final_loc, "", w, h), orig_ref)
        fo = an_o.persona_face()
        from app.core.validation.geometry import pose_distance
        body = an.main_body()
        pose = pose_distance(scene.base_pose, body.keypoints) if scene.base_pose and body else None
        protect = scene.masks.person if scene.masks is not None else np.clip(scene.identity, 0, 1)
        if modified_mask is not None:  # corpo da Persona pode passar um pouco do contorno original (folga medida)
            protect = np.maximum(protect, modified_mask)
        background = changed_fraction(orig, final, 1 - dilate((protect > 0.02).astype(np.float32), 3))
        tattoo_res = self._markings_residual(scene, final, skip=body_region)
        hair_res = None
        if scene.masks is not None and scene.masks.hair.any():
            hm = scene.masks.hair > 0.5
            b0 = float((luma(orig)[hm] > 120).mean())
            if b0 > 0.2:  # cabelo original claro (Luna: castanho escuro)
                hair_res = round(float((luma(final)[hm] > 120).mean()), 4)
        face_skin = (scene.face_full > 0.5) & (skin_pixels(final) > 0.5)
        tex_final = texture_energy(final, face_skin.astype(np.float32))
        tex_ref = tone_delta = None
        if scene.masks is not None:
            body_skin = (scene.masks.skin > 0.5) & (scene.masks.person > 0.5) & ~(dilate(scene.identity, 6) > 0.5)
            tex_ref = texture_energy(orig, body_skin.astype(np.float32))
            lf, lb = lab_mean(final, face_skin.astype(np.float32)), lab_mean(final, body_skin.astype(np.float32))
            of = lab_mean(orig, ((scene.face_full > 0.5) & (scene.masks.skin > 0.5)).astype(np.float32))
            ob = lab_mean(orig, body_skin.astype(np.float32))
            if lf is not None and lb is not None and of is not None and ob is not None:
                tone_delta = round(max(0.0, float(np.linalg.norm(lf - lb) - np.linalg.norm(of - ob))), 2)
        shoulder = None
        if scene.base_pose and body:
            def width(kp):
                a, b = point(kp, "rsho"), point(kp, "lsho")
                return None if not (a and b) else float(np.hypot(a[0] - b[0], a[1] - b[1]))
            wo, wf = width(scene.base_pose), width(body.keypoints)
            shoulder = round(wf / wo, 3) if wo and wf else None
        # spec Master 14/19: acessorio por objeto e pixels da pessoa original nas regioes que a politica reconstroi
        kept = np.zeros((h, w), np.float32) if scene.accessory is None else dilate((scene.accessory > 0.5).astype(np.float32), 2)
        face_reg = (scene.face_full > 0.5) & ~(kept > 0.5)
        if scene.masks is not None:
            face_reg &= scene.masks.person > 0.5
        body_px = None
        if scene.masks is not None and attrs is not None and attrs.is_("body", RECONSTRUCT):
            src_body = scene.source_masks.get("source_body_mask")
            if src_body is not None:
                reg = (src_body > 0.5) & (scene.masks.skin > 0.5) & ~(kept > 0.5)
                hm = self._hand_mask(scene)
                if hm is not None and attrs.is_("hands", PRESERVE):
                    reg &= ~(hm > 0.5)
                if scene.base_pose:  # calcado/pes ficam da foto
                    ankles = [p for p in (point(scene.base_pose, "rank"), point(scene.base_pose, "lank")) if p]
                    if ankles:
                        reg[int(max(a[1] for a in ankles)):, :] = False
                body_px = source_pixel_residual(orig, final, reg.astype(np.float32))
        faces_orig = await self._faces_in(req.image, w, h, req.master)
        extra = {"clothes_segmented": scene.clothes_ok, "accessory_objects": measure_accessories(orig, final, scene.layers),
                 "source_face_pixels": source_pixel_residual(orig, final, face_reg.astype(np.float32)),
                 "source_body_pixels": body_px, "faces_original": faces_orig}
        return {**extra, "identity": face.similarity if face else None, "original_sim": fo.similarity if fo else None, "pose": pose,
                "background": background, "tattoo_residual": tattoo_res, "hair_residual": hair_res, "texture_final": tex_final,
                "texture_ref": tex_ref, "tone_delta": tone_delta, "persona_instances": persona_n, "faces": len(an.faces),
                "shoulder_ratio": shoulder, "composition_shift": 0.0, "person_found": face is not None or len(an.faces) > 0,
                "modified_area": modified_area,
                "clothing_change": self._preserved_change(scene, final, "clothing", modified_mask)
                if not (attrs is not None and attrs.is_("clothing", RECONSTRUCT)) else None,
                "clothing_color_delta": self._clothing_color_delta(scene, final)
                if attrs is not None and attrs.is_("clothing", RECONSTRUCT) else None,
                "hair_change": self._preserved_change(scene, final, "hair", modified_mask) if attrs_hair_preserved else None,
                "accessory_change": self._preserved_change(scene, final, "accessories", modified_mask),
                "hand_anatomy": hand_ratio(await self._hand_counts(req.image, w, h, req.master),
                                           await self._hand_counts(final_loc, w, h, req.master)),
                "seam_excess": None if modified_mask is None else seam_excess(orig, final, modified_mask),
                "straight_edges": None if modified_mask is None else straight_edges(
                    orig, final, modified_mask,
                    ignore=np.maximum(dilate(np.maximum(scene.identity, scene.face_full if scene.masks is None else
                                                        np.maximum(scene.face_full, scene.masks.hair)), 4),
                                      np.zeros_like(scene.identity) if body_region is None else body_region))}

    # --- job completo --------------------------------------------------------------------------
    def _configured(self, plan: StagePlan, req: ReplacementRequest) -> StagePlan:
        """Config dedicada (persona_replacement_v2.json) -> plano. Vale para FAST/QUALITY/MAX_QUALITY; a escada A..H do
        benchmark fica como esta. O que o pedido fixou explicitamente (forcas, advanced) nao e sobrescrito."""
        c = self.cfg
        extra = dict(plan.extra)
        qwen = c.get("qwen") or {}
        extra["qwen_face_lock"] = bool(req.qwen_face_lock) if req.qwen_face_lock is not None else bool(qwen.get("enabled", False))
        kw: dict[str, Any] = {"extra": extra}
        if plan.name in POLICIES:
            for key, val in (("pose_strength", (c.get("pose") or {}).get("strength")),
                             ("depth_strength", (c.get("depth") or {}).get("strength")),
                             ("face_reference_strength", (c.get("identity") or {}).get("strength"))):
                if val is not None and not req.explicit(key):
                    kw[key] = float(val)
            if (c.get("depth") or {}).get("enabled") is False:
                kw["depth"] = False
            if (c.get("tattoo_removal") or {}).get("enabled") is False:
                kw["tattoo_cleanup"] = False
            if c.get("adaptive_retry") is False and "max_retries" not in req.advanced:
                kw["max_retries"] = 0
        return plan.with_(**kw)

    def _run_log(self, req: ReplacementRequest, tel: JobTelemetry, plan: StagePlan, scene: _Scene, report: ValidationReportV2,
                 m: dict[str, Any], retry_reasons: list[str]) -> dict[str, Any]:
        sheet = req.persona_sheet or {}
        masters = {r.get("id") or r.get("reference_id"): r for r in (sheet.get("master_references") or [])
                   if isinstance(r, dict)}
        body = masters.get("master_body") or {}
        meta = self.adapter.metadata()
        return {
            "replacement_id": tel.job_id, "persona_id": req.persona_id, "replacement_version": req.replacement_version,
            "source_image": req.image,
            "master_face": {"file": getattr(req.master, "file", ""), "sha256": getattr(req.master, "sha256", "")},
            "master_body": {"file": body.get("file"), "sha256": body.get("sha256")} if body else None,
            "base_model": meta.get("model", ""), "lora": (self.cfg.get("lora") or {}).get("name", "lunavox_sdxl_v1"),
            "lora_strength": meta.get("lora_strength", "registro (fixa)"),
            "instantid_strength": plan.face_reference_strength if plan.face_reference else 0.0,
            "pose_strength": plan.pose_strength if plan.pose else 0.0,
            "depth_strength": plan.depth_strength if plan.depth else 0.0, "seed": req.seed, "mode": plan.name,
            "passes": [{"pass": p["pass"], "accepted": p.get("accepted"), "seconds": p.get("seconds"),
                        "identity": p.get("identity"), "reason": p.get("reason")} for p in tel.passes],
            "masks": {k: round(float((v > 0.5).mean()), 4) for k, v in scene.source_masks.items()},
            "validators": tel.gate.get("validators", {}),
            "scores": {k: m.get(k) for k in ("identity", "original_sim", "pose", "tattoo_residual", "hair_residual",
                                             "hand_anatomy", "accessory_change", "source_face_pixels", "source_body_pixels",
                                             "background", "seam_excess", "straight_edges")},
            "retry_reason": retry_reasons, "final_status": report.status, "gate_decision": tel.gate.get("decision"),
            "processing_time": tel.duration_s, "cost": tel.estimated_cost_usd,
            "fallback_used": tel.fallback_used, "fallback_model": tel.fallback_model,
            "experimental_stages": list(tel.experimental_stages),
        }

    async def run(self, req: ReplacementRequest) -> ReplacementOutcome:
        req.validate()
        if os.environ.get((self.cfg.get("debug") or {}).get("env", "REPLACEMENT_DEBUG"), "").lower() in ("1", "true", "yes"):
            req.debug = True
        if req.debug:
            req.keep_intermediates = True
        attrs = req.attributes()
        plan = self._configured(req.plan(attrs), req)
        start = time.monotonic()
        meta = self.adapter.metadata()
        tel = JobTelemetry(uuid.uuid4().hex[:12], req.persona_id, "replacement", plan.name, model=meta.get("model", ""),
                           checkpoint_hash=meta.get("hash", ""), lora_hash=meta.get("lora_hash", self.lora_hash), seed=req.seed,
                           steps=plan.steps, cfg=plan.cfg, provider=self.provider, engine_version=ENGINE_VERSION,
                           license={k: meta.get(k) for k in ("license", "commercial_use", "license_status")},
                           workflow_versions={"inpaint": meta.get("workflow", "")}, attributes=attrs.to_dict())
        scene = await self._scene(req.image, req.master, plan, attrs)
        tel.attributes["boxes"] = scene.box_policy
        tel.attributes["source_details"] = source_details(attrs, scene)
        tel.attributes["mask_notes"] = scene.notes
        # a MEDICAO usa sempre a segmentacao real (mesmo quando a geracao nao usa: degraus A..D),
        # senao tatuagem/fundo/cabelo ficariam "desconhecidos" justamente onde falham
        mscene = scene if plan.segmentation else await self._scene(req.image, req.master, plan.with_(segmentation=True), attrs)
        h, w = scene.original.shape[:2]
        src_hands = await self._source_hands(req.image, w, h, req.master)
        scene.hands = mscene.hands = src_hands
        # spec Master 5: analise estruturada da foto (so dados; nenhum modelo gera nada aqui)
        analysis = analyze_scene(mscene, attrs, await self._faces_in(req.image, w, h, req.master))
        tel.attributes["scene_analysis"] = analysis.to_dict()
        hierarchy = [] if mscene.masks is None else check_hierarchy(semantic_masks(mscene))
        if hierarchy:
            tel.attributes["mask_hierarchy_issues"] = hierarchy
        tel.resolution = [w, h]
        retry = RetryPolicyV2()
        gate = QualityGate(self.cfg)
        attempts, best = [], None
        attempt = 0
        retry_reasons: list[str] = []
        while True:
            tel.controlnet_strength = {"pose": plan.pose_strength if plan.pose else 0.0, "depth": plan.depth_strength if plan.depth else 0.0}
            tel.reference_strength = plan.face_reference_strength if plan.face_reference else 0.0
            tel.denoise = {"identity": plan.identity_denoise, "face": plan.face_denoise, "tattoo": plan.tattoo_denoise,
                           "body": plan.body_denoise}
            final, loc, inter, info = await self._attempt(req, scene, plan, tel, attrs)
            m = await self._measure(req, mscene, final, loc, info["modified_area"], info.get("modified_mask"), attrs,
                                    info.get("body_region"))
            report = gate.evaluate(m, attrs.policy)
            verdict = gate.decide(report, can_retry=attempt < plan.max_retries)
            rec = {"attempt": attempt, "plan": plan.name, "status": report.status, "decision": verdict.decision,
                   "failures": report.failures(), "warnings": report.warnings(), "image": loc, "identity": m["identity"]}
            attempts.append(rec)
            rank = (report.status == PASS, report.status != REJECT, m["identity"] or 0.0)
            if best is None or rank > best[0]:
                best = (rank, final, loc, inter, report, m, info, verdict)
            if verdict.decision != "RETRY":
                break
            fails = sorted(report.failures(), key=lambda f: PRIORITY.index(_GATE_FAILURE.get(f, f))
                           if _GATE_FAILURE.get(f, f) in PRIORITY else len(PRIORITY))
            failures = _specific(report, fails or _by_severity(report, report.warnings()))
            nxt = retry.next_plan(plan, failures, attempt + 1)
            if nxt is None:
                break
            plan, step = nxt
            step.result = report.status
            retry_reasons.append(f"{step.failure_type}: {step.strategy}")
            attempt += 1
        _, final, loc, inter, report, m, info, verdict = best
        tel.retry_count = len(retry.history)
        tel.retries = [s.to_dict() for s in retry.history]
        tel.duration_s = round(time.monotonic() - start, 2)
        tel.estimated_cost_usd = self.adapter.estimate_cost(tel.gpu_seconds, self.price)
        tel.validation_result = report.status
        tel.failure_reason = "; ".join(f"{n}: {report.checks[n].reason}" for n in report.failures()) or None
        final_decision = verdict.decision if verdict.decision != "RETRY" else ("REJECT" if report.failures() else "PASS")
        tel.gate = {**gate.decide(report, can_retry=False).to_dict(), "decision": final_decision}
        tel.run_log = self._run_log(req, tel, plan, scene, report, m, retry_reasons)
        areas = {"identity": round(float((scene.identity > 0.5).mean()), 4), **({} if scene.ink_zone is None else
                 {"tattoo_zone": round(float((scene.ink_zone > 0.5).mean()), 4)}), "modified": info["modified_area"],
                 "clothes_segmented": scene.clothes_ok}
        out = ReplacementOutcome(loc, final, report.status, report, m, tel, inter if req.keep_intermediates else {},
                                 attempts, areas)
        if req.debug:
            out.debug = {"scene_analysis": analysis.to_dict(), "validation_report": report.to_dict(), "gate": tel.gate,
                         "run_log": tel.run_log, "mask_hierarchy_issues": hierarchy,
                         # passe adaptativo que nao rodou (ex.: corpo da Persona ja refez a pele: nada para limpar)
                         "passes_not_run": [a for a in ("initial_generation", "face_pass", "skin_pass", "tattoo_pass",
                                                        "integration_pass") if a not in inter],
                         "depth_map": "gerado dentro do ComfyUI (Depth Anything V2); nao exportado pelo workflow"}
        return out


# falhas dos validadores novos -> tipo de retry da spec (cada falha com a SUA estrategia)
_GATE_FAILURE = {"source_pixel_residual": "original_residual", "accessory_objects": "accessories",
                 "person_count": "duplicate_persona", "hair": "original_residual", "clothing": "background"}


# spec 45.11: violacao da politica vira a falha ESPECIFICA do atributo (nunca retry generico)
def source_details(attrs: AttributePolicy, scene: _Scene) -> list[dict[str, Any]]:
    """Spec 46.9: cada detalhe da foto com a sua classificacao (o que fica, o que e refeito, o que sai)."""
    det = [{"detail": a, "policy": attrs.get(a)} for a in ("pose", "composition", "background", "lighting", "clothing")]
    det += [{"detail": a, "policy": attrs.get(a)} for a in ("face", "skin", "body", "hair")]
    hp = attrs.get("hands")
    det.append({"detail": "hands", "policy": hp, "mode": "POSE_LOCK" if hp == RECONSTRUCT else "PIXEL_LOCK"})
    det += [{"detail": a, "policy": attrs.get(a)} for a in ("tattoos", "scars", "birthmarks", "original_person_marks")]
    for b in scene.box_policy:
        det.append({"detail": b.get("item") or b.get("label") or "objeto", "policy": b["policy"], "box": b["box"],
                    "note": b.get("note", "")})
    for layer in scene.layers:
        det.append({"detail": f"camada:{layer.item or layer.label}", "policy": layer.policy, "kind": layer.kind,
                    "order": layer.order})
    return det


def restore_background(px: np.ndarray, original: np.ndarray, person: np.ndarray, region: np.ndarray,
                       tol: int = 28) -> np.ndarray:
    """Onde a foto era FUNDO e o modelo so repintou o fundo (cor perto da original), volta o pixel original com borda
    suave: o cabelo novo sobre a parede fica, o halo claro (parede levemente diferente) some."""
    bg = (region > 0.02) & (dilate((person > 0.5).astype(np.float32), 1) < 0.5)
    if not bg.any():
        return px
    close = np.abs(px.astype(np.int16) - original.astype(np.int16)).max(axis=2) <= tol
    m = (bg & close).astype(np.float32)
    if not m.any():
        return px
    a = np.clip(feather(m, 2), 0, 1)[..., None] * (m[..., None] > 0)
    return np.clip(original.astype(np.float32) * a + px.astype(np.float32) * (1 - a) + 0.5, 0, 255).astype(np.uint8)


def skin_band(masks, body_region: np.ndarray, ident: np.ndarray) -> np.ndarray | None:
    """Pele da foto que sobrou ENTRE o corpo refeito e a roupa (folga de seguranca da roupa): recebe o tom do corpo
    novo, senao vira uma faixa clara na barra da roupa."""
    near = dilate((body_region > 0.5).astype(np.float32), 12) > 0.5
    band = ((masks.skin > 0.5) & (masks.person > 0.5) & (masks.clothing < 0.5) & (body_region < 0.5) & (ident < 0.5)
            & (masks.protect < 0.5) & near)
    return band.astype(np.float32) if band.sum() > 30 else None


def zone_texture_ratio(img: np.ndarray, zone: np.ndarray, known: np.ndarray | None) -> float | None:
    """Microtextura dentro da zona refeita / na pele em volta (mesma imagem). Mancha borrada ou lisa fica bem abaixo
    de 1 mesmo quando a cor casa - o check de emenda nao ve isso (quarto 2026-10-08: mao virou mancha laranja)."""
    if known is None:
        return None
    z = (zone > 0.5)
    ring = (dilate(z.astype(np.float32), 12) > 0.5) & ~(dilate(z.astype(np.float32), 3) > 0.5) & (known > 0.5)
    inner = erode(z.astype(np.float32), 2) > 0.5  # a borda da zona (degrau de cor) nao e textura
    tz = texture_energy(img, (inner if inner.sum() >= 50 else z).astype(np.float32))
    tr = texture_energy(img, ring.astype(np.float32))
    if tz is None or tr is None or tr <= 0:
        return None
    return round(float(tz / tr), 3)


def hand_ratio(before: list[int], after: list[int]) -> float | None:
    """Pior mao: pontos de dedo que o DWPose acha no final / na foto (so maos que a foto mostra bem, >= 10 pontos)."""
    pairs = [(b, a) for b, a in zip(before, after + [0] * max(0, len(before) - len(after))) if b >= 10]
    if not pairs:
        return None
    return round(min(min(a, b) / b for b, a in pairs), 3)


_ATTR_FAILURE = {"tattoos": "tattoo", "scars": "tattoo", "birthmarks": "tattoo", "original_person_marks": "tattoo",
                 "source_identity_residual": "original_residual", "face": "identity", "skin": "skin",
                 "hands": "hands", "jewelry": "accessories",
                 "clothing": "background", "background": "background", "hair": "background", "accessories": "accessories",
                 "pose": "pose", "composition": "composition"}


def _by_severity(report: ValidationReportV2, warnings: list[str]) -> list[str]:
    """Teste de 2026-10-07: com fundo 0,0031 (limite 0,002) e tatuagem 0,114 (limite 0,03) o retry foi para
    o FUNDO (primeiro da lista) e piorou o rosto. Agora so avisos = o mais fundo na faixa de aviso primeiro."""
    def sev(name: str) -> float:
        c = report.checks.get(name)
        thr = c.threshold if c is not None else None
        if c is None or not isinstance(thr, dict) or not isinstance(c.score, (int, float)):
            return 0.5  # sem escala (ex.: politica, emendas): meio da fila
        lo, hi = thr.get("pass"), thr.get("reject")
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or hi == lo:
            return 0.5
        return abs(c.score - lo) / abs(hi - lo)
    return sorted(warnings, key=sev, reverse=True)


def _specific(report: ValidationReportV2, failures: list[str]) -> list[str]:
    out = list(dict.fromkeys(_GATE_FAILURE.get(f, f) for f in failures if f != "attribute_policy"))
    orr =report.checks.get("original_residual")
    if "original_residual" in out and orr is not None:
        face = (orr.metadata or {}).get("rosto_original")
        # residuo so de tatuagem/cabelo (rosto original ja nao parece): a falha especifica e a da marca
        if face is not None and face <= 0.30 and "tattoo" in out:
            out.remove("original_residual")
    if "attribute_policy" in failures:
        viol = report.checks["attribute_policy"].metadata.get("violations", [])
        out += [_ATTR_FAILURE[a] for a in viol if a in _ATTR_FAILURE and _ATTR_FAILURE[a] not in out]
    return out


_IDENTITY_WORDS = ("hair", "curl", "blond", "brunette", "redhead", "face", "eyes", "eyebrow", "lips", "makeup", "freckle",
                   "tattoo", "skin", "complexion", "ethnic", "asian", "latina", "caucasian", "african", "beard")


def scrub_identity(text: str) -> str:
    """Tira da descricao da cena o que e da pessoa ORIGINAL (cabelo, rosto, olhos, pele, tatuagem). A legenda do
    Florence dizia "long curly dark hair" e isso ia para o prompt da Luna (2026-10-07: cabelo diferente da Luna)."""
    import re

    out = []
    for sent in re.split(r"(?<=[.!?;])\s+", text or ""):
        s2 = re.sub(r"\s*\b(with|has|having)\b[^.;]*?\b(hair|eyes|face|skin|makeup|lips|freckles|tattoos?)\b", "", sent,
                    flags=re.I)
        if any(w in s2.lower() for w in _IDENTITY_WORDS):
            continue  # frase ainda sobre a pessoa original: sai inteira
        s2 = s2.strip().rstrip(".").strip()
        if s2:
            out.append(s2)
    return ". ".join(out)


_PERSON_WORDS = ("wear", "top", "shirt", "blouse", "dress", "jeans", "shorts", "pants", "skirt", "corset", "bikini", "swimsuit",
                 "jacket", "coat", "sweater", "hoodie", "tank", "crop", "bra", "outfit", "clothes", "clothing", "stand",
                 "sitting", "seated", "lean", "pose", "posing", "arm", "hand", "holding", "selfie", "mirror", "smil",
                 "looking", "woman", "girl", "person", "photo", "shot", "portrait", "full body", "half body", "close-up")
_PLACE_WORDS = ("background", "beach", "sea", "ocean", "mountain", "hill", "sky", "city", "street", "building", "balcony",
                "view", "landscape", "room", "door", "wall", "window", "bed", "garden", "park", "copacabana", "rio")


def person_text(text: str) -> str:
    """So o que descreve a PESSOA (roupa, pose, enquadramento). Lugar/cenario fica de fora: ele ja esta na foto."""
    keep = []
    for sent in (text or "").replace(";", ".").split("."):
        for part in sent.split(","):
            low = part.lower()
            if any(w in low for w in _PERSON_WORDS) and not any(w in low for w in _PLACE_WORDS):
                keep.append(part.strip())
    return ", ".join(dict.fromkeys(k for k in keep if k)) or "a woman"


_BODY_PT_EN = (("curvilinea", "curvy"), ("ampulheta", "hourglass figure"), ("busto cheio", "full bust"),
               ("cintura fina", "slim waist"), ("quadril arredondado", "rounded hips"), ("bracos tonificados", "toned arms"))


def body_text_en(text: str) -> str:
    """Corpo da Persona Sheet (pt) em termos que o SDXL entende."""
    out = [en for pt, en in _BODY_PT_EN if pt in (text or "").lower()]
    return ", ".join(out) if out else "curvy hourglass figure"


def _png(pixels: np.ndarray) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8), "RGB").save(buf, "PNG")
    return buf.getvalue()


__all__ = ["ENGINE_VERSION", "ReplacementEngine", "ReplacementOutcome", "ReplacementRequest", "ReplacementRequestError"]
