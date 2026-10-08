"""Validation Engine V2: uma decisao POR DIMENSAO (nunca um score composto unico).

Dimensoes: identity, body, pose, anatomy, skin, tattoo, original_residual, duplicate_persona,
background, composition, integration. Cada check -> score, threshold, status (PASS/WARN/REJECT/
UNKNOWN), reason, metadata. Hard fail (REJECT) so com confianca. Uma imagem com identidade 0,85
pode ser REJECT por residuo da pessoa original.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

PASS, WARN, REJECT, UNKNOWN = "PASS", "WARN", "REJECT", "UNKNOWN"

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "identity": {"pass": 0.70, "reject": 0.50},
    "original_face_similarity": {"warn": 0.30, "reject": 0.40},
    "pose": {"pass": 0.12, "reject": 0.25},
    "background": {"pass": 0.002, "reject": 0.02},
    "tattoo_residual": {"pass": 0.03, "reject": 0.15},
    "hair_residual": {"pass": 0.08, "reject": 0.30},
    "skin_texture_ratio": {"low": 0.55, "high": 1.9},
    "face_body_tone_delta": {"pass": 6.0, "warn": 10.0},
    "body_shoulder_ratio": {"tolerance": 0.08},
    "composition_shift": {"pass": 0.01, "reject": 0.05},
    # emenda: degrau de cor na borda da regiao alterada ALEM do que a foto ja tinha na mesma borda;
    # blocos: bordas retas longas novas por mil pixels da regiao. Calibrado no smoke test de 2026-10-07
    # (etapas limpas: emenda 5,1-5,9 / bordas 6,4-8,4; final com mancha e quadrados: 11,2 / 13,3) - preliminar, 1 foto.
    "seam_excess": {"pass": 7.0, "reject": 10.0},
    "straight_edges": {"pass": 9.0, "reject": 12.0},
    # atributos PRESERVE: fracao alterada DENTRO do atributo (fora do que a politica manda reconstruir)
    "clothing_change": {"pass": 0.01, "reject": 0.05},
    "hair_change": {"pass": 0.02, "reject": 0.08},
    "accessory_change": {"pass": 0.02, "reject": 0.08},
    "clothing_color_delta": {"pass": 12.0, "reject": 25.0},  # roupa REDESENHADA: cor media Lab parecida
}


@dataclass
class CheckResult:
    name: str
    status: str
    score: float | None = None
    threshold: Any = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "score": self.score, "threshold": self.threshold,
                "reason": self.reason, "metadata": self.metadata}


@dataclass
class ValidationReportV2:
    checks: dict[str, CheckResult]

    @property
    def status(self) -> str:
        sts = [c.status for c in self.checks.values()]
        return REJECT if REJECT in sts else WARN if WARN in sts else PASS

    def failures(self) -> list[str]:
        return [n for n, c in self.checks.items() if c.status == REJECT]

    def warnings(self) -> list[str]:
        return [n for n, c in self.checks.items() if c.status == WARN]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "failures": self.failures(), "warnings": self.warnings(),
                "checks": {n: c.to_dict() for n, c in self.checks.items()}}


# --- medidas de imagem (numpy puro) --------------------------------------------------------------

def luma(rgb: np.ndarray) -> np.ndarray:
    x = rgb.astype(np.float32)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


def _laplacian(y: np.ndarray) -> np.ndarray:
    p = np.pad(y, 1, mode="edge")
    return p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4 * y


def texture_energy(rgb: np.ndarray, region: np.ndarray) -> float | None:
    """Microtextura (poros/grao): desvio do laplaciano na regiao, normalizado pela luminancia media."""
    sel = region > 0.5
    if sel.sum() < 50:
        return None
    y = luma(rgb)
    lap = _laplacian(y)[sel]
    return float(lap.std() / max(float(y[sel].mean()), 1.0) * 100)


def lab_mean(rgb: np.ndarray, region: np.ndarray) -> np.ndarray | None:
    sel = region > 0.5
    if sel.sum() < 50:
        return None
    x = rgb[sel].astype(np.float32) / 255.0
    # sRGB -> Lab aproximado (D65), suficiente para delta de tom
    def lin(c):
        return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    r, g, b = lin(x[:, 0]), lin(x[:, 1]), lin(x[:, 2])
    X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)  # noqa: E731
    fx, fy, fz = f(X), f(Y), f(Z)
    return np.array([float((116 * fy - 16).mean()), float((500 * (fx - fy)).mean()), float((200 * (fy - fz)).mean())])


def changed_fraction(a: np.ndarray, b: np.ndarray, region: np.ndarray, threshold: int = 6) -> float | None:
    sel = region > 0.5
    if sel.sum() == 0:
        return None
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > threshold)[sel].mean())


def _box(y: np.ndarray, r: int) -> np.ndarray:
    """Media numa janela (2r+1)^2 (soma acumulada; bordas replicadas)."""
    k = 2 * r + 1
    c = np.pad(np.pad(y.astype(np.float64), r, mode="edge").cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return ((c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)).astype(np.float32)


def _grow(m: np.ndarray, r: int) -> np.ndarray:
    return _box((m > 0.5).astype(np.float32), r) > 1e-6


def _shrink(m: np.ndarray, r: int) -> np.ndarray:
    return _box((m > 0.5).astype(np.float32), r) > 1 - 1e-6


def seam_excess(original: np.ndarray, final: np.ndarray, region: np.ndarray, radius: int = 3,
                ignore: np.ndarray | None = None) -> float | None:
    """Emenda na borda da regiao: media local de cada lado da borda (dentro x fora), o degrau de cor
    resultante, MENOS o degrau que a foto original ja tinha na mesma borda (cabelo x parede existe nas
    duas). Media do excesso no anel da borda; ~5 numa colagem limpa, >10 com mancha de borda dura."""
    reg = region > 0.5
    if reg.sum() < 50 or (~reg).sum() < 50:
        return None
    ins, out = reg.astype(np.float32), (~reg).astype(np.float32)
    ring = _grow(reg, 2) & ~_shrink(reg, 2)
    if ignore is not None:  # ex.: borda cabelo x fundo (varanda: cabelo loiro -> escuro contra o ceu e legitimo)
        ring &= ~(ignore > 0.5)
    if not ring.any():
        return None
    di, do = np.maximum(_box(ins, radius), 1e-3), np.maximum(_box(out, radius), 1e-3)
    steps = []
    for src in (final, original):
        src = src.astype(np.float32)
        steps.append(np.max([np.abs(_box(src[..., c] * ins, radius) / di - _box(src[..., c] * out, radius) / do)
                             for c in range(3)], axis=0))
    return round(float(np.clip(steps[0] - steps[1], 0, None)[ring].mean()), 3)


def _runs(b: np.ndarray, run: int) -> np.ndarray:
    """Pixels em sequencias verticais (eixo 0) de True com comprimento >= run."""
    h = b.shape[0]
    if h < run:
        return np.zeros_like(b)
    c = np.concatenate([np.zeros((1, b.shape[1]), np.int32), b.astype(np.int32).cumsum(0)])
    start = (c[run:] - c[:-run]) == run  # janela [i, i+run) toda True
    d = np.zeros((h + 1, b.shape[1]), np.int32)
    d[:h - run + 1] += start
    d[run:] -= start
    return d.cumsum(0)[:h] > 0


def straight_edges(original: np.ndarray, final: np.ndarray, region: np.ndarray, run: int = 8, thr: float = 10.0,
                   ignore: np.ndarray | None = None, min_area_frac: float = 0.01) -> float | None:
    """Bordas retas (horizontais/verticais) longas DENTRO da regiao que a foto original nao tinha, por mil
    pixels da regiao: pega os quadrados/blocos de remendo que o olho ve na hora."""
    inner = _shrink(region, 3)
    if ignore is not None:  # rosto/cabelo NOVOS tem tracos novos (olhos, boca, fios) - nao sao blocos de remendo
        inner &= ~(ignore > 0.5)
    if inner.sum() < 50:
        return None

    def straight(src):
        y = luma(src)
        vx = np.zeros(y.shape, bool)
        vx[:, 1:] = _runs(np.abs(np.diff(y, axis=1)) > thr, run)
        hy = np.zeros(y.shape, bool)
        hy[1:, :] = _runs((np.abs(np.diff(y, axis=0)) > thr).T, run).T
        return vx | hy

    extra = straight(final) & ~_grow(straight(original), 2) & inner
    # denominador minimo: regiao minuscula (faixa de 5 mil px) com 80 px de cacho virava "14 por mil" (foto da porta)
    denom = max(float(inner.sum()), min_area_frac * inner.size)
    return round(float(extra.sum()) / denom * 1000.0, 3)


# --- checks --------------------------------------------------------------------------------------

def _band(name, score, thr, higher_is_better=True, unit_pass="pass", unit_rej="reject", reason=""):
    if score is None:
        return CheckResult(name, UNKNOWN, None, thr, reason or "sem medida")
    p, r = thr[unit_pass], thr[unit_rej]
    if higher_is_better:
        st = PASS if score >= p else REJECT if score < r else WARN
    else:
        st = PASS if score <= p else REJECT if score > r else WARN
    return CheckResult(name, st, round(float(score), 4), thr, reason)


def check_identity(similarity, thr):
    return _band("identity", similarity, thr, True, reason="semelhanca com a Luna (media das referencias)")


def check_original_residual(original_sim, tattoo_residual, hair_residual, thr_face, thr_tattoo, thr_hair):
    """Residuo da pessoa ORIGINAL (separado de identidade)."""
    parts = {"rosto_original": original_sim, "tatuagem": tattoo_residual, "cabelo": hair_residual}
    worst, reasons = PASS, []
    if original_sim is None:  # nao medido nao e "passou": o rosto original e justamente o que mais importa aqui
        worst, reasons = WARN, ["semelhanca com o rosto original NAO medida"]
    if original_sim is not None and original_sim > thr_face["reject"]:
        worst, reasons = REJECT, reasons + [f"rosto original ainda presente ({original_sim:.2f})"]
    elif original_sim is not None and original_sim > thr_face["warn"]:
        worst, reasons = WARN, reasons + [f"semelhanca com o rosto original {original_sim:.2f}"]
    for nome, val, thr in (("tatuagem", tattoo_residual, thr_tattoo), ("cabelo", hair_residual, thr_hair)):
        if val is None:
            continue
        if val > thr["reject"]:
            worst, reasons = REJECT, reasons + [f"{nome} original evidente ({val:.2f})"]
        elif val > thr["pass"] and worst != REJECT:
            worst, reasons = WARN, reasons + [f"{nome} original parcial ({val:.2f})"]
    score = max([v for v in (original_sim, tattoo_residual, hair_residual) if v is not None], default=None)
    return CheckResult("original_residual", worst if any(v is not None for v in parts.values()) else UNKNOWN,
                       None if score is None else round(float(score), 4),
                       {"face": thr_face, "tattoo": thr_tattoo, "hair": thr_hair}, "; ".join(reasons), parts)


def check_skin(texture_final, texture_ref, tone_delta, thr_tex, thr_tone):
    """Pele fotografica: microtextura proxima da foto (nem plastico nem nitidez exagerada) e tom do
    rosto coerente com o corpo. Nunca e o unico criterio de aprovacao."""
    meta = {"textura_final": texture_final, "textura_ref": texture_ref, "delta_tom_rosto_corpo": tone_delta}
    if texture_final is None or texture_ref is None or texture_ref <= 0:
        return CheckResult("skin", UNKNOWN, None, thr_tex, "sem regiao de pele medivel", meta)
    ratio = texture_final / texture_ref
    meta["razao_textura"] = round(ratio, 3)
    status, reasons = PASS, []
    if ratio < thr_tex["low"]:
        status, reasons = WARN, ["pele lisa/plastica (pouca microtextura)"]
    elif ratio > thr_tex["high"]:
        status, reasons = WARN, ["nitidez/textura exagerada"]
    if tone_delta is not None and tone_delta > thr_tone["warn"]:
        status, reasons = WARN, reasons + [f"tom do rosto diferente do corpo (dE {tone_delta:.1f})"]
    score = max(0.0, 1.0 - abs(np.log(max(ratio, 1e-3))) / np.log(3.0))
    return CheckResult("skin", status, round(float(score), 3), {"textura": thr_tex, "tom": thr_tone}, "; ".join(reasons), meta)


def check_duplicate(persona_instances, faces):
    if persona_instances is None:
        return CheckResult("duplicate_persona", UNKNOWN, None, 1, "sem analise de rostos")
    if persona_instances > 1:
        return CheckResult("duplicate_persona", REJECT, float(persona_instances), 1, "segunda Luna na imagem",
                           {"rostos": faces})
    return CheckResult("duplicate_persona", PASS, float(persona_instances), 1,
                       "pessoas de fundo permitidas (nao parecidas com a Luna)" if (faces or 0) > 1 else "", {"rostos": faces})


def check_body(shoulder_ratio, thr):
    if shoulder_ratio is None:
        return CheckResult("body", UNKNOWN, None, thr, "ombros nao detectados nas duas imagens")
    dev = abs(shoulder_ratio - 1.0)
    st = PASS if dev <= thr["tolerance"] else WARN if dev <= 2 * thr["tolerance"] else REJECT
    return CheckResult("body", st, round(float(shoulder_ratio), 3), thr, "largura de ombros final/original (pose e escala mantidas)")


def check_seams(seam, edges, thr_seam, thr_edges):
    """Emendas e blocos visiveis na regiao alterada (o defeito que o olho ve primeiro)."""
    meta = {"emenda": seam, "bordas_retas": edges}
    if seam is None and edges is None:
        return CheckResult("seams", UNKNOWN, None, {"seam": thr_seam, "edges": thr_edges}, "sem regiao alterada", meta)
    worst, reasons = PASS, []
    for nome, val, thr in (("emenda/mancha de borda", seam, thr_seam), ("blocos/bordas retas", edges, thr_edges)):
        if val is None:
            continue
        if val > thr["reject"]:
            worst, reasons = REJECT, reasons + [f"{nome} visivel ({val:.1f})"]
        elif val > thr["pass"] and worst != REJECT:
            worst, reasons = WARN, reasons + [f"{nome} ({val:.1f})"]
    score = max(v for v in (seam, edges) if v is not None)
    return CheckResult("seams", worst, round(float(score), 3), {"seam": thr_seam, "edges": thr_edges}, "; ".join(reasons), meta)


def check_anatomy(person_found: bool):
    if not person_found:
        return CheckResult("anatomy", REJECT, 0.0, None, "pessoa principal desaparecida")
    return CheckResult("anatomy", UNKNOWN, None, None, "sem detector de anatomia (maos/dedos): conferir no olho")


# atributo -> (medida, limite, maior_e_melhor, o que significa). Sem medida = UNKNOWN (listado, nao aprova sozinho).
_REMOVE_MEASURES = {
    "tattoos": ("tattoo_residual", "tattoo_residual", "tinta da pessoa original que sobrou"),
    "scars": ("tattoo_residual", "tattoo_residual", "marca escura na pele (mesmo detector das tatuagens; parcial)"),
    "birthmarks": ("tattoo_residual", "tattoo_residual", "marca escura na pele (mesmo detector das tatuagens; parcial)"),
    "original_person_marks": ("tattoo_residual", "tattoo_residual", "marcas da pessoa original na pele"),
}
_PRESERVE_MEASURES = {
    "background": ("background", "background", "fracao alterada fora da pessoa"),
    "clothing": ("clothing_change", "clothing_change", "fracao alterada na roupa"),
    "pose": ("pose", "pose", "distancia da pose original"),
    "composition": ("composition_shift", "composition_shift", "deslocamento do enquadramento"),
    "hair": ("hair_change", "hair_change", "fracao alterada no cabelo (cabelo PRESERVE)"),
    "accessories": ("accessory_change", "accessory_change", "fracao alterada nos acessorios mantidos"),
}


def check_attribute_policy(m: dict[str, Any], policy: dict[str, str], t: dict[str, Any]) -> CheckResult:
    """Regra final (spec 45.12): PERSONA + PRESERVE + REMOVE + RECONSTRUCT ao mesmo tempo. Uma violacao
    REJEITA mesmo com identidade excelente - identidade nao compensa atributo proibido."""
    verdicts: dict[str, dict[str, Any]] = {}

    def put(attr, pol, status, score=None, thr=None, reason=""):
        verdicts[attr] = {"policy": pol, "status": status, "score": score, "threshold": thr, "reason": reason}

    for attr, pol in policy.items():
        if pol == "REMOVE":
            if attr in _REMOVE_MEASURES and attr != "tattoos" and policy.get("tattoos") == "PRESERVE":
                put(attr, pol, UNKNOWN, reason="mesmo detector das tatuagens, que foram mantidas: sem medida separada")
            elif attr in _REMOVE_MEASURES:
                key, tk, why = _REMOVE_MEASURES[attr]
                c = _band(attr, m.get(key), t[tk], False, reason=why)
                put(attr, pol, c.status, c.score, t[tk], why)
            elif attr in ("piercings", "jewelry", "makeup"):
                put(attr, pol, UNKNOWN, reason="sem detector automatico: o rosto/regiao e reconstruido; conferir no olho")
        elif pol == "PRESERVE":
            if attr in _PRESERVE_MEASURES:
                key, tk, why = _PRESERVE_MEASURES[attr]
                c = _band(attr, m.get(key), t[tk], False, reason=why)
                put(attr, pol, c.status, c.score, t[tk], why)
            else:
                put(attr, pol, UNKNOWN, reason="sem medida automatica")
        elif pol == "RECONSTRUCT":
            if attr == "face":
                c = check_identity(m.get("identity"), t["identity"])
                put(attr, pol, c.status if m.get("identity") is not None else REJECT, c.score, t["identity"],
                    "rosto da Persona (identidade)")
                src = m.get("original_sim")
                thr = t["original_face_similarity"]
                st = UNKNOWN if src is None else REJECT if src > thr["reject"] else WARN if src > thr["warn"] else PASS
                put("source_identity_residual", "REMOVE", st, src, thr, "semelhanca com o rosto ORIGINAL")
            elif attr == "clothing":
                c = _band(attr, m.get("clothing_color_delta"), t["clothing_color_delta"], False,
                          reason="roupa redesenhada: diferenca de cor media (Lab)")
                put(attr, pol, c.status, c.score, t["clothing_color_delta"], "roupa redesenhada parecida (mesma cor)")
            elif attr == "body":
                put(attr, pol, UNKNOWN, reason="corpo da Persona: sem medida automatica de proporcao (DWPose nao mede cintura)")
            elif attr == "skin":
                c = check_skin(m.get("texture_final"), m.get("texture_ref"), m.get("tone_delta"), t["skin_texture_ratio"],
                               t["face_body_tone_delta"])
                put(attr, pol, c.status, c.score, None, c.reason or "pele natural")
            else:
                put(attr, pol, UNKNOWN, reason="sem medida automatica")
    sts = [v["status"] for v in verdicts.values()]
    status = REJECT if REJECT in sts else WARN if WARN in sts else PASS
    bad = [f"{a} ({v['policy']})" for a, v in verdicts.items() if v["status"] == REJECT]
    reason = ("violacao da politica de atributos: " + ", ".join(bad)) if bad else "politica de atributos cumprida nas medidas disponiveis"
    return CheckResult("attribute_policy", status, None, None, reason, {"verdicts": verdicts,
                                                                        "violations": [a for a, v in verdicts.items() if v["status"] == REJECT]})


def validate_v2(m: dict[str, Any], thresholds: dict[str, Any] | None = None,
                policy: dict[str, str] | None = None) -> ValidationReportV2:
    """m: medidas ja calculadas pela engine (identity, original_sim, pose, background, tattoo_residual,
    hair_residual, texture_final, texture_ref, tone_delta, persona_instances, faces, shoulder_ratio,
    composition_shift, person_found)."""
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    checks = {
        "identity": check_identity(m.get("identity"), t["identity"]),
        "pose": _band("pose", m.get("pose"), t["pose"], False, reason="distancia da pose original"),
        "background": _band("background", m.get("background"), t["background"], False, reason="fracao alterada fora da pessoa"),
        "tattoo": _band("tattoo", m.get("tattoo_residual"), t["tattoo_residual"], False,
                        reason="tinta da pessoa original que sobrou (fracao da tinta original)"),
        "original_residual": check_original_residual(m.get("original_sim"), m.get("tattoo_residual"), m.get("hair_residual"),
                                                     t["original_face_similarity"], t["tattoo_residual"], t["hair_residual"]),
        "duplicate_persona": check_duplicate(m.get("persona_instances"), m.get("faces")),
        "skin": check_skin(m.get("texture_final"), m.get("texture_ref"), m.get("tone_delta"), t["skin_texture_ratio"],
                           t["face_body_tone_delta"]),
        "body": check_body(m.get("shoulder_ratio"), t["body_shoulder_ratio"]),
        "anatomy": check_anatomy(bool(m.get("person_found", True))),
        "seams": check_seams(m.get("seam_excess"), m.get("straight_edges"), t["seam_excess"], t["straight_edges"]),
        "composition": _band("composition", m.get("composition_shift"), t["composition_shift"], False,
                             reason="deslocamento global da imagem (enquadramento)"),
    }
    if m.get("identity") is None and m.get("person_found", True):
        checks["identity"] = CheckResult("identity", REJECT, None, t["identity"], "rosto nao encontrado na imagem final")
    if policy is not None:
        if policy.get("tattoos") == "PRESERVE":  # tatuagem pedida como PRESERVE: nao e residuo
            checks["tattoo"] = CheckResult("tattoo", PASS, None, None, "tatuagens PRESERVE pela politica (nao validadas como residuo)")
            checks["original_residual"] = check_original_residual(m.get("original_sim"), None, m.get("hair_residual"),
                                                                  t["original_face_similarity"], t["tattoo_residual"],
                                                                  t["hair_residual"])
        checks["attribute_policy"] = check_attribute_policy(m, policy, t)
    return ValidationReportV2(checks)


__all__ = ["CheckResult", "DEFAULT_THRESHOLDS", "PASS", "REJECT", "UNKNOWN", "ValidationReportV2", "WARN",
           "changed_fraction", "lab_mean", "luma", "texture_energy", "validate_v2"]
