"""V2: InstantID so na 1a passada de rosto (identidade da master_face), conferido
antes de rodar; as outras passadas continuam sem adaptador."""
from app.core.generation.multipass import face_region
from app.providers.base import ProviderImage, RegionPassRequest
from app.providers.comfyui.region_pass import ComfyRegionPassAdapter
from app.providers.comfyui.session import ComfySession
from app.workflow_manager.manager import WorkflowManager
from tests.conftest import REPO
from tests.fakes import face
from tests.test_v2_models import FakeComfyV2
from tests.test_v2_multipass import CFG, MASTER, look, run, runner


class ComfyWithInstantID(FakeComfyV2):
    def __init__(self, nodes=True, files=True):
        super().__init__()
        self.nodes, self.files = nodes, files

    async def get_object_info(self):
        if not self.nodes:
            return {}
        return {"ApplyInstantID": {}, "InstantIDFaceAnalysis": {},
                "InstantIDModelLoader": {"input": {"required": {"instantid_file": [["instantid_ip-adapter.bin"] if self.files else []]}}},
                "ControlNetLoader": {"input": {"required": {"control_net_name": [["instantid_controlnet.safetensors"]]}}}}


def adapter(comfy):
    return ComfyRegionPassAdapter(ComfySession(comfy, WorkflowManager(REPO / "workflows")), CFG.model("realvisxl"), CFG.lora,
                                  identity_adapters=CFG.identity_adapters)


def test_config_puts_instantid_only_on_the_first_face_pass():
    assert [p.identity_adapter for p in CFG.face_passes] == ["instantid", None, None]
    assert CFG.face_passes[0].adapter_weight == 0.8 and CFG.identity_adapters["instantid"]["model"] == "instantid_ip-adapter.bin"


async def test_runner_sends_the_master_only_to_the_instantid_pass():
    r, region = runner({"base": look(0.40)})
    res = await run(r)
    req = {c[1].name: c[1] for c in region.calls}
    assert req["face_1"].identity_adapter == "instantid" and req["face_1"].reference is MASTER
    assert req["face_2"].identity_adapter is None and req["face_3"].reference is None
    assert res.records[1].identity_adapter == "instantid" and res.records[1].adapter_weight == 0.8
    assert res.records[2].identity_adapter is None


async def test_instantid_graph_keeps_head_direction_and_lora():
    comfy = ComfyWithInstantID()
    a = adapter(comfy)
    assert await a.validate_configuration() == []
    reg = face_region(face(0.5), "face_full", 832, 1216).to_dict()
    out = await a.refine(ProviderImage("comfyui", "b.png [output]", "u", 832, 1216),
                         RegionPassRequest(reg, "face", "neg", 0.42, 0.85, 5, "face_1", identity_adapter="instantid",
                                           adapter_weight=0.8, reference=MASTER))
    g = comfy.graphs[0]
    apply = g["20"]["inputs"]
    assert apply["weight"] == 0.8 and apply["model"] == ["2", 0]  # InstantID por cima da LoRA
    assert apply["image_kps"] == ["12", 0]  # pontos do proprio recorte: a cabeca nao vira
    assert g["8"]["inputs"]["image"].startswith("v2ref_") and g["14"]["inputs"]["model"] == ["20", 0]
    assert out.effective_parameters["workflow"] == "realvis-face-pass-instantid"
    # a master sobe uma vez so
    await a.refine(ProviderImage("comfyui", "c.png [output]", "u", 832, 1216),
                   RegionPassRequest(reg, "face", "neg", 0.42, 0.85, 6, "face_1", identity_adapter="instantid",
                                     adapter_weight=0.8, reference=MASTER))
    assert len([u for u in comfy.uploads if u.startswith("v2ref_")]) == 1


async def test_missing_instantid_is_reported_before_running():
    assert any("instantid_ip-adapter.bin" in p for p in await adapter(ComfyWithInstantID(files=False)).validate_configuration())
    assert any("ApplyInstantID" in p for p in await adapter(ComfyWithInstantID(nodes=False)).validate_configuration())
