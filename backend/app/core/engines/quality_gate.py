"""QualityGate do Replacement (spec Master 22/23): validadores com NOME e responsabilidade unica, hard fails
configuraveis e uma decisao por tentativa: PASS / RETRY / REJECT.

Os validadores reaproveitam as medidas da Validation V2 (validate_v2: uma decisao por dimensao) e acrescentam o que
faltava: acessorio POR OBJETO (presenca, posicao, forma, cor, escala), residuo de PIXELS da pessoa original nas
regioes reconstruidas, contagem de pessoas, cabelo e roupa com veredito proprio. Nenhum validador sozinho aprova:
ArcFace alto nao compensa tatuagem, acessorio perdido, pele plastica ou rosto original sobrando.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.engines.validation import (
    DEFAULT_THRESHOLDS,
    PASS,
    REJECT,
    UNKNOWN,
    WARN,
    CheckResult,
    ValidationReportV2,
    _band,
    changed_fraction,
    lab_mean,
    luma,
    validate_v2,
)

RETRY = "RETRY"

# validador -> checks da Validation V2 que ele assina
VALIDATORS: dict[str, tuple[str, ...]] = {
    "IdentityValidator": ("identity",),
    "PoseValidator": ("pose",),
    "AnatomyValidator": ("anatomy", "body"),
    "TattooResidualValidator": ("tattoo",),
    "SourceIdentityResidualValidator": ("original_residual", "source_pixel_residual"),
    "SkinConsistencyValidator": ("skin", "seams"),
    "AccessoryPreservationValidator": ("accessories", "accessory_objects"),
    "ClothingPreservationValidator": ("clothing",),
    "HandValidator": ("hands",),
    "HairValidator": ("hair",),
    "SceneConsistencyValidator": ("background", "composition"),
    "PersonCountValidator": ("duplicate_persona", "person_count"),
    # V3
    "FaceGeometryValidator": ("face_geometry",),
    "GazeValidator": ("gaze",),
    "ClothingValidator": ("clothing_v3",),
    "ReplacementBoundaryValidator": ("boundary",),
    "ReflectionValidator": ("reflection",),
    "TextureValidator": ("skin", "photometric"),
    # V2.1
    "BodyIdentityValidator": ("body_identity",),
    "BodyConsistencyValidator": ("body", "body_identity"),
    "SkinIdentityValidator": ("skin_identity",),
    "SkinContinuityValidator": ("skin_continuity",),
    "PhotometricIntegrationValidator": ("photometric",),
}

GATE_DEFAULTS = {"skin_texture_ratio_min": 0.35, "accessory_presence_min": 0.5, "source_pixel_residual_max": 0.35,
                 "source_pixel_residual_warn": 0.15}


# --- medidas novas (numpy puro) ------------------------------------------------------------------------

def _edges(rgb: np.ndarray, thr: float = 14.0) -> np.ndarray:
    y = luma(rgb)
    gx = np.zeros_like(y)
    gy = np.zeros_like(y)
    gx[:, 1:] = np.abs(np.diff(y, axis=1))
    gy[1:, :] = np.abs(np.diff(y, axis=0))
    return np.maximum(gx, gy) > thr


def measure_accessories(original: np.ndarray, final: np.ndarray, layers: list) -> list[dict[str, Any]]:
    """Cada objeto PRESERVE: presenca (pixels do objeto iguais a foto), cor (dE Lab), forma (IoU das bordas na caixa),
    posicao (deslocamento do centro das bordas / diagonal da caixa) e escala (massa de bordas final/original)."""
    out = []
    h, w = original.shape[:2]
    for layer in layers:
        if layer.policy != "PRESERVE":
            continue
        core = layer.mask > 0.5
        if core.sum() < 10:
            continue
        x1, y1, x2, y2 = (int(v) for v in layer.bbox)
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2 + 1), min(h, y2 + 1)
        changed = changed_fraction(original, final, core.astype(np.float32), threshold=12)
        presence = None if changed is None else round(1.0 - changed, 4)
        a, b = lab_mean(original, core.astype(np.float32)), lab_mean(final, core.astype(np.float32))
        color = None if a is None or b is None else round(float(np.linalg.norm(a - b)), 2)
        eo = _edges(original[y1:y2, x1:x2]) & core[y1:y2, x1:x2]
        ef = _edges(final[y1:y2, x1:x2]) & core[y1:y2, x1:x2]
        shape = pos = scale = None
        if eo.sum() >= 8:
            inter = (eo & ef).sum()
            union = (eo | ef).sum()
            shape = round(float(inter) / max(1.0, float(union)), 4)
            scale = round(float(ef.sum()) / float(eo.sum()), 4)
            if ef.sum() >= 4:
                yo, xo = np.nonzero(eo)
                yf, xf = np.nonzero(ef)
                diag = max(1.0, float(np.hypot(x2 - x1, y2 - y1)))
                pos = round(float(np.hypot(xo.mean() - xf.mean(), yo.mean() - yf.mean())) / diag, 4)
            else:
                pos = 1.0
        out.append({"item": layer.item or layer.label or "objeto", "kind": layer.kind, "presence": presence,
                    "color_delta": color, "shape_iou": shape, "position_shift": pos, "scale_ratio": scale,
                    "area_px": int(core.sum())})
    return out


def source_pixel_residual(original: np.ndarray, final: np.ndarray, region: np.ndarray, threshold: int = 6) -> float | None:
    """Fracao da regiao que a politica manda RECONSTRUIR e que ficou com o pixel da pessoa original (praticamente
    igual a foto). Rosto refeito de verdade fica perto de 0; rosto original sobrando (mascara curta, etapa
    descartada) aparece aqui mesmo quando o ArcFace nao percebe."""
    sel = region > 0.5
    if sel.sum() < 50:
        return None
    same = np.abs(original.astype(np.int16) - final.astype(np.int16)).max(axis=2) <= threshold
    return round(float(same[sel].mean()), 4)


# --- validadores novos -------------------------------------------------------------------------------

def check_accessory_objects(objs: list[dict[str, Any]] | None, required: bool, presence_min: float,
                            hard_fail: bool) -> CheckResult:
    if not objs:
        return CheckResult("accessory_objects", UNKNOWN, None, None, "nenhum acessorio mantido na foto")
    worst, reasons = PASS, []
    for o in objs:
        pres = o.get("presence")
        if pres is None:
            continue
        if pres < presence_min:
            st = REJECT if (required and hard_fail) else WARN
            reasons.append(f"{o['item']} desapareceu/mudou ({pres:.2f} do objeto igual a foto)")
        elif (o.get("shape_iou") is not None and o["shape_iou"] < 0.6) or (o.get("position_shift") or 0) > 0.1 \
                or (o.get("color_delta") or 0) > 12 or abs((o.get("scale_ratio") or 1.0) - 1.0) > 0.3:
            st = WARN
            reasons.append(f"{o['item']}: forma/posicao/cor/escala diferente")
        else:
            continue
        worst = REJECT if REJECT in (worst, st) else WARN
    score = min((o["presence"] for o in objs if o.get("presence") is not None), default=None)
    return CheckResult("accessory_objects", worst, score, {"presence_min": presence_min, "hard_fail": hard_fail},
                       "; ".join(reasons) or "todos os acessorios mantidos no lugar", {"objects": objs})


def check_source_pixels(face_res: float | None, body_res: float | None, warn: float, reject: float,
                        hard_fail: bool) -> CheckResult:
    meta = {"rosto": face_res, "corpo": body_res}
    vals = [v for v in (face_res, body_res) if v is not None]
    if not vals:
        return CheckResult("source_pixel_residual", UNKNOWN, None, None, "sem regiao reconstruida medivel", meta)
    worst, reasons = PASS, []
    for nome, v in (("rosto", face_res), ("corpo", body_res)):
        if v is None:
            continue
        if v > reject:
            st = REJECT if hard_fail else WARN
            reasons.append(f"{nome} da pessoa original ainda na imagem ({v:.0%} da regiao igual a foto)")
        elif v > warn:
            st = WARN
            reasons.append(f"{nome}: {v:.0%} da regiao igual a foto")
        else:
            continue
        worst = REJECT if REJECT in (worst, st) else WARN
    return CheckResult("source_pixel_residual", worst, max(vals), {"warn": warn, "reject": reject}, "; ".join(reasons), meta)


def check_person_count(faces_original: int | None, faces_final: int | None) -> CheckResult:
    if faces_original is None or faces_final is None:
        return CheckResult("person_count", UNKNOWN, None, None, "contagem de rostos indisponivel")
    if faces_final > faces_original:
        return CheckResult("person_count", REJECT, float(faces_final), faces_original,
                           f"apareceu pessoa nova ({faces_original} -> {faces_final} rostos)")
    if faces_final < faces_original:
        return CheckResult("person_count", WARN, float(faces_final), faces_original,
                           f"rosto sumiu ({faces_original} -> {faces_final}): conferir oclusao/oculos")
    return CheckResult("person_count", PASS, float(faces_final), faces_original, "mesmo numero de pessoas")


def check_hair(policy: str | None, hair_residual, hair_change, t) -> CheckResult:
    if policy == "PRESERVE":
        return _band("hair", hair_change, t["hair_change"], False, reason="cabelo PRESERVE: fracao alterada")
    c = _band("hair", hair_residual, t["hair_residual"], False,
              reason="cabelo da Persona: fios claros da pessoa original que sobraram")
    if hair_residual is None:
        c.reason = "cabelo original escuro (sem cor para medir residuo): conferir no olho"
    return c


def check_clothing(policy: str | None, clothing_change, color_delta, t, segmented: bool | None = True) -> CheckResult:
    if policy == "RECONSTRUCT":
        return _band("clothing", color_delta, t["clothing_color_delta"], False,
                     reason="roupa redesenhada: diferenca de cor media (Lab)")
    c = _band("clothing", clothing_change, t["clothing_change"], False,
              reason="roupa PRESERVE: fracao alterada (deformacao/perda)")
    if segmented is False and c.status in (PASS, UNKNOWN):
        c = CheckResult("clothing", WARN, c.score, c.threshold,
                        "roupa nao segmentada pelo detector: medida aproximada (nao-pele dentro da pessoa) - conferir no olho")
    return c


def check_v21(m: dict[str, Any], thr: dict[str, Any], hard: bool, body_pixels) -> dict[str, CheckResult]:
    """V2.1: continuidade de pele, identidade de pele, fotometria e identidade corporal. Limites PRELIMINARES (config)."""
    out = {}
    cont = m.get("skin_continuity") or {}
    worst = cont.get("worst")
    t = thr.get("transition_dE", {"pass": 6.0, "reject": 12.0})
    if worst is None:
        out["skin_continuity"] = CheckResult("skin_continuity", UNKNOWN, None, t, "pele exposta insuficiente para medir transicoes",
                                             cont)
    else:
        st = PASS if worst <= t["pass"] else (REJECT if worst > t["reject"] and hard else WARN)
        bad = [k for k, v in (cont.get("transitions") or {}).items() if v["dE"] > t["pass"]]
        out["skin_continuity"] = CheckResult("skin_continuity", st, worst, t,
                                             ("transicao de pele fora da luz da foto: " + ", ".join(bad)) if bad else
                                             "rosto, pescoco e corpo com a mesma pele sob a mesma luz", cont)
    si = m.get("skin_identity") or {}
    hd = si.get("hue_diff_deg")
    th = thr.get("skin_hue_deg", {"pass": 25.0})
    out["skin_identity"] = CheckResult("skin_identity", UNKNOWN if hd is None else (PASS if hd <= th["pass"] else WARN), hd, th,
                                       "subtom da pele x master (a luz da cena muda; so aviso)", si)
    ph = m.get("photometric") or {}
    gr, hl = ph.get("grain_ratio"), ph.get("face_highlight_dL")
    tg = thr.get("grain_ratio", {"low": 0.5, "high": 2.0})
    th_hl = thr.get("face_highlight_dL", {"max": 12.0})
    reasons, st = [], (UNKNOWN if gr is None and hl is None else PASS)
    if gr is not None and not tg["low"] <= gr <= tg["high"]:
        st, reasons = WARN, reasons + [f"grao/nitidez da area refeita {gr:.2f}x o resto da foto"]
    if hl is not None and abs(hl) > th_hl["max"]:
        st, reasons = WARN, reasons + [f"brilho do rosto {hl:+.1f} L x a foto"]
    out["photometric"] = CheckResult("photometric", st, gr, {"grain": tg, "highlight": th_hl}, "; ".join(reasons) or
                                     "grao e brilho compativeis com a foto", ph)
    bi = m.get("body_identity") or {}
    dev, lim = bi.get("deviation"), bi.get("max_deviation")
    reasons = []
    if body_pixels is not None and body_pixels > 0.35:
        status, reasons = REJECT, [f"corpo da pessoa original ({body_pixels:.0%} da pele igual a foto)"]
    elif bi.get("status") == "MEASURED" and dev is not None and lim is not None:
        status = PASS if dev <= lim else WARN
        reasons = [f"proporcoes {dev:.1%} da Persona (limite {lim:.0%}, mesma pose da master)"]
    else:
        status = UNKNOWN
        reasons = [bi.get("reason") or "proporcoes nao comparaveis nesta pose"]
    out["body_identity"] = CheckResult("body_identity", status, dev, {"max_deviation": lim}, "; ".join(reasons), bi)
    return out


def check_v3(m: dict[str, Any], thr: dict[str, Any]) -> dict[str, CheckResult]:
    """V3 (preliminar, sem benchmark): borda/halo, roupa por identidade visual, geometria do rosto, olhar, reflexo."""
    out: dict[str, CheckResult] = {}
    b = m.get("boundary") or {}
    tb = thr.get("halo_dE", {"pass": 3.0, "reject": 6.0})
    if b.get("status") != "MEASURED":
        out["boundary"] = CheckResult("boundary", UNKNOWN, None, tb, "faixa de fundo em volta da pessoa pequena demais", b)
    else:
        d = b["halo_dE"]
        st = PASS if d <= tb["pass"] else REJECT if d > tb["reject"] else WARN
        sr = b.get("sharpness_ratio")
        ts = thr.get("halo_sharpness", {"low": 0.6, "high": 1.6})
        why = [f"fundo repintado em volta da pessoa (dE {d:.1f})"] if st != PASS else []
        if sr is not None and not ts["low"] <= sr <= ts["high"]:
            st, why = (WARN if st == PASS else st), why + [f"nitidez da borda {sr:.2f}x a foto"]
        out["boundary"] = CheckResult("boundary", st, d, {"halo_dE": tb, "sharpness": ts}, "; ".join(why) or "sem halo", b)
    c = m.get("clothing_v3") or {}
    tc = thr.get("clothing_dE", {"pass": 10.0, "reject": 20.0})
    if c.get("status") != "MEASURED":
        out["clothing_v3"] = CheckResult("clothing_v3", UNKNOWN, None, tc, c.get("reason", "sem medida"), c)
    else:
        de, new = c.get("color_dE_max", 0.0), c.get("new_clothing_on_skin", 0.0)
        st = PASS if de <= tc["pass"] else REJECT if de > tc["reject"] else WARN
        why = [] if st == PASS else [f"cor da roupa diferente (dE {de:.1f})"]
        tn = thr.get("new_clothing_on_skin", {"pass": 0.01, "reject": 0.03})
        if c.get("straps_invented") or new > tn["reject"]:
            st, why = REJECT, why + ["peca/alca inventada sobre a pele" + (f" ({new:.1%} da roupa)" if new else "")]
        elif new > tn["pass"] and st == PASS:
            st, why = WARN, why + [f"roupa nova sobre a pele ({new:.1%})"]
        iou = c.get("shape_iou_loose")
        if iou is not None and iou < float(thr.get("clothing_iou_min", 0.5)):
            st, why = (WARN if st == PASS else st), why + [f"forma da roupa diferente (IoU {iou:.2f})"]
        out["clothing_v3"] = CheckResult("clothing_v3", st, de, {"dE": tc, "new_on_skin": tn}, "; ".join(why) or
                                         "mesma roupa (cor, alcas, forma) vestindo o corpo da Persona", c)
    g = m.get("face_geometry") or {}
    tg = thr.get("face_geometry", {"center_shift": 0.1, "roll_deg": 8.0, "yaw": 0.25})
    if "center_shift" not in g:
        out["face_geometry"] = CheckResult("face_geometry", UNKNOWN, None, tg, "rosto nao medido", g)
        out["gaze"] = CheckResult("gaze", UNKNOWN, None, tg, "olhar nao medido (sem rosto)", g)
    else:
        bad = [k for k, lim in (("center_shift", tg["center_shift"]), ("roll_diff_deg", tg["roll_deg"]))
               if g.get(k) is not None and g[k] > lim]
        worse = [k for k, lim in (("center_shift", tg["center_shift"] * 2), ("roll_diff_deg", tg["roll_deg"] * 2))
                 if g.get(k) is not None and g[k] > lim]
        st = REJECT if worse else WARN if bad else PASS
        out["face_geometry"] = CheckResult("face_geometry", st, g.get("center_shift"), tg,
                                           ("posicao/inclinacao do rosto mudou: " + ", ".join(bad)) if bad else
                                           "rosto no mesmo lugar e inclinacao da foto", g)
        yd = g.get("yaw_diff")
        st = UNKNOWN if yd is None else PASS if yd <= tg["yaw"] else REJECT if yd > tg["yaw"] * 2 else WARN
        out["gaze"] = CheckResult("gaze", st, yd, {"yaw": tg["yaw"], "method": "orientacao da cabeca (sem iris)"},
                                  "direcao do rosto/olhar como na foto" if st == PASS else "rosto/olhar virado diferente da foto", g)
    r = m.get("reflection_original_sim")
    tr = thr.get("reflection_original", {"reject": 0.4})
    out["reflection"] = CheckResult("reflection", UNKNOWN if r is None else (REJECT if r > tr["reject"] else PASS), r, tr,
                                    "sem segundo rosto" if r is None else
                                    ("reflexo/segundo rosto ainda e a pessoa original" if r > tr["reject"] else
                                     "reflexo sem a pessoa original"))
    return out


# --- o gate ------------------------------------------------------------------------------------------

@dataclass
class GateResult:
    decision: str  # PASS | RETRY | REJECT
    report: ValidationReportV2
    hard_fails: list[str] = field(default_factory=list)
    validators: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "hard_fails": self.hard_fails, "validators": self.validators,
                "quality_score": QualityGate.quality_score(self.report)}


class QualityGate:
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.cfg = cfg
        self.thresholds = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}
        self.limits = {**GATE_DEFAULTS, **(cfg.get("hard_fail") or {})}

    def _hard(self, key: str) -> bool:
        return bool((self.cfg.get(key) or {}).get("hard_fail", True))

    def evaluate(self, m: dict[str, Any], policy: dict[str, str] | None = None) -> ValidationReportV2:
        t = self.thresholds
        rep = validate_v2(m, t, policy)
        ch = rep.checks
        policy = policy or {}
        # TattooResidualValidator: qualquer tinta da pessoa original acima do limite de aprovacao = REJECT
        if self._hard("tattoo_removal") and policy.get("tattoos", "REMOVE") == "REMOVE":
            c = ch["tattoo"]
            if c.status == WARN:
                ch["tattoo"] = CheckResult("tattoo", REJECT, c.score, c.threshold,
                                           f"tatuagem residual ({c.score}) - hard fail: nao pertence a Persona", c.metadata)
        # SkinConsistencyValidator: pele extremamente plastica = REJECT (o resto da pele segue WARN)
        sk = ch["skin"]
        ratio = (sk.metadata or {}).get("razao_textura")
        if ratio is not None and ratio < float(self.limits["skin_texture_ratio_min"]):
            ch["skin"] = CheckResult("skin", REJECT, sk.score, sk.threshold,
                                     f"pele extremamente plastica (textura {ratio:.2f} da foto)", sk.metadata)
        ch["accessory_objects"] = check_accessory_objects(m.get("accessory_objects"), policy.get("accessories") == "PRESERVE"
                                                          or policy.get("jewelry") == "PRESERVE",
                                                          float(self.limits["accessory_presence_min"]),
                                                          self._hard("accessory_preservation"))
        ch["source_pixel_residual"] = check_source_pixels(m.get("source_face_pixels"), m.get("source_body_pixels"),
                                                          float(self.limits["source_pixel_residual_warn"]),
                                                          float(self.limits["source_pixel_residual_max"]),
                                                          self._hard("source_identity_residual"))
        ch["person_count"] = check_person_count(m.get("faces_original"), m.get("faces"))
        ch["hair"] = check_hair(policy.get("hair"), m.get("hair_residual"), m.get("hair_change"), t)
        if "clothing_v3" in m:  # V3
            ch.update(check_v3(m, self.cfg.get("v3_thresholds") or {}))
        if "skin_continuity" in m:  # V2.1
            sc = self.cfg.get("skin_continuity") or {}
            ch.update(check_v21(m, sc.get("thresholds") or {}, self._hard("skin_continuity"), m.get("source_body_pixels")))
        ch["clothing"] = check_clothing(policy.get("clothing"), m.get("clothing_change"), m.get("clothing_color_delta"), t,
                                        m.get("clothes_segmented"))
        return rep

    def summary(self, rep: ValidationReportV2) -> dict[str, dict[str, Any]]:
        out = {}
        for name, checks in VALIDATORS.items():
            sts = [rep.checks[c].status for c in checks if c in rep.checks]
            st = REJECT if REJECT in sts else WARN if WARN in sts else PASS if PASS in sts else UNKNOWN
            out[name] = {"status": st, "checks": {c: rep.checks[c].status for c in checks if c in rep.checks},
                         "reasons": [rep.checks[c].reason for c in checks if c in rep.checks and rep.checks[c].status
                                     in (REJECT, WARN) and rep.checks[c].reason]}
        return out

    @staticmethod
    def quality_score(rep: ValidationReportV2) -> float | None:
        """Media das notas por check (PASS 1, WARN 0,5, REJECT 0; sem medida fica fora). Nunca esconde hard fail:
        a decisao vem dos REJECT, nao desta media."""
        vals = [{PASS: 1.0, WARN: 0.5, REJECT: 0.0}[c.status] for c in rep.checks.values() if c.status in (PASS, WARN, REJECT)]
        return round(sum(vals) / len(vals), 3) if vals else None

    def decide(self, rep: ValidationReportV2, can_retry: bool) -> GateResult:
        hard = rep.failures()
        if rep.status == PASS:
            decision = PASS
        elif can_retry and self.cfg.get("adaptive_retry", True):
            decision = RETRY
        elif hard:
            decision = REJECT
        else:
            decision = PASS  # so avisos e sem retry possivel: aprovado com observacoes (status WARN no relatorio)
        return GateResult(decision, rep, hard, self.summary(rep))


__all__ = ["GateResult", "QualityGate", "RETRY", "VALIDATORS", "check_accessory_objects", "check_person_count",
           "check_source_pixels", "measure_accessories", "source_pixel_residual"]
