"""Lista as fotos geradas hoje (depois de 13:30) na galeria da Naiaflow e
baixa cada uma para naia_hoje/ - so le, nao gera nada. Tambem mostra se ha
algum "Gerando" travado no topo da galeria."""
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, r"C:\Users\mauri\OneDrive\Área de Trabalho\luna persona\worker")
import luna_worker as lw  # noqa: E402

OUT = Path(__file__).parent / "naia_hoje"
OUT.mkdir(exist_ok=True)
lw.log = print
since = datetime(2026, 10, 29, 13, 25).timestamp() * 1000

naia = lw.Naiaflow()
naia.open()
p = naia.page
p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_selector("text=Modo imagem", timeout=60000)
p.wait_for_timeout(5000)
print("creditos:", naia.credits())
body = p.locator("body").inner_text()
print("topo da pagina (300):", body[:300].replace("\n", " | "))
print("'Gerando' na pagina:", "Gerando" in body, "| nos 300 primeiros:", "Gerando" in body[:300])
p.screenshot(path=str(OUT / "galeria.png"))
srcs = sorted({s for s in p.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)") if "cdn.naiaflow.com/job-" in s})
recent = [s for s in srcs if lw._job_time_ms(s) > since]
print("imagens de job na tela:", len(srcs), "| de hoje depois de 13:25:", len(recent))
jobs = {}
for s in recent:
    m = re.search(r"job-([0-9a-f-]{36})-(\d+)-(\d+)", s)
    jobs.setdefault(m.group(1), []).append(s)
for j, lst in jobs.items():
    p.goto(lw.NAIAFLOW_JOB + j, wait_until="domcontentloaded")
    p.wait_for_timeout(5000)
    inner = sorted({s.rsplit("/", 1)[0] for s in p.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)") if "cdn.naiaflow.com/job-" in s and lw._job_time_ms(s) > since})
    print(f"\njob {j}: {len(lst)} na galeria, {len(inner)} na pagina do job")
    for b in inner:
        t = datetime.fromtimestamp(lw._job_time_ms(b) / 1000).strftime("%H:%M:%S")
        name = re.search(r"job-([0-9a-f]{8})[0-9a-f-]*-(\d+)", b)
        fn = OUT / f"{t.replace(':', '')}_{name.group(1)}_{name.group(2)}.jpg"
        r = requests.get(b + "/public", timeout=60)
        fn.write_bytes(r.content)
        print("   ", t, b[-70:], "->", fn.name, len(r.content) // 1024, "KB")
    p.screenshot(path=str(OUT / f"job_{j[:8]}.png"))
