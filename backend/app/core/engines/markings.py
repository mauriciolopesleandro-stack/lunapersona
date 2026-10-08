"""Completar a mascara de marcas (spec 45.6: a mascara tem de cobrir a tatuagem INTEIRA).

O detector de tinta ve a tatuagem como "buraco" na pele. Onde a tatuagem encosta no top ou na borda do
braco ela nao e buraco, e o pedaco mais escuro ficava de fora (teste de 2026-10-07: restinho junto ao top
no antebraco e no ombro). Aqui a mascara CRESCE a partir do que ja foi detectado, so por pixels vizinhos
com cara de tinta: mais escuros que a pele em volta, pouco saturados (cinza-azulado) e NAO tao escuros
quanto o preto da roupa/cadeira. Cabelo e acessorio mantido nunca entram.
"""
from __future__ import annotations

import numpy as np

from app.core.persona_replacement.transfer import dilate_round


def ink_like(rgb: np.ndarray, skin_ref: np.ndarray, min_delta: float = 15.0, max_sat: float = 0.27,
             black_floor: float = 45.0) -> np.ndarray:
    """Pixel com cara de tinta (medido na foto de 2026-10-07): cinza QUENTE/neutro (vermelho >= azul - tinta
    perdida 81,71,67 e 107,96,88), pouco saturado (pele: ~0,33; tinta: ~0,17), mais escuro que a pele de
    referencia e acima do `black_floor`. Top preto (30,33,44), borda iluminada do top (79,78,88) e cadeira
    (51,50,56) sao cinza AZULADO - ficam de fora."""
    x = rgb.astype(np.float32)
    lum = x @ np.array([0.299, 0.587, 0.114], np.float32)
    ref = skin_ref.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    mx, mn = x.max(axis=2), x.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    warm = (x[..., 0] - x[..., 2]) >= 0.0
    return (ref - lum > min_delta) & (sat < max_sat) & (lum > black_floor) & warm


def complete_markings(rgb: np.ndarray, seeds: np.ndarray, skin_ref: np.ndarray, allowed: np.ndarray,
                      max_grow: int = 24) -> np.ndarray:
    """Crescimento geodesico: a partir de `seeds`, ate `max_grow` px, so por pixels `ink_like` dentro de `allowed`."""
    cand = ink_like(rgb, skin_ref) & (allowed > 0.5)
    cur = seeds > 0.5
    for _ in range(max_grow):
        nxt = cur | ((dilate_round(cur.astype(np.float32), 1) > 0.5) & cand)
        if (nxt == cur).all():
            break
        cur = nxt
    return cur.astype(np.float32)


def clean_skin_reference(rgb: np.ndarray, markings: np.ndarray, person: np.ndarray) -> np.ndarray:
    """Pele de referencia SO de pele de verdade: o classificador de pele aceita metade da tinta cinza-quente
    como pele (teste de 2026-10-07), o que deixava a "pele em volta" tao escura quanto a tinta. Pele real e
    saturada (~0,33 contra ~0,17 da tinta) e fica longe das marcas."""
    from app.core.engines.skin import push_pull_fill
    from app.core.persona_replacement.segmentation import skin_pixels

    x = rgb.astype(np.float32)
    sat = (x.max(axis=2) - x.min(axis=2)) / np.maximum(x.max(axis=2), 1.0)
    known = (skin_pixels(rgb) > 0.5) & (sat > 0.22) & (person > 0.5) & ~(dilate_round(markings, 3) > 0.5)
    return push_pull_fill(rgb, (~known).astype(np.float32), known.astype(np.float32))


def keep_inked_regions(markings: np.ndarray, rgb: np.ndarray, skin_ref: np.ndarray, core_sat: float = 0.25,
                       core_delta: float = 15.0, max_iter: int = 600) -> np.ndarray:
    """So fica a regiao de marca que tem MIOLO de tinta (cinza pouco saturado e mais escuro que a pele limpa).
    Teste de 2026-10-07 (porta de madeira): a borda do braco contra a madeira (marrom, saturada ~0,50) virou
    "tatuagem" numa foto sem tatuagem e reprovou uma troca com identidade 0,80. Tinta real: muitos pixels < 0,1."""
    x = rgb.astype(np.float32)
    lum = x @ np.array([0.299, 0.587, 0.114], np.float32)
    ref = skin_ref.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    sat = (x.max(axis=2) - x.min(axis=2)) / np.maximum(x.max(axis=2), 1.0)
    m = markings > 0.5
    core = m & (sat < core_sat) & (ref - lum > core_delta)
    core = (dilate_round(1.0 - dilate_round(1.0 - core.astype(np.float32), 1), 1) > 0.5)  # abertura: tira ponto solto
    cur = core
    for _ in range(max_iter):  # reconstrucao: a regiao inteira volta se tem miolo
        nxt = (dilate_round(cur.astype(np.float32), 1) > 0.5) & m
        if (nxt == cur).all():
            break
        cur = nxt
    return cur.astype(np.float32)


__all__ = ["clean_skin_reference", "complete_markings", "ink_like", "keep_inked_regions"]
