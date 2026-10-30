"""Luna Studio - automacao local da Naiaflow (parte B do fluxo).

Roda no PC do administrador. Pega na fila do estudio os itens de foto dos
planos aprovados (Assistente -> Planejamento), gera cada foto na Naiaflow com o
clone "luna" (Playwright controlando o Chrome, num perfil separado so para a
Naiaflow), salva na pasta do estudio (conteudos/AAAA-MM, +18 em subpasta),
devolve ao estudio - que cria a publicacao de cada destino - e sobe uma copia
JPEG no formato de cada destino (Feed 4:5, Stories/TikTok 9:16). Item de Reels:
a mesma foto vira um video 9:16 de 7 s com zoom leve, montado aqui no PC
(ffmpeg do pacote imageio-ffmpeg, sem gastar credito de video). O estudio
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
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageFilter

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
EDIT_ACTION = "Editar pelo texto"  # nome da acao no painel
EDIT_CHIP = "Editar"  # etiqueta que ela deixa no composer
CAROUSEL_ACTION = "Gerar um carrossel"  # 3 creditos, 3 variacoes do mesmo look e lugar
# painel de Acoes da tela de 29/10: cada acao fica numa categoria
ACTION_CATEGORY = {EDIT_ACTION: "Editar", CAROUSEL_ACTION: "Nova Cena", "Adicionar rachadinha": "Corpo", "Aumentar os seios": "Corpo", "Aumentar coxas e bunda": "Corpo"}
CLONE = "luna"
MIN_CREDITS = 2  # abaixo disso para e avisa (nao gasta o resto)
GENERATION_TIMEOUT_S = 10 * 60
QUEUE_POLL_S = 60
JPEG_MAX_BYTES = 4 * 1024 * 1024  # limite do TikTok para foto
REELS_SECONDS = 7
REELS_FPS = 30
REELS_ZOOM = 0.06  # 6% de aproximacao do comeco ao fim


def log(msg: str) -> None:
    line = f"[{datetime.now():%d/%m %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# link da foto: job-<id>-<n>-<ms> (ate 28/10) ou job-<id>-pro<n>-<ms> (29/10: cada
# Criar entrega 3 fotos da mesma cena, pro1..pro3); <ms> = hora em que ficou pronta
JOB_LINK = re.compile(r"job-([0-9a-f-]{36})-(?:pro)?(\d+)-(\d{12,14})")


def _job_time_ms(src: str) -> float:
    m = JOB_LINK.search(src)
    return float(m.group(3)) if m else 0.0


def _job_variant(src: str) -> tuple[str, int]:
    m = JOB_LINK.search(src)
    return (m.group(1), int(m.group(2))) if m else ("", 0)


def remember_job(local_path: str, url: str) -> None:
    m = re.search(r"job-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", url)
    if not m:
        return
    jobs = known_jobs()
    # Criar com 3 fotos (pro1..pro3): anota qual delas ("<job>#3"); cada uma tem
    # a sua pagina na Naiaflow
    _, variant = _job_variant(url)
    jobs[local_path] = m.group(1) + (f"#{variant}" if "-pro" in url and variant else "")
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


def fit_blur(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Foto inteira, sem cortar, sobre um fundo da propria foto ampliada e
    desfocada (como o Instagram faz): a foto em pe (9:16) entra no Feed 4:5 sem
    perder cabeca nem pernas."""
    W, H = size
    back = fit_ratio(img, W, H, size).filter(ImageFilter.GaussianBlur(40))
    scale = min(W / img.width, H / img.height)
    front = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    back.paste(front, ((W - front.width) // 2, (H - front.height) // 2))
    return back


def copy_for(img: Image.Image, network: str, fmt: str) -> bytes:
    if network == "instagram" and fmt == "Feed" and abs(img.width / img.height - 1) < 0.05:
        # foto gerada em Quadrado na Naiaflow: Feed 1:1, sem corte
        return to_jpeg(img.resize((1080, 1080), Image.LANCZOS))
    if network == "instagram" and fmt == "Feed":
        # a Naiaflow gera em pe (9:16; nao tem 4:5): no Feed a foto entra
        # inteira, sem cortar cabeca nem pernas, como no Stories
        if img.width / img.height < 4 / 5 * 0.97:
            return to_jpeg(fit_blur(img, (1080, 1350)))
        return to_jpeg(fit_ratio(img, 4, 5, (1080, 1350)))
    # Stories, TikTok e Telegram: vertical 9:16 1080x1920; foto mais larga (Feed 4:5) entra inteira
    if img.width / img.height > 9 / 16 * 1.03:
        return to_jpeg(fit_blur(img, (1080, 1920)))
    return to_jpeg(fit_ratio(img, 9, 16, (1080, 1920)))


def make_reels(img: Image.Image, out: Path) -> None:
    """Video 9:16 1080x1920 de 7 s a 30 fps: zoom leve centrado um pouco acima
    (rosto), com trilha muda (o Instagram poe a musica por cima)."""
    import imageio_ffmpeg

    W, H = 1080, 1920
    base = fit_ratio(img, 9, 16, (1440, 2560)) if img.size[0] >= 1440 else fit_ratio(img, 9, 16, (W, H))
    bw, bh = base.size
    frames = REELS_SECONDS * REELS_FPS
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(REELS_FPS), "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-g", str(REELS_FPS * 2), "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(frames):
            z = 1 + REELS_ZOOM * n / (frames - 1)
            w, h = bw / z, bh / z
            x, y = (bw - w) / 2, (bh - h) * 0.35
            # transformacao com fracao de pixel: o zoom anda liso, sem tremer
            frame = base.transform((W, H), Image.AFFINE, (w / W, 0, x, 0, h / H, y), resample=Image.BICUBIC)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except OSError:  # o ffmpeg parou antes: o motivo sai no erro abaixo
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0 or not out.exists():
        raise RuntimeError(f"Nao consegui montar o video do Reels: {err[-300:]}")


def put_copy(url: str, data: bytes, mime: str = "image/jpeg") -> None:
    name = "0.mp4" if mime == "video/mp4" else "0.jpg"
    r = requests.put(
        url,
        headers={"x-upsert": "true"},
        files={"": (name, data, mime)},
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
        if panels:
            self._close_popovers()
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
        # a etiqueta carrega depois da pagina: espera um pouco antes de achar que falta
        try:
            self._clone_chip().first.wait_for(state="visible", timeout=6000)
            return
        except Exception:
            pass
        self._close_popovers()
        # Tela de 29/10: "Adicionar" abre um menu (Outro clone, Modelos de Poses,
        # Enviar imagem); "Outro clone" abre a lista de clones
        self.page.get_by_role("button", name=re.compile(r"^\s*Adicionar( clone)?\s*$", re.I)).first.click()
        try:
            self.page.get_by_text(re.compile(r"^\s*Outro clone\s*$", re.I)).first.click(timeout=5000)
        except Exception:
            pass  # tela antiga: Adicionar ja abria a lista
        # cartao do clone na lista (carrega depois de abrir); a propria etiqueta
        # do composer tambem e um div.group, por isso o filtro pelo formato do cartao
        card = self.page.locator("div.group[class*='aspect-']").filter(has=self._clone_name()).first
        card.wait_for(state="visible", timeout=20000)
        card.click()
        self.page.wait_for_timeout(1000)
        if self._clone_list_open():
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
        self._close_popovers()
        if not self._clone_chip().count():
            raise RuntimeError(f'Nao consegui selecionar o clone "{CLONE}" na Naiaflow.')

    def _close_popovers(self) -> None:
        """Menus da Naiaflow (proporcao, Adicionar, lista de clones) deixam um
        fundo invisivel (div.fixed.inset-0) por cima da tela que bloqueia os
        cliques; o Escape nem sempre fecha. Fecha com Escape e, se o fundo
        continuar, clicando nele."""
        for _ in range(3):
            backdrop = self.page.locator("div.fixed.inset-0")
            visible = [backdrop.nth(i) for i in range(backdrop.count()) if backdrop.nth(i).is_visible()]
            if not visible:
                return
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(400)
            try:
                if visible[-1].is_visible():
                    visible[-1].click(position={"x": 5, "y": 5}, timeout=2000)
                    self.page.wait_for_timeout(400)
            except Exception:
                pass

    def _set_aspect(self, aspect: str | None) -> None:
        """Proporcao do Criar (botao Vertical/Quadrado/Horizontal do composer).
        A Naiaflow lembra a ultima escolha: o robo sempre escolhe a do item."""
        name = {"quadrado": "Quadrado", "vertical": "Vertical", "horizontal": "Horizontal"}.get((aspect or "").lower())
        if not name:
            return
        button = self.page.get_by_role("button", name=re.compile(r"^\s*(Vertical|Quadrado|Horizontal)\s*$"))
        if not button.count():
            log("Nao achei o botao de proporcao da Naiaflow: segue com a atual.")
            return
        if button.first.inner_text().strip() == name:
            return
        button.first.click()
        self.page.wait_for_timeout(1000)
        self.page.get_by_text(name, exact=True).last.click()
        self.page.wait_for_timeout(800)
        self._close_popovers()
        now = button.first.inner_text().strip()
        if now != name:
            raise RuntimeError(f"Nao consegui escolher a proporcao {name} na Naiaflow (ficou {now}).")
        log(f"Proporcao: {name}.")

    def clear_references(self) -> int:
        """Tira as imagens extras (IMG1, IMG2...) do composer: a Naiaflow guarda
        a ultima referencia e ela entraria no proximo Criar sem querer."""
        removed = 0
        for _ in range(6):
            if not self.page.get_by_text(re.compile(r"^\s*IMG\d+\s*$")).count():
                break
            label = self.page.get_by_text(re.compile(r"^\s*IMG\d+\s*$")).first
            box = label.bounding_box()
            if not box:
                break
            # o "x" fica logo a direita do nome
            self.page.mouse.click(box["x"] + box["width"] + 12, box["y"] + box["height"] / 2)
            self.page.wait_for_timeout(700)
            removed += 1
        return removed

    def attach_reference(self, photo: Path) -> None:
        """Imagem de referencia no Criar: Adicionar -> Enviar imagem -> Enviar do
        dispositivo. Ela entra no composer como IMG1, ao lado do clone."""
        self._close_popovers()
        self.page.get_by_role("button", name=re.compile(r"^\s*Adicionar( clone)?\s*$", re.I)).first.click()
        self.page.wait_for_timeout(1200)
        self.page.get_by_text(re.compile(r"^\s*Enviar imagem\s*$", re.I)).first.click()
        self.page.wait_for_timeout(2500)
        with self.page.expect_file_chooser(timeout=15000) as chooser:
            self.page.get_by_role("button", name=re.compile(r"Enviar do dispositivo", re.I)).last.click()
        chooser.value.set_files(str(photo))
        self.page.wait_for_timeout(8000)
        self._close_popovers()
        if not self.page.get_by_text(re.compile(r"^\s*IMG1\s*$")).count():
            raise RuntimeError("A foto de referencia nao entrou no composer da Naiaflow (IMG1).")
        log("Referência anexada (IMG1).")

    def generate(self, prompt: str, aspect: str | None = None, reference: Path | None = None) -> list[str]:
        """Gera pelo prompt; devolve as fotos do mesmo Criar (3 desde 29/10)."""
        self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
        self.page.wait_for_selector("text=Modo imagem", timeout=60000)
        self.page.wait_for_timeout(1500)  # os avisos aparecem logo depois de carregar
        self._dismiss_overlays()
        self._ensure_clone()
        self._set_aspect(aspect)
        # referencia (IMG1): so a do item; nunca uma que tenha sobrado no composer
        self.clear_references()
        if reference:
            self.attach_reference(reference)
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
        self._open_job(job_id)
        before = self._job_images()
        self._choose_action(EDIT_ACTION)
        box = self.page.get_by_placeholder("Digite seu prompt", exact=False).first
        box.wait_for(state="visible", timeout=15000)
        box.click()
        box.fill(instruction)
        # a lista tem acoes parecidas lado a lado (ex.: "Adicionar esperma"): so
        # gasta o credito se a etiqueta no composer for exatamente a da edicao
        chips = self._action_chips()
        if chips != [EDIT_CHIP]:
            raise RuntimeError(f'A Naiaflow marcou a acao errada ({", ".join(chips) or "nenhuma"}); nao cliquei em Criar.')
        self._create_button().click()
        log("Editando na Naiaflow…")
        return self._wait_result(before)[0]

    def carousel(self, job_id: str, count: int) -> list[str]:
        """Carrossel: na foto base, "Gerar um carrossel" (Nova Cena, 3 creditos)
        gera 3 variacoes do mesmo look e lugar. Repete ate ter `count` fotos."""
        urls: list[str] = []
        while len(urls) < count:
            self._open_job(job_id)
            before = self._job_images()
            self._choose_action(CAROUSEL_ACTION)
            # confirmacao "Carrossel do insta: gerar 3 imagens?" -> Continuar (3)
            dialog = self.page.get_by_text(re.compile(r"Carrossel do insta", re.I))
            go = self.page.get_by_role("button", name=re.compile(r"^\s*Continuar\b", re.I))
            dialog.first.wait_for(state="visible", timeout=15000)
            if go.count() != 1 or "3" not in go.first.inner_text():
                raise RuntimeError("O aviso do carrossel da Naiaflow nao era o esperado (Continuar 3); nao cliquei.")
            go.first.click()
            log("Gerando o carrossel na Naiaflow (3 variações)…")
            urls += self._wait_results(before, 3, skip_job=job_id.partition("#")[0])
        return urls[:count]

    def apply_action(self, job_id: str, name: str, level: str) -> str:
        """Acao de um clique com nivel (ex.: "Adicionar rachadinha": Leve,
        Medio, Pesado; 1 credito) na foto pronta. Confere o nivel marcado e o
        "Continuar 1" antes de gastar."""
        self._open_job(job_id)
        before = self._job_images()
        self._choose_action(name)
        option = self.page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(level)}\s*$", re.I))
        if option.count() != 1:
            raise RuntimeError(f'Nao achei o nivel "{level}" de "{name}" na Naiaflow.')
        option.first.click()
        self.page.wait_for_timeout(600)
        go = self.page.get_by_role("button", name=re.compile(r"^\s*Continuar\b", re.I))
        if go.count() != 1 or go.first.inner_text().split()[-1] != "1":
            raise RuntimeError(f'O botao de "{name}" na Naiaflow nao era o esperado (Continuar 1); nao cliquei.')
        go.first.click()
        log(f"{name} ({level}) na Naiaflow…")
        return self._wait_result(before)[0]

    def _open_job(self, job_id: str) -> None:
        """Foto pronta, com o painel de Acoes (tela de 29/10: categorias).
        "<job>#3": a 3a foto de um Criar - o link do job abre sempre a 1a, entao
        o robo clica na miniatura dela na galeria (cada foto tem a sua pagina)."""
        job, _, variant = job_id.partition("#")
        if variant and variant != "1":
            self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
            self.page.wait_for_selector("text=Modo imagem", timeout=60000)
            self.page.wait_for_timeout(2500)
            self._dismiss_overlays()
            thumb = self.page.locator(f"img[src*='job-{job}-pro{variant}-']").first
            for _ in range(8):  # foto mais antiga: rola a galeria ate aparecer
                if thumb.count():
                    break
                self.page.mouse.wheel(0, 2500)
                self.page.wait_for_timeout(1500)
            if not thumb.count():
                raise RuntimeError(f"Nao achei a foto {variant} do job {job[:8]} na galeria da Naiaflow.")
            thumb.scroll_into_view_if_needed()
            thumb.click()
        else:
            self.page.goto(NAIAFLOW_JOB + job, wait_until="domcontentloaded")
        ready = self.page.get_by_text(re.compile(r"^\s*(Nova Cena|Filtrar ações.*)\s*$")).or_(self.page.get_by_placeholder("Filtrar ações", exact=False))
        ready.first.wait_for(state="visible", timeout=60000)
        self.page.wait_for_timeout(1500)
        self._dismiss_overlays(panels=False)

    def _choose_action(self, name: str) -> None:
        """Escolhe a acao pelo nome exato. Tela de 29/10: categorias (Editar,
        Nova Cena...) e a lista da categoria; tela antiga: campo "Filtrar acoes".
        Exige uma acao so com o nome, para nao clicar na vizinha."""
        search = self.page.get_by_placeholder("Filtrar ações", exact=False)
        if search.count() and search.first.is_visible():
            search.first.fill(name)
        else:
            self.page.get_by_text(ACTION_CATEGORY[name], exact=True).first.click()
        self.page.wait_for_timeout(1200)
        title = self.page.get_by_text(name, exact=True)
        action = self.page.locator("button").filter(has=title)
        if action.count() != 1:
            raise RuntimeError(f'Nao achei uma unica acao "{name}" na Naiaflow (achei {action.count()}).')
        action.click()
        self.page.wait_for_timeout(800)

    def _action_chips(self) -> list[str]:
        # etiqueta da acao escolhida, ao lado de "Pintar para remover"
        chips = self.page.locator("div.rounded-full[class*='border-accent'] span.whitespace-nowrap")
        return [chips.nth(i).inner_text().strip() for i in range(chips.count()) if chips.nth(i).is_visible()]

    def _create_button(self):
        # o botao virou "Criar" (com o custo, ex. "Criar 1"); "Criar novo clone" fica de fora
        return self.page.get_by_role("button", name=re.compile(r"^\s*(Criar|Gerar)(\s*\d+)?\s*$", re.I)).last

    def _wait_result(self, before: set[str]) -> list[str]:
        # o link da foto traz a hora em que ficou pronta (job-<id>-0-<ms>); a
        # galeria carrega fotos antigas aos poucos, entao so vale foto feita
        # depois do clique (2 min de folga para o relogio do PC)
        since_ms = (time.time() - 120) * 1000
        deadline = time.time() + GENERATION_TIMEOUT_S
        last_reload = time.time()
        seen: set[str] = set()
        while time.time() < deadline:
            time.sleep(5)
            try:
                new = {s for s in self._job_images() - before if _job_time_ms(s) > since_ms}
                # a galeria pode nao se atualizar sozinha (e a tela da foto pronta
                # nao mostra a nova): recarrega a galeria a cada minuto
                if not new and time.time() - last_reload > 60:
                    self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
                    last_reload = time.time()
                    continue
            except Exception:  # a pagina recarregou: confere de novo no proximo ciclo
                continue
            # A foto vale quando aparece igual em duas voltas seguidas. Nao olha
            # mais o "Gerando" da pagina: uma geracao travada na galeria deixava
            # o aviso para sempre e o robo nunca achava a foto (29/10).
            stable = new & seen
            if stable:
                # todas as fotos do job novo (pro1, pro2, pro3), na ordem
                job = _job_variant(sorted(stable)[0])[0]
                same = sorted({x for x in new if _job_variant(x)[0] == job}, key=lambda x: _job_variant(x)[1])
                return [x.rsplit("/", 1)[0] + "/public" for x in same]
            seen = new
        raise RuntimeError("A Naiaflow nao terminou a foto em 10 minutos.")

    # --- dancinha: Copiar movimento --------------------------------------------------------------

    def copy_motion(self, link: str | None, video_file: Path | None, seconds: int, outfit: str | None, dry: bool = False) -> str:
        """Copiar movimento (Modo imagem -> Copiar movimento): o movimento vem do
        link ou do video enviado, a pessoa e o clone luna, sem som (a musica entra
        no Instagram), com a roupa escolhida da galeria (ou a do video). So gera
        se o custo for o esperado (1 por segundo + 1 do clone). Devolve o link do MP4."""
        p = self.page
        p.goto(NAIAFLOW, wait_until="domcontentloaded")
        p.wait_for_selector("text=Modo imagem", timeout=60000)
        p.wait_for_timeout(2000)
        self._dismiss_overlays()
        p.get_by_role("button", name=re.compile(r"Modo imagem")).first.click()
        p.wait_for_timeout(1200)
        p.get_by_text(re.compile(r"Copiar movimento", re.I)).first.click()
        p.wait_for_timeout(2500)
        # a janela lembra o passo da ultima vez
        for name in ("Recomeçar", "Limpar"):
            b = p.get_by_role("button", name=name, exact=True)
            if b.count() and b.last.is_visible():
                b.last.click()
                p.wait_for_timeout(1500)
                break
        if video_file:
            p.get_by_role("button", name=re.compile(r"Enviar v[ií]deo")).first.click()
            p.wait_for_timeout(1500)
            with p.expect_file_chooser(timeout=15000) as chooser:
                p.get_by_role("button", name="Escolher arquivo", exact=True).last.click()
            chooser.value.set_files(str(video_file))
            p.wait_for_timeout(15000)
        else:
            p.get_by_role("button", name=re.compile(r"Importar por link")).first.click()
            p.wait_for_timeout(1500)
            p.locator("input[placeholder^='https']").first.fill(link or "")
        # link pede Continuar; o video enviado ja cai no trecho
        dur = p.get_by_role("button", name=f"{seconds}s", exact=True)
        if not (dur.count() and dur.first.is_visible()):
            p.get_by_role("button", name="Continuar", exact=True).last.click()
            dur.first.wait_for(state="visible", timeout=90000)
        dur.first.click()
        p.get_by_text("Sem som", exact=True).first.click()
        p.wait_for_timeout(800)
        p.get_by_role("button", name="Continuar", exact=True).last.click()
        p.wait_for_timeout(5000)
        dialog = p.locator("[role=dialog]").last
        pending = dialog.get_by_text("Selecionar clone", exact=True)
        if pending.count() and pending.first.is_visible():
            pending.first.click()
            p.wait_for_timeout(2500)
            p.locator("[role=dialog]").last.get_by_text(CLONE, exact=True).first.click()
            p.wait_for_timeout(2500)
        if pending.count() and pending.first.is_visible():
            raise RuntimeError(f'Nao consegui escolher o clone "{CLONE}" no Copiar movimento.')
        if outfit:
            p.get_by_role("button", name="Roupa", exact=True).last.click()
            p.wait_for_timeout(1500)
            p.get_by_role("menuitem", name="Abrir galeria").first.click()
            p.wait_for_timeout(3500)
            caption = p.get_by_text(re.compile(re.escape(outfit), re.I)).first
            if not caption.count():
                raise RuntimeError(f'Nao achei a roupa "{outfit}" na galeria da Naiaflow.')
            caption.locator("xpath=ancestor::*[.//img][1]").locator("img").first.click()
            p.wait_for_timeout(3000)
            if not p.evaluate("() => !!document.querySelector(`[role=dialog] button[aria-label='Roupa'] img`)"):
                raise RuntimeError(f'A roupa "{outfit}" nao ficou escolhida no Copiar movimento.')
            log(f"Roupa: {outfit}.")
        final = p.get_by_role("button", name=re.compile(r"Gerar v[ií]deo", re.I))
        if final.count() != 1 or not final.first.is_enabled():
            raise RuntimeError("O botao Gerar video da Naiaflow nao ficou ligado.")
        label = final.first.inner_text().replace("\n", " ").strip()
        if not label.endswith(str(seconds + 1)):
            raise RuntimeError(f"Custo inesperado no Copiar movimento ({label}): nao gerei.")
        if dry:  # conferencia sem gastar credito: para antes do Gerar
            return label
        before = set(self._stream_thumbs())
        final.first.click(timeout=15000, force=True)
        log(f"Gerando a dancinha ({seconds} s, {seconds + 1} créditos)…")
        deadline = time.time() + 25 * 60
        while time.time() < deadline:
            time.sleep(15)
            try:
                p.goto(NAIAFLOW, wait_until="domcontentloaded")
                p.wait_for_timeout(4000)
                new = [t for t in self._stream_thumbs() if t not in before]
                if not new:
                    continue
                p.locator(f"img[src='{new[0]}']").first.click()
                p.wait_for_timeout(6000)
                srcs = p.eval_on_selector_all("video, video source", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
                mp4 = next((x for x in srcs if x.startswith("http") and x.endswith(".mp4")), None)
                if mp4:
                    return mp4
            except Exception:
                continue
        raise RuntimeError("A Naiaflow nao terminou a dancinha em 25 minutos.")

    def _stream_thumbs(self) -> list[str]:
        # miniaturas dos videos da galeria (Cloudflare Stream)
        return self.page.evaluate("() => [...document.querySelectorAll('img')].map(e => e.currentSrc || e.src).filter(s => s.includes('cloudflarestream'))")


    def _wait_results(self, before: set[str], count: int, skip_job: str = "") -> list[str]:
        """Varias fotos de uma acao (carrossel): espera `count` fotos novas.
        A galeria pode empilhar as fotos de um job num cartao so ("+3"), entao
        a pagina de cada job novo tambem e lida. skip_job: a foto base (acabou
        de ficar pronta e passaria no filtro de horario)."""
        since_ms = (time.time() - 120) * 1000
        deadline = time.time() + GENERATION_TIMEOUT_S
        found: dict[str, None] = {}
        base = lambda s: s.rsplit("/", 1)[0]  # noqa: E731 (sem /mini ou /public)
        while time.time() < deadline and len(found) < count:
            time.sleep(15)
            try:
                self.page.goto(NAIAFLOW, wait_until="domcontentloaded")
                self.page.wait_for_timeout(4000)
                new = sorted(s for s in self._job_images() - before if _job_time_ms(s) > since_ms and not (skip_job and skip_job in s))
                jobs = {re.search(r"job-([0-9a-f-]{36})", s).group(1) for s in new}
                for s in new:
                    found.setdefault(base(s))
                for job in jobs:
                    if len(found) >= count:
                        break
                    self.page.goto(NAIAFLOW_JOB + job, wait_until="domcontentloaded")
                    self.page.wait_for_timeout(4000)
                    for s in sorted(self._job_images()):
                        if _job_time_ms(s) > since_ms and not (skip_job and skip_job in s):
                            found.setdefault(base(s))
            except Exception:  # pagina recarregando: tenta no proximo ciclo
                continue
        if len(found) < count:
            raise RuntimeError(f"A Naiaflow entregou {len(found)} de {count} fotos do carrossel em 10 minutos.")
        return [f"{b}/public" for b in list(found)[:count]]


# --- um item da fila -----------------------------------------------------------------------------------

def process_motion(studio: Studio, item: dict, naia: Naiaflow | None, test_video: Path | None) -> None:
    """Dancinha: video da Naiaflow (ou o de teste) -> quadro em JPEG para a
    biblioteca -> Reels e TikTok com o mesmo MP4."""
    m = item["motion"]
    log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): dancinha {m.get('seconds')} s pelo {'link' if m.get('link') else 'video enviado'}"
        + (f", roupa {m['outfit']}" if m.get("outfit") else ""))
    folder = STUDIO_ROOT / item["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    mp4 = folder / item["file"].replace(".jpg", ".mp4")
    if mp4.exists() and mp4.stat().st_size > 10000:
        log(f"Dancinha ja estava pronta: {mp4.name} (nao gasta credito de novo).")
    elif test_video:
        mp4.write_bytes(test_video.read_bytes())
    else:
        source = None
        if m.get("file_url"):
            source = folder / f"referencia-{item['id'][:8]}{Path(m.get('file_name') or 'video.mp4').suffix}"
            r = requests.get(m["file_url"], timeout=300)
            r.raise_for_status()
            source.write_bytes(r.content)
        url = naia.copy_motion(m.get("link"), source, int(m.get("seconds") or 5), m.get("outfit"))
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        mp4.write_bytes(r.content)
        if source and source.exists():
            source.unlink()
    import imageio_ffmpeg
    # o Instagram recusa Reels sem trilha de audio (ERROR no conteiner, 30/10):
    # 1080x1920 com trilha muda, como o make_reels (a musica entra no Instagram)
    raw = mp4.with_name(mp4.stem + "-naia.mp4")
    if not raw.exists():
        mp4.replace(raw)
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(raw),
             "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
             "-map", "0:v:0", "-map", "1:a:0", "-shortest",
             "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(mp4)],
            check=True,
        )
        raw.unlink()
    jpg = folder / item["file"]
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", "1", "-i", str(mp4), "-frames:v", "1", "-q:v", "3", str(jpg)], check=True)
    local_path = f"{item['folder']}/{item['file']}"
    video_path = f"{item['folder']}/{mp4.name}"
    data = mp4.read_bytes()
    log(f"Dancinha salva em {video_path}")
    done = studio.call("done", id=item["id"], local_path=local_path, size_bytes=jpg.stat().st_size, video_path=video_path, video_size=len(data))
    for t in done["targets"]:
        up = studio.call("upload-url", id=item["id"], post_id=t["post_id"], version=t["version"], size_bytes=len(data), mime_type="video/mp4")
        put_copy(up["url"], data, "video/mp4")
    sent = studio.call("uploaded", id=item["id"])
    log(f"Mandada para você aprovar no Telegram ({sent['sent']} publicação(ões)).")


def process(studio: Studio, item: dict, naia: Naiaflow | None, test_image: Path | None) -> None:
    edit = item.get("edit")
    if edit:
        log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): correção da foto {edit['source']}: {edit['instruction'][:90]}")
    else:
        log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): {item['prompt'][:90]}")
    folder = STUDIO_ROOT / item["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    # foto de referencia mandada na conversa (vai como IMG1 no Criar)
    reference: Path | None = None
    if item.get("reference_url") and not test_image:
        reference = folder / f"referencia-{item['id'][:8]}.jpg"
        r = requests.get(item["reference_url"], timeout=120)
        r.raise_for_status()
        reference.write_bytes(r.content)
    # carrossel: uma variacao do pedido por foto (a 1a e o proprio pedido)
    prompts = item.get("prompts") or [item["prompt"]]
    carousel = len(prompts) > 1
    # carrossel pela Naiaflow: foto 1 = base pelo prompt; as outras saem do
    # "Gerar um carrossel" na base (mesmo look e lugar, 3 variacoes por vez)
    use_action = carousel and bool(item.get("carousel_action"))
    # fotos que ja sairam e ainda nao foram usadas: as outras do mesmo Criar
    # (3 por credito desde 29/10) e as do "Gerar um carrossel"
    spare: list[str] = []
    imgs: list[Image.Image] = []
    names: list[str] = []
    act = item.get("naia_action")
    if act:
        # acao pedida no Telegram (ex.: rachadinha) nas fotos escolhidas; as
        # outras fotos da publicacao continuam iguais
        imgs, names = apply_action(naia, item, act, folder)
        prompts = []
        carousel = len(names) > 1
    for i, prompt in enumerate(prompts):
        name = item["file"] if i == 0 else item["file"].replace(".jpg", f"-{i + 1}.jpg")
        path_i = folder / name
        local_i = f"{item['folder']}/{name}"
        if carousel and path_i.exists():
            # tentativa anterior caiu no meio: a foto que ja saiu nao gasta credito de novo
            log(f"Foto {i + 1}/{len(prompts)} já estava pronta: {local_i}")
            imgs.append(Image.open(path_i).convert("RGB"))
            names.append(name)
            continue
        url = ""
        if test_image:
            raw = test_image.read_bytes()
        else:
            if edit:
                job = known_jobs().get(edit["source"])
                if not job:
                    raise RuntimeError(f"Nao sei qual foto da Naiaflow e {edit['source']} (foto gerada antes de o robo anotar os jobs).")
                url = naia.edit(job, edit["instruction"])
            elif carousel and i > 0 and spare:
                url = spare.pop(0)
            elif use_action and i > 0:
                base_local = f"{item['folder']}/{names[0]}"
                base_job = known_jobs().get(base_local)
                if not base_job:
                    raise RuntimeError(f"Nao sei qual foto da Naiaflow e a base do carrossel ({base_local}).")
                missing = sum(1 for k in range(i, len(prompts)) if not (folder / item["file"].replace(".jpg", f"-{k + 1}.jpg")).exists())
                spare = naia.carousel(base_job, missing)
                url = spare.pop(0)
            else:
                urls = naia.generate(prompt, item.get("aspect"), reference)
                url = urls[0]
                if carousel:
                    spare = urls[1:]
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            raw = r.content
        img_i = Image.open(io.BytesIO(raw)).convert("RGB")
        path_i.write_bytes(to_jpeg(img_i, 95))
        if url:
            remember_job(local_i, url)
        log(f"Salva em {local_i}" + (f" (foto {i + 1}/{len(prompts)} do carrossel)" if carousel else ""))
        imgs.append(img_i)
        names.append(name)
    img = imgs[0]
    path = folder / names[0]
    local_path = f"{item['folder']}/{names[0]}"
    if carousel:
        extra_more = {
            "more_paths": [f"{item['folder']}/{n}" for n in names[1:]],
            "more_sizes": [(folder / n).stat().st_size for n in names[1:]],
        }
    else:
        extra_more = {}
    extra = {}
    video: bytes | None = None
    if item.get("kind") == "video":
        mp4 = path.with_suffix(".mp4")
        make_reels(img, mp4)
        video = mp4.read_bytes()
        extra = {"video_path": f"{item['folder']}/{mp4.name}", "video_size": len(video)}
        log(f"Vídeo do Reels montado no PC: {extra['video_path']}")
    # a referencia ja foi usada: nao fica na pasta (nem entra na biblioteca)
    if reference and reference.exists():
        reference.unlink()
    done = studio.call("done", id=item["id"], local_path=local_path, size_bytes=path.stat().st_size, **extra, **extra_more)
    for t in done["targets"]:
        photos = int(t.get("photos") or 1)
        if photos > 1:
            # carrossel: todas as fotos da publicacao, na ordem, num pedido so
            datas = [copy_for(imgs[j], t["network"], t["format"]) for j in range(photos)]
            up = studio.call(
                "upload-url",
                id=item["id"],
                post_id=t["post_id"],
                version=t["version"],
                items=[{"position": j, "size_bytes": len(d), "mime_type": "image/jpeg"} for j, d in enumerate(datas)],
            )
            for u in up["urls"]:
                put_copy(u["url"], datas[u["position"]], "image/jpeg")
            continue
        if video is not None and t["format"] in ("Reels", "Vídeo"):
            data, mime = video, "video/mp4"
        else:
            data, mime = copy_for(img, t["network"], t["format"]), "image/jpeg"
        up = studio.call("upload-url", id=item["id"], post_id=t["post_id"], version=t["version"], size_bytes=len(data), mime_type=mime)
        put_copy(up["url"], data, mime)
    sent = studio.call("uploaded", id=item["id"])
    log(f"Mandada para você aprovar no Telegram ({sent['sent']} publicação(ões)).")
    if not test_image and not act:
        learn(studio, item, img, edit["instruction"] if edit else item["prompt"])


def apply_action(naia: Naiaflow | None, item: dict, act: dict, folder: Path) -> tuple[list[Image.Image], list[str]]:
    """Aplica a acao da Naiaflow (ex.: "Adicionar rachadinha", nivel Leve/Medio/
    Pesado) nas fotos escolhidas (1 = primeira) e devolve todas as fotos da
    publicacao na ordem, com os nomes na pasta do item."""
    chosen = set(act["photos"])
    # nome fixo por acao e nivel: numa nova tentativa a foto ja feita e aproveitada
    plain = unicodedata.normalize("NFKD", f"{act['name'].split()[-1]}-{act['level']}").encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z]+", "-", plain).strip("-") or "acao"
    log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): {act['name']} ({act['level']}) na(s) foto(s) {sorted(chosen)}")
    imgs: list[Image.Image] = []
    names: list[str] = []
    for n, src in enumerate(act["sources"], 1):
        src_path = STUDIO_ROOT / src
        if n not in chosen:
            imgs.append(Image.open(src_path).convert("RGB"))
            names.append(src_path.name)
            continue
        new_name = f"{src_path.stem}-{slug}.jpg"
        if (folder / new_name).exists():
            imgs.append(Image.open(folder / new_name).convert("RGB"))
            names.append(new_name)
            continue
        job = known_jobs().get(src)
        if not job or naia is None:
            raise RuntimeError(f"Nao sei qual foto da Naiaflow e {src} (o robo nao anotou o job).")
        url = naia.apply_action(job, act["name"], act["level"])
        img = Image.open(io.BytesIO(requests.get(url, timeout=120).content)).convert("RGB")
        (folder / new_name).write_bytes(to_jpeg(img, 95))
        remember_job(f"{item['folder']}/{new_name}", url)
        log(f"Salva em {item['folder']}/{new_name} (foto {n})")
        imgs.append(img)
        names.append(new_name)
    return imgs, names


def learn(studio: Studio, item: dict, img: Image.Image, prompt: str) -> None:
    """Aprendizado: a IA do estudio compara a foto com o pedido e guarda uma
    licao para a Conversa escrever prompts melhores. Falhar aqui nao atrapalha a
    foto (ela ja foi para o Telegram)."""
    small = img.copy()
    small.thumbnail((768, 768))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=80)
    image = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    try:
        lesson = studio.call("learn", id=item["id"], prompt=prompt, image=image).get("lesson")
        log(f"Aprendizado: {lesson['lesson'][:160]}" if lesson else "Aprendizado: a foto saiu como o pedido.")
    except Exception as exc:
        log(f"Aprendizado: não consegui analisar a foto ({str(exc)[:160]}).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Luna Studio - automacao local da Naiaflow")
    ap.add_argument("--uma-vez", action="store_true", help="processa a fila e sai")
    ap.add_argument("--imagem-teste", type=Path, help="usa esta imagem em vez de gerar na Naiaflow (teste)")
    ap.add_argument("--video-teste", type=Path, help="usa este video em vez da dancinha da Naiaflow (teste)")
    args = ap.parse_args()

    url, token = load_config()
    studio = Studio(url, token)
    queue = studio.call("ping")["queue"]
    log(f"Conectado ao estúdio ({url}). Itens na fila: {queue}.")
    naia: Naiaflow | None = None
    try:
        if not (args.imagem_teste or args.video_teste):
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
                if item.get("motion"):
                    process_motion(studio, item, naia, args.video_teste)
                else:
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
