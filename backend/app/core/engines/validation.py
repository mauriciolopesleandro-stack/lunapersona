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


def check_anatomy(person_found: bool):
    if not person_found:
        return CheckResult("anatomy", REJECT, 0.0, None, "pessoa principal desaparecida")
    return CheckResult("anatomy", UNKNOWN, None, None, "sem detector de anatomia (maos/dedos): conferir no olho")


def validate_v2(m: dict[str, Any], thresholds: dict[str, Any] | None = None) -> ValidationReportV2:
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
        "composition": _band("composition", m.get("composition_shift"), t["composition_shift"], False,
                             reason="deslocamento global da imagem (enquadramento)"),
    }
    if m.get("identity") is None and m.get("person_found", True):
        checks["identity"] = CheckResult("identity", REJECT, None, t["identity"], "rosto nao encontrado na imagem final")
    return ValidationReportV2(checks)


__all__ = ["CheckResult", "DEFAULT_THRESHOLDS", "PASS", "REJECT", "UNKNOWN", "ValidationReportV2", "WARN",
           "changed_fraction", "lab_mean", "luma", "texture_energy", "validate_v2"]
