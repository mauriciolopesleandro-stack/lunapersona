"""Resumo do benchmark E + C2 (scripts/benchmark_persona2.py).

  python scripts/analyze_benchmark2.py <pasta com results.json, gpu.csv, ram.log>

So numeros medidos: ArcFace (LunaFaces), corpos e pontos (DWPose), diferenca de
pixels contra a imagem base, distancia de pose, tempos, VRAM e RAM do pod.
"""
import calendar
import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

folder = Path(sys.argv[1])
data = json.loads((folder / "results.json").read_text(encoding="utf-8"))
refs = data.pop("_refs", {})
RATIOS = ["shoulders", "hips", "r_thigh", "l_thigh", "r_shin", "l_shin", "r_upper_arm", "l_upper_arm"]
PRICE = float(sys.argv[2]) if len(sys.argv) > 2 else 0.57

gpu = []
for line in (folder / "gpu.csv").read_text().splitlines():
    try:
        ts, used, total, util = [x.strip() for x in line.split(",")]
        t = calendar.timegm(datetime.strptime(ts, "%Y/%m/%d %H:%M:%S.%f").timetuple())
        gpu.append((t, int(used.split()[0]), int(total.split()[0]), int(util.split()[0])))
    except ValueError:
        continue
ram = []
if (folder / "ram.log").exists():
    for line in (folder / "ram.log").read_text().splitlines():
        a, b = line.split()
        ram.append((int(a), int(b)))


def stats(xs, digits=3):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "NOT MEASURED"
    return f"{st.mean(xs):.{digits}f} ± {st.pstdev(xs):.{digits}f} (min {min(xs):.{digits}f}, max {max(xs):.{digits}f}, n={len(xs)})"


def persona_face(m):
    faces = m.get("faces") or []
    return max(faces, key=lambda f: f.get("sim") or 0) if faces else None


def window(rows):
    spans = [(r["started"], r["started"] + r["seconds"]) for r in rows if r.get("started")]
    return (min(s for s, _ in spans), max(e for _, e in spans)) if spans else (None, None)


print("REFS:", {k: (v.get("body") or {}).get("shoulders") for k, v in refs.items()})
for stage in ["E0", "E1", "E2", "C2A", "C2B", "C2C", "CQ", "ST"]:
    rows = [r for k, r in sorted(data.items()) if r.get("stage") == stage and (stage != "ST" or True)]
    if not rows:
        continue
    for sub in sorted({r.get("kind", "") for r in rows}):
        group = [r for r in rows if r.get("kind", "") == sub]
        ok = [r for r in group if r.get("measure")]
        name = f"{stage}{'/' + sub if sub else ''}"
        print(f"\n=== {name}: {len(group)} jobs, {len(ok)} medidos, erros {[r['key'] for r in group if r.get('error')]}")
        print("  modelo:", group[0].get("model"), "| LoRA:", group[0].get("lora"), "| workflow:", group[0].get("workflow"))
        sims = [(persona_face(r["measure"]) or {}).get("sim") for r in ok]
        ages = [(persona_face(r["measure"]) or {}).get("age") for r in ok]
        print("  rosto ArcFace (melhor rosto vs MASTER):", stats(sims))
        print("  idade estimada:", stats(ages, 1))
        nf = [len(r["measure"].get("faces") or []) for r in ok]
        nb = [len(r["measure"].get("bodies") or []) for r in ok]
        big = [sum(1 for b in r["measure"].get("bodies") or [] if b["height_frac"] >= 0.25) for r in ok]
        print("  rostos/imagem:", nf, "| corpos DWPose:", nb, "| corpos >=25% da altura:", big)
        fails = [r["key"] for r in ok if len(r["measure"].get("faces") or []) > 1 or len(r["measure"].get("bodies") or []) > 1]
        print(f"  MULTIPLE_SUBJECTS FAIL (todos os rostos/corpos): {len(fails)}/{len(ok)} {fails}")
        for k in RATIOS:
            vals = [(r["measure"].get("body") or {}).get(k) for r in ok]
            vals = [v for v in vals if v]
            if vals:
                m = st.mean(vals)
                print(f"  {k:12s} media {m:.3f} CV {st.pstdev(vals) / m * 100:5.1f}% n={len(vals)}")
        vb = [r["measure"].get("vs_base") for r in ok if r["measure"].get("vs_base")]
        if vb:
            print("  vs BASE: diferenca geral", stats([v.get("whole") for v in vb], 1),
                  "| fundo", stats([v.get("background") for v in vb], 1),
                  "| distancia de pose", stats([v.get("pose_distance") for v in vb]))
            changes = []
            for r in ok:
                base = data.get(r.get("base"), {}).get("measure") or {}
                b0, b1 = base.get("body") or {}, r["measure"].get("body") or {}
                for k in ("shoulders", "hips", "r_thigh", "l_thigh"):
                    if b0.get(k) and b1.get(k):
                        changes.append(abs(b1[k] - b0[k]) / b0[k])
            print("  mudanca de proporcao vs BASE (ombro/quadril/coxas):", stats(changes))
            dsim = []
            for r in ok:
                base = data.get(r.get("base"), {}).get("measure") or {}
                a, b = (persona_face(base) or {}).get("sim"), (persona_face(r["measure"]) or {}).get("sim")
                if a is not None and b is not None:
                    dsim.append(b - a)
            print("  ganho de rosto vs BASE (pareado):", stats(dsim))
        pa = [r["measure"].get("pose_adherence") for r in ok]
        if any(p is not None for p in pa):
            print("  aderencia a pose (distancia do esqueleto fonte; 0 = igual):", stats(pa))
        secs = [r["seconds"] for r in group if r.get("seconds")]
        steady = [s for s in secs if s > 5]
        print("  tempo/imagem: todos", stats(secs, 1), "| primeira", secs[0] if secs else None, "| mediana", st.median(steady) if steady else None)
        meas = [r["measure"].get("measure_seconds") for r in ok]
        print("  tempo de medicao (LunaFaces + DWPose):", stats(meas, 1))
        t0, t1 = window(group)
        if t0 and gpu:
            sample = [g for g in gpu if t0 <= g[0] <= t1]
            if sample:
                print(f"  VRAM pico {max(g[1] for g in sample)} MiB de {sample[0][2]} | util media {st.mean(g[3] for g in sample):.0f}%")
            rs = [m for t, m in ram if t0 <= t <= t1]
            if rs:
                print(f"  RAM usada pico {max(rs)} MiB")
        if steady:
            med = st.median(steady)
            print(f"  custo/imagem (mediana, US$ {PRICE}/h): US$ {PRICE * med / 3600:.5f} | imagens/hora {3600 / med:.0f}")
