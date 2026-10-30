"""So olha (nao gera): abre o menu de proporcao do composer da Naiaflow e
lista as opcoes. Uso: py naia_proporcao.py [Quadrado|Vertical] (escolhe e confere)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))
import luna_worker as lw  # noqa: E402

OUT = ROOT / "conteudos"
lw.log = print
naia = lw.Naiaflow()
naia.open()
p = naia.page
try:
    p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
    p.wait_for_selector("text=Modo imagem", timeout=60000)
    p.wait_for_timeout(2000)
    naia._dismiss_overlays()
    print("creditos:", naia.credits())
    btn = p.get_by_role("button", name=re.compile(r"^\s*(Vertical|Quadrado|Horizontal)\s*$"))
    print("botao de proporcao:", [btn.nth(i).inner_text() for i in range(btn.count())])
    btn.first.click()
    p.wait_for_timeout(1500)
    p.screenshot(path=str(OUT / "naia_proporcao_menu.png"))
    opts = p.evaluate("""() => [...document.querySelectorAll('[role=menu] *, [role=listbox] *, [data-radix-popper-content-wrapper] *')].filter(e => e.childElementCount === 0 && e.getBoundingClientRect().width > 0).map(e => e.textContent.trim()).filter(Boolean)""")
    print("opcoes do menu:", opts[:30])
    if len(sys.argv) > 1:
        p.get_by_text(sys.argv[1], exact=True).last.click()
        p.wait_for_timeout(1200)
        naia._close_popovers()
        print("agora:", [btn.nth(i).inner_text() for i in range(btn.count())])
    else:
        p.keyboard.press("Escape")
finally:
    naia.close()
