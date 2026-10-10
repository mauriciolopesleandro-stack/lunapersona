"""FASE 0 (sem GPU): compara metodos de COMPOSICAO da cabeca trocada (Qwen 2511 + BFS), usando as saidas JA geradas
no benchmark de 07/10. Nada e gerado; so a colagem muda.

Metodos:
  M0 producao  - recorte inteiro colado com borda linear (o que head_swap.py faz hoje)
  M1 compor    - mascara organica M + cor no anel + degrade (scripts/bench_cabeca/compor_cabeca.py)
  M2 laplaciano- mesma M e cor, mistura em piramide (bandas largas no grave, estreitas no agudo)
  M3 poisson   - mesma M, cv2.seamlessClone (gradiente; o tom da borda vem da foto)
  M4 lapl+cabelo - M2 com o cabelo ORIGINAL inteiro na mascara (recorte da pessoa + cor do cabelo)

Medidas (por imagem):
  fora_cabeca_px - pixels alterados (> 3 niveis) fora da zona da cabeca (o que NAO deveria mudar)
  emenda         - excesso de borda: |gradiente final| - |gradiente original| na faixa da borda de M (ou do recorte)
  anel_dE        - diferenca de cor media logo FORA da mascara (deve ser ~0)
  semelhanca     - ArcFace (antelopev2) da final com a media das referencias da Luna

  python compor_metodos.py --bench C:/Users/mauri/lv/bench --saida <pasta>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "bench_cabeca"))
sys.path.insert(0, str(AQUI.parent / "retoque"))
from compor_cabeca import _disco, alinhar, cabeca_original, compor  # noqa: E402
from retoque_tatuagens import _bgr, _lab, _media_ponderada, preencher_suave  # noqa: E402

FEATHER = 0.08  # head_swap.py


def m0_producao(original, saida, meta):
    x, y, w, h = meta["recorte"]
    s = cv2.resize(saida, (w, h), interpolation=cv2.INTER_AREA if saida.shape[1] > w else cv2.INTER_CUBIC)
    f = max(1, int(min(w, h) * FEATHER))
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.minimum.reduce([xx, w - 1 - xx, yy, h - 1 - yy]).astype(np.float32)
    # FeatherMask do ComfyUI so suaviza as bordas que nao encostam na borda da imagem
    a = np.clip(d / f, 0, 1)
    if x == 0:
        a = np.where(xx < f, np.clip((np.minimum.reduce([w - 1 - xx, yy, h - 1 - yy])) / f, 0, 1), a)
    if y == 0:
        a = np.where(yy < f, np.clip((np.minimum.reduce([xx, w - 1 - xx, h - 1 - yy])) / f, 0, 1), a)
    final = original.copy()
    bloco = s.astype(np.float32) * a[..., None] + original[y:y + h, x:x + w].astype(np.float32) * (1 - a[..., None])
    final[y:y + h, x:x + w] = np.clip(bloco + 0.5, 0, 255).astype(np.uint8)
    alfa = np.zeros(original.shape[:2], np.float32)
    alfa[y:y + h, x:x + w] = a
    return final, alfa


def corrigida_e_mascara(original, saida, meta):
    """Repete os passos 1-4 do compor (alinhamento, M, cor), devolvendo a cabeca corrigida e M no recorte."""
    final1, alfa1, M_t, rel = compor(original, saida, meta, meta["modelo"])
    if final1 is None:
        return None
    x, y, w, h = meta["recorte"]
    s = cv2.resize(saida, (w, h), interpolation=cv2.INTER_AREA if saida.shape[1] > w else cv2.INTER_CUBIC)
    orig_c = original[y:y + h, x:x + w]
    face = [meta["alvo"][0] - x, meta["alvo"][1] - y, meta["alvo"][2] - x, meta["alvo"][3] - y]
    fw = face[2] - face[0]
    zona = cv2.dilate(cabeca_original(face, (h, w)).astype(np.uint8), _disco(fw * 0.9)) > 0
    zona[int(min(h, face[3] + fw * 2.2)):] = False
    alinhada, _ = alinhar(orig_c, s, zona)
    M = M_t[y:y + h, x:x + w]
    la, lo = _lab(alinhada), _lab(orig_c)
    e = min(original.shape[:2]) / 1000
    anel = (cv2.dilate(M.astype(np.uint8), _disco(30 * e)) > 0) & ~(cv2.dilate(M.astype(np.uint8), _disco(4 * e)) > 0)
    if anel.sum() > 50:
        dif, den = _media_ponderada(lo - la, anel, 12 * e)
        ok = den > 0.15
        if ok.any():
            la = la + preencher_suave(dif, ~ok, ok)
    return final1, alfa1, M_t, _bgr(la), alinhada, (x, y, w, h), zona


def m2_laplaciano(original, corr, M, box, niveis=5):
    x, y, w, h = box
    o = original[y:y + h, x:x + w].astype(np.float32)
    c = corr.astype(np.float32)
    a = cv2.GaussianBlur(M.astype(np.float32), (0, 0), 2)
    go, gc, ga = [o], [c], [a]
    for _ in range(niveis):
        go.append(cv2.pyrDown(go[-1]))
        gc.append(cv2.pyrDown(gc[-1]))
        ga.append(cv2.pyrDown(ga[-1]))

    def lap(g):
        ls = []
        for i in range(niveis):
            up = cv2.pyrUp(g[i + 1], dstsize=(g[i].shape[1], g[i].shape[0]))
            ls.append(g[i] - up)
        ls.append(g[niveis])
        return ls

    lo_, lc_ = lap(go), lap(gc)
    mix = [lc_[i] * ga[i][..., None] + lo_[i] * (1 - ga[i][..., None]) for i in range(niveis + 1)]
    r = mix[-1]
    for i in range(niveis - 1, -1, -1):
        r = cv2.pyrUp(r, dstsize=(mix[i].shape[1], mix[i].shape[0])) + mix[i]
    final = original.copy()
    bloco = np.clip(r + 0.5, 0, 255).astype(np.uint8)
    # fora do alcance da mascara (dilatada pelo nivel mais grave) a foto fica identica
    alcance = cv2.dilate(M.astype(np.uint8), _disco(2 ** niveis)) > 0
    final[y:y + h, x:x + w] = np.where(alcance[..., None], bloco, original[y:y + h, x:x + w])
    alfa = np.zeros(original.shape[:2], np.float32)
    alfa[y:y + h, x:x + w] = alcance.astype(np.float32)
    return final, alfa


def m3_poisson(original, alinhada, M, box):
    x, y, w, h = box
    m = (cv2.erode(M.astype(np.uint8), _disco(2)) > 0).astype(np.uint8) * 255
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None, None
    # seamlessClone exige que a mascara nao encoste na borda da imagem destino
    m[:2, :] = m[-2:, :] = 0
    m[:, :2] = m[:, -2:] = 0
    cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
    dest = original[y:y + h, x:x + w].copy()
    src = np.zeros_like(dest)
    src[:] = alinhada
    bx, by, bw, bh = cv2.boundingRect(m)
    clone = cv2.seamlessClone(src[by:by + bh, bx:bx + bw], dest, m[by:by + bh, bx:bx + bw],
                              (bx + bw // 2, by + bh // 2), cv2.NORMAL_CLONE)
    final = original.copy()
    final[y:y + h, x:x + w] = clone
    alfa = np.zeros(original.shape[:2], np.float32)
    alfa[y:y + h, x:x + w] = (m > 0).astype(np.float32)
    return final, alfa


_REMBG = None


def matte_pessoa(img_bgr):
    """Recorte da pessoa (rembg/BiRefNet portrait, CPU). Usado so para achar o cabelo ORIGINAL inteiro."""
    global _REMBG
    from rembg import new_session, remove

    if _REMBG is None:
        _REMBG = new_session("u2net_human_seg")  # 170 MB: o BiRefNet portrait (973 MB) estourou os 7,7 GB do PC
    m = remove(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB), session=_REMBG, only_mask=True)
    return np.asarray(m) > 127


def cabelo_original(orig_c, face, zona, matte, alinhada):
    """Cabelo da pessoa ORIGINAL: pixels da pessoa, na zona da cabeca, com a cor do cabelo amostrada acima da testa
    (mesma ideia do hair.py). A elipse geometrica nao cobre cabelo comprido/volumoso - sobrava fio claro na cena 5."""
    x1, y1, x2, y2 = face
    fw, fh = x2 - x1, y2 - y1
    h, w = orig_c.shape[:2]
    lab = _lab(orig_c)
    amo = np.zeros((h, w), bool)
    amo[int(max(0, y1 - fh * 0.30)):int(max(1, y1 + fh * 0.02)), int(x1 + fw * 0.2):int(x2 - fw * 0.2)] = True
    amo &= matte
    if amo.sum() < 30:
        return np.zeros((h, w), bool), {"amostra_px": int(amo.sum())}
    v = lab[amo].reshape(-1, 3)
    mu, cov = v.mean(0), np.cov(v.T) + np.eye(3) * 4.0
    inv = np.linalg.inv(cov)
    d = lab.reshape(-1, 3) - mu
    md = np.sqrt(np.einsum("ij,jk,ik->i", d, inv, d)).reshape(h, w)
    # cor de cabelo E redesenhado pelo modelo (dE > 6 entre a saida alinhada e a original): o sueter creme da cena 5
    # tem a cor do cabelo platinado, mas o modelo nao o redesenhou - fica de fora
    la_, lo_ = cv2.GaussianBlur(_lab(alinhada), (0, 0), 2), cv2.GaussianBlur(lab, (0, 0), 2)
    mudou = np.sqrt(((la_ - lo_) ** 2).sum(2)) > 6
    cab = (md < 3.0) & matte & zona & mudou
    cab = cv2.morphologyEx(cab.astype(np.uint8), cv2.MORPH_OPEN, _disco(2))
    cab = cv2.morphologyEx(cab, cv2.MORPH_CLOSE, _disco(max(2, fw * 0.03))) > 0
    # so o que encosta na cabeca
    n, lb, st, _ = cv2.connectedComponentsWithStats(cab.astype(np.uint8), 8)
    perto = cv2.dilate(cabeca_original(face, (h, w)).astype(np.uint8), _disco(fw * 0.1)) > 0
    keep = np.zeros(n, bool)
    for i in range(1, n):
        keep[i] = (lb == i)[perto].any()
    cab = keep[lb]
    return cab, {"amostra_px": int(amo.sum()), "cabelo_px": int(cab.sum()), "cor_lab": [round(float(t), 1) for t in mu]}


def m2h_laplaciano_cabelo(original, saida, meta, box, M, zona, alinhada):
    """M2 + cabelo original inteiro na mascara (a cor e refeita com a mascara nova)."""
    x, y, w, h = box
    orig_c = original[y:y + h, x:x + w]
    face = [meta["alvo"][0] - x, meta["alvo"][1] - y, meta["alvo"][2] - x, meta["alvo"][3] - y]
    matte = matte_pessoa(orig_c)
    cab, info = cabelo_original(orig_c, face, zona, matte, alinhada)
    M2 = M | cab
    la, lo = _lab(alinhada), _lab(orig_c)
    e = min(original.shape[:2]) / 1000
    anel = (cv2.dilate(M2.astype(np.uint8), _disco(30 * e)) > 0) & ~(cv2.dilate(M2.astype(np.uint8), _disco(4 * e)) > 0)
    if anel.sum() > 50:
        dif, den = _media_ponderada(lo - la, anel, 12 * e)
        ok = den > 0.15
        if ok.any():
            la = la + preencher_suave(dif, ~ok, ok)
    fin, alf = m2_laplaciano(original, _bgr(la), M2, box)
    return fin, alf, info, cab


def medidas(original, final, alfa, zona_t, rostos_fn, luna, alvo):
    dif = np.abs(final.astype(np.int16) - original.astype(np.int16)).max(2)
    fora = ~zona_t
    r = {"fora_cabeca_px": int((dif[fora] > 3).sum()), "alterados_px": int((dif > 3).sum())}
    # emenda: excesso de gradiente na faixa da borda da area alterada
    alt = (alfa > 0.02).astype(np.uint8)
    borda = (cv2.dilate(alt, _disco(3)) > 0) & ~(cv2.erode(alt, _disco(3)) > 0)
    lf, lo = _lab(final)[..., 0], _lab(original)[..., 0]
    gf = np.hypot(cv2.Sobel(lf, cv2.CV_32F, 1, 0), cv2.Sobel(lf, cv2.CV_32F, 0, 1))
    go = np.hypot(cv2.Sobel(lo, cv2.CV_32F, 1, 0), cv2.Sobel(lo, cv2.CV_32F, 0, 1))
    r["emenda"] = round(float(np.clip(gf - go, 0, None)[borda].mean()), 2) if borda.any() else 0.0
    anel = (cv2.dilate(alt, _disco(8)) > 0) & ~(cv2.dilate(alt, _disco(2)) > 0)
    dE = np.sqrt(((cv2.GaussianBlur(_lab(final), (0, 0), 2) - cv2.GaussianBlur(_lab(original), (0, 0), 2)) ** 2).sum(2))
    r["anel_dE"] = round(float(dE[anel].mean()), 2) if anel.any() else 0.0
    sims = []
    for f in rostos_fn(final):
        bx1, by1, bx2, by2 = f.bbox
        if bx1 < alvo[2] and bx2 > alvo[0] and by1 < alvo[3] and by2 > alvo[1]:
            sims.append(float(np.dot(luna, f.normed_embedding)))
    r["semelhanca"] = round(max(sims), 3) if sims else None
    return r


def recorte_visual(img, alvo, escala=1.6):
    x1, y1, x2, y2 = alvo
    fw = x2 - x1
    X1, Y1 = int(max(0, x1 - fw * 1.0)), int(max(0, y1 - fw * 0.6))
    X2, Y2 = int(min(img.shape[1], x2 + fw * 1.0)), int(min(img.shape[0], y2 + fw * 1.6))
    c = img[Y1:Y2, X1:X2]
    return cv2.resize(c, (int(c.shape[1] * escala), int(c.shape[0] * escala)), interpolation=cv2.INTER_LANCZOS4)


def main():
    from rostos import ler, rostos

    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True)
    ap.add_argument("--saida", required=True)
    ap.add_argument("--variante", default="B")
    a = ap.parse_args()
    b, out = Path(a.bench), Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)
    plano = json.load(open(b / "plano.json", encoding="utf-8"))
    execs = [e for e in json.load(open(b / "execucoes.json", encoding="utf-8")) if e["variante"] == a.variante]
    luna = np.load(b / "luna_ref_media.npy")
    tabela = []
    for ex in execs:
        meta = plano["cenas"][str(ex["cena"])]
        p = b / "saidas" / f"{ex['id']}.png"
        if not p.exists():
            continue
        original, saida = ler(str(b / "entradas" / meta["imagem"])), ler(str(p))
        res = corrigida_e_mascara(original, saida, meta)
        if res is None:
            tabela.append({"id": ex["id"], "erro": "alinhamento"})
            continue
        f1, a1, M_t, corr, alinhada, box, zona = res
        x, y, w, h = box
        zona_t = np.zeros(original.shape[:2], bool)
        zona_t[y:y + h, x:x + w] = cv2.dilate(zona.astype(np.uint8), _disco(4)) > 0
        M = M_t[y:y + h, x:x + w]
        f4, a4, info_cab, cab = m2h_laplaciano_cabelo(original, saida, meta, box, M, zona, alinhada)
        metodos = {"M0_producao": m0_producao(original, saida, meta), "M1_compor": (f1, a1),
                   "M2_laplaciano": m2_laplaciano(original, corr, M, box), "M3_poisson": m3_poisson(original, alinhada, M, box),
                   "M4_lapl_cabelo": (f4, a4)}
        linha = {"id": ex["id"], "cabelo_original": info_cab}
        dbg = original[y:y + h, x:x + w].copy()
        dbg[cab] = (0.5 * dbg[cab] + np.array([0, 0, 255]) * 0.5).astype(np.uint8)
        cv2.imwrite(str(out / f"{ex['id']}__cabelo_original.jpg"), dbg)
        vis = [recorte_visual(original, meta["alvo"])]
        for nome, (fin, alf) in metodos.items():
            if fin is None:
                linha[nome] = {"erro": "falhou"}
                continue
            linha[nome] = medidas(original, fin, alf, zona_t, rostos, luna, meta["alvo"])
            cv2.imwrite(str(out / f"{ex['id']}__{nome}.png"), fin)
            vis.append(recorte_visual(fin, meta["alvo"]))
        hh = min(v.shape[0] for v in vis)
        faixa = np.concatenate([v[:hh] for v in vis], axis=1)
        rot = ["original", *metodos.keys()]
        for i, t in enumerate(rot):
            cv2.putText(faixa, t, (10 + i * vis[0].shape[1], 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
        cv2.imwrite(str(out / f"{ex['id']}__comparacao.jpg"), faixa, [cv2.IMWRITE_JPEG_QUALITY, 90])
        # onde cada metodo mexeu fora da cabeca (vermelho)
        tabela.append(linha)
        print(json.dumps(linha, ensure_ascii=False), flush=True)
    json.dump(tabela, open(out / "medidas.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
