"""Diagnostico OFFLINE da V3 a partir das imagens de debug de uma rodada (nada roda na GPU).

  python diagnostico_v3.py <pasta_saida_v2> <id_execucao> <pasta_destino>

Gera:
- etapas.jpg ............ as etapas lado a lado (entrada limpa, reconstrucao, refino, pele, fotometria, final)
- halo_mapa.png ......... diferenca de cor (dE Lab) final x original FORA da pessoa original, dentro da regiao refeita
                          (fundo que o modelo repintou = halo); quanto mais claro, maior a diferenca
- etapa_que_mudou.png ... para cada pixel, a ETAPA em que ele mais mudou (cores por etapa) - acha a origem de manchas
- diagnostico.json ...... numeros: halo por faixa de distancia da pessoa, grao/nitidez por regiao e por etapa,
                          variacao introduzida por cada etapa dentro da roupa e da pele
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.engines.skin_continuity import rgb_to_lab  # noqa: E402
from app.core.persona_replacement.segmentation import dilate, skin_pixels  # noqa: E402

STAGES = ["v3_clean_input", "initial_reconstruction", "identity_refinement", "skin_integration",
          "photometric_integration", "final"]
COLORS = {"initial_reconstruction": (230, 60, 60), "identity_refinement": (60, 160, 230), "skin_integration": (240, 200, 40),
          "photometric_integration": (160, 60, 220), "final": (40, 200, 120)}


def load(d: Path, rid: str, tag: str):
    p = d / f"{rid}__{tag}.png"
    return np.asarray(Image.open(p).convert("RGB")) if p.exists() else None


def mask(d: Path, rid: str, tag: str):
    a = load(d, rid, tag)
    return None if a is None else (a[..., 0] > 127)


def noise(rgb: np.ndarray, sel: np.ndarray) -> float | None:
    if sel.sum() < 200:
        return None
    y = rgb_to_lab(rgb)[..., 0]
    p = np.pad(y, 1, mode="edge")
    blur = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] + y) / 5
    return round(float((y - blur)[sel].std()), 3)


def main() -> None:
    d, rid, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    orig = load(d, rid, "original")
    imgs = {t: load(d, rid, t) for t in STAGES}
    person = mask(d, rid, "person_mask")
    region = mask(d, rid, "v3_region")
    clothes = mask(d, rid, "clothing_mask")
    h, w = orig.shape[:2]
    lo, lf = rgb_to_lab(orig), rgb_to_lab(imgs["final"])
    de = np.linalg.norm(lo - lf, axis=-1)
    rep: dict = {"id": rid}
    # halo: fundo repintado, por faixa de distancia da pessoa original
    bands = {}
    prev = dilate(person.astype(np.float32), 2) > 0.5
    for r in (6, 12, 24, 48):
        cur = dilate(person.astype(np.float32), r) > 0.5
        band = cur & ~prev & region
        if band.sum() > 50:
            bands[f"ate_{r}px"] = {"dE_medio": round(float(de[band].mean()), 2), "px": int(band.sum()),
                                  "fracao_dE>6": round(float((de[band] > 6).mean()), 3)}
        prev = cur
    rep["halo_fundo_repintado"] = bands
    hm = np.zeros((h, w, 3), np.uint8)
    bg_band = region & ~(dilate(person.astype(np.float32), 2) > 0.5)
    hm[..., 0] = np.clip(de * 12, 0, 255).astype(np.uint8) * bg_band
    hm[..., 1] = (person * 60).astype(np.uint8)
    Image.fromarray(hm).save(out / "halo_mapa.png")
    # grao/nitidez: pessoa refeita x fundo, por etapa
    bg = ~(dilate(region.astype(np.float32), 4) > 0.5)
    inner = person & ~(dilate((~person).astype(np.float32), 4) > 0.5)
    rep["grao"] = {"fundo_foto": noise(orig, bg), "pessoa_original": noise(orig, inner)}
    for t, im in imgs.items():
        if im is not None and t != "v3_clean_input":
            rep["grao"][f"pessoa_{t}"] = noise(im, inner)
    # quem mudou o que: variacao de cada etapa sobre a anterior, na roupa e na pele
    sk = (skin_pixels(imgs["final"]) > 0.5) & person & ~(clothes if clothes is not None else np.zeros_like(person))
    order = [t for t in STAGES[1:] if imgs.get(t) is not None]
    stage_map = np.zeros((h, w, 3), np.uint8)
    best = np.zeros((h, w), np.float32)
    rep["mudanca_por_etapa"] = {}
    for a, b in zip(order, order[1:]):
        dd = np.linalg.norm(rgb_to_lab(imgs[a]) - rgb_to_lab(imgs[b]), axis=-1)
        rep["mudanca_por_etapa"][b] = {
            "pele_dE_medio": round(float(dd[sk].mean()), 2) if sk.any() else None,
            "roupa_dE_medio": round(float(dd[clothes].mean()), 2) if clothes is not None and clothes.any() else None,
            "fundo_dE_medio": round(float(dd[bg].mean()), 2),
            "px_mudou_>8": int((dd > 8).sum())}
        sel = dd > np.maximum(best, 6)
        stage_map[sel] = COLORS.get(b, (255, 255, 255))
        best = np.maximum(best, dd)
    Image.fromarray((stage_map * 0.75 + imgs["final"] * 0.25).astype(np.uint8)).save(out / "etapa_que_mudou.png")
    # montagem das etapas
    tiles = [(t, im) for t, im in [("original", orig)] + list(imgs.items()) if im is not None]
    H = 640
    ims = [(t, Image.fromarray(im).resize((int(im.shape[1] * H / im.shape[0]), H))) for t, im in tiles]
    m = Image.new("RGB", (sum(i.width + 6 for _, i in ims), H + 24), "white")
    dr = ImageDraw.Draw(m)
    x = 0
    for t, im in ims:
        m.paste(im, (x, 24))
        dr.text((x + 4, 5), t, fill="black")
        x += im.width + 6
    m.save(out / "etapas.jpg", quality=88)
    (out / "diagnostico.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False))


if __name__ == "__main__":
    main()
