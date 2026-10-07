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
from app.core.engines.markings import clean_skin_reference, complete_markings
from app.core.engines.skin import drop_small_blobs, structure_preserving_fill, tone_match
from app.core.engines.integration import integrate
from app.core.engines.policies import StagePlan, plan_for
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

ENGINE_VERSION = "replacement-v2.1-attributes"


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

    OPTIONS = ("preserve_pose", "preserve_clothes", "preserve_background", "preserve_lighting", "remove_original_tattoos",
               "identity_lock", "body_lock", "skin_realism", "photographic_integration")
    ADVANCED = ("steps", "cfg", "identity_denoise", "face_denoise", "face_reference_strength", "pose_strength",
                "depth_strength", "tattoo_denoise", "body_denoise", "max_retries")

    def validate(self) -> None:
        bad = [k for k in self.options if k not in self.OPTIONS]
        if bad:
            raise ReplacementRequestError(f"opcoes desconhecidas: {bad}")
        bad = [k for k in self.advanced if k not in self.ADVANCED]
        if bad:
            raise ReplacementRequestError(f"parametros avancados desconhecidos: {bad}")
        if not self.image or not self.persona_id:
            raise ReplacementRequestError("imagem e persona sao obrigatorias")

    def attributes(self) -> AttributePolicy:
        """Politica de atributos resolvida: padrao < Persona Sheet < pedido. As opcoes antigas viram atributos."""
        preserve, remove = list(self.preserve_attributes), list(self.remove_attributes)
        o = {k: bool(v) for k, v in self.options.items()}
        if o.get("remove_original_tattoos") is False and "tattoos" not in remove:
            preserve.append("tattoos")
        try:
            return resolve(self.persona_sheet, preserve, remove, list(self.reconstruct_attributes))
        except AttributePolicyError as exc:
            raise ReplacementRequestError(str(exc)) from exc

    def plan(self, attrs: AttributePolicy | None = None) -> StagePlan:
        o = {k: bool(v) for k, v in self.options.items()}
        attrs = attrs or self.attributes()
        over: dict[str, Any] = dict(self.advanced)
        if self.mode in ("FAST", "QUALITY", "MAX_QUALITY"):  # a escada A..H do benchmark fica como esta
            over.setdefault("tattoo_cleanup", attrs.removes_skin_markings())
            if attrs.is_("body", RECONSTRUCT) and self.mode != "FAST":
                over.setdefault("body_refinement", True)
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
        return plan_for(self.mode, over)


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

    def to_dict(self) -> dict[str, Any]:
        return {"image": self.image, "status": self.status, "validation": self.report.to_dict(), "measures": self.measures,
                "telemetry": self.telemetry.to_dict(), "intermediates": self.intermediates, "attempts": self.attempts,
                "mask_areas": self.mask_areas}


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


JEWELRY_WORDS = ("earring", "bracelet", "necklace", "ring", "jewel", "brinco", "pulseira", "colar")


def box_class(label: str) -> str:
    """Rotulo do detector -> atributo. Sem rotulo (detector antigo) = acessorio (conservador: mantido)."""
    lab = (label or "").lower()
    return "jewelry" if any(w in lab for w in JEWELRY_WORDS) else "accessories"


class ReplacementEngine:
    def __init__(self, *, reader, segmenter, analyzer, store, adapter: ModelAdapter, config: dict[str, Any],
                 lora_hash: str = "", price_per_hour: float | None = None, provider: str = "") -> None:
        self.reader, self.segmenter, self.analyzer, self.store = reader, segmenter, analyzer, store
        self.adapter = adapter
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
        for b, lab in zip(raw.protect_boxes, labels):
            if b not in plausible:
                continue
            cls = box_class(lab)
            pol = attrs.get(cls)
            box_policy.append({"box": [round(v, 1) for v in b], "label": lab, "attribute": cls, "policy": pol})
            (keep_boxes if pol == PRESERVE else drop_boxes).append(b)
        raw = replace(raw, protect_boxes=keep_boxes, protect_labels=[])
        masks = build_masks(raw, bbox, sheet.target_face.kps, original)
        ident = identity_mask(masks, bbox, float(sg["face_grow_frac"]))
        if attrs.is_("hair", PRESERVE):  # cabelo da foto fica: so rosto/pescoco recebem a identidade
            ident = np.clip(ident * (1 - dilate((masks.hair > 0.5).astype(np.float32), 2)) + masks.face_full * masks.person, 0, 1)
        accessory = None
        if raw.protect_boxes:
            box = np.zeros((h, w), np.float32)
            for bx1, by1, bx2, by2 in raw.protect_boxes:
                box[max(0, int(by1)):int(by2) + 1, max(0, int(bx1)):int(bx2) + 1] = 1
            # so o que NAO e pele dentro da caixa (armacao e lentes); buracos pequenos fechados
            acc = erode(dilate(box * (1 - skin_pixels(original)), 2), 2) * box
            if acc.sum() > 20:
                accessory = acc
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
        # mascara de MARCAS da pessoa original (spec 45.4/45.6): tinta/marcas na pele + joias REMOVE fora do rosto;
        # buracos fechados e margem em volta (evita contorno fantasma); nunca roupa, nem acessorio mantido
        markings = None
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
        return _Scene(original, sheet, masks, ident, masks.face_full, body, ink, raw.tattoos, base_pose, clothes_ok, accessory,
                      markings, source, box_policy)

    # --- uma passada ---------------------------------------------------------------------------
    async def _pass(self, name: str, cur: dict, mask: np.ndarray, prompt: str, negative: str, denoise: float, seed: int,
                    plan: StagePlan, tel: JobTelemetry, *, controls: ControlSpec | None = None, identity: IdentitySpec | None = None,
                    image: str | None = None, work_side: int | None = None,
                    accessory: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[np.ndarray, str, float]:
        req = InpaintRequest(image=image or cur["image"], mask=mask, prompt=prompt, negative=negative, denoise=denoise,
                             seed=seed % 2**32, stage=name, controls=controls or ControlSpec(),
                             identity=identity or IdentitySpec(), steps=plan.steps, cfg=plan.cfg,
                             work_side=work_side or plan.work_side)
        res = await self.adapter.inpaint(req)
        px = await self.store.load(res.image)
        keep = mask > 0.02  # fora da mascara: nada vem do modelo
        px = np.where(keep[..., None], px, cur["pixels"])
        if accessory is not None:  # oculos originais por cima (borda de 1-2 px suave)
            acc_mask, acc_src = accessory
            a = np.clip(feather(acc_mask, 2), 0, 1)[..., None] * keep[..., None]
            px = (acc_src.astype(np.float32) * a + px.astype(np.float32) * (1 - a) + 0.5).astype(np.uint8)
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
        # PASSE 1: reconstrucao da identidade
        prompt = cond(P["identity"].replace("{description}", scene.sheet.description()), "identity")
        paste = None if scene.accessory is None else (scene.accessory, orig)
        px, loc, _ = await self._pass("identity", cur, ident, prompt, negative, plan.identity_denoise, req.seed, plan, tel,
                                      controls=structure, accessory=paste)
        cur, modified = {"pixels": px, "image": loc}, np.maximum(modified, ident)
        inter["identity"] = loc
        an = await self._identity_of(loc, w, h, req.master)
        face = an.persona_face()
        score = face.similarity if face else None
        tel.passes[-1].update(identity=score)
        acc = self.cfg["acceptance"]
        seam_tol, edge_tol = float(acc.get("stage_seam_tolerance", 0.8)), float(acc.get("stage_edges_tolerance", 1.5))

        def visual(px, region):
            return seam_excess(orig, px, region), straight_edges(orig, px, region)

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
            if zone.sum() > 30:
                soft = np.clip(feather(zone, max(2, int(px_min * float(tt["feather_frac"])))), 0, 1) * (zone > 0.02)
                known = ((scene.masks.skin > 0.5) & (scene.masks.person > 0.5) & ~(dilate_round(zone, 2) > 0.5)).astype(np.float32)
                # entrada que tira a marca SEM apagar dedos/juntas (traco fino: fechamento; tinta cheia: push-pull)
                clean, solid = structure_preserving_fill(cur["pixels"], dilate_round(zone, 2), known,
                                                         line_radius=max(2, int(px_min * float(tt.get("line_radius_frac", 0.004)))))
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
        if scene.accessory is not None:  # integracao (grao/tom) tambem nao mexe nos oculos
            a = np.clip(feather(scene.accessory, 2), 0, 1)[..., None]
            final = (orig.astype(np.float32) * a + final.astype(np.float32) * (1 - a) + 0.5).astype(np.uint8)
        final_loc = await self.store.save(final, "final")
        if req.keep_intermediates:
            masks_out = [("mask_identity", ident), ("mask_modified", modified), ("mask_face", scene.face_full),
                         ("mask_ink", scene.ink_zone), ("mask_hair", None if scene.masks is None else scene.masks.hair)]
            masks_out += [(k, v) for k, v in scene.source_masks.items()]
            for nm, mk in masks_out:
                if mk is not None:
                    inter[nm] = await self.store.save(np.repeat((np.clip(mk, 0, 1) * 255).astype(np.uint8)[..., None], 3, 2), nm)
        return final, final_loc, inter, {"modified_area": round(float((modified > 0.5).mean()), 4), "integration": integ,
                                         "modified_mask": modified}

    def _markings_residual(self, scene: _Scene, img: np.ndarray) -> float | None:
        """Fracao da marca original (miolo da zona) ainda detectada como tinta/marca na imagem."""
        if scene.ink_zone is None or scene.masks is None or scene.body is None:
            return None
        h, w = img.shape[:2]
        core = erode(scene.ink_zone, max(2, int(min(h, w) * 0.006)))
        if not (core > 0.5).any():
            return 0.0
        tt = self.cfg["tattoo"]
        f = lambda k: max(1, int(min(h, w) * float(tt[k])))  # noqa: E731
        found = tattoo_zones(img, scene.body, skin_pixels(img) * scene.masks.person, np.zeros((h, w), np.float32),
                             scene.face_full, f("reach_frac"), f("edge_frac"), float(tt["ink_dy"]), float(tt["ink_dcr"]),
                             float(tt["ink_dcr_light"]), 99.0, 99.0, 1, 0, 4)
        return round(float(((found > 0.5) & (core > 0.5)).sum()) / max(1.0, float((core > 0.5).sum())), 4)

    # --- medidas para a validacao --------------------------------------------------------------
    def _preserved_change(self, scene: _Scene, final: np.ndarray, attr: str, modified_mask: np.ndarray | None) -> float | None:
        """Atributo PRESERVE: fracao alterada dentro dele (miolo, sem a borda de transicao)."""
        if scene.masks is None:
            return None
        if attr == "clothing":
            region = scene.masks.clothing
        elif attr == "hair":
            region = scene.masks.hair
        else:
            region = scene.accessory if scene.accessory is not None else None
        if region is None or not (region > 0.5).any():
            return None
        core = erode((region > 0.5).astype(np.float32), 3)
        if attr == "clothing" and modified_mask is not None:  # a roupa encostada no que a politica reconstroi nao conta
            core = core * (1 - dilate((modified_mask > 0.5).astype(np.float32), 4))
        return changed_fraction(scene.original, final, core, threshold=10)

    async def _measure(self, req: ReplacementRequest, scene: _Scene, final: np.ndarray, final_loc: str, modified_area: float,
                       modified_mask: np.ndarray | None = None, attrs: AttributePolicy | None = None) -> dict[str, Any]:
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
        background = changed_fraction(orig, final, 1 - dilate((protect > 0.02).astype(np.float32), 3))
        tattoo_res = self._markings_residual(scene, final)
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
        return {"identity": face.similarity if face else None, "original_sim": fo.similarity if fo else None, "pose": pose,
                "background": background, "tattoo_residual": tattoo_res, "hair_residual": hair_res, "texture_final": tex_final,
                "texture_ref": tex_ref, "tone_delta": tone_delta, "persona_instances": persona_n, "faces": len(an.faces),
                "shoulder_ratio": shoulder, "composition_shift": 0.0, "person_found": face is not None or len(an.faces) > 0,
                "modified_area": modified_area,
                "clothing_change": self._preserved_change(scene, final, "clothing", modified_mask),
                "hair_change": self._preserved_change(scene, final, "hair", modified_mask) if attrs_hair_preserved else None,
                "accessory_change": self._preserved_change(scene, final, "accessories", modified_mask),
                "seam_excess": None if modified_mask is None else seam_excess(orig, final, modified_mask),
                "straight_edges": None if modified_mask is None else straight_edges(orig, final, modified_mask)}

    # --- job completo --------------------------------------------------------------------------
    async def run(self, req: ReplacementRequest) -> ReplacementOutcome:
        req.validate()
        attrs = req.attributes()
        plan = req.plan(attrs)
        start = time.monotonic()
        meta = self.adapter.metadata()
        tel = JobTelemetry(uuid.uuid4().hex[:12], req.persona_id, "replacement", plan.name, model=meta.get("model", ""),
                           checkpoint_hash=meta.get("hash", ""), lora_hash=meta.get("lora_hash", self.lora_hash), seed=req.seed,
                           steps=plan.steps, cfg=plan.cfg, provider=self.provider, engine_version=ENGINE_VERSION,
                           license={k: meta.get(k) for k in ("license", "commercial_use", "license_status")},
                           workflow_versions={"inpaint": meta.get("workflow", "")}, attributes=attrs.to_dict())
        scene = await self._scene(req.image, req.master, plan, attrs)
        tel.attributes["boxes"] = scene.box_policy
        # a MEDICAO usa sempre a segmentacao real (mesmo quando a geracao nao usa: degraus A..D),
        # senao tatuagem/fundo/cabelo ficariam "desconhecidos" justamente onde falham
        mscene = scene if plan.segmentation else await self._scene(req.image, req.master, plan.with_(segmentation=True), attrs)
        h, w = scene.original.shape[:2]
        tel.resolution = [w, h]
        retry = RetryPolicyV2()
        attempts, best = [], None
        attempt = 0
        while True:
            tel.controlnet_strength = {"pose": plan.pose_strength if plan.pose else 0.0, "depth": plan.depth_strength if plan.depth else 0.0}
            tel.reference_strength = plan.face_reference_strength if plan.face_reference else 0.0
            tel.denoise = {"identity": plan.identity_denoise, "face": plan.face_denoise, "tattoo": plan.tattoo_denoise,
                           "body": plan.body_denoise}
            final, loc, inter, info = await self._attempt(req, scene, plan, tel, attrs)
            m = await self._measure(req, mscene, final, loc, info["modified_area"], info.get("modified_mask"), attrs)
            report = validate_v2(m, self.cfg.get("thresholds") or None, attrs.policy)
            rec = {"attempt": attempt, "plan": plan.name, "status": report.status, "failures": report.failures(),
                   "warnings": report.warnings(), "image": loc, "identity": m["identity"]}
            attempts.append(rec)
            rank = (report.status == PASS, report.status != REJECT, m["identity"] or 0.0)
            if best is None or rank > best[0]:
                best = (rank, final, loc, inter, report, m, info)
            if report.status == PASS:
                break
            fails = sorted(report.failures(), key=lambda f: PRIORITY.index(f) if f in PRIORITY else len(PRIORITY))
            failures = _specific(report, fails or _by_severity(report, report.warnings()))
            nxt = retry.next_plan(plan, failures, attempt + 1)
            if nxt is None:
                break
            plan, step = nxt
            step.result = report.status
            attempt += 1
        _, final, loc, inter, report, m, info = best
        tel.retry_count = len(retry.history)
        tel.retries = [s.to_dict() for s in retry.history]
        tel.duration_s = round(time.monotonic() - start, 2)
        tel.estimated_cost_usd = self.adapter.estimate_cost(tel.gpu_seconds, self.price)
        tel.validation_result = report.status
        tel.failure_reason = "; ".join(f"{n}: {report.checks[n].reason}" for n in report.failures()) or None
        areas = {"identity": round(float((scene.identity > 0.5).mean()), 4), **({} if scene.ink_zone is None else
                 {"tattoo_zone": round(float((scene.ink_zone > 0.5).mean()), 4)}), "modified": info["modified_area"],
                 "clothes_segmented": scene.clothes_ok}
        return ReplacementOutcome(loc, final, report.status, report, m, tel, inter if req.keep_intermediates else {},
                                  attempts, areas)


# spec 45.11: violacao da politica vira a falha ESPECIFICA do atributo (nunca retry generico)
_ATTR_FAILURE = {"tattoos": "tattoo", "scars": "tattoo", "birthmarks": "tattoo", "original_person_marks": "tattoo",
                 "source_identity_residual": "original_residual", "face": "identity", "skin": "skin",
                 "clothing": "background", "background": "background", "hair": "background", "accessories": "background",
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
    out = [f for f in failures if f != "attribute_policy"]
    orr = report.checks.get("original_residual")
    if "original_residual" in out and orr is not None:
        face = (orr.metadata or {}).get("rosto_original")
        # residuo so de tatuagem/cabelo (rosto original ja nao parece): a falha especifica e a da marca
        if face is not None and face <= 0.30 and "tattoo" in out:
            out.remove("original_residual")
    if "attribute_policy" in failures:
        viol = report.checks["attribute_policy"].metadata.get("violations", [])
        out += [_ATTR_FAILURE[a] for a in viol if a in _ATTR_FAILURE and _ATTR_FAILURE[a] not in out]
    return out


def _png(pixels: np.ndarray) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8), "RGB").save(buf, "PNG")
    return buf.getvalue()


__all__ = ["ENGINE_VERSION", "ReplacementEngine", "ReplacementOutcome", "ReplacementRequest", "ReplacementRequestError"]
