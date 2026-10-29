"""TESTE (6 creditos): dancinha pelo caminho A - Copiar movimento com o link do
TikTok, 5 s, Som original, personagem "luna". Mostra cada tela e so confirma
se o custo for 6. Depois espera o video, baixa e tira quadros.
Uso: py teste_dancinha_a.py [link]   (sem "--gerar" so mostra as telas)"""
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))
import luna_worker as lw  # noqa: E402

OUT = ROOT / "conteudos"
LINK = next((a for a in sys.argv[1:] if a.startswith("http")), "https://vt.tiktok.com/ZSgt4XUpm/")
GERAR = "--gerar" in sys.argv
lw.log = print
naia = lw.Naiaflow()
naia.open()
p = naia.page

UI_JS = """() => [...document.querySelectorAll('button,input')].filter(e => e.getBoundingClientRect().width > 0 && e.closest('[role=dialog], .fixed')).map(e => (e.innerText || e.getAttribute('placeholder') || '').trim().replace(/\\n/g,' | ').slice(0,70)).filter(Boolean)"""


def show(step):
    p.screenshot(path=str(OUT / f"dancinha_a_{step}.png"))
    print(f"\n== {step}:", p.evaluate(UI_JS)[-22:])


p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_selector("text=Modo imagem", timeout=60000)
p.wait_for_timeout(2500)
naia._dismiss_overlays()
print("creditos antes:", naia.credits())
p.get_by_role("button", name=re.compile(r"Modo imagem")).first.click()
p.wait_for_timeout(1200)
p.get_by_text(re.compile(r"Copiar movimento", re.I)).first.click()
p.wait_for_timeout(2500)
show("0_aberto")
# a janela lembra o passo da ultima vez: Recomecar volta para o inicio
for name in ("Recomeçar", "Limpar"):
    b = p.get_by_role("button", name=name, exact=True)
    if b.count() and b.last.is_visible():
        b.last.click()
        p.wait_for_timeout(1500)
        break
show("0a_recomecado")
LIB =next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--biblioteca=")), None)
if LIB is not None:
    # dancinha da Biblioteca de movimentos (aba Dancinhas), pela posicao na grade
    lib_btn = p.get_by_role("button", name=re.compile(r"Biblioteca de movimentos"))
    if lib_btn.count():
        lib_btn.first.click()
        p.wait_for_timeout(2500)
    p.get_by_text("Dancinhas", exact=True).first.click()
    p.wait_for_timeout(2500)
    tiles = p.locator("[role=dialog] video, [role=dialog] img, .fixed video, .fixed img").filter(has_not_text="x")
    tiles = [t for t in tiles.all() if t.is_visible() and t.bounding_box() and t.bounding_box()["height"] > 120]
    print("dancinhas visiveis:", len(tiles))
    tiles[LIB].click()
    p.wait_for_timeout(1500)
    show("0b_escolhida")
else:
    # link (TikTok, Instagram...): a janela lembra a escolha anterior
    p.get_by_role("button", name=re.compile(r"Importar por link")).first.click()
    p.wait_for_timeout(1500)
    p.locator("input[placeholder^='https']").first.fill(LINK)
p.get_by_role("button", name="Continuar", exact=True).last.click()
p.wait_for_timeout(20000)
p.get_by_role("button", name="5s", exact=True).first.click()
p.get_by_text("Som original", exact=True).first.click()
p.wait_for_timeout(800)
show("1_trecho")
p.get_by_role("button", name="Continuar", exact=True).last.click()
p.wait_for_timeout(5000)
show("2_personagem")
# personagem: o clone luna
# "Quem vai aparecer" ja vem com o clone escolhido no composer (luna); clicar
# nele abre a lista de clones, entao so confere
ok_luna = p.get_by_text("Quem vai aparecer").count() > 0 and p.get_by_text("luna", exact=True).count() > 0
print("luna em 'Quem vai aparecer':", ok_luna)
if not ok_luna:
    sys.exit("A personagem nao e a luna: nao gerei.")
final = p.get_by_role("button", name=re.compile(r"Gerar v[ií]deo", re.I))
labels = [final.nth(i).inner_text().replace("\n", " ") for i in range(final.count())]
print("botoes com custo:", labels)
if not GERAR:
    sys.exit("So olhei (sem --gerar).")
if len(labels) != 1 or not labels[0].strip().endswith("6"):
    sys.exit("Custo diferente de 6: nao cliquei.")
clicked = time.time()
final.first.click(timeout=15000, force=True)
print("cliquei:", labels[0])
before = set()
video = None
for i in range(90):  # ate 15 min
    time.sleep(10)
    try:
        p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
        p.wait_for_timeout(4000)
        thumbs = p.evaluate("""() => [...document.querySelectorAll('img')].filter(e => (e.currentSrc||e.src).includes('cloudflarestream')).map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.y), Math.round(r.x), e.currentSrc || e.src]; }).sort((a, b) => a[0] - b[0] || a[1] - b[1])""")
        if i == 0:
            before = {t[2] for t in thumbs}
            continue
        new = [t for t in thumbs if t[2] not in before]
        if new:
            p.locator(f"img[src='{new[0][2]}']").first.click()
            p.wait_for_timeout(6000)
            srcs = p.eval_on_selector_all("video, video source", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
            video = next((s for s in srcs if s.startswith("http") and s.endswith(".mp4")), None)
            print(f"+{time.time() - clicked:.0f}s video:", video, "| pagina:", p.url)
            if video:
                break
    except Exception as exc:
        print("erro:", str(exc)[:100])
if video:
    dest = OUT / "dancinha_a.mp4"
    dest.write_bytes(requests.get(video, timeout=180).content)
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(dest), "-vf", "fps=1.2,scale=270:-1,tile=6x1", "-frames:v", "1", str(OUT / "dancinha_a_quadros.jpg")], check=True)
    print("salvo:", dest)
p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_timeout(3000)
print("creditos depois:", naia.credits())
