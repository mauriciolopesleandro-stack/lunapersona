"""Tabela do benchmark V1.1 a partir de bench_v11.json."""
import json
import statistics
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
cfgs = data["configs"]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def row(name):
    jobs = cfgs[name]["jobs"]
    res = [j["results"][-1] for j in jobs if j["results"]]
    checks = [r["validation"]["checks"] if r.get("validation") else {} for r in res]
    skin = [r.get("skin") or {} for r in res]
    src = cfgs.get({"C": "A", "D": "B"}.get(name, ""), None)
    out = {
        "config": name,
        "n": len(res),
        "accepted": sum(1 for j in jobs if j["status"] == "ACCEPTED"),
        "face": mean([r.get("face_score") for r in res]),
        "face_min": min([r["face_score"] for r in res if r.get("face_score") is not None], default=None),
        "skin": mean([s.get("skin_realism_score") for s in skin]),
        "skin_status": [s.get("skin_realism_status") for s in skin],
        "skin_texture": mean([(s.get("skin_realism_detail") or {}).get("raw", {}).get("texture") for s in skin]),
        "skin_oversmooth": mean([(s.get("skin_realism_detail") or {}).get("raw", {}).get("oversmoothed_fraction") for s in skin]),
        "age": mean([c.get("age", {}).get("score") for c in checks]),
        "age_consistency": mean([s.get("age_score") for s in skin]),
        "pose": [c.get("pose", {}).get("status") for c in checks],
        "body": [c.get("body_consistency", {}).get("status") for c in checks],
        "subjects": [c.get("subject_count", {}).get("status") for c in checks],
        "corr_applied": sum(1 for s in skin if s.get("skin_correction_applied")),
        "corr_tried": sum(s.get("skin_correction_attempts") or 0 for s in skin),
        "corr_seconds": mean([s.get("skin_correction_duration") for s in skin if s.get("skin_correction_attempts")]),
        "guard_reasons": [g["reasons"] for s in skin for g in (s.get("guard") or []) if not g["accepted"]],
        "failures": [f["failure_type"] for j in jobs for f in j["failures"]],
        "wall_seconds": cfgs[name]["wall_seconds"],
        "vram_max": max([r["metrics"].get("vram_used_mb_max") or 0 for r in res], default=None),
    }
    # tempo/custo por imagem: A/B = wall do lote / n; C/D = A/B + correcao (o resto e o mesmo)
    per = cfgs[name]["wall_seconds"] / max(1, len(res))
    if src:
        per = src["wall_seconds"] / max(1, len(src["jobs"])) + cfgs[name]["wall_seconds"] / max(1, len(res))
    out["seconds_per_image"] = round(per, 1)
    out["cost_per_image"] = round(0.57 * per / 3600, 4)
    out["per_image"] = [
        {"scene": i + 1, "face": r.get("face_score"), "skin": s.get("skin_realism_score"),
         "skin_before": s.get("skin_realism_before_correction"), "applied": s.get("skin_correction_applied"),
         "age": c.get("age", {}).get("score"), "image": r.get("image_url"), "status": r.get("status")}
        for i, (r, s, c) in enumerate(zip(res, skin, checks))
    ]
    return out


for name in ["A", "B", "C", "D"]:
    if name in cfgs:
        print(json.dumps(row(name), ensure_ascii=False))
