"""Medidor de microtextura da pele (SkinRealismAnalyzer) com Pillow + NumPy.

Como mede (sem modelo, sem GPU):
1. Areas de pele pelos 5 pontos do rosto: as duas bochechas (entre o olho e o
   canto da boca, um pouco para fora) e a testa (acima do meio dos olhos).
   Sem os pontos, fracoes da caixa do rosto.
2. O rosto e reduzido para a distancia entre os olhos ficar em 64 px (nunca
   ampliado: ampliar nao cria textura). Rosto com olhos a menos de 28 px de
   distancia = UNKNOWN (pixels de menos para falar de pele).
3. Microtextura = desvio do residuo (cinza - desfoque gaussiano 1.5) em cada
   area; vale a mediana das areas. Areas com residuo muito alto (cabelo,
   borda, sobrancelha) sao descartadas.
4. Excesso de suavizacao = fracao de blocos 6x6 quase sem detalhe.

O que isso NAO mede: aparencia CGI, porcelana, simetria artificial (UNKNOWN).
"""
from __future__ import annotations

import io
import statistics
from typing import Any

from app.clients.comfyui_client import ComfyUIClient, ComfyUIError
from app.core.validation.analysis import DetectedFace
from app.core.validation.skin import SkinRealismResult, grade, unknown
from app.providers.base import ProviderImage

try:  # o backend roda sem eles; sem eles a pele fica UNKNOWN
    import numpy as np
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    Image = ImageFilter = None  # type: ignore[assignment]

TARGET_IOD = 64.0
MIN_IOD = 28.0
MAX_PATCH_STD = 9.0
FLAT_BLOCK_STD = 0.6


def _regions(face: DetectedFace) -> list[tuple[float, float, float]]:
    """(centro x, centro y, lado) de cada area de pele, em pixels da imagem."""
    if len(face.kps) >= 5:
        (lx, ly), (rx, ry), _nose, (mlx, mly), (mrx, mry) = face.kps[:5]
        iod = max(1.0, ((rx - lx) ** 2 + (ry - ly) ** 2) ** 0.5)
        side = 0.32 * iod
        out = 0.12 * iod
        return [
            ((lx + mlx) / 2 - out, (ly + mly) / 2 + 0.05 * iod, side),
            ((rx + mrx) / 2 + out, (ry + mry) / 2 + 0.05 * iod, side),
            ((lx + rx) / 2, (ly + ry) / 2 - 0.55 * iod, side),
        ]
    x1, y1, x2, y2 = face.bbox
    w, h = x2 - x1, y2 - y1
    side = 0.18 * w
    return [(x1 + 0.27 * w, y1 + 0.62 * h, side), (x1 + 0.73 * w, y1 + 0.62 * h, side), (x1 + 0.5 * w, y1 + 0.2 * h, side)]


def _iod(face: DetectedFace) -> float:
    if len(face.kps) >= 2:
        (lx, ly), (rx, ry) = face.kps[0], face.kps[1]
        return ((rx - lx) ** 2 + (ry - ly) ** 2) ** 0.5
    return 0.38 * (face.bbox[2] - face.bbox[0])  # proporcao tipica olhos/largura do rosto


def measure_skin(img: Any, face: DetectedFace | None, config: dict[str, Any]) -> SkinRealismResult:
    if np is None:
        return unknown("Pillow/NumPy ausentes no backend: pele nao medida.")
    if face is None:
        return unknown("Sem rosto da persona: pele nao medida.")
    iod = _iod(face)
    min_iod = float(config.get("min_iod_px", MIN_IOD))
    if iod < min_iod:
        return unknown(f"Rosto pequeno demais (olhos a {iod:.0f} px; minimo {min_iod:.0f}).", {"iod_px": round(iod, 1)})
    scale = min(1.0, TARGET_IOD / iod)
    gray = img.convert("L")
    if scale < 1.0:
        gray = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.LANCZOS)
    g = np.asarray(gray, dtype=np.float32)
    residual = g - np.asarray(gray.filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32)
    stds, flats, used = [], [], 0
    for cx, cy, side in _regions(face):
        cx, cy, half = cx * scale, cy * scale, max(4.0, side * scale / 2)
        x1, y1, x2, y2 = int(cx - half), int(cy - half), int(cx + half), int(cy + half)
        if x1 < 0 or y1 < 0 or x2 > residual.shape[1] or y2 > residual.shape[0]:
            continue
        patch = residual[y1:y2, x1:x2]
        std = float(patch.std())
        if std > float(config.get("max_patch_std", MAX_PATCH_STD)):
            continue  # cabelo, borda, sobrancelha
        used += 1
        stds.append(std)
        blocks = [patch[i:i + 6, j:j + 6].std() for i in range(0, patch.shape[0] - 5, 6) for j in range(0, patch.shape[1] - 5, 6)]
        if blocks:
            flats.append(sum(1 for b in blocks if b < FLAT_BLOCK_STD) / len(blocks))
    raw = {"iod_px": round(iod, 1), "patches_used": used, "patch_std": [round(s, 3) for s in stds],
           "regions": "kps" if len(face.kps) >= 5 else "bbox"}
    if not stds:
        return unknown("Nenhuma area de pele limpa (cabelo/borda em todas).", raw)
    return grade(statistics.median(stds), statistics.mean(flats) if flats else 0.0, config, raw)


def _split_locator(locator: str) -> tuple[str, str, str]:
    name, folder = (locator[:-len(" [output]")], "output") if locator.endswith(" [output]") else (locator, "input")
    sub, _, filename = name.rpartition("/")
    return filename, sub, folder


class PillowSkinTextureAnalyzer:
    def __init__(self, client: ComfyUIClient) -> None:
        self.client = client

    async def analyze(self, image: ProviderImage, face: DetectedFace | None, config: dict[str, Any]) -> SkinRealismResult:
        if np is None:
            return unknown("Pillow/NumPy ausentes no backend: pele nao medida.")
        filename, sub, folder = _split_locator(image.locator)
        try:
            content = await self.client.download_file(filename, sub, folder)
        except ComfyUIError as exc:
            return unknown(f"Nao consegui baixar a imagem para medir a pele: {exc}")
        with Image.open(io.BytesIO(content)) as img:
            return measure_skin(img.convert("RGB"), face, config)
