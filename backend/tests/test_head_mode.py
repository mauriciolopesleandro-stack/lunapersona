"""Modo "Luna na foto": troca so da cabeca. Composicao (fora da cabeca identico), escolha da referencia por angulo e
sorriso, e o servico escolhendo a candidata mais parecida com as fotos da Luna."""
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.services import head_mode as hs
from app.core.head.compose import compor
from tests.conftest import REPO

CFG = json.loads((REPO / "config" / "head_mode.json").read_text(encoding="utf-8"))


def cena(seed=1):
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur((rng.random((400, 300, 3)) * 255).astype(np.uint8), (0, 0), 2)  # fundo com textura
    pessoa = np.zeros((400, 300), bool)
    cv2.ellipse(img, (150, 120), (20, 27), 0, 0, 360, (90, 140, 200), -1)  # cabeca (BGR)
    cv2.rectangle(img, (115, 160), (185, 399), (60, 60, 160), -1)  # roupa
    pm = np.zeros((400, 300), np.uint8)
    cv2.ellipse(pm, (150, 120), (22, 30), 0, 0, 360, 255, -1)
    cv2.rectangle(pm, (115, 160), (185, 399), 255, -1)
    pessoa = pm > 0
    return img, pessoa


def test_compose_changes_only_the_head_and_keeps_the_rest_identical():
    img, pessoa = cena()
    box = (130, 95, 170, 145)
    x, y, w, h = 78, 40, 144, 160
    saida = img[y:y + h, x:x + w].copy()
    cv2.ellipse(saida, (150 - x, 120 - y), (20, 27), 0, 0, 360, (60, 110, 170), -1)  # cabeca nova
    res = compor(img, cv2.resize(saida, (432, 480)), (x, y, w, h), box, [], pessoa)
    assert res.final is not None and res.info["ecc"] > 0.8
    dif = np.abs(res.final.astype(int) - img.astype(int)).max(2)
    assert (dif[~res.alcance] == 0).all()  # fora do alcance da mascara: byte a byte
    assert dif[120, 150] > 10  # a cabeca mudou
    assert (dif[260:, :] == 0).all()  # a roupa nao


def test_compose_refuses_wrong_aspect_and_misalignment():
    img, pessoa = cena()
    res = compor(img, np.zeros((100, 300, 3), np.uint8), (78, 40, 144, 160), (130, 95, 170, 145), [], pessoa)
    assert res.final is None and "proporcao" in res.info["erro"]
    rng = np.random.default_rng(9)
    lixo = (rng.random((160, 144, 3)) * 255).astype(np.uint8)
    res2 = compor(img, lixo, (78, 40, 144, 160), (130, 95, 170, 145), [], pessoa)
    assert res2.final is None and "alinhamento" in res2.info["erro"]


def test_bank_ranking_matches_angle_and_smile():
    bank = CFG["bank"]
    assert hs.rank_bank(bank, 0.0, 0.15)[0]["file"] == "ref_29.png"  # sorriso com dentes -> referencia sorrindo
    assert hs.rank_bank(bank, 0.02, 0.0)[0]["file"] in ("ref_28.png", "ref_27.png")  # seria e frontal
    assert hs.rank_bank(bank, 0.6, 0.0)[0]["file"] == "ref_32_esp.png"  # perfil para a direita
    assert hs.rank_bank(bank, -0.6, 0.0)[0]["file"] == "ref_32.png"


def test_teeth_measure():
    rgb = np.full((200, 200, 3), (180, 120, 100), np.uint8)
    kps = [[70, 80], [130, 80], [100, 110], [80, 140], [120, 140]]
    assert hs.teeth(rgb, kps) == 0.0
    cv2.ellipse(rgb, (100, 140), (14, 6), 0, 0, 360, (245, 245, 240), -1)  # dentes
    assert hs.teeth(rgb, kps) > 0.2
    assert hs.teeth(rgb, None) is None


class FakeJobs:
    def start(self, work):
        return {"job_id": "x", "status": "running", "work": work}


class FakeStore:
    def __init__(self, img):
        self.img, self.saved = img, {}

    async def load(self, loc):
        if loc in self.saved:
            return self.saved[loc]
        if loc.startswith("luna_head_raw"):
            return self.raw
        return self.img

    async def save(self, px, name):
        k = f"{name}_{len(self.saved)}.png"
        self.saved[k] = px
        return k


class FakeClient:
    def __init__(self, store):
        self.store, self.n = store, 0

    async def upload_image(self, name, data):
        return name

    async def queue_prompt(self, g):
        self.n += 1
        assert g["90"]["inputs"]["images"] == ["32", 0]  # saida crua salva para a composicao
        return f"p{self.n}"

    async def wait_for_completion(self, pid):
        return pid

    def extract_images(self, entry):
        return [SimpleNamespace(filename=f"luna_head_raw_{entry}.png", subfolder="")]


async def test_service_picks_the_candidate_most_like_luna(tmp_path, monkeypatch):
    img_bgr, pessoa = cena()
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    for f in [b["file"] for b in CFG["bank"]] + CFG["proxies"]:
        (tmp_path / f).write_bytes(b"x")
    cfg = {**CFG, "refs_dir": str(tmp_path)}
    store = FakeStore(rgb)
    x, y, w, h = hs.crop_box((130, 95, 170, 145), 300, 400)
    raw = rgb[y:y + h, x:x + w].copy()
    cv2.ellipse(raw, (150 - x, 120 - y), (20, 27), 0, 0, 360, (170, 110, 60), -1)
    mw, mh = hs.model_size(w, h)
    store.raw = cv2.resize(raw, (mw, mh))
    client = FakeClient(store)
    sims = iter([0.61, 0.62, 0.60, 0.74, 0.72, 0.73])  # 3 proxies x 2 candidatas

    async def fake_faces(c, image, reference=None):
        if reference is None:
            return [{"bbox": [130, 95, 170, 145], "sex": "F", "yaw": 0.0, "kps": [[140, 112], [160, 112], [150, 122], [142, 135], [158, 135]]}]
        return [{"bbox": [130, 95, 170, 145], "sim": next(sims)}]

    async def fake_matte(px):
        return pessoa

    monkeypatch.setattr(hs, "faces_in", fake_faces)
    from app.workflow_manager.manager import WorkflowManager

    svc = hs.HeadModeService(client, WorkflowManager(REPO / "workflows"), store, lambda loc: f"http://img/{loc}", FakeJobs(),
                             cfg, matte=fake_matte)
    out = await svc.run("foto.png")
    cands = out["telemetry"]["candidates"]
    assert len(cands) == 2 and client.n == 2
    assert out["measures"]["similarity"] == pytest.approx(0.73, abs=1e-3)
    assert out["telemetry"]["chosen"] == cands[1]["ref"] and out["status"] == "PASS"
    assert out["image_url"].startswith("http://img/")


async def test_service_errors_without_face_or_refs(tmp_path, monkeypatch):
    img_bgr, _ = cena()
    store = FakeStore(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))

    async def no_faces(c, image, reference=None):
        return []

    monkeypatch.setattr(hs, "faces_in", no_faces)
    svc = hs.HeadModeService(FakeClient(store), None, store, str, FakeJobs(), {**CFG, "refs_dir": str(tmp_path)})
    with pytest.raises(hs.HeadModeError, match="nenhum rosto"):
        await svc.run("foto.png")
    with pytest.raises(hs.HeadModeError, match="ausente"):
        await svc._ref("ref_28.png")
