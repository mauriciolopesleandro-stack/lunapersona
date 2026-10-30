"""Conferencia SEM gastar credito: faz o Copiar movimento do robo ate antes do
"Gerar video". Uso: py naia_dancinha_seco.py <link> [roupa]"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))
import luna_worker as lw  # noqa: E402

lw.log = print
naia = lw.Naiaflow()
naia.open()
try:
    print("creditos antes:", naia.credits())
    label = naia.copy_motion(sys.argv[1], None, 5, sys.argv[2] if len(sys.argv) > 2 else None, dry=True)
    print("pronto para gerar:", label)
    naia.page.screenshot(path=str(ROOT / "conteudos" / "dancinha_seco.png"))
    print("creditos depois:", naia.credits())
finally:
    naia.close()
