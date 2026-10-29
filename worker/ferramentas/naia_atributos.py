"""Mostra o que esta marcado em Atributos do clone luna (corpo, seios, bunda).
Uso: py naia_atributos.py            -> so mostra
     py naia_atributos.py Seios=G Bunda=GG Corpo=Ampulheta  -> marca e salva"""
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\mauri\OneDrive\Área de Trabalho\luna persona\worker")
import luna_worker as lw  # noqa: E402

OUT = Path(__file__).parent / "naia_clonepage"
lw.log = print
changes = dict(a.split("=", 1) for a in sys.argv[1:])
naia = lw.Naiaflow()
naia.open()
p = naia.page
p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_selector("text=Modo imagem", timeout=60000)
p.wait_for_timeout(2000)
naia._dismiss_overlays()
p.get_by_text("Clones digitais", exact=True).first.click()
p.wait_for_timeout(3000)
card = p.locator("div.group[class*='aspect-']").filter(has=naia._clone_name()).first
card.hover()
card.locator("[aria-label='Editar']").first.click()
p.wait_for_timeout(3000)
p.get_by_role("button", name="Atributos", exact=True).first.click()
p.wait_for_timeout(2000)

STATE_JS = """(sections) => {
  const out = {};
  const all = [...document.querySelectorAll('*')];
  for (const name of sections) {
    const label = all.find(e => e.children.length === 0 && (e.textContent || '').trim().toUpperCase() === name.toUpperCase());
    if (!label) { out[name] = 'nao achei'; continue; }
    let box = label.parentElement;
    for (let i = 0; i < 4 && box && box.querySelectorAll('button').length < 2; i++) box = box.parentElement;
    out[name] = [...(box ? box.querySelectorAll('button') : [])].map(b => {
      const on = b.getAttribute('aria-pressed') === 'true' || b.getAttribute('data-state') === 'on' || b.getAttribute('aria-checked') === 'true' || /bg-accent|border-accent|ring-accent|text-accent/.test(b.className);
      return (on ? '[X] ' : '[ ] ') + (b.innerText || '').trim();
    }).join(' | ');
  }
  return out;
}"""
SECTIONS = ["CORPO", "Seios", "Silicone", "Bunda"]
dialog = p
dialog.locator("text=DETALHES EXTRAS").first.scroll_into_view_if_needed()
p.wait_for_timeout(800)
for k, v in p.evaluate(STATE_JS, SECTIONS).items():
    print(f"{k}: {v}")
p.screenshot(path=str(OUT / "6_corpo_antes.png"))

if changes:
    for section, value in changes.items():
        label = dialog.get_by_text(section.upper() if section == "Corpo" else section, exact=True).first
        box = label.locator("xpath=ancestor::*[.//button][1]")
        box.get_by_role("button", name=value, exact=True).first.click()
        p.wait_for_timeout(400)
    print("depois de marcar:")
    for k, v in p.evaluate(STATE_JS, SECTIONS).items():
        print(f"{k}: {v}")
    p.screenshot(path=str(OUT / "7_corpo_depois.png"))
    p.get_by_role("button", name="Salvar alterações").first.click()
    p.wait_for_timeout(3000)
    print("salvo. creditos:", naia.credits())
