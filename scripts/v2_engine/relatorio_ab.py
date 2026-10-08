"""Relatorio A/B do benchmark do Replacement (spec Master 30/31/32): CURRENT x V2 por foto.

  python relatorio_ab.py resultado_ab.json pasta_imagens saida.html

resultado_ab.json: saida do sessao_v2.py com execucoes "<foto>__current" e "<foto>__v2" (perfis do plano).
pasta_imagens: as imagens coletadas (coletar_v2.py: "<id>__<etapa>.png"), ja baixadas do pod.

Para cada foto: original | current | v2 | mascaras (tatuagem, pele, acessorio, identidade original) | pose,
e as medidas lado a lado. No fim, a regra de NAO REGRESSAO: a V2 so pode ser recomendada se nao piorar pele,
tatuagem, acessorios, pose, maos, residuo da pessoa original nem integracao - e a avaliacao VISUAL humana
(coluna vazia para preencher) vale mais que qualquer numero.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

# medida -> (rotulo, maior_e_melhor, tolerancia para "empate")
METRICS = {
    "identity": ("identidade Luna (ArcFace)", True, 0.02),
    "original_sim": ("rosto ORIGINAL (ArcFace)", False, 0.03),
    "source_face_pixels": ("pixels originais no rosto", False, 0.02),
    "source_body_pixels": ("pixels originais no corpo", False, 0.02),
    "tattoo_residual": ("tatuagem residual", False, 0.01),
    "pose": ("distancia da pose", False, 0.01),
    "hand_anatomy": ("dedos (final/original)", True, 0.03),
    "accessory_change": ("mudanca nos acessorios", False, 0.005),
    "background": ("fundo alterado", False, 0.001),
    "seam_excess": ("emenda", False, 0.5),
    "straight_edges": ("blocos", False, 0.5),
}
NON_REGRESSION = ("tattoo_residual", "accessory_change", "pose", "hand_anatomy", "source_face_pixels", "original_sim",
                  "seam_excess")
MASKS = ("tattoo_mask", "skin_mask", "accessory_mask", "source_identity_mask", "hand_mask", "pose_map")


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)


def _skin_ratio(r: dict) -> float | None:
    sk = ((r.get("validation") or {}).get("checks") or {}).get("skin") or {}
    return (sk.get("metadata") or {}).get("razao_textura")


def _worse(name: str, cur, new) -> bool:
    if cur is None or new is None:
        return False
    _, higher, tol = METRICS[name]
    return (new < cur - tol) if higher else (new > cur + tol)


def build(res: dict, img_dir: Path, rel: str) -> tuple[str, dict]:
    runs = {r["id"]: r for r in res.get("resultados", [])}
    fotos = sorted({rid.rsplit("__", 1)[0] for rid in runs if "__" in rid})
    rows, summary = [], {"fotos": len(fotos), "v2_pior": {}, "v2_melhor": {}, "erros": []}
    for foto in fotos:
        cur, new = runs.get(f"{foto}__current", {}), runs.get(f"{foto}__v2", {})
        for tag, r in (("current", cur), ("v2", new)):
            if r.get("erro"):
                summary["erros"].append(f"{foto} {tag}: {r['erro']}")

        def img(rid, tag, label):
            p = img_dir / f"{rid}__{tag}.png"
            if not p.exists():
                return f"<figure><div class='miss'>sem {html.escape(tag)}</div><figcaption>{label}</figcaption></figure>"
            return f"<figure><img loading='lazy' src='{rel}/{p.name}'><figcaption>{label}</figcaption></figure>"

        cm, nm = cur.get("measures") or {}, new.get("measures") or {}
        trs = []
        for k, (lab, _, _) in METRICS.items():
            worse = _worse(k, cm.get(k), nm.get(k))
            better = _worse(k, nm.get(k), cm.get(k))
            if k in NON_REGRESSION and worse:
                summary["v2_pior"].setdefault(k, []).append(foto)
            if better:
                summary["v2_melhor"].setdefault(k, []).append(foto)
            cls = "bad" if worse else "good" if better else ""
            trs.append(f"<tr><td>{lab}</td><td>{_fmt(cm.get(k))}</td><td class='{cls}'>{_fmt(nm.get(k))}</td></tr>")
        trs.append(f"<tr><td>pele (textura final/foto)</td><td>{_fmt(_skin_ratio(cur))}</td><td>{_fmt(_skin_ratio(new))}</td></tr>")
        for lab, key in (("status", "status"),):
            trs.append(f"<tr><td>{lab}</td><td>{cur.get(key, '-')}</td><td>{new.get(key, '-')}</td></tr>")
        trs.append("<tr><td>decisao do gate</td><td>{}</td><td>{}</td></tr>".format(
            (cur.get("gate") or {}).get("decision", "-"), (new.get("gate") or {}).get("decision", "-")))
        for lab, key in (("tempo (s)", "segundos_parede"),):
            trs.append(f"<tr><td>{lab}</td><td>{_fmt(cur.get(key))}</td><td>{_fmt(new.get(key))}</td></tr>")
        cost = lambda r: (r.get("telemetry") or {}).get("estimated_cost_usd")  # noqa: E731
        trs.append(f"<tr><td>custo GPU (US$)</td><td>{_fmt(cost(cur))}</td><td>{_fmt(cost(new))}</td></tr>")
        hard = (new.get("gate") or {}).get("hard_fails") or []
        undesired = (((new.get("debug") or {}).get("scene_analysis") or {}).get("undesired_attributes")) or []
        rows.append(f"""
<section>
  <h2>{html.escape(foto)}</h2>
  <div class="row">
    {img(f"{foto}__v2", "original", "original")}
    {img(f"{foto}__current", "final", "CURRENT (Qwen BFS)")}
    {img(f"{foto}__v2", "final", "V2 (sem Qwen)")}
  </div>
  <div class="row small">{''.join(img(f"{foto}__v2", m, m) for m in MASKS)}</div>
  <table><tr><th>medida</th><th>current</th><th>v2</th></tr>{''.join(trs)}</table>
  <p><b>Atributos indesejados achados:</b> {html.escape(', '.join(undesired) or 'nenhum')}.
     <b>Hard fails V2:</b> {html.escape(', '.join(hard) or 'nenhum')}.</p>
  <p class="review"><b>Revisao visual (humana):</b> rosto Luna? ___ corpo/pele Luna? ___ cabelo Luna? ___
     tatuagem zero? ___ acessorios iguais? ___ roupa igual? ___ maos ok? ___ parece colagem? ___
     <b>vencedor:</b> ___</p>
</section>""")
    pior = summary["v2_pior"]
    verdict = ("NAO PROMOVER: a V2 piorou " + "; ".join(f"{METRICS[k][0]} em {', '.join(v)}" for k, v in pior.items())
               if pior else "Sem regressao nas medidas automaticas - a promocao ainda depende da revisao visual.")
    summary["veredito_automatico"] = verdict
    page = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Benchmark A/B Replacement</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#e3e3e8;--bad:#c62828;--good:#2e7d32}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ededf0;--mut:#9a9aa2;--line:#2c2c31;--bad:#ef6b6b;--good:#6fcf7a}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px}}
h1{{font-size:22px}} h2{{font-size:18px;margin-top:32px;border-top:1px solid var(--line);padding-top:16px}}
.row{{display:flex;gap:8px;flex-wrap:wrap}} figure{{margin:0;flex:1 1 300px;min-width:0}} .small figure{{flex:1 1 150px}}
img{{width:100%;height:auto;border-radius:6px;display:block}} figcaption{{color:var(--mut);font-size:13px;margin-top:4px}}
.miss{{border:1px dashed var(--line);padding:24px;color:var(--mut);text-align:center}}
table{{border-collapse:collapse;margin-top:12px;width:100%;max-width:640px}} td,th{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}}
.bad{{color:var(--bad);font-weight:600}} .good{{color:var(--good);font-weight:600}} .review{{color:var(--mut)}}
.verdict{{padding:12px;border:1px solid var(--line);border-radius:8px}}
</style></head><body>
<h1>Benchmark A/B do Replacement: CURRENT x V2</h1>
<p class="verdict"><b>Regra de nao regressao (automatica):</b> {html.escape(verdict)}</p>
<p>{summary['fotos']} fotos. Verde = V2 melhor, vermelho = V2 pior. ArcFace nao e prova de realismo: a decisao final e a revisao visual.</p>
{''.join(f"<p class='bad'>{html.escape(e)}</p>" for e in summary['erros'])}
{''.join(rows)}
</body></html>"""
    return page, summary


def main() -> None:
    res = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    img_dir, out = Path(sys.argv[2]), Path(sys.argv[3])
    rel = Path(img_dir).resolve().relative_to(out.resolve().parent).as_posix() if img_dir.resolve().is_relative_to(
        out.resolve().parent) else img_dir.resolve().as_uri()
    page, summary = build(res, img_dir, rel)
    out.write_text(page, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
