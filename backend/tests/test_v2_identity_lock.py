"""Trava do rosto da persona (V2): a receita que deu rosto 0,827/0,817/0,803 fica
congelada por uma impressao sha256; qualquer mudanca nela impede a geracao."""
import copy
import json

import pytest

from app.core.generation.v2_config import V2ConfigError, identity_fingerprint, parse_v2_config
from tests.conftest import REPO

RAW = json.loads((REPO / "config" / "persona_engine_v2.json").read_text(encoding="utf-8"))
SCENES = json.loads((REPO / "config" / "v2_cenas_influencer.json").read_text(encoding="utf-8"))


def test_real_config_is_locked_and_consistent():
    cfg = parse_v2_config(RAW)
    lock = cfg.identity_lock
    assert lock["locked"] and lock["version"] == "luna-face-v2.0" and lock["min_final_face"] == 0.70
    assert lock["fingerprint"] == identity_fingerprint(RAW)
    assert lock["approved_result"]["scores"]["face_2"] == 0.817 and lock["approved_result"]["scores"]["face_3"] == 0.803
    assert lock["master_face"]["sha256"].startswith("1c431d3c")


@pytest.mark.parametrize("mutate", [
    lambda d: d["face_passes"][0].update(denoise=0.5),
    lambda d: d["face_passes"][0].update(adapter_weight=0.9),
    lambda d: d["face_passes"][2].update(lora=True),
    lambda d: d["lora"].update(strength=1.2),
    lambda d: d["body_passes"][1].update(strength=0.4),
    lambda d: d["models"]["realvisxl"]["sampling"].update(cfg=6.0),
    lambda d: d["identity_adapters"]["instantid"].update(model="outro.bin"),
    lambda d: d["pass_prompts"].update(identity_structure="outra coisa {style}"),
    lambda d: d.update(generation_model="lustify"),
])
def test_any_change_to_the_face_recipe_is_refused(mutate):
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(V2ConfigError, match="TRAVADA"):
        parse_v2_config(data)


def test_style_and_scenes_can_change_without_unlocking():
    data = copy.deepcopy(RAW)
    data["style"]["positive"] += ", golden hour"
    data["common_negative"].append("watermark")
    data["pass_prompts"]["body"] = "{scene}, other text, {style}"
    assert parse_v2_config(data).identity_lock["locked"]


def test_unlocked_config_skips_the_check():
    data = copy.deepcopy(RAW)
    data["identity_lock"]["locked"] = False
    data["face_passes"][0]["denoise"] = 0.5
    assert parse_v2_config(data).face_passes[0].denoise == 0.5


def test_influencer_scenes_are_natural_and_varied():
    scenes = SCENES["scenes"]
    assert len(scenes) >= 10 and len({s["seed"] for s in scenes}) == len(scenes) and len({s["label"] for s in scenes}) == len(scenes)
    assert sum("selfie" in s["prompt"] for s in scenes) >= 8
    for s in scenes:
        text = s["prompt"].lower()
        assert not any(w in text for w in ("studio", "bokeh", "dslr", "professional", "softbox"))
