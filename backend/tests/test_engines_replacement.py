"""V2: ReplacementEngine de ponta a ponta com GPU MOCKADA (nao prova nada da GPU real - so o fluxo,
o isolamento das mascaras, a validacao, o retry e a telemetria)."""
import numpy as np
import pytest

from app.core.engines.adapter import AdapterResult, ModelAdapter
from app.core.engines.replacement import ReplacementEngine, ReplacementRequest, ReplacementRequestError
from app.core.persona_replacement.contracts import RawSegments
from tests.conftest import REPO
from tests.test_persona_replacement import Analyzer, Reader, Store
from tests.test_persona_transfer import wide_tattoo_photo
from tests.test_v2_multipass import MASTER

CFG = ReplacementEngine.load_config(REPO / "config" / "replacement_engine_v2.json")


class FakeAdapter(ModelAdapter):
    """Pinta a mascara (pele 'da Luna') e suja um canto da imagem (a engine tem de desfazer)."""
    model_id = "realvisxl"

    def __init__(self, store):
        self.store = store
        self.calls = []

    async def load(self):
        return []

    async def generate(self, *a, **kw):
        raise NotImplementedError

    async def inpaint(self, req):
        self.calls.append(req)
        px = self.store.images[req.image].copy()
        px[req.mask > 0.5] = (190, 140, 115)
        px[0:4, 0:4] = (0, 0, 0)
        key = await self.store.save(px, req.stage + "_raw")
        return AdapterResult(key, 9.0, {"name": "Fake GPU"}, {"denoise": req.denoise})

    def metadata(self):
        return {"model": "realvisxl", "hash": "6a35a785", "lora_hash": "0a58a72e", "license": "OpenRAIL++-M",
                "workflow": "sdxl-inpaint-control"}


class Seg:
    async def segment(self, image, sheet):
        _, person, hair, clothes = wide_tattoo_photo()
        return RawSegments(person, hair, [], clothes=clothes)


def engine(faces, original_similarity=0.1):
    img = wide_tattoo_photo()[0]
    store = Store(img)
    ad = FakeAdapter(store)
    eng = ReplacementEngine(reader=Reader(), segmenter=Seg(), analyzer=Analyzer(faces, original_similarity), store=store,
                            adapter=ad, config=CFG, price_per_hour=0.57, provider="comfyui")
    return eng, ad, store


def req(mode="QUALITY", **kw):
    return ReplacementRequest(image="foto.png", persona_id="luna", master=MASTER, mode=mode, **kw)


async def test_quality_pipeline_order_isolation_and_telemetry():
    eng, ad, store = engine({"foto": 0.1, "identity": 0.62, "face_refine": 0.78, "tattoo": 0.78, "integrated": 0.78, "final": 0.78})
    out = await eng.run(req(keep_intermediates=True))
    stages = [c.stage for c in ad.calls]
    assert stages[0] == "identity" and "face_refine" in stages and "tattoo_cleanup" in stages
    ident = ad.calls[0]
    assert ident.controls.pose_strength == 0.8 and ident.controls.depth_strength == 0.5 and ident.identity.use_lora
    face = next(c for c in ad.calls if c.stage == "face_refine")
    assert face.identity.reference is MASTER and 0 < face.identity.reference_strength <= 0.6
    tat = next(c for c in ad.calls if c.stage == "tattoo_cleanup")
    # spec 45.5: a pele e RECONSTRUIDA como pele da Persona (LoRA ligada), a partir da entrada sem a marca
    assert tat.identity.use_lora is True and tat.controls.structure == tat.image and tat.image.startswith("tattoo_prefill")
    assert tat.denoise >= 0.7 and "skin_refine" in stages
    orig = store.images["foto.png"]
    assert (out.pixels[0:4, 0:4] == orig[0:4, 0:4]).all()  # sujeira do modelo fora das mascaras desfeita
    t = out.telemetry.to_dict()
    assert t["engine"] == "replacement" and t["checkpoint_hash"] == "6a35a785" and t["lora_hash"] == "0a58a72e"
    assert t["gpu_seconds"] > 0 and t["estimated_cost_usd"] is not None and t["validation_result"] == out.status
    assert set(out.report.checks) >= {"identity", "tattoo", "original_residual", "skin", "duplicate_persona", "background", "pose"}
    assert "identity" in out.intermediates


async def test_ladder_a_uses_no_segmentation_but_is_measured_with_it():
    eng, ad, _ = engine({"foto": 0.1, "identity": 0.8, "final": 0.8})
    out = await eng.run(req("A", advanced={"max_retries": 0}))
    assert [c.stage for c in ad.calls] == ["identity"]
    a = ad.calls[0]
    assert a.controls.pose_strength == 0 and a.controls.depth_strength == 0
    assert out.measures["tattoo_residual"] is not None  # medido com a segmentacao real
    assert out.report.checks["tattoo"].status == "REJECT"  # sem limpeza a tinta continua


async def test_identity_failure_retries_with_more_reference_never_more_lora():
    eng, ad, _ = engine({"foto": 0.1, "identity": 0.40, "face_refine": 0.45, "tattoo": 0.45, "integrated": 0.45, "final": 0.45})
    out = await eng.run(req(advanced={"max_retries": 1}))
    assert out.telemetry.retry_count == 1 and out.telemetry.retries[0]["failure_type"] in ("identity", "original_residual")
    assert all(c.identity.use_lora in (True, False) for c in ad.calls)
    assert len(out.attempts) == 2


async def test_original_face_residual_rejects_even_with_good_identity():
    eng, _, _ = engine({"foto": 0.1, "identity": 0.85, "face_refine": 0.85, "tattoo": 0.85, "integrated": 0.85, "final": 0.85},
                       original_similarity=0.6)
    out = await eng.run(req(advanced={"max_retries": 0}))
    assert out.report.checks["original_residual"].status == "REJECT" and out.status == "REJECT"


def test_request_validation():
    with pytest.raises(ReplacementRequestError):
        req(options={"inventada": True}).validate()
    with pytest.raises(ReplacementRequestError):
        req(options={"preserve_background": False}).plan()
    p = req(options={"remove_original_tattoos": False, "identity_lock": False}).plan()
    assert p.tattoo_cleanup is False and p.face_reference is False
