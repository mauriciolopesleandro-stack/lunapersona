"""SEM POD: usa da saida do modelo SO a cabeca (item 2d do PROMPT_correcoes_sistema_Luna) e checa (2e).

  1. tamanho: saida -> tamanho do recorte (proporcao diferente do pedido ao modelo > 1% = erro);
  2. alinhamento: cv2.findTransformECC afim em piramide, mascara SEM a cabeca e sem vermelho puro;
     correlacao baixa = reprova (nunca compoe desalinhado);
  3. mascara M = cabeca+cabelo+pescoco da ORIGINAL unida a da SAIDA (o que mudou em volta da cabeca),
     menos a outra pessoa;
  4. cor: diferenca original - saida medida num anel fora de M (so baixa frequencia) levada para dentro;
     grao igualado; degrade proporcional ao tamanho da foto;
  5. fora de M a foto fica IDENTICA a original.
Checagem: fora de M = 0, semelhanca com a Luna NA FINAL (media das referencias), outra pessoa fora
de M, nenhum vermelho puro a mais, emenda (delta-E) no anel <= 4.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "retoque"))
sys.path.insert(0, str(AQUI))
from retoque_tatuagens import _bgr, _grao, _lab, _media_ponderada, _ruido, preencher_suave  # noqa: E402

LIMIAR_ECC = 0.80
MAX_EMENDA = 4.0


def _disco(r):
    r = max(1, int(round(r)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def cabeca_original(face, shape):
    from preparar import mascara_cabeca
    return mascara_cabeca(face, shape) > 127


def vermelho_puro(img):
    b, g, r = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
    return (r > 200) & (g < 50) & (b < 50)


def alinhar(orig_c, saida_c, excluir):
    g1 = cv2.cvtColor(orig_c, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    g2 = cv2.cvtColor(saida_c, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    msk = (~excluir & ~vermelho_puro(saida_c)).astype(np.uint8)
    warp = np.eye(2, 3, dtype=np.float32)
    niveis = 3
    pyr1, pyr2, pyrm = [g1], [g2], [msk]
    for _ in range(niveis - 1):
        pyr1.append(cv2.pyrDown(pyr1[-1]))
        pyr2.append(cv2.pyrDown(pyr2[-1]))
        pyrm.append(cv2.resize(pyrm[-1], (pyr1[-1].shape[1], pyr1[-1].shape[0]), interpolation=cv2.INTER_NEAREST))
    cc = 0.0
    for lvl in range(niveis - 1, -1, -1):
        if lvl != niveis - 1:
            warp[:, 2] *= 2
        try:
            cc, warp = cv2.findTransformECC(pyr1[lvl], pyr2[lvl], warp, cv2.MOTION_AFFINE,
                                            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), pyrm[lvl], 5)
        except cv2.error:
            return None, 0.0
    h, w = g1.shape
    alinhada = cv2.warpAffine(saida_c, warp, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)
    return alinhada, float(cc)


def compor(original, saida, meta, modelo_wh):
    """Devolve (final, alfa_total, M_total, relatorio_parcial)."""
    H, W = original.shape[:2]
    x, y, w, h = meta["recorte"]
    rel = {}
    mw, mh = modelo_wh
    sh, sw = saida.shape[:2]
    rel["proporcao_ok"] = abs((sw / sh) / (mw / mh) - 1) <= 0.01
    if not rel["proporcao_ok"]:
        rel["erro"] = f"saida {sw}x{sh} com proporcao diferente do pedido {mw}x{mh}"
        return None, None, None, rel
    saida_c = cv2.resize(saida, (w, h), interpolation=cv2.INTER_AREA if sw > w else cv2.INTER_CUBIC)
    orig_c = original[y:y + h, x:x + w]
    face = [meta["alvo"][0] - x, meta["alvo"][1] - y, meta["alvo"][2] - x, meta["alvo"][3] - y]
    fw = face[2] - face[0]
    cab0 = cabeca_original(face, (h, w))
    zona_cab = cv2.dilate(cab0.astype(np.uint8), _disco(fw * 0.9)) > 0
    zona_cab[int(min(h, face[3] + fw * 2.2)):] = False  # cabelo longo ate o ombro, nao o corpo todo
    alinhada, cc = alinhar(orig_c, saida_c, zona_cab)
    rel["ecc"] = round(cc, 3)
    if alinhada is None or cc < LIMIAR_ECC:
        rel["erro"] = f"alinhamento ruim (correlacao {cc:.2f} < {LIMIAR_ECC})"
        return None, None, None, rel

    # M = cabeca original U o que mudou em volta da cabeca na saida, menos a outra pessoa
    la, lo = _lab(alinhada), _lab(orig_c)
    dE = np.sqrt(((cv2.GaussianBlur(la, (0, 0), 2) - cv2.GaussianBlur(lo, (0, 0), 2)) ** 2).sum(2))
    mudou = (dE > 10) & zona_cab
    mudou = cv2.morphologyEx(mudou.astype(np.uint8), cv2.MORPH_OPEN, _disco(2)) > 0
    n, lb, st, _ = cv2.connectedComponentsWithStats(mudou.astype(np.uint8), 8)
    perto = cv2.dilate(cab0.astype(np.uint8), _disco(fw * 0.15)) > 0
    manter = np.zeros(n, bool)
    for i in range(1, n):
        manter[i] = (lb == i)[perto].any()
    M = cab0 | manter[lb]
    M = cv2.morphologyEx(M.astype(np.uint8), cv2.MORPH_CLOSE, _disco(fw * 0.08)) > 0
    outros = np.zeros((h, w), bool)
    for o in meta["outros"]:
        ox1, oy1, ox2, oy2 = o[0] - x, o[1] - y, o[2] - x, o[3] - y
        pw, ph = (ox2 - ox1) * 0.35, (oy2 - oy1) * 0.35
        outros[int(max(0, oy1 - ph)):int(max(0, oy2 + ph)), int(max(0, ox1 - pw)):int(max(0, ox2 + pw))] = True
    M &= ~outros

    # cor/luz: diferenca original - saida no anel FORA de M (fundo, ombros), so baixa frequencia
    e = min(H, W) / 1000
    anel = (cv2.dilate(M.astype(np.uint8), _disco(30 * e)) > 0) & ~(cv2.dilate(M.astype(np.uint8), _disco(4 * e)) > 0) & ~outros
    if anel.sum() > 50:
        dif, den = _media_ponderada(lo - la, anel, 12 * e)
        ok = den > 0.15
        if ok.any():
            la = la + preencher_suave(dif, ~ok, ok)
    # grao
    s_o = _grao(lo[..., 0], anel)
    s_r = _grao(la[..., 0], M)
    if s_o > s_r + 0.05:
        la[..., 0] += _ruido(M.shape, float(np.sqrt(s_o ** 2 - s_r ** 2)), np.random.default_rng(7)) * M
    corrigida = _bgr(la)

    # degrade proporcional (dentro de M), fora de M = original
    larg = max(3.0, 10 * e)
    dist = cv2.distanceTransform(M.astype(np.uint8), cv2.DIST_L2, 5)
    t = np.clip(dist / larg, 0, 1)
    alfa = (t * t * (3 - 2 * t)).astype(np.float32)
    final = original.copy()
    bloco = (corrigida.astype(np.float32) * alfa[..., None] + orig_c.astype(np.float32) * (1 - alfa[..., None]))
    final[y:y + h, x:x + w] = np.where(alfa[..., None] > 0, np.clip(bloco + 0.5, 0, 255).astype(np.uint8), orig_c)
    alfa_t = np.zeros((H, W), np.float32)
    alfa_t[y:y + h, x:x + w] = alfa
    M_t = np.zeros((H, W), bool)
    M_t[y:y + h, x:x + w] = M
    rel["area_M"] = round(float(M.mean()), 4)
    return final, alfa_t, M_t, rel


def checar(original, final, alfa, M, meta, luna_ref, rostos_fn):
    rel = {}
    fora = alfa <= 0
    dif = np.abs(final.astype(np.int16) - original.astype(np.int16)).max(2)
    rel["fora_de_M"] = int(dif[fora].max()) if fora.any() else 0
    # semelhanca NA FINAL: o rosto que esta no lugar do alvo
    ax1, ay1, ax2, ay2 = meta["alvo"]
    melhor, iou_m = None, 0.0
    for f in rostos_fn(final):
        bx1, by1, bx2, by2 = f.bbox
        ix = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))
        iou = ix / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - ix + 1e-6)
        if iou > iou_m:
            melhor, iou_m = f, iou
    rel["semelhanca_luna"] = round(float(np.dot(luna_ref, melhor.normed_embedding)), 3) if melhor is not None and iou_m > 0.2 else None
    # outra pessoa fora de M
    tocou = 0.0
    for o in meta["outros"]:
        ox1, oy1, ox2, oy2 = (int(v) for v in o)
        reg = M[max(0, oy1):oy2, max(0, ox1):ox2]
        tocou = max(tocou, float(reg.mean()) if reg.size else 0.0)
    rel["outra_pessoa_em_M"] = round(tocou, 4)
    rel["vermelho_a_mais"] = int(max(0, vermelho_puro(final).sum() - vermelho_puro(original).sum()))
    # emenda: baixa frequencia da metade EXTERNA do degrade (deve ficar igual a original)
    rampa = (alfa > 0.02) & (alfa < 0.5)
    lf, lo = cv2.GaussianBlur(_lab(final), (0, 0), 3), cv2.GaussianBlur(_lab(original), (0, 0), 3)
    rel["emenda_deltaE"] = round(float(np.sqrt(((lf - lo) ** 2).sum(2))[rampa].mean()), 2) if rampa.any() else 0.0
    falhas = []
    if rel["fora_de_M"] > 0:
        falhas.append("mudou fora de M")
    if rel["semelhanca_luna"] is None:
        falhas.append("rosto nao encontrado na final")
    if rel["outra_pessoa_em_M"] > 0.02:
        falhas.append("outra pessoa dentro de M")
    if rel["vermelho_a_mais"] > 0:
        falhas.append("retangulo/vermelho na saida")
    if rel["emenda_deltaE"] > MAX_EMENDA:
        falhas.append("emenda visivel")
    rel["falhas"] = falhas
    rel["aprovado"] = not falhas
    return rel


def main():
    import argparse

    from rostos import ler, rostos
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True, help="pasta do preparar.py (plano.json, entradas/, saidas/)")
    a = ap.parse_args()
    b = Path(a.bench)
    plano = json.load(open(b / "plano.json", encoding="utf-8"))
    execs = json.load(open(b / "execucoes.json", encoding="utf-8"))
    luna = np.load(b / "luna_ref_media.npy")
    tempos = json.load(open(b / "tempos.json", encoding="utf-8")) if (b / "tempos.json").exists() else {}
    (b / "finais").mkdir(exist_ok=True)
    tabela = []
    for ex in execs:
        meta = plano["cenas"][str(ex["cena"])]
        saida_p = b / "saidas" / f"{ex['id']}.png"
        linha = {"id": ex["id"], "variante": ex["variante"], "cena": ex["cena"], "semente": ex["semente"],
                 **tempos.get(ex["id"], {})}
        if not saida_p.exists():
            linha.update(aprovado=False, falhas=["sem saida (caiu ou nao rodou)"])
            tabela.append(linha)
            continue
        original = ler(str(b / "entradas" / meta["imagem"]))
        final, alfa, M, r1 = compor(original, ler(str(saida_p)), meta, meta["modelo"])
        linha.update(r1)
        if final is None:
            linha.update(aprovado=False, falhas=[r1.get("erro", "composicao falhou")])
        else:
            cv2.imwrite(str(b / "finais" / f"{ex['id']}.png"), final)
            linha.update(checar(original, final, alfa, M, meta, luna, rostos))
        tabela.append(linha)
        print(json.dumps(linha, ensure_ascii=False))
    json.dump(tabela, open(b / "tabela.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
