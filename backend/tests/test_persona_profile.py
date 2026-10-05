import json

import pytest

from app.core.persona import PersonaProfile, PersonaRepository, PersonaValidationError
from app.persona_manager.manager import PersonaManager, PersonaNotFoundError


def test_profile_validate_rejects_unknown_identity_field():
    profile = PersonaProfile(id="x", name="X")
    profile.identity.traits["cor_dos_olhos_errada"] = "azul"
    with pytest.raises(PersonaValidationError):
        profile.validate()


@pytest.mark.parametrize("age", [0, 121])
def test_profile_validate_rejects_bad_age(age):
    profile = PersonaProfile(id="x", name="X")
    profile.identity.apparent_age = age
    with pytest.raises(PersonaValidationError):
        profile.validate()


def test_profile_validate_rejects_bad_threshold():
    profile = PersonaProfile(id="x", name="X")
    profile.validation.threshold = 1.5
    with pytest.raises(PersonaValidationError):
        profile.validate()


def test_identity_text_keeps_field_order():
    profile = PersonaProfile(id="x", name="X")
    profile.identity.traits = {"olhos": "brown eyes", "formato_rosto": "oval face"}
    assert profile.identity.text() == "oval face, brown eyes"


def test_reads_legacy_persona_without_engine_block(personas_dir):
    repo = PersonaRepository(personas_dir)
    luna = repo.get("luna")
    assert luna.name == "Luna"
    assert luna.version == 1 and luna.active
    assert luna.identity.traits["olhos"] == "dark brown eyes"
    # expressao vinha de variable_defaults e vira aparencia
    assert "expressao" in luna.appearance.values
    assert luna.identity_assets["lora"]["trigger"] == "lunavox"
    assert luna.created_at


def test_create_separates_identity_appearance_style(personas_dir):
    repo = PersonaRepository(personas_dir)
    persona = repo.create({
        "name": "Ana Lúcia",
        "identity": {"traits": {"olhos": "green eyes"}, "apparent_age": 30, "sex": "F"},
        "appearance": {"maquiagem": "natural makeup"},
        "style": {"iluminacao": "soft window light", "realismo": "photorealistic"},
        "constraints": {"rules": ["do not change identity"]},
    })
    assert persona.id == "ana-lucia"
    again = repo.get("ana-lucia")
    assert again.identity.traits == {"olhos": "green eyes"}
    assert again.appearance.values == {"maquiagem": "natural makeup"}
    assert again.style.values == {"iluminacao": "soft window light", "realismo": "photorealistic"}
    assert again.constraints.rules == ["do not change identity"]
    # o pipeline antigo enxerga a mesma persona
    legacy = PersonaManager(personas_dir).get_persona("ana-lucia")
    assert legacy.identity.fixed["olhos"] == "green eyes"
    assert legacy.identity.variable_defaults["iluminacao"] == "soft window light"


def test_create_with_same_name_gets_new_id(personas_dir):
    repo = PersonaRepository(personas_dir)
    first = repo.create({"name": "Luna"})
    assert first.id != "luna" and first.id.startswith("luna-")


def test_update_bumps_version_and_keeps_snapshot(personas_dir):
    repo = PersonaRepository(personas_dir)
    updated = repo.update("luna", {"identity": {"apparent_age": 25}, "style": {"realismo": "photo"}})
    assert updated.version == 2
    assert repo.get("luna").identity.apparent_age == 25
    versions = repo.versions("luna")
    assert [v["version"] for v in versions] == [2]
    # campos antigos (LoRA, voz) continuam no arquivo
    data = json.loads((personas_dir / "luna" / "persona.json").read_text(encoding="utf-8"))
    assert data["lora"]["trigger"] == "lunavox" and data["voice"]["file"]


def test_update_empty_value_removes_field(personas_dir):
    repo = PersonaRepository(personas_dir)
    repo.update("luna", {"identity": {"traits": {"nariz": "small nose"}}})
    repo.update("luna", {"identity": {"traits": {"nariz": ""}}})
    assert "nariz" not in repo.get("luna").identity.traits


def test_update_rejects_unknown_top_level_field(personas_dir):
    with pytest.raises(PersonaValidationError):
        PersonaRepository(personas_dir).update("luna", {"id": "outra"})


def test_deactivate_hides_from_both_lists(personas_dir):
    repo = PersonaRepository(personas_dir)
    repo.deactivate("luna")
    assert repo.list() == []
    assert [p.id for p in repo.list(include_inactive=True)] == ["luna"]
    assert PersonaManager(personas_dir).list_personas() == []
    with pytest.raises(PersonaNotFoundError):
        repo.get("luna")
    # nada foi apagado do disco
    assert (personas_dir / "luna" / "persona.json").exists()


def test_legacy_save_keeps_engine_block(personas_dir):
    repo = PersonaRepository(personas_dir)
    repo.update("luna", {"identity": {"apparent_age": 25}})
    PersonaManager(personas_dir).update_identity("luna", {"olhos": "brown eyes"}, None)
    assert repo.get("luna").identity.apparent_age == 25


@pytest.mark.parametrize("bad", ["../etc", "Luna", "", "a/b"])
def test_unsafe_ids_are_not_found(personas_dir, bad):
    with pytest.raises(PersonaNotFoundError):
        PersonaRepository(personas_dir).get(bad)
