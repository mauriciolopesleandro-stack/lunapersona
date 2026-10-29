"""Copiar movimento com o link do TikTok do administrador, passo a passo,
PARANDO antes de qualquer botao que gere (Criar/Gerar/Continuar com custo).
Mostra cada tela (botoes, campos) para eu montar o fluxo do robo."""
import re
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\mauri\OneDrive\Área de Trabalho\luna persona\worker")
import luna_worker as lw  # noqa: E402

OUT = Path(__file__).parent / "teste_corpo"
LINK = "https://vt.tiktok.com/ZSgt4XUpm/"
lw.log = print
naia = lw.Naiaflow()
naia.open()
p = naia.page

UI_JS = """() => [...document.querySelectorAll('button,input,textarea,[role=dialog] *')].filter(e => e.getBoundingClientRect().width > 0 && (e.tagName==='BUTTON'||e.tagName==='INPUT'||e.tagName==='TEXTAREA')).map(e => [e.tagName, (e.innerText||'').trim().replace(/\\n/g,' | ').slice(0,70), e.getAttribute('placeholder')]).filter(x => x[1] || x[2])"""


def show(step):
    p.screenshot(path=str(OUT / f"copiar_{step}.png"))
    items = [x for x in p.evaluate(UI_JS) if x[1] not in ("Limpar", "luna", "Adicionar", "Vertical", "Melhorar", "Celular", "Maquiagem", "Luz")]
    print(f"\n== {step}")
    for x in items[-18:]:
        print("   ", x)


p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_selector("text=Modo imagem", timeout=60000)
p.wait_for_timeout(2500)
naia._dismiss_overlays()
p.get_by_role("button", name=re.compile(r"Modo imagem")).first.click()
p.wait_for_timeout(1200)
p.get_by_text(re.compile(r"Copiar movimento", re.I)).first.click()
p.wait_for_timeout(2500)
p.get_by_text("Importar por link", exact=True).first.click()
p.wait_for_timeout(2000)
show("2_link")
field = p.locator("input[type=url], input[placeholder*='link' i], input[placeholder*='http' i], input[placeholder*='cole' i]").first
field.fill(LINK)
p.wait_for_timeout(800)
show("3_link_colado")
# botao de importar (gratis): so clica se nao tiver custo
imp = p.get_by_role("button", name=re.compile(r"^\s*(Importar|Buscar|Continuar|Baixar)\b", re.I))
labels = [imp.nth(i).inner_text().replace("\n", " ") for i in range(imp.count())]
print("botoes de importar:", labels)
if imp.count() and not re.search(r"\d", labels[-1]):
    imp.last.click()
    print("cliquei em", labels[-1])
    p.wait_for_timeout(20000)
    show("4_importado")
print("creditos:", naia.credits())
