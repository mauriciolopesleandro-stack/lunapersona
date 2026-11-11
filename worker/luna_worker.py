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
import math
import os
import re
import shutil
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
# teste (outra pasta do estudio): o log fica junto do teste, nao mistura com o do robo de verdade
LOG_FILE = CONFIG_FILE.with_name("luna-worker.log") if "LUNA_WORKER_CONFIG" in os.environ else HERE / "luna-worker.log"
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
# com foto de referencia (IMG1) a Naiaflow puxava o rosto e o cabelo da mulher da
# foto (31/10: saiu loira); a IMG1 serve so para pose, roupa e cenario
REFERENCE_IDENTITY = (" IMG1 é só referência de pose, roupa, enquadramento e cenário: o rosto, o cabelo"
                      " (castanho escuro, longo) e o corpo são os da luna, nunca os da mulher da IMG1."
                      # 07/11: frase de reforco que a Naia recomendou
                      " Mantenha o rosto da Luna exatamente igual ao das fotos do clone, sem alterar as feições.")
# 07/11 (Naia): o rosto nitido da IMG1 vaza para o clone; borrar o rosto da
# referencia e o que mais segura o rosto da Luna. A copia borrada fica aqui.
IMG1_DIR = Path(os.environ.get("TEMP", ".")) / "luna-img1"

# Varias personas (11/11, Fase 2): um robo so, uma fila por persona. O estudio
# diz no ping quais personas existem (id, nome, clone na Naiaflow, cabelo). A Luna
# usa a pasta do estudio como sempre; as outras, a subpasta delas (cleuza/),
# a mesma que o estudio usa. Antes de cada pedido o robo troca para a persona dele.
BASE_ROOT = STUDIO_ROOT
LUNA = {"id": "luna", "name": "Luna", "clone": "luna", "hair": "castanho escuro, longo"}
PERSONA = LUNA
OTHER_CLONES: list[str] = []


def reference_identity(p: dict) -> str:
    # (para a Luna sai exatamente a frase de sempre)
    return (" IMG1 é só referência de pose, roupa, enquadramento e cenário: o rosto, o cabelo"
            f" ({p['hair']}) e o corpo são os da {p['clone']}, nunca os da mulher da IMG1."
            f" Mantenha o rosto da {p['name']} exatamente igual ao das fotos do clone, sem alterar as feições.")


def use_persona(p: dict, everyone: list[dict]) -> None:
    global STUDIO_ROOT, CLONE, REFERENCE_IDENTITY, PERSONA, OTHER_CLONES
    PERSONA = p
    STUDIO_ROOT = BASE_ROOT if p["id"] == "luna" else BASE_ROOT / p["id"]
    CLONE = p["clone"]
    REFERENCE_IDENTITY = reference_identity(p)
    OTHER_CLONES = [o["clone"] for o in everyone if o["clone"].lower() != CLONE.lower()]


def face_hidden(photo: Path) -> Path:
    """A foto de referencia com o rosto da pessoa borrado (NudeNet do borrar.py).
    Sem rosto achado, sem NudeNet ou com erro: segue com a foto original."""
    try:
        import borrar

        dst = IMG1_DIR / f"{photo.stem}-sem-rosto.jpg"
        n = borrar.borrar_rosto(photo, dst)
    except Exception as e:  # noqa: BLE001 - borrar o rosto nunca pode parar o Criar
        log(f"Não consegui borrar o rosto da IMG1 ({e}); vai a foto original.")
        return photo
    if not n:
        return photo
    log(f"Rosto da pessoa da IMG1 borrado ({n}).")
    return dst
# 05/11: a IA do Telegram escreveu "de costas" para uma foto de frente e a Naiaflow
# seguiu o texto, nao a IMG1. No 1o Criar a pose da IMG1 vem primeiro e vale mais
# que o texto (nos outros Criar do carrossel o texto pede outra pose de proposito).
REFERENCE_POSE = ("Copie da IMG1 exatamente a pose (de frente, de costas ou de lado), a posição dos braços e das mãos,"
                  " a direção do olhar, o ângulo e a altura da câmera e o enquadramento. Se o texto abaixo descrever"
                  " outra pose ou outro ângulo, vale a IMG1.\n\n")
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


class NeedsUser(RuntimeError):
    """Algo que so o administrador resolve (ex.: aceitar os termos novos da Naiaflow)."""


class Studio:
    def __init__(self, url: str, token: str, persona: str = "luna") -> None:
        self.url = f"{url}/api/studio?r=worker"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Persona": persona}

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


def download_video(url: str, dest: Path, timeout: int) -> None:
    """Baixa o video de referencia (armazenamento ou Telegram). O link do Telegram
    leva o token do bot: o erro sai sem o endereco."""
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        raise RuntimeError(f"Nao consegui baixar o video de referencia ({type(e).__name__}{f' {status}' if status else ''}).") from None
    dest.write_bytes(r.content)


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

class NaiaNotStarted(RuntimeError):
    """O Gerar foi clicado mas a Naiaflow nao comecou (nenhum credito gasto)."""


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

    def _clone_pills(self):
        # etiquetas de clone no composer (a da luna, a da Cleuza...)
        return self.page.locator('[data-onboarding="pill-do-clone"]')

    def _wait_clone_pills(self, seconds: float = 10) -> None:
        """11/11: as etiquetas dos clones aparecem uns 5 s depois da pagina; antes
        disso o composer parece vazio (a etiqueta da luna nao foi tirada e a foto
        da Cleuza saiu com as duas). Espera elas aparecerem (sem nenhuma: segue)."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._clone_pills().count():
                self.page.wait_for_timeout(500)  # a segunda etiqueta pode vir logo depois
                return
            self.page.wait_for_timeout(300)

    def _other_clones_on(self) -> list[str]:
        names = [t.strip().lower() for t in self._clone_pills().all_inner_texts()]
        return [o for o in OTHER_CLONES if o.lower() in names]

    def _remove_other_clones(self) -> None:
        """Etiqueta do clone de outra persona no composer (ex.: a Cleuza ficou de
        um pedido anterior) sai antes: com duas, a foto sairia com as duas."""
        for name in OTHER_CLONES:
            chip = self._clone_pills().filter(
                has=self.page.locator("span").filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$", re.I))
            )
            for _ in range(2):
                if not chip.count():
                    break
                chip.first.click()
                self.page.wait_for_timeout(800)
                rem = self.page.get_by_text(re.compile(r"^\s*(Remover|Remover clone|Tirar)\s*$", re.I))
                if not rem.count():
                    self.page.keyboard.press("Escape")
                    break
                rem.first.click()
                self.page.wait_for_timeout(800)
            if chip.count():
                raise RuntimeError(f'Nao consegui tirar o clone "{name}" do composer da Naiaflow.')

    def _ensure_clone(self) -> None:
        self._wait_clone_pills()
        self._remove_other_clones()
        self._select_clone()
        # trava final: com o clone de outra persona no composer, nao gera (nenhum credito gasto)
        others = self._other_clones_on()
        if others:
            raise RuntimeError(f"O composer da Naiaflow ainda tem o clone {', '.join(others)} junto do {CLONE}: nao gerei nada (nenhum credito gasto).")

    def _select_clone(self) -> None:
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
        """Imagem de referencia no Criar: Adicionar -> Enviar imagem -> Do
        dispositivo. Ela entra no composer como IMG1, ao lado do clone."""
        photo = face_hidden(photo)
        self._close_popovers()
        self.page.get_by_role("button", name=re.compile(r"^\s*Adicionar( clone)?\s*$", re.I)).first.click()
        self.page.wait_for_timeout(1200)
        self.page.get_by_text(re.compile(r"^\s*Enviar imagem\s*$", re.I)).first.click()
        self.page.wait_for_timeout(2500)
        with self.page.expect_file_chooser(timeout=15000) as chooser:
            # 02/11 a Naiaflow trocou "Enviar do dispositivo" por "Do dispositivo"
            self.page.get_by_role("button", name=re.compile(r"(Enviar )?do dispositivo", re.I)).last.click()
        chooser.value.set_files(str(photo))
        self.page.wait_for_timeout(8000)
        self._close_popovers()
        if not self.page.get_by_text(re.compile(r"^\s*IMG1\s*$")).count():
            raise RuntimeError("A foto de referencia nao entrou no composer da Naiaflow (IMG1).")
        log("Referência anexada (IMG1).")

    def generate(self, prompt: str, aspect: str | None = None, reference: Path | None = None, copy_pose: bool = False) -> list[str]:
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
        box.fill((REFERENCE_POSE if reference and copy_pose else "") + prompt + (REFERENCE_IDENTITY if reference else ""))
        self._create_button().click()
        self.page.wait_for_timeout(2500)
        self._check_terms()
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
        self.page.wait_for_timeout(2500)
        self._check_terms()
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

    def _check_terms(self) -> None:
        """11/11: a Naiaflow atualizou os termos (Politica de Privacidade v3) e,
        ate alguem aceitar, o Criar so abre a janela "Atualizamos os nossos termos"
        e nao gera nada. O robo nao aceita termos sozinho: avisa e para o pedido
        (nenhum credito gasto), em vez de esperar 10 minutos calado."""
        try:
            hit = self.page.get_by_text("Atualizamos os nossos termos", exact=False)
            if hit.count() and hit.first.is_visible():
                raise NeedsUser(
                    "A Naiaflow pediu para aceitar os novos termos (Política de Privacidade) e não gera nada antes disso. "
                    "Abra \"Entrar na Naiaflow.bat\" (pasta worker), aceite os termos e religue o robô. Nenhum crédito gasto."
                )
        except NeedsUser:
            raise
        except Exception:
            pass

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
            self._check_terms()
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

    def copy_motion(self, link: str | None, video_file: Path | None, seconds: int, outfit: str | None, dry: bool = False, sound: str = "original", on_wait=None, on_started=None, outfit_file: Path | None = None) -> str:
        """Copiar movimento (Modo imagem -> Copiar movimento): o movimento vem do
        link ou do video enviado, a pessoa e o clone luna, sem som (a musica entra
        no Instagram), com a roupa escolhida da galeria (ou a do video). So gera
        se o custo for o esperado (1 por segundo + 1 do clone). Devolve o link do MP4."""
        p = self.page
        p.goto(NAIAFLOW, wait_until="domcontentloaded")
        p.wait_for_selector("text=Modo imagem", timeout=60000)
        p.wait_for_timeout(2000)
        self._dismiss_overlays()
        # para conferir depois do Gerar se a Naiaflow comecou mesmo (05/11)
        credits_before, busy_before = self.credits(), self._busy_count()
        p.get_by_role("button", name=re.compile(r"Modo imagem")).first.click()
        p.wait_for_timeout(1200)
        # 04/11 a Naiaflow renomeou "Copiar movimento" para "Copiar dancinha"
        p.get_by_text(re.compile(r"Copiar (movimento|dancinha)", re.I)).first.click()
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
        if dur.first.is_enabled():
            dur.first.click()
        else:
            # video mais curto que o pedido (31/10: 4,97 s para 5 s): a Naiaflow
            # desliga o botao; usa a maior duracao ligada abaixo (nunca uma mais cara)
            options = []
            for b in p.get_by_role("button", name=re.compile(r"^\d+s$")).all():
                m = re.fullmatch(r"(\d+)s", b.inner_text().strip())
                if m and b.is_visible() and b.is_enabled() and int(m.group(1)) < seconds:
                    options.append((int(m.group(1)), b))
            if not options:
                raise RuntimeError(f"O vídeo enviado é mais curto que {seconds} s e a Naiaflow não liberou uma duração menor: mande um vídeo de pelo menos {seconds} s.")
            seconds, shorter = max(options, key=lambda o: o[0])
            log(f"O vídeo enviado é mais curto que o pedido: a dancinha sai com {seconds} s ({seconds + 1} créditos).")
            shorter.click()
        # "Som original" mantem a musica da dancinha (na aprovacao do Telegram da para
        # trocar pela musica do Instagram); "Sem som" so se pedido. O custo e conferido
        # no botao Gerar (segundos + 1): se a opcao mudar o custo, o robo nao gera.
        choice = p.get_by_text("Som original", exact=True)
        if sound == "original" and choice.count():
            choice.first.click()
            log("Som: original da dancinha.")
        else:
            p.get_by_text("Sem som", exact=True).first.click()
        p.wait_for_timeout(800)
        p.get_by_role("button", name="Continuar", exact=True).last.click()
        p.wait_for_timeout(5000)
        dialog = p.locator("[role=dialog]").last
        pending = dialog.get_by_text("Selecionar clone", exact=True)
        # 05/11: "Quem vai aparecer" pode demorar a carregar; conferir cedo pulava a
        # escolha da Luna e o Gerar ficava desligado (ou nao comecava). Espera
        # aparecer o "Selecionar clone" ou o clone ja escolhido (botao Roupa).
        chosen = dialog.get_by_role("button", name="Roupa", exact=True)
        for _ in range(30):
            if (pending.count() and pending.first.is_visible()) or (chosen.count() and chosen.last.is_visible()):
                break
            p.wait_for_timeout(2000)
        if pending.count() and pending.first.is_visible():
            pending.first.click()
            p.wait_for_timeout(2500)
            p.locator("[role=dialog]").last.get_by_text(CLONE, exact=True).first.click()
            p.wait_for_timeout(2500)
        for _ in range(5):
            if chosen.count() and chosen.last.is_visible():
                break
            p.wait_for_timeout(2000)
        if (pending.count() and pending.first.is_visible()) or not (chosen.count() and chosen.last.is_visible()):
            raise RuntimeError(f'Nao consegui escolher o clone "{CLONE}" no Copiar movimento.')
        if outfit or outfit_file:
            p.get_by_role("button", name="Roupa", exact=True).last.click()
            p.wait_for_timeout(1500)
            p.get_by_role("menuitem", name="Abrir galeria").first.click()
            p.wait_for_timeout(3500)
            if outfit_file:
                # 05/11: foto da roupa mandada no pedido (gratis: so vale para esta dancinha)
                with p.expect_file_chooser(timeout=15000) as chooser:
                    p.locator("[role=dialog]").last.get_by_role("button", name="Do dispositivo", exact=True).first.click()
                chooser.value.set_files(str(outfit_file))
                p.wait_for_timeout(8000)
            else:
                # nome exato (senao "Biquíni azul" pegava o "Biquíni azul dourado")
                caption = p.get_by_text(re.compile(rf"^\s*{re.escape(outfit)}\s*$", re.I)).first
                if not caption.count():
                    raise RuntimeError(f'Nao achei a roupa "{outfit}" na galeria da Naiaflow.')
                caption.locator("xpath=ancestor::*[.//img][1]").locator("img").first.click()
                p.wait_for_timeout(3000)
            what = "a da foto mandada" if outfit_file else outfit
            if not p.evaluate("() => !!document.querySelector(`[role=dialog] button[aria-label='Roupa'] img`)"):
                raise RuntimeError(f'A roupa ({what}) nao ficou escolhida no Copiar movimento.')
            log(f"Roupa: {what}.")
        # 05/11: sem escolha, a Naiaflow nova inventa roupa e cenario (saiu de blazer
        # numa sala). "Copiar do vídeo" mantem os do video (mesmo custo, conferido).
        for slot in ("Roupa", "Cenário") if not (outfit or outfit_file) else ("Cenário",):
            self._copy_from_video(slot)
        final = p.get_by_role("button", name=re.compile(r"Gerar v[ií]deo", re.I))
        # 05/11: o Gerar fica desligado alguns segundos enquanto a Naiaflow prepara
        # a ultima tela (o robo chegava cedo: "nao ficou ligado", ou clicava logo
        # que ligava e nada comecava). Espera ligar e da mais um tempo.
        for _ in range(30):
            if final.count() == 1 and final.first.is_enabled():
                break
            p.wait_for_timeout(2000)
        if final.count() != 1 or not final.first.is_enabled():
            raise RuntimeError("O botao Gerar video da Naiaflow nao ficou ligado em 1 minuto.")
        p.wait_for_timeout(5000)
        label = final.first.inner_text().replace("\n", " ").strip()
        if not label.endswith(str(seconds + 1)):
            raise RuntimeError(f"Custo inesperado no Copiar movimento ({label}): nao gerei.")
        if dry:  # conferencia sem gastar credito: para antes do Gerar
            return label
        before = set(self._stream_thumbs())
        # 05/11 o Gerar nao comecava nada: guarda as respostas com erro do site
        # logo depois do clique, para o diagnostico
        bad: list[str] = []
        on_resp = lambda r: bad.append(f"{r.status} {r.request.method} {r.url[:120]}") if r.status >= 400 else None  # noqa: E731
        p.on("response", on_resp)
        try:  # clique normal (espera o botao estar pronto); forcado so se nao der
            final.first.click(timeout=15000)
        except Exception:  # noqa: BLE001
            final.first.click(timeout=15000, force=True)
        log(f"Gerando a dancinha ({seconds} s, {seconds + 1} créditos)…")
        p.wait_for_timeout(3000)
        self._check_terms()
        p.remove_listener("response", on_resp)
        self._after_click_report(bad)
        self._wait_preparing()
        if on_started:  # o credito ja foi gasto: a proxima tentativa so procura o video
            on_started(sorted(before))
        # 05/11: o clique no Gerar nao comecou nada (creditos iguais, galeria sem
        # video) e o robo esperou 35 min a toa. Confere em ate 2 min; se nada
        # comecou, para e avisa (o pedido falha e o aviso chega no Telegram).
        if not self._motion_started(credits_before, busy_before, before):
            shot = STUDIO_ROOT / "conteudos" / "_erros" / f"gerar-nao-comecou-{time.strftime('%Y%m%d-%H%M%S')}.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.screenshot(path=str(shot))
            except Exception:  # noqa: BLE001
                pass
            raise NaiaNotStarted(
                "A Naiaflow não começou a dancinha depois do Gerar (nenhum crédito gasto). "
                f"Mande o vídeo de novo. Tela guardada em conteudos/_erros/{shot.name}"
            )
        # 03/11: a de 10 s passou dos 25 min; 5 s levou 12 a 14 min
        minutes = 15 + 4 * seconds
        url = self.find_motion(before, minutes * 60, on_wait, seconds=seconds)
        if url:
            return url
        raise RuntimeError(f"A Naiaflow nao terminou a dancinha em {minutes} minutos.")

    def swap_person(self, link: str | None, video_file: Path | None, seconds: int, sound: str = "original",
                    point: tuple[float, float] | None = None, dry: bool = False, on_wait=None, on_started=None) -> str:
        """Trocar pessoa (06/11, bem melhor que o Copiar dancinha): a cena, a roupa e o
        fundo vem do video e so a pessoa tocada vira a Luna. Passos: busca "trocar" ->
        Enviar video (ou Importar por link) -> trecho e som -> Continuar -> toca no
        video em quem sai (point = fracao x, y do quadro; sem point, o meio) -> clone
        luna -> "Trocar pessoa N" (N = segundos + 1, conferido). Devolve o MP4."""
        p = self.page
        p.goto(NAIAFLOW, wait_until="domcontentloaded")
        p.wait_for_selector("text=Modo imagem", timeout=60000)
        p.wait_for_timeout(2000)
        self._dismiss_overlays()
        credits_before, busy_before = self.credits(), self._busy_count()
        p.get_by_role("button", name=re.compile(r"Modo imagem")).first.click()
        p.wait_for_timeout(1200)
        p.get_by_placeholder(re.compile(r"Filtrar a", re.I)).first.fill("trocar")
        p.wait_for_timeout(1500)
        p.get_by_text(re.compile(r"Trocar (a )?pessoa", re.I)).first.click()
        p.wait_for_timeout(2500)
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
        dur = p.get_by_role("button", name=f"{seconds}s", exact=True)
        if not (dur.count() and dur.first.is_visible()):
            p.get_by_role("button", name="Continuar", exact=True).last.click()
            dur.first.wait_for(state="visible", timeout=90000)
        if dur.first.is_enabled():
            dur.first.click()
        else:
            options = []
            for b in p.get_by_role("button", name=re.compile(r"^\d+s$")).all():
                m = re.fullmatch(r"(\d+)s", b.inner_text().strip())
                if m and b.is_visible() and b.is_enabled() and int(m.group(1)) < seconds:
                    options.append((int(m.group(1)), b))
            if not options:
                raise RuntimeError(f"O vídeo enviado é mais curto que {seconds} s e a Naiaflow não liberou uma duração menor: mande um vídeo de pelo menos {seconds} s.")
            seconds, shorter = max(options, key=lambda o: o[0])
            log(f"O vídeo enviado é mais curto que o pedido: a troca sai com {seconds} s ({seconds + 1} créditos).")
            shorter.click()
        choice = p.get_by_text("Som original" if sound == "original" else "Sem som", exact=True)
        if choice.count():
            choice.first.click()
        p.wait_for_timeout(800)
        p.get_by_role("button", name="Continuar", exact=True).last.click()
        dialog = p.locator("[role=dialog]").last
        frame = dialog.locator("video").first
        frame.wait_for(state="visible", timeout=90000)
        # 06/11: a previa demora a aceitar o toque (tocar logo nao marcava ninguem)
        p.wait_for_timeout(10000)
        # quem sai: toca no quadro do video no ponto da pessoa escolhida; aparece o
        # marcador "1" e o menu "Selecionar clone / Usar personagem"
        box = frame.bounding_box()
        if not box:
            raise RuntimeError("Não achei o vídeo para tocar em quem vai sair.")
        px, py = point or (0.5, 0.45)
        menu = p.get_by_text("Selecionar clone", exact=True)
        for _ in range(3):
            p.mouse.move(box["x"] + box["width"] * px, box["y"] + box["height"] * py)
            p.wait_for_timeout(400)
            p.mouse.down()
            p.wait_for_timeout(120)
            p.mouse.up()
            for _ in range(8):
                if menu.count() and menu.last.is_visible():
                    break
                p.wait_for_timeout(1000)
            if menu.count() and menu.last.is_visible():
                break
            p.wait_for_timeout(4000)
        if not (menu.count() and menu.last.is_visible()):
            raise RuntimeError("A Naiaflow não marcou a pessoa no toque (nenhum crédito gasto).")
        log(f"Quem sai: toquei no vídeo em {px:.2f} x {py:.2f}.")
        menu.last.click()
        p.wait_for_timeout(3000)
        hit = p.get_by_text(CLONE, exact=True)
        if not hit.count():
            raise RuntimeError(f'Não achei o clone "{CLONE}" na lista do Trocar pessoa.')
        # roupa fica "Manter original" (a do video)
        hit.last.click()
        p.wait_for_timeout(4000)
        # na pagina toda: o menu da Luna (Roupa / Mudar / Remover) tambem e um dialogo
        final = p.get_by_role("button", name=re.compile(r"Trocar pessoa", re.I))
        for _ in range(30):
            if final.count() and final.last.is_enabled():
                break
            p.wait_for_timeout(2000)
        shot = STUDIO_ROOT / "conteudos" / "_erros" / f"trocar-pessoa-{time.strftime('%Y%m%d-%H%M%S')}.png"
        if not (final.count() and final.last.is_enabled()):
            shot.parent.mkdir(parents=True, exist_ok=True)
            p.screenshot(path=str(shot))
            raise RuntimeError(f"O botão Trocar pessoa não ligou depois de tocar em quem sai (nenhum crédito gasto). Tela em conteudos/_erros/{shot.name}")
        p.wait_for_timeout(3000)
        label = final.last.inner_text().replace("\n", " ").strip()
        if not label.endswith(str(seconds + 1)):
            raise RuntimeError(f"Custo inesperado no Trocar pessoa ({label}): não gerei.")
        if dry:  # conferencia sem gastar credito: para antes do clique
            shot.parent.mkdir(parents=True, exist_ok=True)
            p.screenshot(path=str(shot))
            return label
        before = set(self._stream_thumbs())
        final.last.click(timeout=15000)
        log(f"Trocando a pessoa ({seconds} s, {seconds + 1} créditos)…")
        p.wait_for_timeout(3000)
        self._wait_preparing()
        if on_started:  # o credito ja foi gasto: a proxima tentativa so procura o video
            on_started(sorted(before))
        if not self._motion_started(credits_before, busy_before, before):
            raise NaiaNotStarted("A Naiaflow não começou a troca depois do clique (nenhum crédito gasto). Mande o vídeo de novo.")
        minutes = 15 + 4 * seconds
        url = self.find_motion(before, minutes * 60, on_wait, seconds=seconds)
        if url:
            return url
        raise RuntimeError(f"A Naiaflow não terminou a troca em {minutes} minutos.")

    def list_outfits(self) -> list[str]:
        """Nomes das roupas em "Minhas roupas" da Naiaflow (05/11: a lista do
        Telegram e do estudio vem daqui). So le: Adicionar -> Enviar imagem ->
        Roupas, e fecha com Esc."""
        p = self.page
        p.goto(NAIAFLOW, wait_until="domcontentloaded")
        p.wait_for_selector("text=Modo imagem", timeout=60000)
        p.wait_for_timeout(2500)
        self._dismiss_overlays()
        p.get_by_role("button", name=re.compile(r"^\s*Adicionar( clone)?\s*$", re.I)).first.click()
        p.get_by_text(re.compile(r"^\s*Enviar imagem\s*$", re.I)).first.click(timeout=10000)
        p.get_by_text("Roupas", exact=True).first.click(timeout=15000)
        p.get_by_text("Minhas roupas", exact=True).first.wait_for(state="visible", timeout=15000)
        p.wait_for_timeout(2000)
        # o texto tem as abas ("Minhas roupas | 17 | Casual | 33 ...") e depois as
        # secoes; a secao "Minhas roupas | 17" e seguida dos nomes ate o titulo da
        # proxima secao (um nome seguido de numero)
        lines = [s.strip() for s in p.locator("body").inner_text().split("\n") if s.strip()]
        p.keyboard.press("Escape")
        heads = [k for k in range(len(lines) - 1) if lines[k] == "Minhas roupas" and lines[k + 1].isdigit()]
        names: list[str] = []
        for k in heads:
            found: list[str] = []
            j = k + 2
            while j < len(lines) and not (j + 1 < len(lines) and lines[j + 1].isdigit()) and not lines[j].isdigit():
                found.append(lines[j])
                j += 1
            if len(found) > len(names):  # a lista de abas da 0 nomes; a secao, os nomes
                names = found
        seen: list[str] = []
        for n in names:
            if n not in seen and len(n) <= 60:
                seen.append(n)
        return seen

    def _copy_from_video(self, slot: str) -> None:
        """Roupa ou Cenario do Copiar dancinha -> "Copiar do vídeo"; confere que o
        botao ficou com a imagem (escolhido)."""
        p = self.page
        p.locator("[role=dialog]").last.get_by_role("button", name=slot, exact=True).last.click()
        p.wait_for_timeout(1500)
        p.get_by_role("menuitem", name="Copiar do vídeo").first.click()
        for _ in range(10):
            p.wait_for_timeout(1500)
            if p.locator("[role=dialog]").last.locator(f"button[aria-label='{slot}'] img").count():
                log(f"{slot}: a do vídeo.")
                return
        raise RuntimeError(f'"{slot}: Copiar do vídeo" não ficou escolhido na Naiaflow (não gerei).')

    def _after_click_report(self, bad: list[str]) -> None:
        """Foto da tela 3 s depois do Gerar (antes de recarregar) e o que ficou
        aberto por cima: janela, botoes, erros do site. Vai para conteudos/_erros
        e para o log, para achar por que a Naiaflow nao comeca."""
        p = self.page
        try:
            shot = STUDIO_ROOT / "conteudos" / "_erros" / f"depois-do-gerar-{time.strftime('%Y%m%d-%H%M%S')}.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            p.screenshot(path=str(shot))
            dialogs = [d for d in p.locator("[role=dialog], [role=alertdialog]").all() if d.is_visible()]
            if dialogs:
                top = dialogs[-1]
                buttons = [b.inner_text().replace("\n", " ").strip()[:30] for b in top.get_by_role("button").all() if b.is_visible()]
                log(f"Depois do Gerar: janela aberta | botões: {[b for b in buttons if b]} | texto: {top.inner_text()[:250].replace(chr(10), ' | ')}")
            else:
                log("Depois do Gerar: a janela fechou.")
            if bad:
                log(f"Depois do Gerar: respostas com erro do site: {bad[:5]}")
            log(f"Tela de depois do Gerar: conteudos/_erros/{shot.name}")
        except Exception as e:  # noqa: BLE001
            log(f"Depois do Gerar: não consegui guardar a tela ({e}).")

    def _wait_preparing(self, wait_s: int = 600) -> None:
        """05/11: depois do Gerar a Naiaflow prepara o trecho NO NAVEGADOR
        ("Preparando o trecho · 3 de 5 s · 18%") e so depois manda o pedido.
        Recarregar a pagina nesse meio cancelava tudo (nada comecava, nenhum
        credito gasto). Espera a preparacao acabar sem sair da pagina."""
        p = self.page
        prep = p.get_by_text(re.compile(r"Preparando", re.I))
        deadline = time.time() + wait_s
        next_log = 0.0
        while time.time() < deadline:
            try:
                visible = [t for t in prep.all() if t.is_visible()]
            except Exception:  # noqa: BLE001
                visible = []
            if not visible:
                p.wait_for_timeout(3000)  # o envio pode ainda estar terminando
                return
            if time.time() >= next_log:  # progresso no log a cada 30 s
                log(f"Naiaflow: {visible[0].inner_text().strip()[:80]}")
                next_log = time.time() + 30
            p.wait_for_timeout(2000)
        log("A preparação do trecho na Naiaflow passou de 10 minutos; sigo conferindo.")

    def _busy_count(self) -> int:
        # cartoes "Gerando"/"Na fila"/"Processando" na galeria
        try:
            return len(re.findall(r"\b(Gerando|Na fila|Processando)\b", self.page.locator("body").inner_text(timeout=5000), re.I))
        except Exception:  # noqa: BLE001
            return 0

    def _motion_started(self, credits_before: int | None, busy_before: int, thumbs_before: set[str], wait_s: int = 120) -> bool:
        """Depois do Gerar: a Naiaflow comecou? Sim se os creditos baixaram, se
        apareceu um "Gerando" a mais ou um video novo na galeria. Sem como ler os
        creditos, supoe que sim (nunca arrisca gerar e pagar duas vezes)."""
        p = self.page
        p.wait_for_timeout(5000)
        # aviso da Naiaflow logo depois do clique (erro, limite...) vai para o log
        try:
            toasts = p.locator("[role=alert], [role=status], [data-sonner-toast]")
            msgs = [t.inner_text().strip() for t in toasts.all() if t.is_visible() and t.inner_text().strip()]
            if msgs:
                log(f"Aviso da Naiaflow depois do Gerar: {' | '.join(msgs)[:300]}")
        except Exception:  # noqa: BLE001
            pass
        if credits_before is None:
            return True
        deadline = time.time() + wait_s
        while True:
            try:
                credits = self.credits()
                if credits is not None and credits < credits_before:
                    return True
                if self._busy_count() > busy_before or set(self._stream_thumbs()) - thumbs_before:
                    return True
            except Exception:  # noqa: BLE001
                pass
            if time.time() > deadline:
                return False
            p.wait_for_timeout(15000)
            try:
                p.goto(NAIAFLOW, wait_until="domcontentloaded")
                p.wait_for_selector("text=Modo imagem", timeout=60000)
                p.wait_for_timeout(4000)
            except Exception:  # noqa: BLE001
                pass

    def find_motion(self, before: set[str], wait_s: int, on_wait=None, seconds: int | None = None) -> str | None:
        """Espera o video novo (que nao estava na galeria antes do Gerar) e devolve o MP4.
        03/11: a galeria pode mostrar videos antigos que nao estavam na tela no
        "antes" (pegou um trecho +18 de 5 s no lugar da dancinha de 8 s): cada video
        candidato e baixado e so vale se tiver a duracao pedida (+-1 s).
        04/11: so o PRIMEIRO video da galeria (o mais novo) e so se for uma geracao
        (a pagina tem "Reutilizar" / "Antes / Depois"); o video de referencia que o
        robo envia tambem entra na galeria, mas sem esses botoes."""
        p = self.page
        before = set(before)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            time.sleep(15)
            if on_wait:
                on_wait()
            try:
                p.goto(NAIAFLOW, wait_until="domcontentloaded")
                p.wait_for_timeout(4000)
                thumbs = self._stream_thumbs()
                if not thumbs or thumbs[0] in before:
                    continue
                first = thumbs[0]
                p.locator(f"img[src='{first}']").first.click()
                p.wait_for_timeout(6000)
                generated = p.get_by_role("button", name=re.compile(r"Reutilizar|Antes.{1,3}Depois", re.I)).count() > 0
                if not generated:
                    continue  # referencia enviada no topo: a geracao ainda nao terminou
                srcs = p.eval_on_selector_all("video, video source", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
                mp4 = next((x for x in srcs if x.startswith("http") and x.endswith(".mp4")), None)
                if mp4:
                    length = video_seconds_at(mp4) if seconds else None
                    if length is not None and abs(length - seconds) > 1.0:
                        log(f"Vídeo da galeria com {length:.1f} s (pedido {seconds} s): não é este, sigo esperando.")
                        before.add(first)
                        continue
                    return mp4
            except Exception:
                continue
        return None

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

def video_seconds_at(url: str) -> float | None:
    """Baixa o MP4 (temporario) e devolve a duracao; None se nao der para ler."""
    import imageio_ffmpeg
    tmp = STUDIO_ROOT / "conteudos" / "_nota" / "candidato.mp4"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        tmp.write_bytes(r.content)
        return imageio_ffmpeg.count_frames_and_secs(str(tmp))[1]
    except Exception:  # noqa: BLE001
        return None
    finally:
        tmp.unlink(missing_ok=True)


def reference_frames(video: Path, count: int = 6) -> list[str]:
    """Quadros espalhados do video de referencia (JPEG pequeno em data URL) para a nota."""
    import imageio_ffmpeg
    _, secs = imageio_ffmpeg.count_frames_and_secs(str(video))
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    out = []
    for i in range(count):
        t = max(0.0, secs * (i + 0.5) / count)
        r = subprocess.run([ff, "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=512:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                           capture_output=True, check=True)
        if r.stdout:
            out.append("data:image/jpeg;base64," + base64.b64encode(r.stdout).decode())
    return out


def rate_reference(studio: Studio, item: dict, video: Path | None) -> bool:
    """Nota de 0 a 100 do video de referencia antes de gastar credito (03/11).
    True = pode gerar; False = nota baixa, a decisao ficou no Telegram."""
    if not video or not video.exists():
        log("Nota da referência: vídeo por link, não deu para analisar antes (segue sem nota).")
        return True
    try:
        res = studio.call("rate-ref", id=item["id"], frames=reference_frames(video))
    except Exception as e:  # sem nota nao trava a dancinha
        log(f"Nota da referência: não consegui avaliar ({e}); segue sem nota.")
        return True
    log(f"Nota prevista da referência: {res.get('score')}/100.")
    if res.get("go"):
        return True
    log("Nota baixa: não gerei (nenhum crédito gasto). Você decide no Telegram.")
    return False


def rate_orders(studio: Studio, test_video: Path | None = None) -> None:
    """Nota dos videos mandados no Telegram, antes do Gerar (03/11): baixa, tira os
    quadros e devolve; a nota chega no bot. Link sem yt-dlp: avisa que nao deu."""
    order = studio.call("ref-pending").get("order")
    if not order:
        return
    tmp = STUDIO_ROOT / "conteudos" / "_nota"
    tmp.mkdir(parents=True, exist_ok=True)
    video = tmp / f"{order['id']}.mp4"
    try:
        if test_video:
            shutil.copyfile(test_video, video)
        elif order.get("file_url"):
            download_video(order["file_url"], video, 300)
        elif order.get("link"):
            try:
                import yt_dlp
            except ImportError:
                studio.call("ref-rated", order_id=order["id"], error="link sem nota (o robô precisa do yt-dlp para baixar o vídeo do link)")
                return
            with yt_dlp.YoutubeDL({"outtmpl": str(video), "format": "mp4/best", "quiet": True, "noprogress": True, "overwrites": True}) as ydl:
                ydl.download([order["link"]])
        else:
            studio.call("ref-rated", order_id=order["id"], error="sem o vídeo para analisar")
            return
        res = studio.call("ref-rated", order_id=order["id"], frames=reference_frames(video))
        log(f"Nota do vídeo do pedido {order['id']}: {res.get('score')}/100.")
    except Exception as e:  # noqa: BLE001
        log(f"Nota do vídeo do pedido {order['id']}: não consegui ({e}).")
        try:
            studio.call("ref-rated", order_id=order["id"], error="não consegui baixar ou analisar o vídeo")
        except Exception:  # noqa: BLE001
            pass
    finally:
        video.unlink(missing_ok=True)


def outfit_photo(m: dict, folder: Path, item: dict) -> Path | None:
    """Foto da roupa mandada no pedido (05/11): baixa para entrar na Roupa da dancinha."""
    url = m.get("outfit_url")
    if not url:
        return None
    dest = folder / f"roupa-{item['id'][:8]}.jpg"
    if dest.exists() and dest.stat().st_size > 1000:  # video longo: uma vez para todos os trechos
        return dest
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def sync_outfits(studio: Studio, naia: Naiaflow) -> None:
    """Manda os nomes de "Minhas roupas" da Naiaflow para o estudio (a lista do
    Telegram e do estudio). Falhar aqui nao para o robo."""
    try:
        names = naia.list_outfits()
        if names:
            studio.call("outfits", names=names)
            log(f"Roupas da galeria: {len(names)} (lista do Telegram e do estúdio atualizada).")
    except Exception as e:  # noqa: BLE001
        log(f"Não consegui ler as roupas da galeria: {str(e)[:150]}")


def process_motion(studio: Studio, item: dict, naia: Naiaflow | None, test_video: Path | None) -> None:
    """Dancinha: video da Naiaflow (ou o de teste) -> quadro em JPEG para a
    biblioteca -> Reels e TikTok com o mesmo MP4."""
    m = item["motion"]
    if m.get("long"):
        process_long_motion(studio, item, naia, test_video)
        return
    log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): dancinha {m.get('seconds')} s pelo {'link' if m.get('link') else 'video enviado'}"
        + (f", roupa {m['outfit']}" if m.get("outfit") else ""))
    folder = STUDIO_ROOT / item["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    mp4 = folder / item["file"].replace(".jpg", ".mp4")
    # 03/11: a dancinha ja pedida (credito gasto) fica anotada; nova tentativa so
    # procura o video na Naiaflow, nunca gera (e paga) de novo
    marker = mp4.with_name(mp4.stem + ".naia.json")
    ready = mp4.exists() and mp4.stat().st_size > 10000
    source = None
    if not ready and not marker.exists():
        if m.get("file_url") and not test_video:
            source = folder / f"referencia-{item['id'][:8]}{Path(m.get('file_name') or 'video.mp4').suffix}"
            download_video(m["file_url"], source, 300)
        # nota do video antes de gastar credito; "gerar mesmo assim" no Telegram pula
        # (a nota ja dada no pedido pelo Telegram tambem: rated)
        if not m.get("force") and m.get("rated") is None and not rate_reference(studio, item, test_video or source):
            return
    if ready:
        log(f"Dancinha ja estava pronta: {mp4.name} (nao gasta credito de novo).")
    elif test_video:
        mp4.write_bytes(test_video.read_bytes())
    else:
        if marker.exists():
            before = json.loads(marker.read_text(encoding="utf-8")).get("before")
            if before is None:
                raise RuntimeError("Dancinha ja pedida na Naiaflow: confira la e baixe o video. Nao gerei de novo para nao gastar creditos.")
            log("Dancinha ja pedida antes: procurando o video pronto na Naiaflow (sem gastar credito).")
            url = naia.find_motion(set(before), 20 * 60)
            if not url:
                raise RuntimeError("A dancinha pedida antes ainda nao apareceu na Naiaflow. Nao gerei de novo para nao gastar creditos.")
        else:
            started = lambda before: marker.write_text(json.dumps({"before": before, "at": time.time()}), encoding="utf-8")  # noqa: E731
            try:
                if m.get("mode") == "trocar":
                    pt = m.get("point") or {}
                    point = (float(pt["x"]), float(pt["y"])) if "x" in pt and "y" in pt else None
                    url = naia.swap_person(m.get("link"), source, int(m.get("seconds") or 5), sound=m.get("sound") or "original", point=point, on_started=started)
                else:
                    url = naia.copy_motion(m.get("link"), source, int(m.get("seconds") or 5), m.get("outfit"), sound=m.get("sound") or "original", on_started=started, outfit_file=outfit_photo(m, folder, item))
            except NaiaNotStarted:
                # nada foi pago: a proxima tentativa pode gerar de novo
                marker.unlink(missing_ok=True)
                raise
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        mp4.write_bytes(r.content)
        marker.unlink(missing_ok=True)
        if source and source.exists():
            source.unlink()
    import imageio_ffmpeg
    # o Instagram recusa Reels sem trilha de audio (ERROR no conteiner, 30/10):
    # 1080x1920 com o som da dancinha (Som original) ou, sem som, com trilha muda
    raw = mp4.with_name(mp4.stem + "-naia.mp4")
    if not raw.exists():
        mp4.replace(raw)
        reader = imageio_ffmpeg.read_frames(str(raw))
        has_audio = bool(next(reader).get("audio_codec"))
        reader.close()
        audio = ["-map", "0:v:0", "-map", "0:a:0"] if has_audio else [
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        if has_audio:
            log("Dancinha com o som original.")
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(raw), *audio,
             "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(mp4)],
            check=True,
        )
        raw.unlink()
    send_motion(studio, item, folder, mp4, "Dancinha salva")


def send_motion(studio: Studio, item: dict, folder: Path, mp4: Path, what: str) -> None:
    """Video pronto: quadro em JPEG para a biblioteca, cadastra no estudio e sobe
    uma copia para cada publicacao (que chega no Telegram para aprovar)."""
    import imageio_ffmpeg

    # a foto da roupa ja foi usada: nao fica na pasta (nem entra na biblioteca)
    (folder / f"roupa-{item['id'][:8]}.jpg").unlink(missing_ok=True)
    jpg = folder / item["file"]
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", "1", "-i", str(mp4), "-frames:v", "1", "-q:v", "3", str(jpg)], check=True)
    local_path = f"{item['folder']}/{item['file']}"
    video_path = f"{item['folder']}/{mp4.name}"
    data = mp4.read_bytes()
    log(f"{what} em {video_path}")
    done = studio.call("done", id=item["id"], local_path=local_path, size_bytes=jpg.stat().st_size, video_path=video_path, video_size=len(data))
    for t in done["targets"]:
        up = studio.call("upload-url", id=item["id"], post_id=t["post_id"], version=t["version"], size_bytes=len(data), mime_type="video/mp4")
        put_copy(up["url"], data, "video/mp4")
    sent = studio.call("uploaded", id=item["id"])
    log(f"Mandada para você aprovar no Telegram ({sent['sent']} publicação(ões)).")


LONG_SEGMENT_S = 5
LONG_CREDITS_PER_SEGMENT = LONG_SEGMENT_S + 1  # 1 por segundo + 1 do clone
TELEGRAM_VIDEO_MAX = 18 * 1024 * 1024  # o Telegram so busca video por link ate 20 MB


def video_seconds(path: Path) -> float:
    import imageio_ffmpeg

    reader = imageio_ffmpeg.read_frames(str(path))
    meta = next(reader)
    reader.close()
    return float(meta.get("duration") or 0)


def has_audio(path: Path) -> bool:
    import imageio_ffmpeg

    reader = imageio_ffmpeg.read_frames(str(path))
    meta = next(reader)
    reader.close()
    return bool(meta.get("audio_codec"))


def run_ff(args: list[str]) -> None:
    """ffmpeg com o erro dele na mensagem (para o log e o aviso no Telegram)."""
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {(r.stderr or '').strip()[-400:]}")


def process_long_motion(studio: Studio, item: dict, naia: Naiaflow | None, test_video: Path | None) -> None:
    """Video longo +18 (31/10): corta o video em trechos de 5 s, faz o Copiar
    movimento da luna em cada um (o trecho pronto fica salvo: se cair no meio,
    nao gasta credito de novo), emenda tudo e poe o som do video original
    inteiro por cima. O ultimo trecho comeca 5 s antes do fim e a emenda tira a
    parte repetida."""
    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    m = item["motion"]
    approved = int(m.get("segments") or 0)
    start = float(m.get("start") or 0)
    length = float(m.get("length") or 0)
    log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): vídeo longo +18 em {approved} trechos de {LONG_SEGMENT_S} s"
        + (f", do {start:g} ao {start + length:g} s do vídeo" if length else "")
        + (f", roupa {m['outfit']}" if m.get("outfit") else ""))
    folder = STUDIO_ROOT / item["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    mp4 = folder / item["file"].replace(".jpg", ".mp4")
    if mp4.exists() and mp4.stat().st_size > 10000:
        log(f"Vídeo longo já estava pronto: {mp4.name} (não gasta crédito de novo).")
        send_motion(studio, item, folder, mp4, "Vídeo longo salvo")
        return
    parts = folder / f"trechos-{item['id'][:8]}"
    parts.mkdir(exist_ok=True)
    source = parts / f"original{Path(m.get('file_name') or 'video.mp4').suffix}"
    if not source.exists():
        download_video(m["file_url"], source, 600)
    if length:
        # so um pedaco do video (02/11): corta antes, com o som, e segue como um video curto
        piece = parts / "pedaco.mp4"
        if not piece.exists():
            run_ff([ff, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{length:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", str(piece)])
        source = piece
    total = video_seconds(source)
    count = max(1, math.ceil(total / LONG_SEGMENT_S - 0.02))
    if total < LONG_SEGMENT_S:
        raise RuntimeError(f"O vídeo tem {total:.1f} s: precisa de pelo menos {LONG_SEGMENT_S} s.")
    if count > approved:
        # nunca gasta mais do que o administrador confirmou no estudio
        raise RuntimeError(f"O vídeo tem {total:.1f} s ({count} trechos), mas foram aprovados {approved}: mande de novo pelo estúdio.")
    starts = [min(i * LONG_SEGMENT_S, max(0.0, total - LONG_SEGMENT_S)) for i in range(count)]
    clip = lambda i: parts / f"luna-{i + 1:02d}.mp4"  # noqa: E731
    missing = [i for i in range(count) if not (clip(i).exists() and clip(i).stat().st_size > 10000)]
    if missing and naia and not test_video:
        have = naia.credits()
        need = len(missing) * LONG_CREDITS_PER_SEGMENT
        if have is not None and have < need + MIN_CREDITS:
            raise RuntimeError(f"Faltam créditos na Naiaflow: o vídeo precisa de {need} ({len(missing)} trechos) e tem {have}. Os trechos prontos ficam guardados.")
    last_beat = [0.0]

    def beat() -> None:
        # segura o item como "gerando" no estudio enquanto trabalha (a cada 5 min)
        if time.time() - last_beat[0] > 300:
            last_beat[0] = time.time()
            try:
                studio.call("progress", id=item["id"])
            except Exception:  # noqa: BLE001
                pass

    for i in missing:
        beat()
        cut = parts / f"corte-{i + 1:02d}.mp4"
        run_ff([ff, "-y", "-loglevel", "error", "-ss", f"{starts[i]:.3f}", "-i", str(source), "-t", str(LONG_SEGMENT_S),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(cut)])
        if test_video:
            clip(i).write_bytes(test_video.read_bytes())
        else:
            log(f"Trecho {i + 1} de {count} na Naiaflow…")
            url = naia.copy_motion(None, cut, LONG_SEGMENT_S, m.get("outfit"), sound="sem", on_wait=beat, outfit_file=outfit_photo(m, folder, item))
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            clip(i).write_bytes(r.content)
        cut.unlink(missing_ok=True)
        log(f"Trecho {i + 1} de {count} pronto.")
    # emenda: cada trecho entra a partir de onde o anterior parou (o ultimo
    # tira a parte repetida), tudo em 720x1280 30 fps, sem som
    joined = parts / "emendado.mp4"
    listing = parts / "lista.txt"
    pieces = []
    covered = 0.0
    for i in range(count):
        skip = max(0.0, covered - starts[i])
        length = min(starts[i] + LONG_SEGMENT_S, total) - max(covered, starts[i])
        if length <= 0.05:
            continue
        piece = parts / f"parte-{i + 1:02d}.mp4"
        run_ff([ff, "-y", "-loglevel", "error", "-ss", f"{skip:.3f}", "-i", str(clip(i)), "-t", f"{length:.3f}",
                        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=30",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(piece)])
        pieces.append(piece)
        covered = starts[i] + LONG_SEGMENT_S
    # caminho inteiro com / (o concat do ffmpeg nao achava o trecho na pasta "+18")
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in pieces), encoding="utf-8", newline="\n")
    run_ff([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])
    # som do video original inteiro; video que cabe no Telegram (ate ~18 MB)
    kbps = max(500, min(4000, int(TELEGRAM_VIDEO_MAX * 8 / 1024 / max(total, 1)) - 160))
    audio = ["-map", "0:v:0", "-map", "1:a:0"] if has_audio(source) else [
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-map", "0:v:0", "-map", "2:a:0"]
    run_ff([ff, "-y", "-loglevel", "error", "-i", str(joined), "-i", str(source), *audio, "-shortest",
                    "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(mp4)])
    log(f"Vídeo longo emendado: {len(pieces)} trechos, {video_seconds(mp4):.1f} s, {mp4.stat().st_size / 1024 / 1024:.1f} MB, com o som original.")
    send_motion(studio, item, folder, mp4, "Vídeo longo salvo")
    shutil.rmtree(parts, ignore_errors=True)


def process(studio: Studio, item: dict, naia: Naiaflow | None, test_image: Path | None) -> None:
    edit = item.get("edit")
    if edit:
        log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): correção da foto {edit['source']}: {edit['instruction'][:90]}")
    else:
        log(f"Item {item['id'][:8]} (tentativa {item['attempt']}): {item['prompt'][:90]}")
    folder = STUDIO_ROOT / item["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    # foto de referencia mandada na conversa (vai como IMG1 no Criar); album do
    # Telegram (01/11): 2 ou 3 fotos parecidas, uma por Criar, na ordem
    references: list[Path] = []
    ref_urls = item.get("reference_urls") or ([item["reference_url"]] if item.get("reference_url") else [])
    if ref_urls and not test_image:
        for k, ref_url in enumerate(ref_urls):
            ref_k = folder / f"referencia-{item['id'][:8]}{'' if k == 0 else f'-{k + 1}'}.jpg"
            r = requests.get(ref_url, timeout=120)
            r.raise_for_status()
            ref_k.write_bytes(r.content)
            references.append(ref_k)
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
                # um Criar a cada 3 fotos: o 1o com a 1a referencia, o 2o com a 2a...
                reference = references[(i // 3) % len(references)] if references else None
                if len(references) > 1:
                    log(f"Criar {i // 3 + 1} com a referência {references.index(reference) + 1}/{len(references)}")
                urls = naia.generate(prompt, item.get("aspect"), reference, copy_pose=(i == 0))
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
    for ref_k in references:
        if ref_k.exists():
            ref_k.unlink()
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


_blur_off = False


def blur_previews(studio: "Studio") -> None:
    """Previas do bot de vendas: borra as partes intimas (NudeNet, gratis, no PC)
    do que estiver em conteudos/vendas/previa-borrar e do que foi pedido pelo
    📣 do Telegram, poe em vendas/previa e manda para aprovar no Telegram (so
    entra na chamada depois de aprovada)."""
    global _blur_off
    if _blur_off:
        return
    try:
        import borrar
    except ImportError as e:  # NudeNet nao instalado: avisa uma vez e segue sem borrar
        _blur_off = True
        log(f"Borrar prévias desligado (falta instalar: pip install nudenet). {e}")
        return
    conteudos = STUDIO_ROOT / "conteudos"
    entrada = conteudos / "vendas" / "previa-borrar"
    try:
        tasks = studio.call("blur-next").get("tasks", [])
    except Exception as e:  # estudio antigo ou fora do ar: so a pasta
        tasks = []
        if "desconhecida" not in str(e):
            log(f"Fila do borrão indisponível: {e}")
    for t in tasks:
        lp = str(t.get("local_path", ""))
        src = STUDIO_ROOT / lp if lp.startswith("conteudos/") else conteudos / lp
        if ".." in lp or not src.is_file():
            log(f"📣 Não achei {lp} no PC para borrar.")
            continue
        entrada.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, entrada / src.name)  # mantem a data: o borrao pega no mesmo ciclo
    if not entrada.is_dir():
        return
    for dst, found in borrar.processar_pasta(conteudos, log):
        if not found:
            # sem parte intima achada: fica na pasta para conferir, nao entra sozinha na chamada
            try:
                studio.call("notice", text=f"📣 Não achei partes íntimas em {dst.name}: ficou em vendas/previa para você conferir (não entrou sozinha na chamada).")
            except Exception:
                pass
            continue
        try:
            data = dst.read_bytes()
            mime = "video/mp4" if dst.suffix.lower() == ".mp4" else "image/jpeg"
            up = studio.call("preview-url", local_path=dst.relative_to(conteudos).as_posix(), mime_type=mime, size_bytes=len(data))
            put_copy(up["url"], data, mime)
            studio.call("preview-add", media_id=up["media_id"], path=up["path"], name=dst.name)
            log(f"📣 Prévia borrada mandada para aprovar no Telegram: vendas/previa/{dst.name}")
        except Exception as e:  # a previa borrada fica na pasta; da para mandar pela tela de Previas
            log(f"Não consegui pôr {dst.name} na chamada: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Luna Studio - automacao local da Naiaflow")
    ap.add_argument("--uma-vez", action="store_true", help="processa a fila e sai")
    ap.add_argument("--imagem-teste", type=Path, help="usa esta imagem em vez de gerar na Naiaflow (teste)")
    ap.add_argument("--video-teste", type=Path, help="usa este video em vez da dancinha da Naiaflow (teste)")
    args = ap.parse_args()

    url, token = load_config()
    studio = Studio(url, token)
    # start: o que ficou "gerando" quando o robo foi fechado no meio volta para a fila
    pong = studio.call("ping", start=True)
    # as personas que o estudio conhece (estudio antigo, sem a lista: so a Luna)
    personas = [p for p in (pong.get("personas") or []) if p.get("id") and p.get("clone")] or [LUNA]
    personas = [{**LUNA, **p} if p["id"] == "luna" else {"hair": "do clone", **p} for p in personas]
    studios = {p["id"]: (studio if p["id"] == "luna" else Studio(url, token, p["id"])) for p in personas}
    queue = pong["queue"]
    released = pong.get("released") or 0
    for p in personas:
        if p["id"] != "luna":
            other = studios[p["id"]].call("ping", start=True)
            queue += other.get("queue") or 0
            released += other.get("released") or 0
    names = ", ".join(p["name"] for p in personas)
    log(f"Conectado ao estúdio ({url}). Personas: {names}. Itens na fila: {queue}.")
    if released:
        log(f"{released} pedido(s) que ficaram no meio quando o robô foi fechado voltaram para a fila.")
    naia: Naiaflow | None = None
    try:
        if not (args.imagem_teste or args.video_teste):
            naia = Naiaflow()
            naia.open()
            log("Naiaflow pronta.")
            sync_outfits(studio, naia)
        last_sync = time.time()
        while True:
            if naia:
                credits = naia.credits()
                if credits is not None and credits < MIN_CREDITS:
                    log(f"Só {credits} crédito(s) na Naiaflow: parei para não zerar. Compre créditos e reinicie.")
                    if args.uma_vez:
                        break
                    time.sleep(10 * QUEUE_POLL_S)
                    continue
            # erro do estudio (ou sem internet) nao derruba o robo: espera e tenta de novo.
            # Cada persona na vez dela (a Luna primeiro); o primeiro pedido achado e o feito.
            item = None
            failed = False
            for p in personas:
                use_persona(p, personas)
                studio = studios[p["id"]]
                try:
                    blur_previews(studio)
                    rate_orders(studio, args.video_teste)
                    item = studio.call("next")["item"]
                except Exception as e:  # noqa: BLE001
                    log(f"Não consegui pegar o próximo pedido{'' if p['id'] == 'luna' else ' da ' + p['name']}: {e}")
                    failed = True
                    continue
                if item:
                    if p["id"] != "luna":
                        log(f"Pedido da {p['name']} (clone {p['clone']}, pasta {STUDIO_ROOT.name}).")
                    break
            if not item and failed:
                if args.uma_vez:
                    break
                time.sleep(QUEUE_POLL_S)
                continue
            if not item:
                if args.uma_vez:
                    log("Fila vazia: terminei.")
                    break
                # roupas novas na galeria aparecem no Telegram e no estudio (a cada 6 h)
                if naia and time.time() - last_sync > 6 * 3600:
                    use_persona(personas[0], personas)
                    sync_outfits(studios[personas[0]["id"]], naia)
                    last_sync = time.time()
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
