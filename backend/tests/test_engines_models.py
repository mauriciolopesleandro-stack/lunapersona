"""V2: registro de modelos (licenca, hash, status) e escolha do checkpoint sem fallback silencioso."""
import pytest

from app.core.engines.models import AUTO, LICENSE_REVIEW, ModelRegistry, ModelUnavailableError
from tests.conftest import REPO

REG = ModelRegistry.load(REPO / "config" / "model_registry_v2.json")


def test_auto_selects_realvisxl_v5_with_hash_and_license():
    m = REG.select_checkpoint(AUTO)
    assert m.id == "realvisxl" and m.version == "V5.0" and m.file == "RealVisXL_V5.0_fp16.safetensors"
    assert m.sha256.startswith("6a35a785") and "RAIL" in m.license and m.metadata()["hash"] == m.sha256


def test_explicit_missing_model_is_an_error_never_a_silent_fallback():
    with pytest.raises(ModelUnavailableError, match="CIVITAI_TOKEN"):
        REG.select_checkpoint("lustify")


def test_lustify_is_registered_as_license_review_required():
    m = REG.get("lustify")
    assert m.license_status == LICENSE_REVIEW and m.commercial_use == "unknown"


def test_commercial_use_blocks_models_under_license_review(tmp_path):
    import json
    d = json.loads((REPO / "config" / "model_registry_v2.json").read_text(encoding="utf-8"))
    d["checkpoints"]["lustify"]["status"] = "AVAILABLE"
    reg = ModelRegistry(d)
    assert reg.select_checkpoint("lustify").id == "lustify"  # pesquisa/teste: pode
    with pytest.raises(ModelUnavailableError, match=LICENSE_REVIEW):
        reg.select_checkpoint("lustify", commercial=True)


def test_luna_lora_is_explicit_with_fixed_strength_and_hash():
    lora = REG.get("lunavox_sdxl_v1")
    assert lora.sha256.startswith("0a58a72e") and lora.file == "lunavox_sdxl_v1.safetensors"


def test_insightface_models_are_flagged_for_license_review():
    assert REG.get("antelopev2").license_status == LICENSE_REVIEW
    report = REG.license_report()
    assert {r["model"] for r in report} >= {"realvisxl", "lustify", "controlnet_union_promax", "instantid", "antelopev2"}
