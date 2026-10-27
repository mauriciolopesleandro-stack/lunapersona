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
import json
import os
import re
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
# a foto pronta abre direto por este link (painel de Acoes: Editar pelo texto)
NAIAFLOW_JOB = "https://app.naiaflow.com/generations/preview/job/"
# qual job da Naiaflow gerou cada foto do estudio (para a correcao editar a foto certa)
JOBS_FILE = HERE / "naia-jobs.json"
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


def _job_time_ms(src: str) -> float:
    m = re.search(r"job-[0-9a-f-]{36}-\d+-(\d{12,14})", src)
    return float(m.group(1)) if m else 0.0


def remember_job(local_path: str, url: str) -> None:
    m = re.search(r"job-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", url)
    if not m:
        return
    jobs = known_jobs()
    jobs[local_path] = m.group(1)
    JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf-8")


def known_jobs() -> dict[str, str]:
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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
            # So avisa depois de um tempo: logo de cara a pagina ainda esta
            # carregando e o aviso saia mesmo com a sessao salva.
            if not warned and time.time() > deadline - 15 * 60 + 20:
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
        # Tela de 10/2026: "Créditos  22" no topo da barra lateral.
        try:
            text = self.page.locator("body").inner_text(timeout=5000)
            m = re.search(r"Cr[ée]ditos\s*\n?\s*(\d+)", text)
            if m:
                return int(m.group(1))
            first = text.strip().split("\n")[0]
            return int(first) if first.isdigit() else None
        except Exception:
            return None

    def _job_images(self) -> set[str]:
        srcs = self.page.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)")
        return {s for s in srcs if "cdn.naiaflow.com/job-" in s}

    def _dismiss_overlays(self, panels: bool = True) -> None:
        """Avisos fixos da Naiaflow (cookies, boas-vindas, novidades) ficam por
        cima do composer e bloqueiam os cliques ("subtree intercepts pointer
        events"). Prefere recusar/fechar; se nao achar botao, esconde o aviso.
        Tambem fecha o seletor de acoes ("Filtrar acoes...") e a lista de
        clones, que as vezes ficam abertos por cima do composer (panels=False
        na tela da foto pronta, onde o painel de acoes e o que o robo usa)."""
        for placeholder in ("Filtrar ações", "Pesquisar") if panels else ():
            try:
                panel = self.page.get_by_placeholder(placeholder)
                for _ in range(3):
                    if not (panel.count() and panel.first.is_visible()):
                        break
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(500)
            except Exception:
                pass
        try:
            banners = self.page.locator("div.fixed.bottom-6")
            for i in range(banners.count()):
                banner = banners.nth(i)
                if not banner.is_visible():
                    continue
                button = banner.get_by_role("button").filter(
                    has_text=re.compile(r"recus|rejeit|essenc|necess|fechar|entendi|agora n|depois|ok|aceit", re.I)
                )
                if button.count():
                    # recusar/so essenciais antes de aceitar
                    order = ["recus", "rejeit", "essenc", "necess", "fechar", "entendi", "agora n", "depois", "ok", "aceit"]
                    texts = [button.nth(j).inner_text().lower() for j in range(button.count())]
                    best = min(range(len(texts)), key=lambda j: next((k for k, w in enumerate(order) if w in texts[j]), len(order)))
                    button.nth(best).click(timeout=3000)
                    log(f'Fechei um aviso da Naiaflow ("{texts[best].strip()[:30]}").')
                else:
                    banner.evaluate("el => el.style.display = 'none'")
                    log("Escondi um aviso da Naiaflow que estava por cima dos botoes.")
        except Exception as exc:
            log(f"Nao consegui fechar um aviso da Naiaflow: {exc}")

    def _clone_name(self):
        return self.page.locator("span").filter(has_text=re.compile(rf"^\s*{re.escape(CLONE)}\s*$", re.I))

    def _clone_chip(self):
        # etiqueta do clone ja escolhido no composer (botao arredondado com o nome)
        return self.page.locator("button.rounded-full").filter(has=self._clone_name())

    def _clone_list_open(self) -> bool:
        return self.page.get_by_placeholder("Pesquisar").count() > 0 and self.page.get_by_placeholder("Pesquisar").first.is_visible()

    def _ensure_clone(self) -> None:
        # Tela de 10/2026: com o clone escolhido aparece a etiqueta "luna" no
        # composer (e o cartao dele fica com um check). Clicar no cartao de novo
        # tiraria o clone - por isso so abre a lista se a etiqueta nao estiver la.
        if self._clone_chip().count():
            return
        self.page.get_by_role("button", name=re.compile(r"^\s*Adicionar( clone)?\s*$", re.I)).first.click()
        # cartao do clone na lista (carrega depois de abrir)
        card = self.page.locator("div.group").filter(has=self._clone_name()).first
        card.wait_for(state="visible", timeout=20000)
        card.click()
        self.page.wait_for_timeout(800)
        if self._clone_list_open():
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
        if not self._clone_chip().count():
            raise RuntimeError(f'Nao consegui selecionar o clone "{CLONE}" na Naiaflow.')

    def generate(self, prompt: str) -> str:
        self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
        self.page.wait_for_selector("text=Modo imagem", timeout=60000)
        self.page.wait_for_timeout(1500)  # os avisos aparecem logo depois de carregar
        self._dismiss_overlays()
        self._ensure_clone()
        before = self._job_images()
        box = self.page.get_by_placeholder("Digite seu prompt", exact=False).first
        if not box.count():
            box = self.page.locator("textarea").first
        box.click()
        box.fill(prompt)
        self._create_button().click()
        log("Gerando na Naiaflow…")
        return self._wait_result(before)

    def edit(self, job_id: str, instruction: str) -> str:
        """Correcao: abre a foto pronta, escolhe Editar pelo texto (1 credito),
        escreve o pedido e espera a foto editada."""
        self.page.goto(NAIAFLOW_JOB + job_id, wait_until="domcontentloaded")
        action = self.page.get_by_role("button", name=re.compile(r"Editar pelo texto", re.I)).first
        action.wait_for(state="attached", timeout=60000)
        self.page.wait_for_timeout(1500)
        self._dismiss_overlays(panels=False)
        before = self._job_images()
        action.scroll_into_view_if_needed()
        action.click()
        box = self.page.get_by_placeholder("Digite seu prompt", exact=False).first
        box.wait_for(state="visible", timeout=15000)
        box.click()
        box.fill(instruction)
        self._create_button().click()
        log("Editando na Naiaflow…")
        return self._wait_result(before)

    def _create_button(self):
        # o botao virou "Criar" (com o custo, ex. "Criar 1"); "Criar novo clone" fica de fora
        return self.page.get_by_role("button", name=re.compile(r"^\s*(Criar|Gerar)(\s*\d+)?\s*$", re.I)).last

    def _wait_result(self, before: set[str]) -> str:
        # o link da foto traz a hora em que ficou pronta (job-<id>-0-<ms>); a
        # galeria carrega fotos antigas aos poucos, entao so vale foto feita
        # depois do clique (2 min de folga para o relogio do PC)
        since_ms = (time.time() - 120) * 1000
        deadline = time.time() + GENERATION_TIMEOUT_S
        last_reload = time.time()
        while time.time() < deadline:
            time.sleep(5)
            try:
                head = self.page.locator("body").inner_text()[:300]
                new = {s for s in self._job_images() - before if _job_time_ms(s) > since_ms}
                # a tela da foto pronta pode nao mostrar a nova: a galeria mostra
                if not new and time.time() - last_reload > 90 and "/preview/" in self.page.url:
                    self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
                    last_reload = time.time()
                    continue
            except Exception:  # a pagina recarregou: confere de novo no proximo ciclo
                continue
            if new and "Gerando" not in head and "Enviando" not in head:
                src = sorted(new)[0]
                return src.rsplit("/", 1)[0] + "/public"
        raise RuntimeError("A Naiaflow nao terminou a foto em 10 minutos.")


# --- um item da fila -----------------------------------------------------------------------------------

def process(studio: Studio, item: dict, naia: Naiaflow | None, test_image: Path | None) -> None:
    edit = item.get("edit")
    if edit:
        log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): correção da foto {edit['source']}: {edit['instruction'][:90]}")
    else:
        log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): {item['prompt'][:90]}")
    url = ""
    if test_image:
        raw = test_image.read_bytes()
    else:
        if edit:
            job = known_jobs().get(edit["source"])
            if not job:
                raise RuntimeError(f"Nao sei qual foto da Naiaflow e {edit['source']} (foto gerada antes de o robo anotar os jobs).")
            url = naia.edit(job, edit["instruction"])
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
    if url:
        remember_job(local_path, url)
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
