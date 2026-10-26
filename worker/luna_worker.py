"""Luna Studio - automacao local da Naiaflow (parte B do fluxo).

Roda no PC do administrador. Pega na fila do estudio os itens de foto dos
planos aprovados (Assistente -> Planejamento), gera cada foto na Naiaflow com o
clone "luna" (Playwright controlando o Chrome, num perfil separado so para a
Naiaflow), salva na pasta do estudio (conteudos/AAAA-MM, +18 em subpasta),
devolve ao estudio - que cria a publicacao de cada destino - e sobe uma copia
JPEG no formato de cada destino (Feed 4:5, Stories/TikTok 9:16). O estudio
manda cada publicacao para a analise no bot do Telegram.

O PC nao guarda a chave do banco: fala so com /api/studio?r=worker, com a
chave do arquivo luna-worker.env (gerado em Configuracoes -> Automacao do PC).

Uso:
  python luna_worker.py                 fica rodando e olha a fila a cada minuto
  python luna_worker.py --uma-vez       processa o que houver na fila e sai
  python luna_worker.py --imagem-teste foto.png --uma-vez
                                        nao abre a Naiaflow: usa a foto dada (teste)
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

HERE = Path(__file__).resolve().parent
# a pasta do estudio (luna persona); LUNA_STUDIO_ROOT e LUNA_WORKER_CONFIG so para testes
STUDIO_ROOT = Path(os.environ.get("LUNA_STUDIO_ROOT", HERE.parent))
CONFIG_FILE = Path(os.environ.get("LUNA_WORKER_CONFIG", HERE / "luna-worker.env"))
PROFILE_DIR = HERE / ".perfil-naiaflow"
LOG_FILE = HERE / "luna-worker.log"
NAIAFLOW = "https://app.naiaflow.com/generations"
CLONE = "luna"
MIN_CREDITS = 2  # abaixo disso para e avisa (nao gasta o resto)
GENERATION_TIMEOUT_S = 10 * 60
QUEUE_POLL_S = 60
JPEG_MAX_BYTES = 4 * 1024 * 1024  # limite do TikTok para foto


def log(msg: str) -> None:
    line = f"[{datetime.now():%d/%m %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config() -> tuple[str, str]:
    if not CONFIG_FILE.exists():
        sys.exit(
            "Falta o arquivo luna-worker.env nesta pasta.\n"
            "No estudio: Configuracoes -> Automacao do PC -> Conectar este computador, e mova o arquivo baixado para cá."
        )
    values: dict[str, str] = {}
    for raw in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in raw and not raw.lstrip().startswith("#"):
            k, v = raw.split("=", 1)
            values[k.strip()] = v.strip()
    url, token = values.get("STUDIO_URL", ""), values.get("WORKER_TOKEN", "")
    if not url or not token:
        sys.exit("O luna-worker.env esta incompleto. Gere de novo no estudio.")
    return url.rstrip("/"), token


class Studio:
    def __init__(self, url: str, token: str) -> None:
        self.url = f"{url}/api/studio?r=worker"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(self, action: str, **payload) -> dict:
        r = requests.post(self.url, json={"action": action, **payload}, headers=self.headers, timeout=60)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not r.ok:
            raise RuntimeError(body.get("detail") or f"Estudio respondeu HTTP {r.status_code}")
        return body


# --- imagem ----------------------------------------------------------------------------------------

def to_jpeg(img: Image.Image, quality: int = 92) -> bytes:
    for q in (quality, 88, 84, 80, 75):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= JPEG_MAX_BYTES:
            return buf.getvalue()
    return buf.getvalue()


def fit_ratio(img: Image.Image, ratio_w: int, ratio_h: int, size: tuple[int, int]) -> Image.Image:
    """Corta no centro (um pouco acima, onde fica o rosto) para a proporcao e redimensiona."""
    w, h = img.size
    target = ratio_w / ratio_h
    if w / h > target:  # larga demais: corta dos lados
        nw = int(h * target)
        box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
    else:  # alta demais: corta em cima e embaixo, puxando para cima
        nh = int(w / target)
        top = int((h - nh) * 0.35)
        box = (0, top, w, top + nh)
    return img.crop(box).resize(size, Image.LANCZOS)


def copy_for(img: Image.Image, network: str, fmt: str) -> bytes:
    if network == "instagram" and fmt == "Feed":
        return to_jpeg(fit_ratio(img, 4, 5, (1080, 1350)))
    # Stories, TikTok e Telegram: vertical 9:16 ate 1080x1920
    return to_jpeg(fit_ratio(img, 9, 16, (1080, 1920)))


def put_copy(url: str, data: bytes) -> None:
    r = requests.put(
        url,
        headers={"x-upsert": "true"},
        files={"": ("0.jpg", data, "image/jpeg")},
        data={"cacheControl": "3600"},
        timeout=120,
    )
    if not r.ok:
        raise RuntimeError(f"Nao consegui subir a copia (HTTP {r.status_code}).")


# --- Naiaflow (Playwright) --------------------------------------------------------------------------

class Naiaflow:
    def __init__(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=False, viewport={"width": 1280, "height": 900}
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def close(self) -> None:
        try:
            self.ctx.close()
        finally:
            self._pw.stop()

    def _prompt_box(self):
        return self.page.locator("textarea").filter(has_not=self.page.locator("[readonly]")).first

    def open(self) -> None:
        self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
        deadline = time.time() + 15 * 60
        warned = False
        while time.time() < deadline:
            # durante o login a pagina troca de endereco (e a aba pode ser outra): so tenta de novo
            try:
                if self.page.is_closed():
                    self.page = self.ctx.pages[-1] if self.ctx.pages else self.ctx.new_page()
                if "/generations" in self.page.url and self.page.locator("text=Modo imagem").count():
                    return
            except Exception:
                pass
            if not warned:
                # O Google recusa login com conta Google num Chrome controlado
                # por automacao ("Esse navegador ou app pode nao ser seguro").
                log(
                    "A Naiaflow nao esta logada. Feche esta janela e abra 'Entrar na Naiaflow.bat' "
                    "(Chrome normal, mesmo perfil): entre com a conta Google, feche o Chrome e "
                    "abra 'Iniciar automacao.bat' de novo."
                )
                warned = True
            time.sleep(3)
        raise RuntimeError("Nao consegui abrir a Naiaflow (login nao concluido em 15 minutos).")

    def credits(self) -> int | None:
        try:
            text = self.page.locator("body").inner_text(timeout=5000)[:40]
            first = text.strip().split("\n")[0]
            return int(first) if first.isdigit() else None
        except Exception:
            return None

    def _job_images(self) -> set[str]:
        srcs = self.page.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)")
        return {s for s in srcs if "cdn.naiaflow.com/job-" in s}

    def _ensure_clone(self) -> None:
        composer = self.page.locator("text=Adicionar")
        chip = self.page.get_by_role("button", name=CLONE, exact=True)
        if chip.count():
            return
        self.page.get_by_text("Adicionar clone").first.click()
        self.page.get_by_text(CLONE, exact=True).first.click(timeout=15000)
        self.page.wait_for_timeout(800)
        if not self.page.get_by_role("button", name=CLONE, exact=True).count() and composer.count() == 0:
            raise RuntimeError(f'Nao consegui selecionar o clone "{CLONE}" na Naiaflow.')

    def generate(self, prompt: str) -> str:
        self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
        self.page.wait_for_selector("text=Modo imagem", timeout=60000)
        self._ensure_clone()
        before = self._job_images()
        box = self.page.get_by_placeholder("Digite seu prompt", exact=False).first
        if not box.count():
            box = self.page.locator("textarea").first
        box.click()
        box.fill(prompt)
        self.page.get_by_role("button", name="Gerar").last.click()
        log("Gerando na Naiaflow…")
        deadline = time.time() + GENERATION_TIMEOUT_S
        while time.time() < deadline:
            time.sleep(5)
            try:
                head = self.page.locator("body").inner_text()[:300]
                new = self._job_images() - before
            except Exception:  # a pagina recarregou: confere de novo no proximo ciclo
                continue
            if new and "Gerando" not in head and "Enviando" not in head:
                src = sorted(new)[0]
                return src.rsplit("/", 1)[0] + "/public"
        raise RuntimeError("A Naiaflow nao terminou a foto em 10 minutos.")


# --- um item da fila -----------------------------------------------------------------------------------

def process(studio: Studio, item: dict, naia: Naiaflow | None, test_image: Path | None) -> None:
    log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): {item['prompt'][:90]}")
    if test_image:
        raw = test_image.read_bytes()
    else:
        url = naia.generate(item["prompt"])
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        raw = r.content
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    folder = STUDIO_ROOT / item["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / item["file"]
    path.write_bytes(to_jpeg(img, 95))
    local_path = f"{item['folder']}/{item['file']}"
    log(f"Salva em {local_path}")
    done = studio.call("done", id=item["id"], local_path=local_path, size_bytes=path.stat().st_size)
    for t in done["targets"]:
        data = copy_for(img, t["network"], t["format"])
        up = studio.call("upload-url", id=item["id"], post_id=t["post_id"], version=t["version"], size_bytes=len(data))
        put_copy(up["url"], data)
    sent = studio.call("uploaded", id=item["id"])
    log(f"Mandada para você aprovar no Telegram ({sent['sent']} publicação(ões)).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Luna Studio - automacao local da Naiaflow")
    ap.add_argument("--uma-vez", action="store_true", help="processa a fila e sai")
    ap.add_argument("--imagem-teste", type=Path, help="usa esta imagem em vez de gerar na Naiaflow (teste)")
    args = ap.parse_args()

    url, token = load_config()
    studio = Studio(url, token)
    queue = studio.call("ping")["queue"]
    log(f"Conectado ao estúdio ({url}). Fotos na fila: {queue}.")
    naia: Naiaflow | None = None
    try:
        if not args.imagem_teste:
            naia = Naiaflow()
            naia.open()
            log("Naiaflow pronta.")
        while True:
            if naia:
                credits = naia.credits()
                if credits is not None and credits < MIN_CREDITS:
                    log(f"Só {credits} crédito(s) na Naiaflow: parei para não zerar. Compre créditos e reinicie.")
                    if args.uma_vez:
                        break
                    time.sleep(10 * QUEUE_POLL_S)
                    continue
            item = studio.call("next")["item"]
            if not item:
                if args.uma_vez:
                    log("Fila vazia: terminei.")
                    break
                time.sleep(QUEUE_POLL_S)
                continue
            try:
                process(studio, item, naia, args.imagem_teste)
            except Exception as e:  # o item volta para a fila (ou falha de vez, com aviso no Telegram)
                log(f"Erro no item {item['id'][:8]}: {e}")
                try:
                    studio.call("fail", id=item["id"], error=str(e)[:800])
                except Exception as e2:
                    log(f"Nao consegui avisar o estudio: {e2}")
    except KeyboardInterrupt:
        log("Parado pelo usuário.")
    finally:
        if naia:
            naia.close()


if __name__ == "__main__":
    main()
