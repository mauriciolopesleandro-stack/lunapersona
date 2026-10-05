"""Contas com os pontos do DWPose (formato OpenPose de 18 pontos), as mesmas
usadas no benchmark (scripts/benchmark_persona2.py)."""
from __future__ import annotations

import math

Keypoint = tuple[float, float, float]  # x, y, confianca

KP = {"nose": 0, "neck": 1, "rsho": 2, "relb": 3, "rwri": 4, "lsho": 5, "lelb": 6, "lwri": 7,
      "rhip": 8, "rkne": 9, "rank": 10, "lhip": 11, "lkne": 12, "lank": 13}
SEGMENTS = {
    "shoulders": ("rsho", "lsho"), "hips": ("rhip", "lhip"),
    "r_thigh": ("rhip", "rkne"), "l_thigh": ("lhip", "lkne"),
    "r_shin": ("rkne", "rank"), "l_shin": ("lkne", "lank"),
    "r_upper_arm": ("rsho", "relb"), "l_upper_arm": ("lsho", "lelb"),
}
MIN_CONF = 0.3


def point(kp: list[Keypoint], name: str) -> tuple[float, float] | None:
    i = KP[name]
    if i >= len(kp):
        return None
    x, y, c = kp[i]
    return (x, y) if c and c > MIN_CONF else None


def visible(kp: list[Keypoint]) -> int:
    return sum(1 for k in KP if point(kp, k))


def torso(kp: list[Keypoint]) -> tuple[float | None, tuple[float, float] | None]:
    neck, rh, lh = point(kp, "neck"), point(kp, "rhip"), point(kp, "lhip")
    if not (neck and rh and lh):
        return None, None
    mid = ((rh[0] + lh[0]) / 2, (rh[1] + lh[1]) / 2)
    length = math.dist(neck, mid)
    return (length, neck) if length > 1 else (None, None)


def body_ratios(kp: list[Keypoint]) -> dict[str, float]:
    """Segmentos divididos pelo tronco (pescoco ao meio do quadril)."""
    length, _ = torso(kp)
    if not length:
        return {}
    out = {}
    for label, (a, b) in SEGMENTS.items():
        pa, pb = point(kp, a), point(kp, b)
        if pa and pb:
            out[label] = round(math.dist(pa, pb) / length, 3)
    return out


def pose_distance(a: list[Keypoint], b: list[Keypoint], min_points: int = 6) -> float | None:
    """Distancia media entre os pontos do corpo, com o pescoco na origem e
    dividida pelo tronco. 0 = mesma pose. None = pontos insuficientes."""
    ta, na = torso(a)
    tb, nb = torso(b)
    if not (ta and tb):
        return None
    ds = []
    for k in KP:
        pa, pb = point(a, k), point(b, k)
        if pa and pb:
            ds.append(math.dist(((pa[0] - na[0]) / ta, (pa[1] - na[1]) / ta), ((pb[0] - nb[0]) / tb, (pb[1] - nb[1]) / tb)))
    return round(sum(ds) / len(ds), 3) if len(ds) >= min_points else None


def ratio_deviation(measured: dict[str, float], reference: dict[str, float]) -> float | None:
    """Desvio relativo medio nos segmentos medidos dos dois lados."""
    common = [k for k in reference if measured.get(k) and reference.get(k)]
    if len(common) < 3:
        return None
    return round(sum(abs(measured[k] - reference[k]) / reference[k] for k in common) / len(common), 4)
