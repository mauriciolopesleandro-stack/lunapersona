"""V2: FaceSwapEngine (modos separados, sem escalar sozinho) e GenerationEngine (delegacao V1 sem mudanca)."""
import numpy as np
import pytest

from app.core.engines.face_swap import FACE_INTEGRATED, FACE_NECK, FACE_ONLY, FULL_PERSON, FaceSwapEngine, FaceSwapError, FaceSwapRequest
from app.core.engines.generation import GenerationEngine, GenerationResult
from tests.test_engines_replacement import CFG, FakeAdapter, Seg, engine, req
from tests.test_persona_replacement import Analyzer, Reader, Store
from tests.test_persona_transfer import wide_tattoo_photo
from tests.test_v2_multipass import MASTER


def fs(faces, head_swap=None, with_replacement=False):
    img = wide_tattoo_photo()[0]
    store = Store(img)
    ad = FakeAdapter(store)
    rep = engine(faces)[0] if with_replacement else None
    return FaceSwapEngine(reader=Reader(), segmenter=Seg(), analyzer=Analyzer(faces), store=store, adapter=ad, config=CFG,
                          replacement=rep, head_swap=head_swap), ad, store


async def test_face_only_touches_only_the_face_with_reference():
    eng, ad, store = fs({"foto": 0.1, "face_swap": 0.8, "faceswap_final": 0.8})
    out = await eng.run(FaceSwapRequest("foto.png", "luna", MASTER, FACE_ONLY))
    assert [c.stage for c in ad.calls] == ["face_swap"] and ad.calls[0].identity.reference is MASTER
    orig = store.images["foto.png"]
    assert (out.pixels[30, 80] == orig[30, 80]).all()  # cabelo fica o da foto no FACE_ONLY
    assert out.mode == FACE_ONLY and out.telemetry.engine == "face_swap"


async def test_neck_and_integrated_masks_grow_and_integrated_runs_identity_pass():
    eng, ad, _ = fs({"foto": 0.1, "fs_identity": 0.7, "face_swap": 0.8, "faceswap_final": 0.8})
    await eng.run(FaceSwapRequest("foto.png", "luna", MASTER, FACE_NECK))
    neck_area = float(ad.calls[-1].mask.sum())
    await eng.run(FaceSwapRequest("foto.png", "luna", MASTER, FACE_INTEGRATED))
    assert [c.stage for c in ad.calls[-2:]] == ["fs_identity", "face_swap"]
    assert float(ad.calls[-2].mask.sum()) > neck_area * 0.9 and ad.calls[-2].controls.pose_strength == 0.8


async def test_full_person_never_runs_unless_asked_and_configured():
    eng, ad, _ = fs({"foto": 0.1})
    with pytest.raises(FaceSwapError):
        await eng.run(FaceSwapRequest("foto.png", "luna", MASTER, FULL_PERSON))
    eng2, _, _ = fs({"foto": 0.1, "identity": 0.8, "final": 0.8}, with_replacement=True)
    out = await eng2.run(FaceSwapRequest("foto.png", "luna", MASTER, FULL_PERSON, replacement=req(advanced={"max_retries": 0})))
    assert out.telemetry.engine == "replacement"


async def test_qwen_head_backend_requires_the_port_no_fallback():
    eng, _, _ = fs({"foto": 0.1})
    with pytest.raises(FaceSwapError, match="sem fallback"):
        await eng.run(FaceSwapRequest("foto.png", "luna", MASTER, FACE_INTEGRATED, head_backend="qwen_bfs"))


async def test_generation_engine_only_delegates_to_v1():
    class Orq:
        def __init__(self):
            self.calls = []

        def prepare(self, r):
            self.calls.append(("prepare", r))
            return {"job_id": "j1"}

        async def run(self, job_id):
            self.calls.append(("run", job_id))
            return {"job_id": job_id, "status": "ACCEPTED", "result": {"image_url": "u", "validation": {"status": "PASS"}}}

    o = Orq()
    g = GenerationEngine(o)
    assert g.submit("pedido") == {"job_id": "j1"}
    r = await g.run("j1")
    assert isinstance(r, GenerationResult) and r.image_url == "u" and o.calls == [("prepare", "pedido"), ("run", "j1")]
