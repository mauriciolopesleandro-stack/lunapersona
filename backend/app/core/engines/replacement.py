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
from app.core.engines.integration import integrate
from app.core.engines.policies import StagePlan, plan_for
from app.core.engines.retry import RetryPolicyV2
from app.core.engines.telemetry import JobTelemetry
from app.core.engines.validation import (
    PASS,
    REJECT,
    ValidationReportV2,
    changed_fraction,
    lab_mean,
    luma,
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
    tattoo_zones,
)
from app.core.validation.geometry import point
from app.providers.base import ProviderImage, ReferenceImage

ENGINE_VERSION = "replacement-v2.0"


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

    def plan(self) -> StagePlan:
        o = {k: bool(v) for k, v in self.options.items()}
        over: dict[str, Any] = dict(self.advanced)
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
    async def _scene(self, image: str, master: ReferenceImage, plan: StagePlan) -> _Scene:
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
            return _Scene(original, sheet, None, ident, face_full, None, None, None, base_pose, False)
        raw = await self.segmenter.segment(image, sheet)
        raw = replace(raw, protect_boxes=plausible_accessories(raw.protect_boxes, bbox, float(sg["max_accessory_face_ratio"])))
        masks = build_masks(raw, bbox, sheet.target_face.kps, original)
        ident = identity_mask(masks, bbox, float(sg["face_grow_frac"]))
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
        return _Scene(original, sheet, masks, ident, masks.face_full, body, ink, raw.tattoos, base_pose, clothes_ok)

    # --- uma passada ---------------------------------------------------------------------------
    async def _pass(self, name: str, cur: dict, mask: np.ndarray, prompt: str, negative: str, denoise: float, seed: int,
                    plan: StagePlan, tel: JobTelemetry, *, controls: ControlSpec | None = None, identity: IdentitySpec | None = None,
                    image: str | None = None, work_side: int | None = None) -> tuple[np.ndarray, str, float]:
        req = InpaintRequest(image=image or cur["image"], mask=mask, prompt=prompt, negative=negative, denoise=denoise,
                             seed=seed % 2**32, stage=name, controls=controls or ControlSpec(),
                             identity=identity or IdentitySpec(), steps=plan.steps, cfg=plan.cfg,
                             work_side=work_side or plan.work_side)
        res = await self.adapter.inpaint(req)
        px = await self.store.load(res.image)
        keep = mask > 0.02  # fora da mascara: nada vem do modelo
        px = np.where(keep[..., None], px, cur["pixels"])
        loc = await self.store.save(px, name)
        tel.add_pass(name, res.seconds, res.parameters, True)
        return px, loc, res.seconds

    async def _identity_of(self, locator: str, w: int, h: int, master: ReferenceImage):
        return await self.analyzer.analyze(ProviderImage("comfyui", locator, "", w, h), master)

    # --- execucao de um plano ------------------------------------------------------------------
    async def _attempt(self, req: ReplacementRequest, scene: _Scene, plan: StagePlan, tel: JobTelemetry) -> tuple[np.ndarray, str, dict, dict]:
        orig, h, w = scene.original, scene.original.shape[0], scene.original.shape[1]
        P = self.cfg["prompts"]
        negative = ", ".join(dict.fromkeys([t for t in req.negative.split(", ") if t] + list(self.cfg["negative"])))
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
        prompt = P["identity"].replace("{description}", scene.sheet.description())
        px, loc, _ = await self._pass("identity", cur, ident, prompt, negative, plan.identity_denoise, req.seed, plan, tel,
                                      controls=structure)
        cur, modified = {"pixels": px, "image": loc}, np.maximum(modified, ident)
        inter["identity"] = loc
        an = await self._identity_of(loc, w, h, req.master)
        face = an.persona_face()
        score = face.similarity if face else None

        async def try_stage(name, mask, prompt, denoise, seed_off, **kw):
            nonlocal cur, modified, score
            px2, loc2, secs = await self._pass(name, cur, mask, prompt, negative, denoise, req.seed + seed_off, plan, tel, **kw)
            an2 = await self._identity_of(loc2, w, h, req.master)
            f2 = an2.persona_face()
            s2 = f2.similarity if f2 else None
            ok = s2 is not None and (score is None or score - s2 <= drop)
            tel.passes[-1].update(accepted=ok, identity=s2,
                                  reason=None if ok else f"identidade caiu ({score} -> {s2}): mantido o anterior")
            inter[name] = loc2
            if ok:
                cur, modified, score = {"pixels": px2, "image": loc2}, np.maximum(modified, mask), s2

        # PASSE 2: refino de rosto (referencia facial)
        if plan.refinement and (score is None or score < plan.face_refine_if_identity_below):
            idsp = IdentitySpec(use_lora=True, reference=req.master if plan.face_reference else None,
                                reference_strength=plan.face_reference_strength if plan.face_reference else 0.0)
            await try_stage("face_refine", scene.face_full, P["face"], plan.face_denoise, 101, identity=idsp,
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
                await try_stage("body_refine", band, P["body"], plan.body_denoise, 151, controls=structure)
        # limpeza de tatuagem: entrada SEM a tinta; profundidade da imagem LIMPA (a da foto redesenha a tinta)
        if plan.tattoo_cleanup and scene.ink_zone is not None and scene.ink_zone.any() and scene.masks is not None:
            boost = int(plan.extra.get("tattoo_margin_boost", 0))
            zone = dilate(scene.ink_zone, 3 * boost) * (1 - scene.masks.clothing) if boost else scene.ink_zone
            tt = self.cfg["tattoo"]
            soft = np.clip(feather(zone, max(2, int(min(h, w) * float(tt["feather_frac"])))) * (scene.body > 0.5), 0, 1)
            clean = fill_tattoos(cur["pixels"], zone, (scene.masks.skin > 0.5) & (zone < 0.5),
                                 max(6, int(min(h, w) * float(tt["prefill_radius_frac"]))))
            clean_loc = await self.store.save(clean, "tattoo_prefill")
            px2, loc2, _ = await self._pass("tattoo_cleanup", {"pixels": cur["pixels"], "image": clean_loc}, soft, P["tattoo"],
                                            negative, plan.tattoo_denoise, req.seed + 171, plan, tel,
                                            controls=ControlSpec(depth_strength=plan.tattoo_depth_strength, end_percent=0.9,
                                                                 structure=clean_loc),
                                            identity=IdentitySpec(use_lora=False))
            known = (scene.masks.skin > 0.5) & (dilate(zone, max(6, int(min(h, w) * 0.02))) > 0.5) & ~(dilate(zone, 2) > 0.5)
            px2 = soft_tone_match(px2, orig, soft, known, max(6, int(min(h, w) * float(tt["tone_radius_frac"]))))
            loc2 = await self.store.save(px2, "tattoo_tone")
            cur, modified = {"pixels": px2, "image": loc2}, np.maximum(modified, soft)
            inter["tattoo_cleanup"] = loc2
        # integracao fotografica (CPU)
        integ: dict[str, Any] = {}
        if plan.photographic_integration:
            body_skin = None
            if scene.masks is not None:
                body_skin = np.clip(scene.masks.skin * scene.masks.person - modified, 0, 1)
            t0 = time.monotonic()
            px2, integ = integrate(orig, cur["pixels"], modified, face=scene.face_full, body_skin=body_skin, seed=req.seed)
            loc2 = await self.store.save(px2, "integrated")
            cur = {"pixels": px2, "image": loc2}
            tel.add_pass("photographic_integration", 0.0, {"cpu_seconds": round(time.monotonic() - t0, 2), **integ}, True)
            inter["integrated"] = loc2
        # fora do que foi alterado: a FOTO ORIGINAL (garantido aqui, nao confiado ao modelo)
        keep = dilate((modified > 0.02).astype(np.float32), 2) > 0.5
        final = np.where(keep[..., None], cur["pixels"], orig)
        final_loc = await self.store.save(final, "final")
        return final, final_loc, inter, {"modified_area": round(float((modified > 0.5).mean()), 4), "integration": integ}

    # --- medidas para a validacao --------------------------------------------------------------
    async def _measure(self, req: ReplacementRequest, scene: _Scene, final: np.ndarray, final_loc: str, modified_area: float) -> dict[str, Any]:
        orig = scene.original
        h, w = orig.shape[:2]
        an = await self._identity_of(final_loc, w, h, req.master)
        face = an.persona_face()
        dup = float(self.cfg.get("duplicate_similarity", 0.45))
        persona_n = len([f for f in an.faces if f.similarity is not None and f.similarity >= dup])
        x1, y1, x2, y2 = (int(v) for v in scene.sheet.target_face.bbox)
        crop = orig[max(0, y1 - 20):y2 + 20, max(0, x1 - 20):x2 + 20]
        orig_ref = ReferenceImage("original", "original.png", _png(crop), "")
        an_o = await self.analyzer.analyze(ProviderImage("comfyui", final_loc, "", w, h), orig_ref)
        fo = an_o.persona_face()
        from app.core.validation.geometry import pose_distance
        body = an.main_body()
        pose = pose_distance(scene.base_pose, body.keypoints) if scene.base_pose and body else None
        protect = scene.masks.person if scene.masks is not None else np.clip(scene.identity, 0, 1)
        background = changed_fraction(orig, final, 1 - dilate((protect > 0.02).astype(np.float32), 3))
        tattoo_res = None
        if scene.ink_zone is not None and scene.masks is not None and scene.body is not None:
            core = erode(scene.ink_zone, max(2, int(min(h, w) * 0.006)))
            denom = max(1.0, float((core > 0.5).sum()))
            tt = self.cfg["tattoo"]
            px_min = min(h, w)
            f = lambda k: max(1, int(px_min * float(tt[k])))  # noqa: E731
            ink_final = tattoo_zones(final, scene.body, skin_pixels(final) * scene.masks.person, np.zeros((h, w), np.float32),
                                     scene.face_full, f("reach_frac"), f("edge_frac"), float(tt["ink_dy"]), float(tt["ink_dcr"]),
                                     float(tt["ink_dcr_light"]), 99.0, 99.0, 1, 0, 4)
            tattoo_res = round(float(((ink_final > 0.5) & (core > 0.5)).sum()) / denom, 4) if (core > 0.5).any() else 0.0
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
                "modified_area": modified_area}

    # --- job completo --------------------------------------------------------------------------
    async def run(self, req: ReplacementRequest) -> ReplacementOutcome:
        req.validate()
        plan = req.plan()
        start = time.monotonic()
        meta = self.adapter.metadata()
        tel = JobTelemetry(uuid.uuid4().hex[:12], req.persona_id, "replacement", plan.name, model=meta.get("model", ""),
                           checkpoint_hash=meta.get("hash", ""), lora_hash=meta.get("lora_hash", self.lora_hash), seed=req.seed,
                           steps=plan.steps, cfg=plan.cfg, provider=self.provider, engine_version=ENGINE_VERSION,
                           license={k: meta.get(k) for k in ("license", "commercial_use", "license_status")},
                           workflow_versions={"inpaint": meta.get("workflow", "")})
        scene = await self._scene(req.image, req.master, plan)
        # a MEDICAO usa sempre a segmentacao real (mesmo quando a geracao nao usa: degraus A..D),
        # senao tatuagem/fundo/cabelo ficariam "desconhecidos" justamente onde falham
        mscene = scene if plan.segmentation else await self._scene(req.image, req.master, plan.with_(segmentation=True))
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
            final, loc, inter, info = await self._attempt(req, scene, plan, tel)
            m = await self._measure(req, mscene, final, loc, info["modified_area"])
            report = validate_v2(m, self.cfg.get("thresholds") or None)
            rec = {"attempt": attempt, "plan": plan.name, "status": report.status, "failures": report.failures(),
                   "warnings": report.warnings(), "image": loc, "identity": m["identity"]}
            attempts.append(rec)
            rank = (report.status == PASS, report.status != REJECT, m["identity"] or 0.0)
            if best is None or rank > best[0]:
                best = (rank, final, loc, inter, report, m, info)
            if report.status == PASS:
                break
            failures = report.failures() or report.warnings()
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


def _png(pixels: np.ndarray) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8), "RGB").save(buf, "PNG")
    return buf.getvalue()


__all__ = ["ENGINE_VERSION", "ReplacementEngine", "ReplacementOutcome", "ReplacementRequest", "ReplacementRequestError"]
