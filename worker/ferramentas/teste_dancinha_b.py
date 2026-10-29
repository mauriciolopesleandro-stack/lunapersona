"""TESTE (5 creditos): dancinha pelo caminho B - foto 1 do teste do corpo como
primeiro quadro, Cena unica, 5 s, Sem som, prompt da danca. Baixa o video."""
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, r"C:\Users\mauri\OneDrive\Área de Trabalho\luna persona\worker")
import luna_worker as lw  # noqa: E402

OUT = Path(__file__).parent / "teste_corpo"
lw.log = print
lw.ACTION_CATEGORY["Usar como primeiro quadro"] = "Vídeo"
PROMPT = ("Luna dançando arrocha na borda da piscina, rebolando devagar e mexendo o quadril no ritmo, "
          "virando de lado e de costas para mostrar o biquíni, sorrindo e olhando para a câmera, "
          "câmera de celular parada na mão de uma amiga, movimento natural e suave, corpo inteiro no quadro")
naia = lw.Naiaflow()
naia.open()
p = naia.page
print("creditos antes:", naia.credits())
naia._open_job("4d324c97-c234-4812-ae1f-1a52a2da9542#1")
naia._choose_action("Usar como primeiro quadro")
p.wait_for_timeout(2500)


def pick(button: str, option: str) -> None:
    p.get_by_role("button", name=re.compile(rf"^\s*{button}\s*$")).first.click()
    p.wait_for_timeout(1000)
    p.get_by_text(option, exact=True).first.click()
    p.wait_for_timeout(800)
    naia._close_popovers()


pick("Multicenas", "Cena única")
labels = [b.inner_text().strip().replace("\n", " ") for b in p.locator("button").all() if b.is_visible() and b.inner_text().strip()]
print("opcoes marcadas:", [x for x in labels if x in ("Cena única", "Multicenas", "5s", "Sem som", "Com som", "Vertical")])
box = p.get_by_placeholder("Digite seu prompt", exact=False).first
box.click()
box.fill(PROMPT)
create = naia._create_button()
txt = create.inner_text().replace("\n", " ")
print("botao:", txt)
if not ("Cena única" in labels and "5s" in labels and txt.strip().endswith("5")):
    sys.exit("Opcoes diferentes do esperado: nao cliquei em Criar.")
before_videos = set(p.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)"))
create.click()
clicked = time.time()
print("cliquei em Criar 5")

video_url = None
for i in range(72):  # ate 12 min
    time.sleep(10)
    try:
        p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
        p.wait_for_timeout(4000)
        thumbs = [s for s in p.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)") if "cloudflarestream.com" in s and s not in before_videos]
        if thumbs:
            print(f"+{time.time() - clicked:.0f}s video novo:", thumbs[0][:110])
            p.locator(f"img[src='{thumbs[0]}']").first.click()
            p.wait_for_timeout(5000)
            srcs = p.eval_on_selector_all("video, video source", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
            print("video na pagina:", srcs[:3], "| pagina:", p.url)
            video_url = next((s for s in srcs if s.startswith("http")), None)
            break
    except Exception as exc:
        print("erro lendo a galeria:", str(exc)[:100])
if video_url:
    m = re.search(r"cloudflarestream\.com/([0-9a-f]{32})", video_url) or re.search(r"cloudflarestream\.com/([0-9a-f]{32})", thumbs[0])
    for cand in [video_url] + ([f"https://customer-tq8a4hlz6zlrg1ef.cloudflarestream.com/{m.group(1)}/downloads/default.mp4"] if m else []):
        r = requests.get(cand, timeout=120)
        print("baixando", cand[:100], r.status_code, r.headers.get("content-type"), len(r.content) // 1024, "KB")
        if r.ok and "mp4" in (r.headers.get("content-type") or "") and len(r.content) > 100_000:
            (OUT / "dancinha_b.mp4").write_bytes(r.content)
            print("salvo dancinha_b.mp4")
            break
p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_timeout(3000)
print("creditos depois:", naia.credits())
