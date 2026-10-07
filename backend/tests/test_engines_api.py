"""V2: rotas /api/v2 (integracao rota -> servico -> engine) com a GPU MOCKADA.

Prova o contrato (validacao antes de gastar GPU, sem fallback de modelo, LoRA fixa, so processamento
local, retencao, telemetria em log); nao prova nada da qualidade da imagem.
"""
import io
import json
import os
import time

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.core.engines.face_swap import FaceSwapEngine
from app.core.engines.models import ModelRegistry
from app.core.engines.service import EnginesV2Service, RetentionRule, RetentionSweeper
from app.core.generation.negative import NegativePromptBuilder
from app.core.persona.sheet import PersonaSheetRepository
from app.jobs import JobRegistry
from app.routes import engines_v2
from app.security import TOKEN_HEADER, token_middleware
from tests.conftest import REPO
from tests.test_engines_replacement import CFG, FakeAdapter, Seg, engine
from tests.test_persona_replacement import Analyzer, Reader, Store
from tests.test_persona_transfer import wide_tattoo_photo

FACES = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "tattoo": 0.8, "integrated": 0.8, "final": 0.8,
         "fs_identity": 0.8, "face_swap": 0.8, "faceswap_final": 0.8}
TOKEN = "segredo"
H = {TOKEN_HEADER: TOKEN}


class FakeFactory:
    def __init__(self):
        self.models = []
        self.uploads = {}

    async def replacement(self, model):
        self.models.append(model.id)
        return engine(FACES)[0]

    async def face_swap(self, model):
        self.models.append(model.id)
        store = Store(wide_tattoo_photo()[0])
        return FaceSwapEngine(reader=Reader(), segmenter=Seg(), analyzer=Analyzer(FACES), store=store,
                              adapter=FakeAdapter(store), config=CFG)

    async def upload(self, name, content):
        self.uploads[name] = content
        return name


def make(engine_dir, tmp_path, sweeper=None):
    factory = FakeFactory()
    cfg = json.loads((REPO / "config" / "engines_v2.json").read_text(encoding="utf-8"))
    pe = json.loads((REPO / "config" / "persona_engine.json").read_text(encoding="utf-8"))
    svc = EnginesV2Service(config=cfg, registry=ModelRegistry.load(REPO / "config" / "model_registry_v2.json"),
                           sheets=PersonaSheetRepository(engine_dir), negative_builder=NegativePromptBuilder(pe["global_negative"]),
                           factory=factory, upload=factory.upload, url_for=lambda loc: f"/view/{loc}", jobs=JobRegistry(),
                           sweeper=sweeper, telemetry_log=tmp_path / "tel.jsonl")
    app = FastAPI()
    app.middleware("http")(token_middleware(TOKEN))
    app.include_router(engines_v2.router, prefix="/api")
    app.state.engines_v2 = svc
    return app, factory


def wait(client, job_id):
    for _ in range(200):
        r = client.get(f"/api/v2/jobs/{job_id}", headers=H).json()
        if r["status"] != "running":
            return r
        time.sleep(0.02)
    raise AssertionError("job nao terminou")


def test_catalog_and_licenses(engine_dir, tmp_path):
    app, _ = make(engine_dir, tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/v2/engines").status_code == 401  # token obrigatorio
        cat = c.get("/api/v2/engines", headers=H).json()
        assert cat["engines"] == ["generation", "replacement", "face_swap"] and cat["modes"] == ["FAST", "QUALITY", "MAX_QUALITY"]
        ids = {m["id"]: m for m in cat["models"]}
        assert {"auto", "realvisxl", "lustify"} <= set(ids) and ids["lustify"]["status"] == "MISSING"
        assert "remove_original_tattoos" in cat["replacement"]["options"] and "comfyui_input_dir" not in cat["retention"]
        lic = c.get("/api/v2/models/licenses", headers=H).json()["models"]
        assert any(m["license_status"] == "LICENSE_REVIEW_REQUIRED" for m in lic)


def test_replace_job_end_to_end_with_mocked_gpu(engine_dir, tmp_path):
    app, factory = make(engine_dir, tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/v2/replace", headers=H, data={"persona_id": "luna", "image": "foto.png", "mode": "QUALITY",
                                                       "advanced": json.dumps({"max_retries": 0})})
        assert r.status_code == 202
        job = wait(c, r.json()["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["engine"] == "replacement" and res["model"] == "realvisxl" and res["processing"] == "local"
    assert res["image_url"].startswith("/view/repl_final") or res["image_url"].startswith("/view/final")
    assert res["telemetry"]["engine"] == "replacement" and set(res["validation"]["checks"]) >= {"identity", "tattoo", "background"}
    assert factory.models == ["realvisxl"]  # auto = RealVisXL do registro
    line = json.loads((tmp_path / "tel.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["engine"] == "replacement" and line["status"] == res["status"]


def test_requests_are_refused_before_any_gpu(engine_dir, tmp_path):
    app, factory = make(engine_dir, tmp_path)
    base = {"persona_id": "luna", "image": "foto.png"}
    with TestClient(app) as c:
        lus = c.post("/api/v2/replace", headers=H, data={**base, "model": "lustify"})
        assert lus.status_code == 400 and "indisponivel" in lus.json()["detail"]  # sem fallback para RealVisXL
        assert c.post("/api/v2/replace", headers=H, data={**base, "advanced": json.dumps({"lora_strength": 1.4})}).status_code == 400
        assert c.post("/api/v2/replace", headers=H, data={**base, "options": json.dumps({"preserve_background": False})}).status_code == 400
        assert c.post("/api/v2/replace", headers=H, data={**base, "processing": "external"}).status_code == 400
        assert c.post("/api/v2/replace", headers=H, data={**base, "mode": "TURBO"}).status_code == 400
        assert c.post("/api/v2/replace", headers=H, data={"persona_id": "fulana", "image": "foto.png"}).status_code == 400
        assert c.post("/api/v2/replace", headers=H, data={"persona_id": "luna"}).status_code == 400  # sem foto
        assert c.post("/api/v2/faceswap", headers=H, data={**base, "mode": "FACE_EVERYTHING"}).status_code == 400
        assert c.post("/api/v2/faceswap", headers=H, data={**base, "reference_strength": 0.9}).status_code == 400
    assert factory.models == []


def test_upload_is_resized_and_faceswap_runs(engine_dir, tmp_path):
    app, factory = make(engine_dir, tmp_path)
    big = Image.fromarray(np.full((2400, 1800, 3), 120, np.uint8))
    buf = io.BytesIO()
    big.save(buf, "JPEG")
    with TestClient(app) as c:
        bad = c.post("/api/v2/faceswap", headers=H, data={"persona_id": "luna"}, files={"file": ("x.png", b"nao e imagem", "image/png")})
        assert bad.status_code == 400
        r = c.post("/api/v2/faceswap", headers=H, data={"persona_id": "luna", "mode": "FACE_ONLY"},
                   files={"file": ("minha foto!.jpg", buf.getvalue(), "image/jpeg")})
        assert r.status_code == 202
        wait(c, r.json()["job_id"])
    name, content = next(iter(factory.uploads.items()))
    assert name.startswith("v2in_minhafoto_") and max(Image.open(io.BytesIO(content)).size) == 1600


def test_retention_sweeps_only_known_prefixes_by_age(tmp_path):
    now = time.time()
    files = {"repl_final_a.png": 100, "repl_identity_b.png": 30, "repl_identity_c.png": 2, "v2in_d.png": 30,
             "luna_master.png": 9999}
    for name, hours in files.items():
        p = tmp_path / name
        p.write_bytes(b"x")
        os.utime(p, (now - hours * 3600, now - hours * 3600))
    sw = RetentionSweeper(tmp_path, [RetentionRule("repl_final_", 720), RetentionRule("repl_", 24), RetentionRule("v2in_", 24)])
    assert sorted(sw.sweep(now)) == ["repl_identity_b.png", "v2in_d.png"]
    assert {p.name for p in tmp_path.iterdir()} == {"repl_final_a.png", "repl_identity_c.png", "luna_master.png"}
    assert RetentionSweeper(None, []).sweep() == []
