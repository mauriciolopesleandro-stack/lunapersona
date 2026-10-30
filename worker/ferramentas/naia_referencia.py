"""So olha (nao gera): abre o menu "Adicionar" do composer da Naiaflow e mostra
como anexar uma imagem de referencia. Uso: py naia_referencia.py [foto.jpg]
(com foto: anexa e confere a miniatura no composer, sem clicar em Criar)."""
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
    naia._ensure_clone()
    print("creditos:", naia.credits())
    p.get_by_role("button", name=re.compile(r"^\s*Adicionar( clone)?\s*$", re.I)).first.click()
    p.wait_for_timeout(1500)
    p.screenshot(path=str(OUT / "naia_ref_menu.png"))
    opts = p.evaluate("""() => [...document.querySelectorAll('[role=menu] *, [data-radix-popper-content-wrapper] *')].filter(e => e.childElementCount === 0 && e.getBoundingClientRect().width > 0).map(e => e.textContent.trim()).filter(Boolean)""")
    print("menu Adicionar:", opts[:20])
    if len(sys.argv) > 1:
        p.get_by_text(re.compile(r"^\s*Enviar imagem\s*$", re.I)).first.click()
        p.wait_for_timeout(3000)
        p.screenshot(path=str(OUT / "naia_ref_galeria.png"))
        print("tela do Enviar imagem:", p.evaluate("""() => [...document.querySelectorAll('button, [role=dialog] span')].filter(e => e.getBoundingClientRect().width > 0).map(e => (e.innerText || '').trim().split(String.fromCharCode(10)).join(' | ').slice(0, 50)).filter(Boolean).slice(-25)"""))
        with p.expect_file_chooser(timeout=15000) as chooser:
            p.get_by_role("button", name=re.compile(r"Enviar do dispositivo", re.I)).last.click()
        chooser.value.set_files(sys.argv[1])
        p.wait_for_timeout(8000)
        naia._close_popovers()
        p.screenshot(path=str(OUT / "naia_ref_anexada.png"))
        thumbs = p.evaluate("""() => [...document.querySelectorAll('img')].filter(i => i.getBoundingClientRect().bottom > window.innerHeight - 260 && i.getBoundingClientRect().width < 120).map(i => (i.currentSrc || i.src).slice(0, 80))""")
        print("miniaturas perto do composer:", thumbs)
    else:
        p.keyboard.press("Escape")
finally:
    naia.close()
