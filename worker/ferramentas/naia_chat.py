"""Conversa com a Naia (assistente da Naiaflow) pelo perfil do robo.
Uso: py naia_chat.py "pergunta 1" "pergunta 2" ...
Abre uma conversa nova, manda cada pergunta pelo botao Enviar e grava as
respostas em naia_chat/respostas.txt. Nunca clica em Criar/Gerar; se os
creditos baixarem, para na hora."""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\mauri\OneDrive\Área de Trabalho\luna persona\worker")
import luna_worker as lw  # noqa: E402

OUT = Path(__file__).parent / "naia_chat"
OUT.mkdir(exist_ok=True)
lw.log = lambda m: None

naia = lw.Naiaflow()
naia.open()
p = naia.page
p.goto(lw.NAIAFLOW, wait_until="domcontentloaded")
p.wait_for_selector("text=Modo imagem", timeout=60000)
p.wait_for_timeout(2000)
naia._dismiss_overlays()
start_credits = naia.credits()
print("creditos no inicio:", start_credits)
p.get_by_text("A Naia te ajuda").first.click()
p.wait_for_timeout(3000)
try:
    # conversa nova, sem mexer na antiga (se a aberta ja estiver vazia, o botao some)
    p.get_by_text("Novo", exact=True).first.click(timeout=4000)
    p.wait_for_timeout(1500)
except Exception:
    pass
# campo da Naia: a textarea sem placeholder (a outra e o composer de prompt)
box = p.locator("textarea:not([placeholder])").last
send = p.get_by_role("button", name="Enviar", exact=True).first
# a resposta e o texto novo da pagina depois de mandar a pergunta
panel = p.locator("body")

with (OUT / "respostas.txt").open("w", encoding="utf-8") as out:
    for n, question in enumerate(sys.argv[1:], 1):
        before = panel.inner_text()
        box.click()
        box.fill(question)
        send.click()
        last, still = "", 0
        for _ in range(120):
            time.sleep(2)
            now = panel.inner_text()
            if now == last and len(now) > len(before) + len(question) + 40:
                still += 1
                if still >= 3:
                    break
            else:
                still = 0
            last = now
        p.screenshot(path=str(OUT / f"{n}_resposta.png"))
        answer = last[len(before):] if last.startswith(before) else last
        answer = answer.replace(question, "").strip()
        out.write(f"\n===== PERGUNTA {n}: {question}\n{answer}\n")
        out.flush()
        print(f"pergunta {n}: {len(answer)} caracteres de resposta")
        credits = naia.credits()
        if start_credits is not None and credits is not None and credits < start_credits:
            print(f"PAREI: os creditos baixaram de {start_credits} para {credits}")
            break
print("creditos no fim:", naia.credits())
