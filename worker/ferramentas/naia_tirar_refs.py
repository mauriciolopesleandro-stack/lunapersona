"""Tira as imagens extras (IMG1, IMG2...) do composer da Naiaflow, sem gerar."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))
import luna_worker as lw  # noqa: E402

lw.log = print
naia = lw.Naiaflow()
naia.open()
try:
    naia.page.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
    naia.page.wait_for_selector("text=Modo imagem", timeout=60000)
    naia.page.wait_for_timeout(2500)
    naia._dismiss_overlays()
    print("tiradas:", naia.clear_references())
    naia.page.screenshot(path=str(ROOT / "conteudos" / "naia_composer_limpo.png"))
finally:
    naia.close()
