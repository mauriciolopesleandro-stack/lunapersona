"""Composicao da cabeca trocada (modo "Luna na foto"): leva para a foto SO a cabeca e o cabelo que o modelo refez.

Vencedor da fase 0 (10/10, scripts/fase0/compor_metodos.py, metodo M4):
  1. alinhamento ECC da saida do modelo com a foto (nunca compoe desalinhado);
  2. mascara M = cabeca original (elipses) U o que o modelo mudou perto da cabeca U o cabelo ORIGINAL inteiro
     (pessoa x cor do cabelo x mudou) - a elipse sozinha deixava fio do cabelo antigo;
  3. cor: diferenca de baixa frequencia (foto - saida) medida num anel FORA de M e levada para dentro;
  4. mistura Laplaciana (bandas largas no grave, estreitas no agudo): emenda 0,2-0,4 contra 1,8-3,3 do degrade;
  5. fora do alcance de M a foto fica IDENTICA (byte a byte).
Imagens em BGR uint8 (convencao do OpenCV); quem chama converte.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

LIMIAR_ECC = 0.80


def disco(r: float) -> np.ndarray:
    r = max(1, int(round(r)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def lab(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)


def bgr(lab_img: np.ndarray) -> np.ndarray:
    out = cv2.cvtColor(lab_img.astype(np.float32), cv2.COLOR_LAB2BGR)
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def media_ponderada(x: np.ndarray, w: np.ndarray, sigma: float):
    w = w.astype(np.float32)
    den = cv2.GaussianBlur(w, (0, 0), sigma)
    num = cv2.GaussianBlur(x * w[..., None], (0, 0), sigma)
    return num / np.maximum(den, 1e-6)[..., None], den


def preencher_suave(img: np.ndarray, buraco: np.ndarray, validos: np.ndarray) -> np.ndarray:
    """Push-pull: interpola dentro de `buraco` a partir de `validos` (baixa frequencia, sem emenda)."""
    x = img.astype(np.float32)
    w = (validos & ~buraco).astype(np.float32)
    if w.sum() < 1:
        return x
    niveis = []
    xi, wi = x * w[..., None], w
    while min(wi.shape[:2]) > 3:
        niveis.append((xi, wi))
        xi, wi = cv2.pyrDown(xi), cv2.pyrDown(wi)
    media = (x * w[..., None]).reshape(-1, x.shape[2]).sum(0) / w.sum()
    est = np.where(wi[..., None] > 1e-6, xi / np.maximum(wi, 1e-6)[..., None], media)
    for xi, wi in reversed(niveis):
        sobe = cv2.pyrUp(est, dstsize=(wi.shape[1], wi.shape[0]))
        conf = np.clip(wi * 2.0, 0, 1)[..., None]
        val = xi / np.maximum(wi, 1e-6)[..., None]
        est = val * conf + sobe * (1 - conf)
    out = x.copy()
    out[buraco] = est[buraco]
    return out


def mascara_cabeca(face, shape) -> np.ndarray:
    """Cabeca + cabelo + pescoco pela caixa do rosto (elipse da cabeca e elipse do pescoco)."""
    h, w = shape
    x1, y1, x2, y2 = face
    fw, fh = x2 - x1, y2 - y1
    m = np.zeros((h, w), np.uint8)
    cx = (x1 + x2) / 2
    cv2.ellipse(m, (int(cx), int(y1 + fh * 0.42)), (int(fw * 0.95), int(fh * 0.95)), 0, 0, 360, 255, -1)
    cv2.ellipse(m, (int(cx), int(y2 + fh * 0.25)), (int(fw * 0.42), int(fh * 0.42)), 0, 0, 360, 255, -1)
    return m > 127


def alinhar(orig_c: np.ndarray, saida_c: np.ndarray, excluir: np.ndarray):
    """ECC afim em piramide, ignorando a cabeca (que mudou de proposito). Devolve (alinhada, correlacao)."""
    g1 = cv2.cvtColor(orig_c, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    g2 = cv2.cvtColor(saida_c, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    msk = (~excluir).astype(np.uint8)
    warp = np.eye(2, 3, dtype=np.float32)
    niveis = 3
    p1, p2, pm = [g1], [g2], [msk]
    for _ in range(niveis - 1):
        p1.append(cv2.pyrDown(p1[-1]))
        p2.append(cv2.pyrDown(p2[-1]))
        pm.append(cv2.resize(pm[-1], (p1[-1].shape[1], p1[-1].shape[0]), interpolation=cv2.INTER_NEAREST))
    cc = 0.0
    for lvl in range(niveis - 1, -1, -1):
        if lvl != niveis - 1:
            warp[:, 2] *= 2
        try:
            cc, warp = cv2.findTransformECC(p1[lvl], p2[lvl], warp, cv2.MOTION_AFFINE,
                                            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), pm[lvl], 5)
        except cv2.error:
            if lvl != 0:
                continue  # nivel grosso com pouca area fora da cabeca: segue para o nivel mais fino
            try:  # o chute da piramide nao convergiu: tenta do zero na resolucao cheia
                cc, warp = cv2.findTransformECC(p1[0], p2[0], np.eye(2, 3, dtype=np.float32), cv2.MOTION_AFFINE,
                                                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), pm[0], 5)
            except cv2.error:
                return None, 0.0
    h, w = g1.shape
    return cv2.warpAffine(saida_c, warp, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE), float(cc)


def cabelo_original(orig_c, face, zona, pessoa, alinhada) -> tuple[np.ndarray, dict[str, Any]]:
    """Cabelo da pessoa ORIGINAL: pessoa x cor do cabelo (amostrada acima da testa) x redesenhado pelo modelo.
    O 'redesenhado' tira roupa da mesma cor (o sueter creme da cena 5 tinha a cor do cabelo platinado)."""
    x1, y1, x2, y2 = face
    fw, fh = x2 - x1, y2 - y1
    h, w = orig_c.shape[:2]
    lo = lab(orig_c)
    amo = np.zeros((h, w), bool)
    amo[int(max(0, y1 - fh * 0.30)):int(max(1, y1 + fh * 0.02)), int(x1 + fw * 0.2):int(x2 - fw * 0.2)] = True
    amo &= pessoa
    if amo.sum() < 30:
        return np.zeros((h, w), bool), {"amostra_px": int(amo.sum())}
    v = lo[amo].reshape(-1, 3)
    mu, cov = v.mean(0), np.cov(v.T) + np.eye(3) * 4.0
    inv = np.linalg.inv(cov)
    d = lo.reshape(-1, 3) - mu
    md = np.sqrt(np.einsum("ij,jk,ik->i", d, inv, d)).reshape(h, w)
    la_, lo_ = cv2.GaussianBlur(lab(alinhada), (0, 0), 2), cv2.GaussianBlur(lo, (0, 0), 2)
    mudou = np.sqrt(((la_ - lo_) ** 2).sum(2)) > 6
    cab = (md < 3.0) & pessoa & zona & mudou
    cab = cv2.morphologyEx(cab.astype(np.uint8), cv2.MORPH_OPEN, disco(2))
    cab = cv2.morphologyEx(cab, cv2.MORPH_CLOSE, disco(max(2, fw * 0.03))) > 0
    n, lb, _, _ = cv2.connectedComponentsWithStats(cab.astype(np.uint8), 8)
    perto = cv2.dilate(mascara_cabeca(face, (h, w)).astype(np.uint8), disco(fw * 0.1)) > 0
    keep = np.zeros(n, bool)
    for i in range(1, n):
        keep[i] = (lb == i)[perto].any()
    cab = keep[lb]
    return cab, {"cabelo_original_px": int(cab.sum())}


def laplaciano(o: np.ndarray, c: np.ndarray, M: np.ndarray, niveis: int = 5) -> tuple[np.ndarray, np.ndarray]:
    o, c = o.astype(np.float32), c.astype(np.float32)
    a = cv2.GaussianBlur(M.astype(np.float32), (0, 0), 2)
    go, gc, ga = [o], [c], [a]
    for _ in range(niveis):
        go.append(cv2.pyrDown(go[-1]))
        gc.append(cv2.pyrDown(gc[-1]))
        ga.append(cv2.pyrDown(ga[-1]))

    def lap(g):
        ls = [g[i] - cv2.pyrUp(g[i + 1], dstsize=(g[i].shape[1], g[i].shape[0])) for i in range(niveis)]
        return ls + [g[niveis]]

    lo_, lc_ = lap(go), lap(gc)
    mix = [lc_[i] * ga[i][..., None] + lo_[i] * (1 - ga[i][..., None]) for i in range(niveis + 1)]
    r = mix[-1]
    for i in range(niveis - 1, -1, -1):
        r = cv2.pyrUp(r, dstsize=(mix[i].shape[1], mix[i].shape[0])) + mix[i]
    alcance = cv2.dilate(M.astype(np.uint8), disco(2 ** niveis)) > 0
    bloco = np.clip(r + 0.5, 0, 255).astype(np.uint8)
    return np.where(alcance[..., None], bloco, o.astype(np.uint8)), alcance


@dataclass
class Composicao:
    final: np.ndarray | None
    mascara: np.ndarray | None = None  # M no tamanho da foto
    alcance: np.ndarray | None = None  # onde a foto pode ter mudado
    info: dict[str, Any] = field(default_factory=dict)


def compor(original: np.ndarray, saida: np.ndarray, recorte, alvo, outros, pessoa: np.ndarray | None) -> Composicao:
    """original/saida em BGR; saida = recorte gerado pelo modelo (qualquer tamanho com a mesma proporcao);
    recorte = (x, y, w, h) na foto; alvo = caixa do rosto trocado; outros = caixas de outros rostos;
    pessoa = mascara da pessoa na foto inteira (None = sem a etapa do cabelo original)."""
    H, W = original.shape[:2]
    x, y, w, h = (int(v) for v in recorte)
    info: dict[str, Any] = {}
    sh, sw = saida.shape[:2]
    if abs((sw / sh) / (w / h) - 1) > 0.02:
        return Composicao(None, info={"erro": f"proporcao da saida {sw}x{sh} diferente do recorte {w}x{h}"})
    saida_c = cv2.resize(saida, (w, h), interpolation=cv2.INTER_AREA if sw > w else cv2.INTER_CUBIC)
    orig_c = original[y:y + h, x:x + w]
    face = [alvo[0] - x, alvo[1] - y, alvo[2] - x, alvo[3] - y]
    fw = face[2] - face[0]
    cab0 = mascara_cabeca(face, (h, w))
    zona = cv2.dilate(cab0.astype(np.uint8), disco(fw * 0.9)) > 0
    zona[int(min(h, face[3] + fw * 2.2)):] = False
    alinhada, cc = alinhar(orig_c, saida_c, zona)
    info["ecc"] = round(cc, 3)
    if alinhada is None or cc < LIMIAR_ECC:
        return Composicao(None, info={**info, "erro": f"alinhamento ruim (correlacao {cc:.2f})"})
    la, lo = lab(alinhada), lab(orig_c)
    dE = np.sqrt(((cv2.GaussianBlur(la, (0, 0), 2) - cv2.GaussianBlur(lo, (0, 0), 2)) ** 2).sum(2))
    mudou = cv2.morphologyEx(((dE > 10) & zona).astype(np.uint8), cv2.MORPH_OPEN, disco(2)) > 0
    n, lb, _, _ = cv2.connectedComponentsWithStats(mudou.astype(np.uint8), 8)
    perto = cv2.dilate(cab0.astype(np.uint8), disco(fw * 0.15)) > 0
    manter = np.zeros(n, bool)
    for i in range(1, n):
        manter[i] = (lb == i)[perto].any()
    M = cab0 | manter[lb]
    if pessoa is not None:
        cab, ci = cabelo_original(orig_c, face, zona, pessoa[y:y + h, x:x + w], alinhada)
        M |= cab
        info.update(ci)
    M = cv2.morphologyEx(M.astype(np.uint8), cv2.MORPH_CLOSE, disco(fw * 0.08)) > 0
    outros_m = np.zeros((h, w), bool)
    for o in outros or []:
        ox1, oy1, ox2, oy2 = o[0] - x, o[1] - y, o[2] - x, o[3] - y
        pw, ph = (ox2 - ox1) * 0.35, (oy2 - oy1) * 0.35
        outros_m[int(max(0, oy1 - ph)):int(max(0, oy2 + ph)), int(max(0, ox1 - pw)):int(max(0, ox2 + pw))] = True
    M &= ~outros_m
    e = min(H, W) / 1000
    anel = (cv2.dilate(M.astype(np.uint8), disco(30 * e)) > 0) & ~(cv2.dilate(M.astype(np.uint8), disco(4 * e)) > 0) & ~outros_m
    if anel.sum() > 50:
        dif, den = media_ponderada(lo - la, anel, 12 * e)
        ok = den > 0.15
        if ok.any():
            la = la + preencher_suave(dif, ~ok, ok)
    bloco, alcance = laplaciano(orig_c, bgr(la), M)
    alcance &= ~(cv2.dilate(outros_m.astype(np.uint8), disco(2)) > 0)
    final = original.copy()
    final[y:y + h, x:x + w] = np.where(alcance[..., None], bloco, orig_c)
    M_t = np.zeros((H, W), bool)
    M_t[y:y + h, x:x + w] = M
    al_t = np.zeros((H, W), bool)
    al_t[y:y + h, x:x + w] = alcance
    info["area_M"] = round(float(M.mean()), 4)
    return Composicao(final, M_t, al_t, info)


__all__ = ["Composicao", "compor", "laplaciano", "mascara_cabeca"]
