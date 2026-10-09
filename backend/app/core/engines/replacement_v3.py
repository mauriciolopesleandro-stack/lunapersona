"""Persona Replacement V3 - FULL PERSON RECONSTRUCTION.

  "Reconstruir a Luna na estrutura da fotografia" - nao editar a pessoa original ate ela parecer a Luna.

  FOTO -> analise da cena -> segmentacao (pessoa x cena) -> DWPose (corpo, maos, 68 pontos do rosto) ->
  geometria do rosto / olhar -> profundidade (da foto LIMPA, so espacial) -> roupa e acessorios como CONDICAO ->
  Persona Canon (corpo/pele/cabelo) -> UMA reconstrucao da pessoa inteira (rosto, pescoco, corpo, bracos, pernas,
  maos, pele, cabelo e roupa no mesmo passe: mesma luz, textura, grao e nitidez) -> refino de identidade ADAPTATIVO
  (InstantID; Qwen so no rosto e so se a identidade cair) -> pele/luz -> composicao na cena (fundo devolvido,
  acessorios/objetos da frente por cima) -> validacao (FullReconstructionQualityGate)

Diferenca para a V2: a V2 editava regioes (rosto, corpo, mao, tatuagem) e colava; a V3 refaz a pessoa toda de uma
vez e so recorre a passes locais (mao, reflexo) no modo HIBRIDO, quando a reconstrucao completa falha num ponto.
A roupa nao e colada por pixel: a foto vira ClothingCondition (cor medida, alcas, corte, comprimento) e a roupa e
re-renderizada vestindo o corpo da Persona. A V2 continua disponivel (replacement_version "v2"/"v2.1").
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.engines.adapter import ControlSpec, IdentitySpec
from app.core.engines.accessories import composite_layers
from app.core.engines.attributes import PRESERVE, RECONSTRUCT, AttributePolicy, resolve
from app.core.engines.conditions_v3 import (
    boundary_halo,
    clothing_sentences,
    clothing_condition,
    clothing_consistency,
    compare_face_geometry,
    face_geometry,
    gaze,
    silhouette,
)
from app.core.engines.integration import integrate
from app.core.engines.policies import StagePlan
from app.core.engines.quality_gate import QualityGate, check_v3
from app.core.engines.replacement import (
    QWEN_PRESERVE_CONTEXT,
    ReplacementEngine,
    ReplacementRequest,
    ReplacementRequestError,
    hand_ratio,
    person_text,
    restore_background,
    scrub_identity,
)
from app.core.engines.skin import structure_preserving_fill
from app.core.engines.skin_continuity import harmonize
from app.core.engines.scene_analysis import semantic_masks
from app.core.engines.telemetry import JobTelemetry
from app.core.persona_replacement.blending import feather
from app.core.persona_replacement.segmentation import dilate, ellipse, erode
from app.core.persona_replacement.transfer import dilate_round
from app.providers.base import ProviderImage, ReferenceImage

ENGINE_VERSION_V3 = "replacement-v3.0-full-person"
MODES = ("full_reconstruction", "hybrid")
NO_BEAUTIFY = ["heavy makeup", "glamour makeup", "contour", "false eyelashes", "enlarged eyes", "plump lips",
               "slim nose", "beauty retouch", "airbrushed skin", "glossy skin", "doll face", "earrings", "choker",
               "new jewelry"]

# nomes da spec V3 -> atributos que a politica conhece (os estruturais nao sao atributos: vem das condicoes)
_STRUCTURAL = {"gaze", "camera", "scene", "anatomy", "source_identity"}
_ALIASES = {"camera": "camera_angle", "scene": "background", "source_marks": "original_person_marks"}


@dataclass
class ReplacementV3Request:
    """Pedido no formato da spec V3. `to_request` traduz para o ReplacementRequest da engine (politica de
    atributos, versao, modo). Roupa em preserve E reconstruct = roupa RE-RENDERIZADA com a mesma identidade visual."""
    source_image: str
    persona_id: str
    master: ReferenceImage
    mode: str = "full_reconstruction"
    preserve: list[str] = field(default_factory=list)
    reconstruct: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    identity_refinement: dict[str, Any] = field(default_factory=lambda: {"provider": "adaptive", "qwen_allowed": True})
    seed: int = 7801
    negative: str = ""
    persona_sheet: dict[str, Any] | None = None
    debug: bool = False
    advanced: dict[str, Any] = field(default_factory=dict)

    def to_request(self) -> ReplacementRequest:
        if self.mode not in MODES:
            raise ReplacementRequestError(f"modo V3 invalido: {self.mode} (use {MODES})")

        def norm(xs):
            return [_ALIASES.get(x, x) for x in xs if x not in _STRUCTURAL]

        rec = norm(self.reconstruct)
        pre = [x for x in norm(self.preserve) if x not in rec]  # roupa nos dois: re-renderizada (RECONSTRUCT)
        rem = [x for x in norm(self.remove) if x not in pre]
        qa = self.identity_refinement or {}
        return ReplacementRequest(image=self.source_image, persona_id=self.persona_id, master=self.master, mode="QUALITY",
                                  seed=self.seed, negative=self.negative, persona_sheet=self.persona_sheet,
                                  preserve_attributes=pre, reconstruct_attributes=rec, remove_attributes=rem,
                                  replacement_version="v3", debug=self.debug, advanced=dict(self.advanced),
                                  qwen_face_lock=None if qa.get("qwen_allowed", True) else False,
                                  reconstruction_mode=self.mode)


class PersonaReplacementV3(ReplacementEngine):
    """Mesma analise/segmentacao/medidas/gate da V2; o _attempt e a reconstrucao da PESSOA INTEIRA."""

    engine_version = ENGINE_VERSION_V3

    def _gate(self) -> QualityGate:
        return QualityGate(self.cfg)

    def _configured(self, plan: StagePlan, req: ReplacementRequest) -> StagePlan:
        p = super()._configured(plan, req)
        fr = self.cfg.get("full_reconstruction") or {}
        kw: dict[str, Any] = {}
        if not req.explicit("pose_strength") and fr.get("pose_strength") is not None:
            kw["pose_strength"] = float(fr["pose_strength"])
        if not req.explicit("depth_strength") and fr.get("depth_strength") is not None:
            kw["depth_strength"] = float(fr["depth_strength"])
        ir = self.cfg.get("identity_refinement") or {}
        extra = dict(p.extra)
        # Qwen ADAPTATIVO: so se permitido e so quando a identidade cair (decidido no _attempt)
        extra["qwen_face_lock"] = False
        extra["qwen_allowed"] = (req.qwen_face_lock is not False) and bool(ir.get("qwen_allowed", True))
        extra["mode"] = req.reconstruction_mode or fr.get("mode", "full_reconstruction")
        return p.with_(extra=extra, **kw)

    # --- reconstrucao ---------------------------------------------------------------------------------
    async def _attempt(self, req: ReplacementRequest, scene, plan: StagePlan, tel: JobTelemetry,
                       attrs: AttributePolicy | None = None):
        if scene.masks is None:
            raise ReplacementRequestError("V3 precisa da segmentacao da pessoa (degraus sem segmentacao sao da V2)")
        attrs = attrs or resolve()
        orig = scene.original
        h, w = orig.shape[:2]
        m = scene.masks
        fr = self.cfg.get("full_reconstruction") or {}
        acc = self.cfg["acceptance"]
        P = self.cfg["prompts"]
        cond, negative = self._conditioning(attrs, req.negative)
        canon = self.__dict__.get("_canon")
        inter: dict[str, str] = {}
        kp = scene.base_pose
        face = scene.sheet.target_face

        # condicoes estruturais (a foto da a ESTRUTURA)
        geom = face_geometry(face)
        gz = gaze(geom)
        keep_clothes = attrs.is_("clothing", PRESERVE) and plan.extra.get("mode") == "hybrid"
        caption = (getattr(scene.sheet, "fields", {}) or {}).get("clothing", "") or \
            clothing_sentences(getattr(scene.sheet, "caption", "") or "")
        cloth = clothing_condition(orig, m.clothing if scene.clothes_ok else None, kp, caption=caption)
        await self._describe_garments(req, cloth, w, h)
        tel.attributes["v3_conditions"] = {
            "mode": plan.extra.get("mode"), "pose": {"keypoints": len(kp or []), "strength": plan.pose_strength,
                                                     "hands": len(scene.hands or [])},
            "face_geometry": None if geom is None else geom.to_dict(), "gaze": gz.to_dict(),
            "depth": {"strength": plan.depth_strength, "source": "foto limpa (sem marcas)", "role": "estrutura espacial"},
            "clothing": cloth.to_dict(), "clothing_pixels_kept": keep_clothes,
            "accessories": [x.to_dict() for x in scene.layers]}

        # PESSOA = reconstruir; CENA = preservar. Regiao = pessoa + folga (silhueta da Persona cabe) + cabelo/cabeca
        grow = max(3, int(min(h, w) * float(fr.get("grow_frac", 0.025)) * (1 + int(plan.extra.get("region_grow", 0)))))
        person = (m.person > 0.5).astype(np.float32)
        region = np.maximum(dilate(person, grow), (scene.identity > 0.5).astype(np.float32))
        if keep_clothes:  # HIBRIDO: roupa pixel a pixel (so quando pedido)
            region *= 1 - (dilate((m.clothing > 0.5).astype(np.float32), 2) > 0.5)
        # pes/calcado ficam da foto (abaixo dos tornozelos)
        from app.core.validation.geometry import point
        if kp:
            ankles = [p for p in (point(kp, "rank"), point(kp, "lank")) if p]
            if ankles:
                region[int(max(a[1] for a in ankles)):, :] = 0

        # entrada LIMPA: marcas da pessoa original tiradas antes (a reconstrucao nao redesenha a tatuagem)
        src = orig
        if scene.markings is not None and attrs.removes_skin_markings():
            known = ((m.skin > 0.5) & (m.person > 0.5) & ~(dilate_round(scene.markings, 2) > 0.5)).astype(np.float32)
            src, _ = structure_preserving_fill(orig, dilate_round(scene.markings, 2), known,
                                               line_radius=max(2, int(min(h, w) * 0.004)))
        clean_loc = await self.store.save(src, "v3_clean")
        inter["v3_clean_input"] = clean_loc

        body_txt = canon.physical.body_text_en() if canon is not None else "curvy hourglass figure"
        skin_txt = canon.physical.skin_text_en() if canon is not None else "natural skin"
        hair_txt = "long dark brown wavy hair"
        scene_txt = person_text(scrub_identity(scene.sheet.description()))
        template = (self.cfg.get("prompts_v3") or {}).get(
            "full", "lunavox, a woman with {body}, {skin}, {hair}, wearing {clothing}, {scene}, natural face, natural "
                    "skin with pores, same lighting as the photo, candid unretouched smartphone photo")
        prompt = cond(template.format(body=body_txt, skin=skin_txt, hair=hair_txt,
                                      clothing=cloth.prompt() or "the same clothes as the photo", scene=scene_txt), "identity")
        neg = ", ".join(dict.fromkeys([t for t in negative.split(", ") if t] + cloth.negative() + NO_BEAUTIFY
                                      + list(self.cfg.get("body_skin_negative", []))))
        layers = scene.layers
        if plan.extra.get("accessory_grow"):
            from dataclasses import replace as dc_replace
            layers = [dc_replace(x, mask=dilate(x.mask, int(plan.extra["accessory_grow"]))) for x in scene.layers]
        paste = None if not layers else (lambda px_, within=None: composite_layers(px_, orig, layers, within))
        soft = np.clip(feather(region, max(2, grow // 3)), 0, 1) * (region > 0.02)
        denoise = float(plan.extra.get("full_denoise", fr.get("denoise", 0.85)))
        ctrl = ControlSpec(pose_strength=plan.pose_strength, depth_strength=plan.depth_strength if plan.depth else 0.0,
                           end_percent=plan.control_end, structure=clean_loc)
        px, loc, _ = await self._pass("full_reconstruction", {"pixels": orig, "image": clean_loc}, soft, prompt, neg, denoise,
                                      req.seed + 301, plan, tel, controls=ctrl, identity=IdentitySpec(use_lora=True),
                                      accessory=paste, work_side=int(fr.get("work_side", plan.work_side)))
        tel.passes[-1]["params"].update(prompt=prompt[:400], region_area=round(float((region > 0.5).mean()), 4))
        px = restore_background(px, orig, m.person, region,
                                tol=int(plan.extra.get("background_tolerance", fr.get("background_tolerance", 28))))
        loc = await self.store.save(px, "v3_full")
        inter["initial_reconstruction"] = loc
        cur, modified = {"pixels": px, "image": loc}, (region > 0.02).astype(np.float32)
        an = await self._identity_of(loc, w, h, req.master)
        f0 = an.persona_face()
        score = f0.similarity if f0 else None
        tel.passes[-1]["identity"] = score

        # refino de identidade ADAPTATIVO: InstantID leve no rosto (so se precisar)
        below = float((self.cfg.get("identity_refinement") or {}).get("instantid_below", 0.72))
        if plan.face_reference and (score is None or score < below):
            idsp = IdentitySpec(use_lora=True, reference=req.master, reference_strength=plan.face_reference_strength)
            px2, loc2, _ = await self._pass("face_refine", cur, scene.face_full, cond(P["face"], "face"),
                                            ", ".join([neg]), float(fr.get("face_denoise", 0.35)), req.seed + 311, plan, tel,
                                            identity=idsp, controls=ControlSpec(structure=loc), accessory=paste)
            an2 = await self._identity_of(loc2, w, h, req.master)
            s2 = an2.persona_face().similarity if an2.persona_face() else None
            ok = s2 is not None and (score is None or s2 >= score)
            tel.passes[-1].update(identity=s2, accepted=ok, reason=None if ok else f"identidade nao subiu ({score} -> {s2})")
            inter["identity_refinement"] = loc2
            if ok:
                cur, score = {"pixels": px2, "image": loc2}, s2

        # Qwen SO no rosto (sem orelhas) e SO se a identidade continuar baixa
        qbelow = float((self.cfg.get("identity_refinement") or {}).get("qwen_below", 0.65))
        if plan.extra.get("qwen_allowed") and self.face_lock is not None and (score is None or score < qbelow):
            from app.providers.base import FaceLockGuidance
            out = await self.face_lock.lock_face(ProviderImage("comfyui", cur["image"], "", w, h), req.master, req.seed + 321,
                                                 guidance=FaceLockGuidance(positive=list(QWEN_PRESERVE_CONTEXT)))
            q = await self.store.load(out.image.locator)
            if q.shape[:2] != (h, w):
                from PIL import Image as _Img
                q = np.asarray(_Img.fromarray(q).resize((w, h), _Img.LANCZOS))
            x1, y1, x2, y2 = face.bbox
            inner = np.zeros((h, w), np.float32)
            fw = x2 - x1
            inner[max(0, int(y1)):int(y2) + 1, max(0, int(x1 + fw * 0.12)):int(x2 - fw * 0.12) + 1] = 1  # sem orelhas
            core = ((scene.face_full > 0.5) * inner).astype(np.float32)
            reg = np.clip(feather(core, 4), 0, 1) * (core > 0.02)  # borda suave so para DENTRO
            q = (q.astype(np.float32) * reg[..., None] + cur["pixels"].astype(np.float32) * (1 - reg[..., None]) + 0.5).astype(np.uint8)
            if paste is not None:
                q = paste(q)
            ql = await self.store.save(q, "face_lock")
            an3 = await self._identity_of(ql, w, h, req.master)
            s3 = an3.persona_face().similarity if an3.persona_face() else None
            ok = s3 is not None and s3 >= float(acc.get("face_lock_floor", 0.65)) and (score is None or s3 > score)
            tel.add_pass("face_lock", out.seconds or 0.0, {"scope": "face_inner", "adaptive": True, "identity_before": score,
                                                           "guidance": QWEN_PRESERVE_CONTEXT}, ok,
                         None if ok else f"Qwen nao melhorou a identidade ({score} -> {s3}): mantido o anterior")
            tel.passes[-1]["identity"] = s3
            if "qwen_face_lock" not in tel.experimental_stages:
                tel.experimental_stages.append("qwen_face_lock")
            inter["face_lock"] = ql
            if ok:
                cur, score = {"pixels": q, "image": ql}, s3

        # HIBRIDO: mao refeita so se a reconstrucao perdeu dedos (gesto medivel)
        if plan.extra.get("mode") == "hybrid" or plan.extra.get("hand_retry"):
            hm = self._hand_mask(scene, measurable_only=True)
            if hm is not None:
                before = await self._hand_counts(req.image, w, h, req.master)
                now = await self._hand_counts(cur["image"], w, h, req.master)
                r = hand_ratio(before, now)
                if r is not None and r < float(acc.get("hand_min_ratio", 0.85)):
                    hand = await self._hand_stage(req, scene, plan, tel, cur, hm * (dilate(person, 4) > 0.5), neg, cond,
                                                  paste, inter, w, h)
                    if hand is not None:
                        cur = hand

        # reflexo da MESMA pessoa (espelho): o reflexo tambem vira Persona
        refl = await self._reflection_regions(req, scene, w, h)
        tel.attributes["v3_reflections"] = len(refl)
        for i, rr in enumerate(refl):
            rr = rr * (1 - (modified > 0.5))
            if rr.sum() < 200:
                continue
            pxr, locr, _ = await self._pass(f"reflection_{i}", cur, rr, prompt, neg, denoise,
                                            req.seed + 331 + i + 17 * int(plan.extra.get("reflection_retry", 0)), plan, tel,
                                            controls=ctrl, identity=IdentitySpec(use_lora=True), accessory=paste)
            cur, modified = {"pixels": pxr, "image": locr}, np.maximum(modified, rr)
            inter[f"reflection_{i}"] = locr

        # pele + luz: a pessoa ja nasceu inteira; continuidade so se a medida pedir
        sc_cfg = self.cfg.get("skin_continuity") or {}
        if sc_cfg.get("enabled"):
            t0 = time.monotonic()
            smasks = semantic_masks(scene, self._hand_mask(scene))
            res = harmonize(cur["pixels"], orig, smasks, kp, face.bbox, max_dl=float(sc_cfg.get("max_dl", 14.0)),
                            max_dab=float(sc_cfg.get("max_dab", 8.0)), boost=1.0 + 0.5 * float(plan.extra.get("skin_boost", 0)))
            ok = res.applied and (res.after.get("worst") or 0) <= (res.before.get("worst") or 0)
            if ok:
                l2 = await self.store.save(res.pixels, "skin_integration")
                inter["skin_integration"] = l2
                changed = np.abs(res.pixels.astype(np.int16) - cur["pixels"].astype(np.int16)).max(axis=2) > 0
                cur, modified = {"pixels": res.pixels, "image": l2}, np.maximum(modified, changed.astype(np.float32))
            tel.add_pass("skin_continuity", 0.0, {"cpu_seconds": round(time.monotonic() - t0, 2), **res.to_dict()}, ok,
                         None if ok else (res.note or "sem ganho"))
        integ = {}
        if plan.photographic_integration:
            px3, integ = integrate(orig, cur["pixels"], modified, face=scene.face_full, body_skin=None, seed=req.seed)
            l3 = await self.store.save(px3, "photometric_integration")
            inter["photometric_integration"] = l3
            cur = {"pixels": px3, "image": l3}
            tel.add_pass("photometric_integration", 0.0, integ, True, None)

        # composicao na cena: fora da pessoa reconstruida = a foto; acessorios/objetos da frente por cima
        keep = dilate((modified > 0.02).astype(np.float32), 2) > 0.5
        final = np.where(keep[..., None], cur["pixels"], orig)
        if scene.layers:
            final = composite_layers(final, orig, scene.layers, within=keep.astype(np.float32))
        final_loc = await self.store.save(final, "final")
        inter["scene_composite"] = final_loc
        if req.keep_intermediates:
            for nm, mk in [("person_mask", person), ("background_mask", 1 - person), ("v3_region", region),
                           ("clothing_mask", m.clothing), ("tattoo_mask", scene.markings),
                           ("accessory_mask", scene.accessory), ("mask_face", scene.face_full)]:
                if mk is not None:
                    inter[nm] = await self.store.save(np.repeat((np.clip(mk, 0, 1) * 255).astype(np.uint8)[..., None], 3, 2), nm)
            if req.debug:
                from app.core.engines.scene_analysis import render_pose_map
                body = scene.sheet.target_body
                inter["pose_map"] = await self.store.save(render_pose_map(h, w, kp, getattr(body, "hands", None) if body else None),
                                                          "pose_map")
                inter["original"] = req.image
                inter["final"] = final_loc
        self._v3_last = {"region": region, "clothing": cloth, "geom": geom}
        return final, final_loc, inter, {"modified_area": round(float((modified > 0.5).mean()), 4), "integration": integ,
                                         "modified_mask": modified, "body_region": region}

    async def _describe_garments(self, req, cloth, w, h) -> None:
        """Descricao de CADA peca recortada (Florence, legenda detalhada): botao, passantes, textura, decote, laco.
        09/10: so cor/alcas/corte no texto -> o modelo trocou short de alfaiataria por short de cordao."""
        describer = getattr(self, "describer", None)
        if describer is None:
            return
        cache = self.__dict__.setdefault("_garment_cache", {})
        for g in cloth.garments:
            key = (req.image, tuple(round(v) for v in g.bbox))
            if key not in cache:
                x1, y1, x2, y2 = g.bbox
                px, py = (x2 - x1) * 0.08, (y2 - y1) * 0.08
                box = (max(0.0, x1 - px), max(0.0, y1 - py), min(float(w), x2 + px), min(float(h), y2 + py))
                try:
                    cache[key] = clothing_sentences(await describer.describe(req.image, box))
                except Exception:  # noqa: BLE001 - sem descricao: fica a cor/corte medidos
                    cache[key] = ""
            g.details = cache[key]

    async def _reflection_regions(self, req, scene, w, h) -> list[np.ndarray]:
        """Rostos da MESMA pessoa original em outro lugar da foto (reflexo no espelho) -> regiao de cabeca a refazer."""
        if not (self.cfg.get("reflection") or {}).get("enabled", True):
            return []
        others = list(getattr(scene.sheet, "others", []) or [])
        if not others:
            return []
        from app.core.engines.replacement import _png
        x1, y1, x2, y2 = (int(v) for v in scene.sheet.target_face.bbox)
        mx, my = int((x2 - x1) * 0.6), int((y2 - y1) * 0.6)
        crop = scene.original[max(0, y1 - my):y2 + my, max(0, x1 - mx):x2 + mx]
        ref = ReferenceImage("original", "original.png", _png(crop), "")
        an = await self.analyzer.analyze(ProviderImage("comfyui", req.image, "", w, h), ref)
        main_c = ((x1 + x2) / 2, (y1 + y2) / 2)
        thr = float((self.cfg.get("reflection") or {}).get("same_person_similarity", 0.45))
        out = []
        for f in an.faces:
            fx1, fy1, fx2, fy2 = f.bbox
            c = ((fx1 + fx2) / 2, (fy1 + fy2) / 2)
            if abs(c[0] - main_c[0]) < (x2 - x1) * 0.5 and abs(c[1] - main_c[1]) < (y2 - y1) * 0.5:
                continue  # e a propria pessoa principal
            if f.similarity is not None and f.similarity >= thr:
                fw, fh = fx2 - fx1, fy2 - fy1
                out.append(np.clip(ellipse(h, w, c[0], c[1] - fh * 0.05, fw * 1.0, fh * 1.1), 0, 1))
        return out

    # --- medidas ------------------------------------------------------------------------------------
    async def _measure(self, req, scene, final, final_loc, modified_area, modified_mask=None, attrs=None, body_region=None):
        m = await super()._measure(req, scene, final, final_loc, modified_area, modified_mask, attrs, body_region)
        h, w = final.shape[:2]
        # roupa re-renderizada: avaliada por identidade visual (nao por pixel)
        m["clothing_change"] = None
        last = self.__dict__.get("_v3_last") or {}
        seg = None
        try:
            seg = await self.segmenter.segment(final_loc, scene.sheet)
        except Exception:  # noqa: BLE001 - sem segmentacao do resultado: medidas de roupa/silhueta ficam UNKNOWN
            seg = None
        clothes_f = getattr(seg, "clothes", None) if seg is not None else None
        person_f = getattr(seg, "person", None) if seg is not None else None
        hair_f = getattr(seg, "hair", None) if seg is not None else None
        an = await self._identity_of(final_loc, w, h, req.master)
        body = an.main_body()
        kp_f = body.keypoints if body is not None else None
        m["clothing_v3"] = clothing_consistency(scene.original, final, scene.masks.clothing if scene.clothes_ok else None,
                                                clothes_f, scene.masks.person, scene.base_pose, hair_f=hair_f)
        m["silhouette"] = {"original": silhouette(scene.masks.person, scene.base_pose),
                           "final": silhouette(person_f, kp_f or scene.base_pose)}
        m["boundary"] = boundary_halo(scene.original, final, scene.masks.person,
                                      last.get("region") if last.get("region") is not None else scene.identity, person_f)
        fface = an.persona_face()
        m["face_geometry"] = compare_face_geometry(last.get("geom") or face_geometry(scene.sheet.target_face), face_geometry(fface))
        # reflexo ainda com a pessoa original?
        refl_orig = None
        if (self.cfg.get("reflection") or {}).get("enabled", True):
            from app.core.engines.replacement import _png
            x1, y1, x2, y2 = (int(v) for v in scene.sheet.target_face.bbox)
            mx, my = int((x2 - x1) * 0.6), int((y2 - y1) * 0.6)
            crop = scene.original[max(0, y1 - my):y2 + my, max(0, x1 - mx):x2 + mx]
            an_o = await self.analyzer.analyze(ProviderImage("comfyui", final_loc, "", w, h),
                                               ReferenceImage("original", "original.png", _png(crop), ""))
            cx, cy, fw = (x1 + x2) / 2, (y1 + y2) / 2, max(1, x2 - x1)
            others = [f.similarity for f in an_o.faces if f.similarity is not None
                      and math.dist(((f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2), (cx, cy)) > fw * 0.6]
            refl_orig = round(max(others), 3) if others else None  # rosto FORA da pessoa principal ainda parecido com a original
        m["reflection_original_sim"] = refl_orig
        return m


__all__ = ["ENGINE_VERSION_V3", "MODES", "PersonaReplacementV3", "ReplacementV3Request"]
