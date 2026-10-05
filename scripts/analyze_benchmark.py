"""Resume o results.json do benchmark: por configuracao, rosto (ArcFace), contagem
de rostos/corpos/pessoas e proporcoes do corpo (media, desvio, CV e distancia da
referencia de corpo inteiro). So numeros medidos; nada estimado a olho."""
import json
import statistics as st
import sys

RATIOS = ["shoulders", "hips", "r_thigh", "l_thigh", "r_shin", "l_shin", "r_upper_arm", "l_upper_arm"]

data = json.load(open(sys.argv[1], encoding="utf-8"))
refs = data.pop("_refs", {})
body_ref = (refs.get("bench_body_ref.png") or {}).get("body") or {}
ds23 = (refs.get("bench_ds23.png") or {}).get("body") or {}
print("REF corpo (pose_01):", {k: body_ref.get(k) for k in RATIOS})
print("REF dataset 23    :", {k: ds23.get(k) for k in RATIOS})
print("REF rosto:", json.dumps(refs.get("bench_face_ref.png", {}).get("faces"))[:200])


def fmt(xs):
    return f"{st.mean(xs):.3f}±{st.pstdev(xs):.3f} (min {min(xs):.3f})" if xs else "-"


for cfg in ["A", "A13", "B", "C", "D"]:
    rows = [r for r in data.values() if r.get("config") == cfg]
    ok = [r for r in rows if r.get("measure")]
    print(f"\n=== {cfg}: {len(rows)} geradas, {len(ok)} medidas, erros {[r['seed'] for r in rows if r.get('error')]}")
    if not ok:
        continue
    sims, n_faces, people, fl, ages = [], [], [], [], []
    for r in ok:
        m = r["measure"]
        faces = m.get("faces") or []
        n_faces.append(len(faces))
        if faces:
            best = max(faces, key=lambda f: f.get("sim") or 0)
            sims.append(best.get("sim") or 0)
            ages.append(best.get("age") or 0)
        people.append(m.get("dwpose_people"))
        fl.append(m.get("florence_person_boxes"))
    print("rosto ArcFace:", fmt(sims), "| idade", fmt(ages))
    print("rostos por imagem:", n_faces)
    print("corpos DWPose  :", people)
    print("pessoas Florence:", fl)
    print("seg/imagem:", fmt([r.get("seconds") or 0 for r in rows if r.get("seconds")]))
    for k in RATIOS:
        vals = [r["measure"]["body"][k] for r in ok if (r["measure"].get("body") or {}).get(k)]
        if not vals:
            continue
        mean = st.mean(vals)
        cv = st.pstdev(vals) / mean if mean else 0
        ref = body_ref.get(k)
        dev = f"{(mean - ref) / ref * 100:+.0f}% vs ref" if ref else ""
        print(f"  {k:12s} n={len(vals):2d} media {mean:.3f} CV {cv * 100:4.1f}% {dev}")
