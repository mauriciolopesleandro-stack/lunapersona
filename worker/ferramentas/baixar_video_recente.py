"""Abre o video mais recente da galeria (o primeiro cartao de video em "Hoje"),
le o link do mp4 e baixa para teste_corpo/dancinha_b.mp4. Tira 6 quadros."""
import re
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, r"C:\Users\mauri\OneDrive\Área de Trabalho\luna persona\worker")
import luna_worker as lw  # noqa: E402

OUT = Path(__file__).parent / "teste_corpo"
lw.log = print
naia = lw.Naiaflow()
naia.open()
p = naia.page
p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_selector("text=Modo imagem", timeout=60000)
p.wait_for_timeout(4000)
naia._dismiss_overlays()
print("creditos:", naia.credits())
thumbs = p.evaluate("""() => [...document.querySelectorAll('img')].filter(e => (e.currentSrc||e.src).includes('cloudflarestream')).map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.y), Math.round(r.x), e.currentSrc || e.src]; }).sort((a, b) => a[0] - b[0] || a[1] - b[1])""")
print("primeiros videos:", [(t[0], t[1], t[2][45:80]) for t in thumbs[:3]])
first = thumbs[0][2]
p.locator(f"img[src='{first}']").first.click()
p.wait_for_timeout(6000)
srcs = p.eval_on_selector_all("video, video source", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
print("pagina:", p.url, "| video:", srcs[:2])
p.screenshot(path=str(OUT / "video_pronto.png"))
uid = re.search(r"cloudflarestream\.com/([0-9a-f]{32})", first).group(1)
saved = None
for cand in [s for s in srcs if s.startswith("http") and ".m3u8" not in s] + [f"https://customer-tq8a4hlz6zlrg1ef.cloudflarestream.com/{uid}/downloads/default.mp4"]:
    r = requests.get(cand, timeout=120)
    print("tentando", cand[:100], r.status_code, r.headers.get("content-type"), len(r.content) // 1024, "KB")
    if r.ok and len(r.content) > 100_000 and "html" not in (r.headers.get("content-type") or ""):
        saved = OUT / "dancinha_b.mp4"
        saved.write_bytes(r.content)
        break
if not saved:
    # stream HLS: o ffmpeg baixa
    import imageio_ffmpeg
    hls = f"https://customer-tq8a4hlz6zlrg1ef.cloudflarestream.com/{uid}/manifest/video.m3u8"
    saved = OUT / "dancinha_b.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", hls, "-c", "copy", str(saved)], check=True)
print("salvo:", saved, saved.stat().st_size // 1024, "KB")
import imageio_ffmpeg  # noqa: E402
ff = imageio_ffmpeg.get_ffmpeg_exe()
subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(saved), "-vf", "fps=1.2,scale=270:-1,tile=6x1", "-frames:v", "1", str(OUT / "dancinha_b_quadros.jpg")], check=True)
print("quadros: dancinha_b_quadros.jpg")
