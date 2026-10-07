#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retoque_tatuagens.py - remocao LOCAL de tatuagens sem trocar o modelo de geracao
================================================================================

O modelo continua o mesmo. Este script cuida de tudo em volta dele (etapas 3 a 11
do pipeline do Teste 7): onde o modelo trabalha, o que ele recebe e como o
resultado volta para a foto.

  1. Protecao automatica: tudo que difere entre a ORIGINAL e a BASE (rosto/cabelo
     da Luna ja aplicados) fica travado. Mais mascaras extras opcionais.
  2. Mascara organica por tatuagem: tinta = pele que perdeu saturacao E ficou mais
     escura que a pele vizinha. Sombra, cabelo e pinta continuam com cor quente,
     tinta fica cinza/azulada. Histerese, fechamento, ilhas pequenas preenchidas,
     margem pequena, nada sobre top/fundo.
  3. Um recorte por tatuagem, com contexto, ampliado para a resolucao do modelo
     (o modelo nunca trabalha numa area minuscula -> sem blocos).
  4. Pre-preenchimento suave + grao dentro da mascara: o modelo nao "ve" a tinta,
     entao nao sobra fantasma, e pode rodar com denoise moderado.
  5. [SEU MODELO] reconstroi so o recorte (inpaint + Depth ControlNet).
  6. Volta para a foto: correcao de cor/luz ancorada na pele limpa em volta,
     grao igual ao original, borda em degrade, composicao so em pele.
  7. Checagem automatica: nada fora da area mudou, tinta residual, emenda de tom,
     arestas retas (blocos) e pontinhos. Reprova sozinho.

Uso (pod ligado so no passo 2)
------------------------------
  # 1) SEM POD: mascaras + recortes pre-preenchidos para o modelo
  python retoque_tatuagens.py preparar --original foto.jpg --base luna_base.png \
         --pessoa auto --trabalho foto1/            # confira foto1/debug_mascara.jpg

  # 2) COM POD, uma vez para todas as fotos: liga, roda, baixa, desliga, compoe
  python comfy_pod.py --trabalho foto1/ foto2/ --workflow inpaint_regiao_api.json \
         --url URL_DO_COMFY --ligar "..." --desligar "..." --compor

  # (ou, sem comfy_pod.py) compor manualmente depois de ter regiao_XX_saida.png:
  python retoque_tatuagens.py compor --trabalho foto1/ --saida foto1/final.png

  Se sobrar tinta, o compor cria foto1/passe2/ so com o que sobrou: rode o passo 2
  nessa pasta (de preferencia junto com as proximas fotos, na mesma sessao do pod).

  Opcoes: --proteger colar.png (nao mexer), --incluir argola.png (reconstruir mesmo
  em area protegida), --excluir x.png, --config ajustes.json (qualquer campo de Config).

Dependencias (CPU, sem GPU): opencv-python-headless numpy scipy
             opcional: rembg[cpu] para --pessoa auto (baixa ~170 MB uma vez)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from dataclasses import asdict, dataclass

import cv2
import numpy as np
from scipy import ndimage as ndi


# =============================================================================
# Parametros (todos os tamanhos valem para 780 px de largura e sao escalados)
# =============================================================================
@dataclass
class Config:
    largura_ref: int = 780
    largura_max_deteccao: int = 1600

    # o que nunca e tatuagem
    L_roupa_escura: float = 28.0      # top/cadeira pretos: L* abaixo disso e cor neutra
    area_min_fundo: float = 800.0     # area minima (px) de uma regiao de fundo/roupa

    # cor da tinta
    sigma_cor: float = 2.5            # JPEG guarda cor em blocos; suaviza antes de medir
    deficit_forte: float = 0.55       # perdeu >55% da saturacao da pele vizinha
    deficit_fraco: float = 0.25
    escuro_forte: float = 5.0         # e esta mais escura que a pele vizinha (L*)
    escuro_fraco: float = 1.5
    linha_forte: float = 5.0          # ou e um traco fino escuro (black-hat)
    linha_fraca: float = 2.5
    raio_linha: float = 4.0
    L_min: float = 28.0               # abaixo: cabelo escuro, sombra profunda, roupa
    pele_ao_redor_min: float = 0.30   # componente sem pele em volta nao e tatuagem
    b_min_tinta: float = -3.0         # mediana b* abaixo disso = objeto azul/cinza frio (cadeira,
                                      # jeans). Se a tatuagem for azul forte, baixe para -10
    area_min_isolado: float = 60.0    # mancha isolada menor que isso = pinta/ruido

    # mascara organica
    raio_fechamento: float = 5.0      # une tracos proximos
    area_ilha_max: float = 400.0      # ilhas de pele menores que isso entram na mascara
    margem: float = 3.0               # expansao alem da tinta (halo + JPEG)
    area_min_componente: float = 20.0

    # recorte / modelo
    raio_degrade: float = 8.0         # largura da borda suave
    contexto: float = 0.45            # recorte = tatuagem + 45% de contexto em volta
    contexto_min: float = 40.0
    distancia_grupo: float = 18.0     # tatuagens mais proximas que isso = um recorte so
    lado_modelo: int = 1024           # lado maior do recorte enviado ao modelo
    multiplo: int = 64
    raio_classico: float = 3.5        # so traco fino: resolve sem chamar o modelo

    # integracao
    sigma_correcao: float = 6.0
    semente: int = 7
    grao_antes_do_modelo: bool = True   # passe 2: False = o modelo recebe a pele lisa; o grao entra so no integrar

    # checagem
    max_tinta_residual: float = 0.03  # fracao da area de tinta original
    max_emenda: float = 4.0           # Delta-E medio na faixa de degrade
    max_tom: float = 4.0              # Delta-E entre o tom reconstruido e o esperado (mancha)
    max_blocos: float = 0.004         # fracao da area editada com arestas retas
    max_pontinhos: float = 2.5        # densidade relativa a pele limpa vizinha
    max_passes: int = 2


# =============================================================================
# Utilidades
# =============================================================================
def _disco(r):
    r = max(1, int(round(r)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def _dil(m, r):
    return cv2.dilate(m.astype(np.uint8), _disco(r)) > 0


def _ero(m, r):
    return cv2.erode(m.astype(np.uint8), _disco(r)) > 0


def _fecha(m, r):
    return cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, _disco(r)) > 0


def _abre(m, r):
    return cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, _disco(r)) > 0


def _lab(bgr):
    return cv2.cvtColor(bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)


def _bgr(lab):
    out = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_LAB2BGR)
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _media_ponderada(x, w, sigma):
    """Media gaussiana usando so os pixels com peso (normalized convolution)."""
    w = w.astype(np.float32)
    den = cv2.GaussianBlur(w, (0, 0), sigma)
    if x.ndim == 3:
        num = cv2.GaussianBlur(x * w[..., None], (0, 0), sigma)
        return num / np.maximum(den, 1e-6)[..., None], den
    num = cv2.GaussianBlur(x * w, (0, 0), sigma)
    return num / np.maximum(den, 1e-6), den


def _grandes(m, area, r_abrir):
    m = _abre(m, r_abrir)
    n, lb, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    ok = np.zeros(n, bool)
    ok[1:] = st[1:, 4] >= area
    return ok[lb]


def _remover_pequenos(m, area):
    n, lb, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    ok = np.zeros(n, bool)
    ok[1:] = st[1:, 4] >= area
    return ok[lb]


def _tapar_buracos(m, area_max):
    """Preenche buracos (fechados, sem tocar a borda da imagem) menores que area_max."""
    inv = (~m).astype(np.uint8)
    n, lb, st, _ = cv2.connectedComponentsWithStats(inv, 4)
    H, W = m.shape
    ok = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a <= area_max and x > 0 and y > 0 and x + w < W and y + h < H:
            ok[i] = True
    return m | ok[lb]


def _histerese(fraco, forte):
    lb, _ = ndi.label(fraco | forte, structure=np.ones((3, 3)))
    ids = np.unique(lb[forte])
    ids = ids[ids > 0]
    return np.isin(lb, ids)


def preencher_suave(img, buraco, validos=None):
    """Interpola img dentro de `buraco` a partir dos pixels `validos` (push-pull).
    Resultado liso e sem emenda: e o 'healing' de baixa frequencia."""
    x = img.astype(np.float32)
    if validos is None:
        validos = ~buraco
    w = (validos & ~buraco).astype(np.float32)
    if w.sum() < 1:
        return x
    tres = x.ndim == 3

    def ex(d):
        return d[..., None] if tres else d

    niveis = []
    xi, wi = x * ex(w), w
    while min(wi.shape[:2]) > 3:
        niveis.append((xi, wi))
        xi, wi = cv2.pyrDown(xi), cv2.pyrDown(wi)
    media = (x * ex(w)).reshape(-1, *x.shape[2:]).sum(0) / w.sum()
    est = np.where(ex(wi) > 1e-6, xi / ex(np.maximum(wi, 1e-6)), media)
    for xi, wi in reversed(niveis):
        sobe = cv2.pyrUp(est, dstsize=(wi.shape[1], wi.shape[0]))
        conf = np.clip(wi * 2.0, 0, 1)
        val = xi / ex(np.maximum(wi, 1e-6))
        est = val * ex(conf) + sobe * ex(1 - conf)
    out = x.copy()
    out[buraco] = est[buraco]
    return out


def _grao(L, onde, sigma=1.0):
    """Grao (ruido fino) da pele, robusto a bordas e restos de tinta (MAD)."""
    hp = L - cv2.GaussianBlur(L, (0, 0), sigma)
    v = hp[onde]
    if v.size < 30:
        return 0.0
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def _ruido(shape, std, rng, sigma=0.8):
    n = rng.standard_normal(shape).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), sigma)
    n /= max(float(n.std()), 1e-6)
    hp = n - cv2.GaussianBlur(n, (0, 0), 1.0)          # mede como _grao mede
    mad = 1.4826 * float(np.median(np.abs(hp)))
    return n * (std / max(mad, 1e-6))


def _ler(caminho, gray=False):
    img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(caminho)
    return img


def _ler_mascara(caminho, shape):
    m = _ler(caminho, gray=True)
    if m.shape != shape[:2]:
        m = cv2.resize(m, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return m > 127


def _salvar_mascara(caminho, m):
    cv2.imwrite(caminho, (m.astype(np.uint8) * 255))


# =============================================================================
# 1. Protecao
# =============================================================================
def protecao_automatica(original, base):
    """Tudo que a base ja mudou em relacao a original (rosto e cabelo da Luna)
    fica travado. Assim nao precisa de detector de rosto."""
    if base is None or base.shape != original.shape:
        return np.zeros(original.shape[:2], bool)
    e = original.shape[1] / Config.largura_ref
    dE = np.sqrt(((_lab(original) - _lab(base)) ** 2).sum(2))
    m = cv2.GaussianBlur(dE, (0, 0), 2 * e) > 4.0
    m = _abre(m, 2 * e)
    m = _remover_pequenos(m, 500 * e * e)
    m = _fecha(m, 6 * e)
    return _dil(m, 3 * e)


def mascara_pessoa_auto(img):
    """Silhueta da pessoa com rembg (opcional). Sem ela, cadeira/piso cinza ou
    marrom colados na pele podem ser confundidos com tinta."""
    try:
        from rembg import new_session, remove
    except ImportError:
        print("  [aviso] rembg nao instalado (pip install rembg[cpu]); seguindo sem silhueta")
        return None
    sess = new_session("u2net_human_seg")
    m = np.array(remove(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), session=sess, only_mask=True)) > 127
    return _dil(m, 8 * img.shape[1] / Config.largura_ref)   # folga: a cor decide a borda


# =============================================================================
# 2. Deteccao da tinta
# =============================================================================
@dataclass
class Deteccao:
    tinta: np.ndarray       # tinta detectada (bool)
    permitido: np.ndarray   # onde pode haver tatuagem / edicao (bool)
    pele: np.ndarray        # pele (cor quente) usada para a referencia (bool)
    pele_limpa: np.ndarray  # pele sem tinta, sem fio de cabelo, sem dobra: fonte de cor/grao
    Lref: np.ndarray        # luminosidade esperada da pele em cada ponto
    cref: np.ndarray        # saturacao esperada da pele em cada ponto
    den: np.ndarray
    escuro: np.ndarray      # roupa/objetos escuros neutros (top, cadeira)
    escala: float


def _caracteristicas(lab, internos, Lref, cref, e, cfg):
    L = lab[..., 0]
    a, _ = _media_ponderada(lab[..., 1], internos, cfg.sigma_cor * e)
    b, _ = _media_ponderada(lab[..., 2], internos, cfg.sigma_cor * e)
    c = np.hypot(a, b)
    dL = Lref - L
    dc = 1.0 - c / np.maximum(cref, 1.0)
    bh = cv2.morphologyEx(L, cv2.MORPH_BLACKHAT, _disco(cfg.raio_linha * e))
    return L, a, b, c, dL, dc, bh


def detectar(original, protegido=None, pessoa=None, cfg=Config()):
    H, W = original.shape[:2]
    f = min(1.0, cfg.largura_max_deteccao / W)
    img = cv2.resize(original, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA) if f < 1 else original
    h, w = img.shape[:2]
    e = w / cfg.largura_ref

    def red(m, default):
        if m is None:
            return np.full((h, w), default, bool)
        return cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0 if f < 1 else m

    prot, pes = red(protegido, False), red(pessoa, True)

    lab = _lab(img)
    L, a0, b0 = lab[..., 0], lab[..., 1], lab[..., 2]
    c0 = np.hypot(cv2.GaussianBlur(a0, (0, 0), cfg.sigma_cor * e), cv2.GaussianBlur(b0, (0, 0), cfg.sigma_cor * e))

    # roupa preta/cadeira (escuro e neutro/frio) e fundo claro frio (parede, rede)
    escuro = _grandes((L < cfg.L_roupa_escura) & (b0 < 3), 2 * cfg.area_min_fundo * e * e, 3 * e)
    escuro = _tapar_buracos(escuro, 3000 * e * e)
    claro_frio = _grandes((L > 70) & (b0 < -1.5) & (c0 < 14), cfg.area_min_fundo * e * e, 2 * e)
    permitido = ~(prot | ~pes | _dil(escuro | claro_frio, 2 * e))

    # cor medida so com pixels internos: borda do top/fundo nao "desbota" a pele
    internos = _ero(permitido, 2 * e)
    a, _ = _media_ponderada(a0, internos, cfg.sigma_cor * e)
    b, _ = _media_ponderada(b0, internos, cfg.sigma_cor * e)
    c = np.hypot(a, b)
    hue = np.degrees(np.arctan2(b, a))
    pele = permitido & (L > cfg.L_min) & (c > 12) & (b > 6) & (hue > 25) & (hue < 85)

    def referencia(x):  # perto: pele vizinha; dentro de tatuagem grande: pele mais longe
        r1, d1 = _media_ponderada(x, pele, 9 * e)
        r2, d2 = _media_ponderada(x, pele, 30 * e)
        k = np.clip(d1 / 0.15, 0, 1)
        return k * r1 + (1 - k) * r2, d2

    Lref, den = referencia(L)
    cref, _ = referencia(c)
    L, a, b, c, dL, dc, bh = _caracteristicas(lab, internos, Lref, cref, e, cfg)
    pele_limpa = pele & (dL < 6) & (dc < 0.25) & (bh < cfg.linha_fraca)

    perto_escuro = _dil(escuro, 8 * e)
    longe = _ero(permitido, 4 * e) & ~perto_escuro
    ok = den > 0.01
    forte = longe & ok & (L > cfg.L_min) & (dc > cfg.deficit_forte) & \
        ((dL > cfg.escuro_forte) | (bh > cfg.linha_forte))
    fraco = permitido & ok & (L > cfg.L_min - 4) & (dc > cfg.deficit_fraco) & \
        ((dL > cfg.escuro_fraco) | (bh > cfg.linha_fraca))
    tinta = _histerese(fraco, forte)
    tinta &= ~_dil(escuro, 7 * e) | _dil(forte, 6 * e)   # borda do top so se colada em tinta
    tinta = _filtrar_componentes(tinta, pele, permitido, b0, e, cfg)

    def amp(x, interp=cv2.INTER_LINEAR):
        if f >= 1:
            return x
        if x.dtype == bool:
            return cv2.resize(x.astype(np.uint8), (W, H), interpolation=cv2.INTER_LINEAR) > 0
        return cv2.resize(x, (W, H), interpolation=interp)

    return Deteccao(amp(tinta), amp(permitido), amp(pele), amp(pele_limpa), amp(Lref), amp(cref), amp(den),
                    amp(escuro), W / cfg.largura_ref)


def _filtrar_componentes(tinta, pele, permitido, b_raw, e, cfg):
    """Remove componentes que nao tem pele em volta (cabelo, objetos), objetos frios
    (cadeira, jeans) e pintas soltas."""
    n, lb, st, _ = cv2.connectedComponentsWithStats(tinta.astype(np.uint8), 8)
    H, W = tinta.shape
    keep = np.zeros(n, bool)
    r = max(3, int(round(10 * e)))
    amin = cfg.area_min_isolado * e * e
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area < amin:
            continue
        if np.median(b_raw[y:y + h, x:x + w][lb[y:y + h, x:x + w] == i]) < cfg.b_min_tinta:
            continue
        x0, y0, x1, y1 = max(0, x - r), max(0, y - r), min(W, x + w + r), min(H, y + h + r)
        comp = lb[y0:y1, x0:x1] == i
        anel = _dil(comp, r) & ~tinta[y0:y1, x0:x1] & permitido[y0:y1, x0:x1]
        if anel.sum() < 0.2 * (_dil(comp, r) & ~comp).sum():
            continue  # cercado de coisa protegida (cabelo, top): nao e tatuagem
        keep[i] = pele[y0:y1, x0:x1][anel].mean() >= cfg.pele_ao_redor_min
    out = keep[lb]
    pequenos = tinta & ~out & _dil(out, 8 * e)  # pontilhado colado numa tatuagem fica
    return out | pequenos


def mascara_organica(tinta, permitido, e, cfg=Config()):
    m = _fecha(tinta, cfg.raio_fechamento * e)
    m = _tapar_buracos(m, cfg.area_ilha_max * e * e)   # ilhas pequenas = pontinhos claros
    m = _dil(m, cfg.margem * e)
    m &= permitido
    return _remover_pequenos(m, cfg.area_min_componente * e * e)


# =============================================================================
# 3-6. Regioes, entrada do modelo, integracao
# =============================================================================
def planejar(dura, e, cfg=Config()):
    """Agrupa tatuagens proximas e devolve (grupos, regioes)."""
    H, W = dura.shape
    grupos, n = ndi.label(_dil(dura, cfg.distancia_grupo * e / 2))
    grupos = (grupos * dura).astype(np.int32)
    regs = []
    for g in range(1, n + 1):
        m = grupos == g
        if not m.any():
            continue
        ys, xs = np.where(m)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        pad = max(cfg.contexto_min * e, cfg.contexto * max(x1 - x0, y1 - y0))
        lado = max(x1 - x0, y1 - y0) + 2 * pad
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        X0, X1 = int(max(0, round(cx - lado / 2))), int(min(W, round(cx + lado / 2)))
        Y0, Y1 = int(max(0, round(cy - lado / 2))), int(min(H, round(cy + lado / 2)))
        raio = float(cv2.distanceTransform(m[Y0:Y1, X0:X1].astype(np.uint8), cv2.DIST_L2, 5).max())
        modo = "classico" if raio <= cfg.raio_classico * e else "modelo"
        regs.append(dict(id=len(regs) + 1, grupo=g, caixa=[X0, Y0, X1, Y1], modo=modo, raio=round(raio, 1)))
    return grupos, regs


def _arrays(reg, grupos, permitido, e, cfg):
    X0, Y0, X1, Y1 = reg["caixa"]
    d = grupos[Y0:Y1, X0:X1] == reg["grupo"]
    p = permitido[Y0:Y1, X0:X1] | d
    dist = cv2.distanceTransform((~d).astype(np.uint8), cv2.DIST_L2, 5)
    t = np.clip(1.0 - dist / (cfg.raio_degrade * e), 0, 1)
    alfa = (t * t * (3 - 2 * t)) * cv2.GaussianBlur(p.astype(np.float32), (0, 0), max(0.8, 0.8 * e))
    alfa[d] = 1.0
    mm = (dist <= cfg.raio_degrade * e + 3 * e) & _dil(p, 2 * e)   # mascara do modelo
    return d, p, alfa.astype(np.float32), mm


def pre_preencher(base_c, d, p, pl, e, rng, grao=True):
    """Tira a tinta antes do modelo: preenchimento suave a partir da pele limpa
    vizinha (nunca de cabelo, roupa ou fundo) + grao igual ao dela."""
    lab = _lab(base_c)
    validos = (pl | (_dil(d, 2 * e) & p)) & ~d      # borda imediata + pele limpa
    if validos.sum() < 30:
        validos = p & ~d
    lab2 = preencher_suave(lab, d, validos)
    s = _grao(lab[..., 0], validos & _dil(d, 10 * e)) if grao else 0.0
    if s > 0:
        lab2[..., 0] += _ruido(d.shape, s, rng) * d
    out = base_c.copy()
    out[d] = _bgr(lab2)[d]
    return out


def para_modelo(img, mascara, cfg=Config()):
    h, w = img.shape[:2]
    s = cfg.lado_modelo / max(h, w)
    nw, nh = max(8, int(round(w * s))), max(8, int(round(h * s)))
    im = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    mk = cv2.resize(mascara.astype(np.uint8) * 255, (nw, nh), interpolation=cv2.INTER_LINEAR)
    mk = np.where(mk >= 128, 255, 0).astype(np.uint8)
    ph, pw = (-nh) % cfg.multiplo, (-nw) % cfg.multiplo
    im = cv2.copyMakeBorder(im, 0, ph, 0, pw, cv2.BORDER_REFLECT_101)
    mk = cv2.copyMakeBorder(mk, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=0)
    return im, mk, dict(w=w, h=h, nw=nw, nh=nh, pw=nw + pw, ph=nh + ph)


def do_modelo(saida, meta):
    if saida.shape[:2] != (meta["ph"], meta["pw"]):
        if abs(saida.shape[1] / saida.shape[0] - meta["nw"] / meta["nh"]) < 0.02:   # veio sem o padding
            saida = cv2.resize(saida, (meta["nw"], meta["nh"]), interpolation=cv2.INTER_AREA)
        else:
            saida = cv2.resize(saida, (meta["pw"], meta["ph"]), interpolation=cv2.INTER_AREA)
    saida = saida[: meta["nh"], : meta["nw"]]
    return cv2.resize(saida, (meta["w"], meta["h"]), interpolation=cv2.INTER_AREA)


def integrar(base_c, rec_c, d, p, pl, alfa, mm, e, cfg, rng):
    """Corrige cor/luz pelo anel de pele limpa, iguala o grao e compoe com degrade."""
    lb, lr = _lab(base_c), _lab(rec_c)
    anel = mm & ~d & p & pl                 # pele limpa que o modelo regenerou
    if anel.sum() < 20:
        anel = mm & ~d & p
    if anel.sum() >= 20:
        sm, den = _media_ponderada(lb - lr, anel, cfg.sigma_correcao * e)
        ok = den > 0.15
        if ok.any():
            lr = lr + preencher_suave(sm, ~ok, ok)   # leva a diferenca de tom para dentro
    fora = p & pl & ~mm & _dil(mm, 8 * e)
    s_o = _grao(lb[..., 0], fora if fora.sum() >= 30 else anel)
    s_r = _grao(lr[..., 0], d)
    if s_o > s_r + 0.05:
        lr[..., 0] += _ruido(d.shape, float(np.sqrt(s_o ** 2 - s_r ** 2)), rng)
    out = _bgr(lb * (1 - alfa[..., None]) + lr * alfa[..., None])
    return np.where(alfa[..., None] > 0, out, base_c)


# =============================================================================
# 7. Checagem
# =============================================================================
def _arestas_retas(L, zona, e):
    Ls = cv2.GaussianBlur(L, (0, 0), 0.8)
    gx = cv2.Sobel(Ls, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(Ls, cv2.CV_32F, 0, 1, ksize=3) / 8
    T = 2.5                                   # degrau de L* por pixel (calibrado nos testes 6/6b)
    v = (np.abs(gx) > T) & (np.abs(gy) < 0.35 * np.abs(gx))
    h = (np.abs(gy) > T) & (np.abs(gx) < 0.35 * np.abs(gy))
    k = max(5, int(round(7 * e)))
    v = cv2.morphologyEx(v.astype(np.uint8), cv2.MORPH_OPEN, np.ones((k, 1), np.uint8)) > 0
    h = cv2.morphologyEx(h.astype(np.uint8), cv2.MORPH_OPEN, np.ones((1, k), np.uint8)) > 0
    return (v | h) & zona


def _pontinhos(L, zona, e):
    Lu = np.clip(L * 2.55, 0, 255).astype(np.uint8)
    hp = L - cv2.medianBlur(Lu, 5).astype(np.float32) / 2.55
    m = (np.abs(hp) > 6) & zona
    n, lb, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    peq = np.zeros(n, bool)
    peq[1:] = st[1:, 4] <= max(4, 8 * e * e)
    return peq[lb]


def checar(original, base, final, det, dura, alfa_total, cfg=Config()):
    e = det.escala
    rel, mapas = {}, {}
    mexido = alfa_total > 0
    dif = np.abs(final.astype(np.int16) - base.astype(np.int16)).max(2)
    rel["mudanca_fora_da_area"] = int(dif[~mexido].max()) if (~mexido).any() else 0

    lab, lb = _lab(final), _lab(base)
    internos = _ero(det.permitido | dura, 2 * e)
    L, a, b, c, dL, dc, bh = _caracteristicas(lab, internos, det.Lref, det.cref, e, cfg)
    perto = _dil(dura, 20 * e) & (det.permitido | dura) & ~_dil(det.escuro, 6 * e)
    resid = perto & (L > cfg.L_min) & (lab[..., 2] > cfg.b_min_tinta) & (dc > cfg.deficit_forte) & \
        ((dL > cfg.escuro_forte) | (bh > cfg.linha_forte))
    resid = _remover_pequenos(resid, max(3, 4 * e * e))
    mapas["tinta_residual"] = resid
    rel["tinta_residual"] = round(float(resid.sum()) / max(int(det.tinta.sum()), 1), 4)

    rampa = (alfa_total > 0.05) & (alfa_total < 0.95)
    rel["emenda_deltaE"] = round(float(np.sqrt(((lab - lb) ** 2).sum(2))[rampa].mean()), 2) if rampa.any() else 0.0

    # tom: o miolo reconstruido comparado com o tom esperado (pele limpa em volta, interpolada)
    esperado = preencher_suave(lb, dura, det.pele_limpa & ~dura)
    lpf = cv2.GaussianBlur(lab, (0, 0), 4 * e)
    lpe = cv2.GaussianBlur(esperado, (0, 0), 4 * e)
    miolo = _ero(dura, 2 * e)
    dtom = np.sqrt(((lpf - lpe) ** 2).sum(2))
    rel["tom_deltaE"] = round(float(np.median(dtom[miolo])), 2) if miolo.any() else 0.0

    # blocos: arestas retas novas no miolo, longe do top, do fundo e da silhueta
    zona = miolo & ~_dil(det.escuro, 8 * e) & ~_dil(~det.permitido, 4 * e)
    blocos = _arestas_retas(L, zona, e) & ~_dil(_arestas_retas(lb[..., 0], zona, e), 2)  # so arestas novas
    mapas["blocos"] = blocos
    n_blocos = int(blocos.sum())
    rel["blocos"] = round(n_blocos / max(int(zona.sum()), int(2000 * e * e)), 5)  # area pequena nao infla

    viz = det.pele & ~_dil(dura, 6 * e) & _dil(dura, 40 * e)
    dens_viz = _pontinhos(lb[..., 0], viz, e).sum() / max(viz.sum(), 1)
    pts = _pontinhos(L, dura, e)
    mapas["pontinhos"] = pts
    dens = pts.sum() / max(dura.sum(), 1)
    rel["pontinhos_relativo"] = round(float(dens / max(dens_viz, 2e-4)), 2)

    falhas = []
    if rel["mudanca_fora_da_area"] > 0:
        falhas.append("algo mudou fora da area editada")
    if rel["tinta_residual"] > cfg.max_tinta_residual:
        falhas.append("tinta residual")
    if rel["emenda_deltaE"] > cfg.max_emenda:
        falhas.append("emenda visivel no degrade")
    if rel["tom_deltaE"] > cfg.max_tom:
        falhas.append("mancha de tom (clara/escura/alaranjada)")
    if rel["blocos"] > cfg.max_blocos and n_blocos >= 15 * e:
        falhas.append("arestas retas / blocos")
    if rel["pontinhos_relativo"] > cfg.max_pontinhos:
        falhas.append("pontinhos")
    rel["falhas"] = falhas
    rel["aprovado"] = not falhas
    return rel, mapas


# =============================================================================
# Visualizacao
# =============================================================================
def salvar_debug(caminho, img, det, dura, regs=None, mapas=None):
    v = img.copy()

    def pinta(m, cor, k=0.5):
        v[m] = (v[m] * (1 - k) + np.array(cor) * k).astype(np.uint8)

    pinta(~det.permitido, (90, 0, 90), 0.35)
    pinta(det.tinta, (0, 0, 255), 0.6)
    cont, _ = cv2.findContours(dura.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(v, cont, -1, (0, 255, 255), 1)
    if mapas:
        pinta(mapas.get("tinta_residual", np.zeros_like(dura)), (255, 0, 255), 0.9)
        pinta(mapas.get("blocos", np.zeros_like(dura)), (0, 255, 0), 0.9)
    for r in regs or []:
        X0, Y0, X1, Y1 = r["caixa"]
        cv2.rectangle(v, (X0, Y0), (X1 - 1, Y1 - 1), (255, 200, 0), 1)
        cv2.putText(v, f"{r['id']}:{r['modo'][0]}", (X0 + 3, Y0 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 1)
    cv2.imwrite(caminho, v)


# =============================================================================
# Orquestracao
# =============================================================================
def _mascaras(original, base, args_proteger, args_pessoa, args_incluir, args_excluir):
    prot = protecao_automatica(original, base)
    for pth in args_proteger or []:
        prot |= _ler_mascara(pth, original.shape)
    pessoa = None
    if args_pessoa == "auto":
        pessoa = mascara_pessoa_auto(original)
    elif args_pessoa:
        pessoa = _ler_mascara(args_pessoa, original.shape)
    incl = _ler_mascara(args_incluir, original.shape) if args_incluir else None
    excl = _ler_mascara(args_excluir, original.shape) if args_excluir else None
    return prot, pessoa, incl, excl


def montar_dura(original, prot, pessoa, incl, excl, cfg):
    det = detectar(original, prot, pessoa, cfg)
    e = det.escala
    dura = mascara_organica(det.tinta, det.permitido, e, cfg) & ~_dil(det.escuro, 4 * e)
    if incl is not None:              # mascara manual (ex.: argola) vale mesmo em area protegida
        dura |= incl
        det.permitido = det.permitido | _dil(incl, cfg.raio_degrade * e + 4 * e)
    if excl is not None:
        dura &= ~excl
    return det, dura


def marcar_editado(det, editado, dura_atual):
    """Depois de um passe, a pele que ja foi limpa vira referencia valida de cor e grao
    para o passe seguinte (na original ela ainda tinha tinta)."""
    det.pele_limpa = det.pele_limpa | (editado & ~_dil(dura_atual, 2 * det.escala))


def executar_passe(base_img, dura, det, cfg, reconstruir, saidas=None):
    """Um passe: para cada regiao, pre-preenche, chama o modelo (ou usa o
    preenchimento classico), integra. Retorna imagem e alfa total."""
    e = det.escala
    rng = np.random.default_rng(cfg.semente)
    grupos, regs = planejar(dura, e, cfg)
    final = base_img.copy()
    alfa_total = np.zeros(dura.shape, np.float32)
    for reg in regs:
        X0, Y0, X1, Y1 = reg["caixa"]
        d, p, alfa, mm = _arrays(reg, grupos, det.permitido, e, cfg)
        pl = det.pele_limpa[Y0:Y1, X0:X1]
        base_c = final[Y0:Y1, X0:X1]
        pre = pre_preencher(base_c, d, p, pl, e, rng, cfg.grao_antes_do_modelo)
        if reg["modo"] == "classico" or (reconstruir is None and saidas is None):
            rec = pre
        elif saidas is not None:
            rec = saidas.get(reg["id"])
            if rec is None:
                print(f"  regiao {reg['id']}: sem saida do modelo, usando preenchimento classico")
                rec = pre
        else:
            entrada, mascara, meta = para_modelo(pre, mm, cfg)
            rec = do_modelo(reconstruir(entrada, mascara), meta)
        final[Y0:Y1, X0:X1] = integrar(base_c, rec, d, p, pl, alfa, mm, e, cfg, rng)
        alfa_total[Y0:Y1, X0:X1] = np.maximum(alfa_total[Y0:Y1, X0:X1], alfa)
    return final, alfa_total, regs


def processar(original, base=None, reconstruir=None, protegido=None, pessoa=None,
              incluir=None, excluir=None, cfg=Config(), pasta_debug=None):
    base = original.copy() if base is None else base
    assert base.shape == original.shape, "original e base precisam ter o mesmo tamanho/enquadramento"
    prot = protecao_automatica(original, base) | (protegido if protegido is not None else False)
    det, dura = montar_dura(original, prot, pessoa, incluir, excluir, cfg)
    atual, alfa_acum, dura_acum = base.copy(), np.zeros(dura.shape, np.float32), dura.copy()
    rel, mapas, alvo = {}, {}, dura
    for passe in range(1, cfg.max_passes + 1):
        atual, alfa, regs = executar_passe(atual, alvo, det, cfg, reconstruir)
        alfa_acum = np.maximum(alfa_acum, alfa)
        dura_acum |= alvo
        rel, mapas = checar(original, base, atual, det, dura_acum, alfa_acum, cfg)
        rel["passe"] = passe
        print(f"  passe {passe}: {len(regs)} regioes | {json.dumps(rel, ensure_ascii=False)}")
        if pasta_debug:
            salvar_debug(os.path.join(pasta_debug, f"debug_passe{passe}.jpg"), original, det, alvo, regs, mapas)
        if rel["tinta_residual"] <= cfg.max_tinta_residual:
            break
        alvo = mascara_organica(mapas["tinta_residual"], det.permitido | dura_acum, det.escala, cfg)
        if not alvo.any():
            break
        marcar_editado(det, alfa_acum > 0, alvo)
    return atual, rel


# =============================================================================
# CLI
# =============================================================================
def _carregar_adaptador(caminho):
    spec = importlib.util.spec_from_file_location("adaptador_modelo", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.reconstruir


def _escrever_trabalho(pasta, original, base, prot, pessoa, incl, det, dura, cfg):
    """Grava tudo que o modelo precisa (recortes pre-preenchidos + mascaras) e o plano."""
    os.makedirs(pasta, exist_ok=True)
    e = det.escala
    grupos, regs = planejar(dura, e, cfg)
    rng = np.random.default_rng(cfg.semente)
    for reg in regs:
        X0, Y0, X1, Y1 = reg["caixa"]
        d, p, alfa, mm = _arrays(reg, grupos, det.permitido, e, cfg)
        pre = pre_preencher(base[Y0:Y1, X0:X1], d, p, det.pele_limpa[Y0:Y1, X0:X1], e, rng, cfg.grao_antes_do_modelo)
        if reg["modo"] == "modelo":
            ent, msk, meta = para_modelo(pre, mm, cfg)
            reg["meta"] = meta
            cv2.imwrite(os.path.join(pasta, f"regiao_{reg['id']:02d}_entrada.png"), ent)
            cv2.imwrite(os.path.join(pasta, f"regiao_{reg['id']:02d}_mascara.png"), msk)
    cv2.imwrite(os.path.join(pasta, "original.png"), original)
    cv2.imwrite(os.path.join(pasta, "base.png"), base)
    _salvar_mascara(os.path.join(pasta, "protegido.png"), prot)
    if pessoa is not None:
        _salvar_mascara(os.path.join(pasta, "pessoa.png"), pessoa)
    if incl is not None:
        _salvar_mascara(os.path.join(pasta, "incluir.png"), incl)
    _salvar_mascara(os.path.join(pasta, "dura.png"), dura)
    with open(os.path.join(pasta, "plano.json"), "w") as fp:
        json.dump(dict(config=asdict(cfg), regioes=regs), fp, indent=1)
    salvar_debug(os.path.join(pasta, "debug_mascara.jpg"), original, det, dura, regs)
    return regs


def cmd_preparar(a, cfg):
    original = _ler(a.original)
    base = _ler(a.base) if a.base else original.copy()
    if base.shape != original.shape:
        raise SystemExit("original e base precisam ter o mesmo tamanho e enquadramento")
    prot, pessoa, incl, excl = _mascaras(original, base, a.proteger, a.pessoa, a.incluir, a.excluir)
    if prot.mean() > 0.5:
        print("  [aviso] mais da metade da base difere da original: parece uma regeneracao global. "
              "Use a base COMPOSTA (original + rosto/cabelo da Luna), senao quase tudo fica protegido.")
    det, dura = montar_dura(original, prot, pessoa, incl, excl, cfg)
    regs = _escrever_trabalho(a.trabalho, original, base, prot, pessoa, incl, det, dura, cfg)
    nm = sum(r["modo"] == "modelo" for r in regs)
    print(f"{len(regs)} regioes ({nm} para o modelo, {len(regs) - nm} resolvidas sem modelo).")
    print(f"Confira {a.trabalho}/debug_mascara.jpg antes de ligar o pod.")


def cmd_compor(a, cfg):
    t = a.trabalho
    with open(os.path.join(t, "plano.json")) as fp:
        plano = json.load(fp)
    cfg = Config(**plano["config"])
    original, base = _ler(os.path.join(t, "original.png")), _ler(os.path.join(t, "base.png"))
    prot = _ler_mascara(os.path.join(t, "protegido.png"), original.shape)
    pessoa = _ler_mascara(os.path.join(t, "pessoa.png"), original.shape) if os.path.exists(os.path.join(t, "pessoa.png")) else None
    det = detectar(original, prot, pessoa, cfg)
    if os.path.exists(os.path.join(t, "incluir.png")):
        incl = _ler_mascara(os.path.join(t, "incluir.png"), original.shape)
        det.permitido |= _dil(incl, cfg.raio_degrade * det.escala + 4 * det.escala)
    dura = _ler_mascara(os.path.join(t, "dura.png"), original.shape)
    if os.path.exists(os.path.join(t, "editado.png")):
        marcar_editado(det, _ler_mascara(os.path.join(t, "editado.png"), original.shape), dura)
    saidas = {}
    for reg in plano["regioes"]:
        cam = os.path.join(t, f"regiao_{reg['id']:02d}_saida.png")
        if reg["modo"] == "modelo" and os.path.exists(cam):
            saidas[reg["id"]] = do_modelo(_ler(cam), reg["meta"])
    final, alfa, regs = executar_passe(base, dura, det, cfg, None, saidas)
    rel, mapas = checar(original, base, final, det, dura, alfa, cfg)
    cv2.imwrite(a.saida, final)
    salvar_debug(os.path.join(t, "debug_final.jpg"), final, det, dura, regs, mapas)
    _salvar_mascara(os.path.join(t, "tinta_residual.png"), mapas["tinta_residual"])
    with open(os.path.join(t, "relatorio.json"), "w") as fp:
        json.dump(rel, fp, indent=1, ensure_ascii=False)
    print(json.dumps(rel, indent=1, ensure_ascii=False))
    print("APROVADO" if rel["aprovado"] else "REPROVADO: " + ", ".join(rel["falhas"]))
    if rel["tinta_residual"] > cfg.max_tinta_residual:
        alvo = mascara_organica(mapas["tinta_residual"], det.permitido | dura, det.escala, cfg)
        if alvo.any():   # segundo passe so na tinta que sobrou, partindo do resultado
            p2 = os.path.join(t, "passe2")
            editado = alfa > 0
            marcar_editado(det, editado, alvo)
            _escrever_trabalho(p2, original, final, prot, pessoa, None, det, alvo, cfg)
            _salvar_mascara(os.path.join(p2, "editado.png"), editado)
            print(f"Tinta residual: preparei {p2}/ so com o que sobrou. Rode o modelo nessa pasta "
                  f"(comfy_pod.py --trabalho {p2}) e depois compor --trabalho {p2}.")
    if not rel["aprovado"]:
        raise SystemExit(2)


def cmd_auto(a, cfg):
    original = _ler(a.original)
    base = _ler(a.base) if a.base else original.copy()
    prot, pessoa, incl, excl = _mascaras(original, base, a.proteger, a.pessoa, a.incluir, a.excluir)
    rec = _carregar_adaptador(a.adaptador) if a.adaptador else None
    pasta = os.path.dirname(os.path.abspath(a.saida))
    final, rel = processar(original, base, rec, prot, pessoa, incl, excl, cfg, pasta_debug=pasta)
    cv2.imwrite(a.saida, final)
    with open(os.path.splitext(a.saida)[0] + "_relatorio.json", "w") as fp:
        json.dump(rel, fp, indent=1, ensure_ascii=False)
    print("APROVADO" if rel["aprovado"] else "REPROVADO: " + ", ".join(rel["falhas"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nome in ("preparar", "auto"):
        s = sub.add_parser(nome)
        s.add_argument("--original", required=True, help="foto original (com tatuagens)")
        s.add_argument("--base", help="mesma foto ja com rosto/cabelo da Luna (Teste 5/6)")
        s.add_argument("--pessoa", help="'auto' (rembg) ou PNG branco=pessoa")
        s.add_argument("--proteger", nargs="*", help="PNGs branco=nao mexer (top, brinco, colar...)")
        s.add_argument("--incluir", help="PNG branco=reconstruir tambem (ex.: argola)")
        s.add_argument("--excluir", help="PNG branco=nunca reconstruir")
        s.add_argument("--config", help="JSON com ajustes de Config, ex. {\"margem\": 4, \"lado_modelo\": 768}")
    sub.choices["preparar"].add_argument("--trabalho", required=True)
    sub.choices["auto"].add_argument("--adaptador", help="arquivo .py com reconstruir(img, mascara)")
    sub.choices["auto"].add_argument("--saida", required=True)
    s = sub.add_parser("compor")
    s.add_argument("--trabalho", required=True)
    s.add_argument("--saida", required=True)
    a = ap.parse_args()
    cfg = Config()
    if getattr(a, "config", None):
        with open(a.config) as fp:
            cfg = Config(**{**asdict(cfg), **json.load(fp)})
    {"preparar": cmd_preparar, "compor": cmd_compor, "auto": cmd_auto}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
