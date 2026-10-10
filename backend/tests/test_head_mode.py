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

    async def fake_cloth(px):
        return np.zeros(px.shape[:2], bool)

    svc = hs.HeadModeService(client, WorkflowManager(REPO / "workflows"), store, lambda loc: f"http://img/{loc}", FakeJobs(),
                             cfg, matte=fake_matte, cloth=fake_cloth)
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


def test_long_hair_is_found_by_color_and_texture_not_skin_and_grows_the_crop():
    """Foto da lingerie (10/10): cabelo loiro ate o quadril fora do recorte; a pele clara tem a cor do cabelo."""
    from app.core.head.compose import cabelo_comprido, recorte_com_cabelo

    rng = np.random.default_rng(3)
    img = np.full((400, 300, 3), (200, 205, 210), np.uint8)
    pessoa = np.zeros((400, 300), bool)
    pessoa[60:400, 90:210] = True
    img[pessoa] = (150, 185, 225)  # pele clara (BGR), lisa
    listras = (np.sin(np.arange(400)[:, None] * 0 + np.arange(30)[None, :] * 2.1) * 25).astype(int)
    for x0 in (90, 180):  # cabelo dos dois lados, mesma cor media da pele, com fios
        bloco = np.clip(np.array([150, 185, 225])[None, None, :] + listras[100:390, :, None]
                        + rng.normal(0, 4, (290, 30, 1)), 0, 255).astype(np.uint8)
        img[100:390, x0:x0 + 30] = bloco
    box = (125, 60, 175, 120)
    cab = cabelo_comprido(img, box, pessoa)
    assert cab[300, 100] and cab[300, 195]  # cabelo comprido dos dois lados, la embaixo
    assert not cab[300, 150]  # a pele (lisa, mesma cor) nao
    crop = recorte_com_cabelo((90, 30, 120, 150), cab, 300, 400)
    assert crop[1] + crop[3] >= 380  # o recorte da troca desce ate o fim do cabelo


def test_body_tone_moves_skin_to_the_face_tone_and_leaves_clothes():
    """A diferenca de tom vem do rosto original x rosto da Luna no MESMO lugar (mesma luz) e vai para o corpo."""
    from app.core.head.compose import tom_do_corpo

    original = np.full((300, 200, 3), (200, 200, 200), np.uint8)
    pessoa = np.zeros((300, 200), bool)
    pessoa[20:300, 50:150] = True
    original[pessoa] = (175, 190, 225)  # pele clara (rosto e corpo da pessoa original)
    original[200:260, 50:150] = (60, 60, 200)  # roupa vermelha
    final = original.copy()
    final[30:90, 70:130] = (90, 140, 200)  # rosto da Luna, moreno
    roupa = np.zeros((300, 200), bool)
    roupa[200:260, 50:150] = True
    cabeca = np.zeros((300, 200), bool)
    cabeca[25:95, 65:135] = True
    out, info = tom_do_corpo(final, original, (70, 30, 130, 90), pessoa, roupa, cabeca)
    assert info["status"] == "aplicado" and info["razao_luz"] < 1
    assert out[150, 100].astype(int).sum() < final[150, 100].astype(int).sum() - 20  # corpo mais moreno
    assert (out[230, 100] == final[230, 100]).all()  # roupa intacta
    assert (out[10, 10] == final[10, 10]).all()  # fundo intacto
    same, info2 = tom_do_corpo(original, original, (70, 30, 130, 90), pessoa, roupa, cabeca)
    assert np.abs(same.astype(int) - original.astype(int)).max() <= 1  # rosto igual = nada muda
