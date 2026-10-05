import pytest

from app.core.persona.references import InvalidReferenceError, ReferenceManager, validate_image
from app.persona_manager.manager import PersonaManager, ReferenceNotFoundError
from tests.helpers import client, engine_app, png


@pytest.fixture
def refs(personas_dir) -> ReferenceManager:
    return ReferenceManager(PersonaManager(personas_dir))


def test_validate_image_rejects_non_images_and_tiny_images():
    with pytest.raises(InvalidReferenceError):
        validate_image(b"isto nao e imagem")
    with pytest.raises(InvalidReferenceError):
        validate_image(png(100, 100))
    assert validate_image(png(512, 768)) == ("png", 512, 768)


def test_first_reference_is_primary_and_types_are_kept(refs):
    first = refs.add("luna", "rosto.png", png(tag=b"1"), "FACE", 0.8)
    body = refs.add("luna", "corpo.png", png(tag=b"2"), "FULL_BODY", 0.5)
    assert first.is_primary and first.type == "PRIMARY"
    assert body.type == "FULL_BODY" and body.weight == 0.5 and not body.is_primary
    # a aba antiga continua lendo o mesmo index.json
    assert len(PersonaManager(refs.personas.personas_dir).list_references("luna")) == 2


def test_add_as_primary_moves_primary(refs):
    old = refs.add("luna", "a.png", png(tag=b"1"), "FACE")
    new = refs.add("luna", "b.png", png(tag=b"2"), "PRIMARY")
    assert new.is_primary and new.type == "PRIMARY"
    # a antiga deixa de ser principal e mostra o proprio tipo
    assert {r.id: r.type for r in refs.list("luna")}[old.id] == "FACE"


def test_identity_references_skip_inactive_and_style(refs):
    primary = refs.add("luna", "a.png", png(tag=b"1"), "FACE")
    profile = refs.add("luna", "b.png", png(tag=b"2"), "PROFILE", 0.4)
    face = refs.add("luna", "c.png", png(tag=b"3"), "FACE", 0.9)
    refs.add("luna", "d.png", png(tag=b"4"), "STYLE")
    refs.update("luna", profile.id, {"active": False})
    assert [r.id for r in refs.identity_references("luna")] == [primary.id, face.id]


def test_primary_cannot_be_deactivated(refs):
    primary = refs.add("luna", "a.png", png(tag=b"1"))
    with pytest.raises(InvalidReferenceError):
        refs.update("luna", primary.id, {"active": False})


def test_bad_type_or_weight_rejected(refs):
    with pytest.raises(InvalidReferenceError):
        refs.add("luna", "a.png", png(), "SELFIE")
    with pytest.raises(InvalidReferenceError):
        refs.add("luna", "a.png", png(), "FACE", 2.0)


def test_remove_keeps_file_and_history_and_promotes_next(refs, personas_dir):
    primary = refs.add("luna", "a.png", png(tag=b"1"))
    other = refs.add("luna", "b.png", png(tag=b"2"), "FACE")
    refs.remove("luna", primary.id)
    assert refs.primary("luna").id == other.id
    assert (personas_dir / "luna" / "references" / "removed" / primary.filename).exists()
    assert [h["action"] for h in refs.history("luna")][-1] == "removed"
    with pytest.raises(ReferenceNotFoundError):
        refs.remove("luna", primary.id)


def test_replace_primary_keeps_role(refs):
    primary = refs.add("luna", "a.png", png(tag=b"1"), "FACE", 0.7)
    new = refs.replace("luna", primary.id, "nova.png", png(tag=b"9"))
    assert new.is_primary and new.weight == 0.7
    assert [r.id for r in refs.list("luna")] == [new.id]


# --- API -----------------------------------------------------------------


def test_api_persona_crud_and_references(personas_dir):
    api = client(engine_app(personas_dir))
    created = api.post("/api/engine/personas", json={
        "name": "Bia", "identity": {"traits": {"olhos": "green eyes"}, "apparent_age": 28, "sex": "F"},
        "style": {"realismo": "photorealistic"},
    })
    assert created.status_code == 201
    pid = created.json()["id"]

    ref = api.post(f"/api/engine/personas/{pid}/references", files={"file": ("r.png", png(), "image/png")},
                   data={"type": "FACE", "weight": "0.9"})
    assert ref.status_code == 201 and ref.json()["type"] == "PRIMARY"

    patched = api.patch(f"/api/engine/personas/{pid}", json={"appearance": {"maquiagem": "light"}})
    assert patched.json()["version"] == 2
    assert patched.json()["primary_reference_id"] == ref.json()["id"]

    assert api.delete(f"/api/engine/personas/{pid}").json() == {"id": pid, "active": False}
    assert api.get(f"/api/engine/personas/{pid}").status_code == 404


def test_api_errors_are_controlled(personas_dir):
    api = client(engine_app(personas_dir))
    assert api.get("/api/engine/personas/nao-existe").status_code == 404
    assert api.post("/api/engine/personas", json={"name": ""}).status_code == 422
    bad = api.post("/api/engine/personas/luna/references", files={"file": ("x.png", b"nada", "image/png")})
    assert bad.status_code == 400
    assert api.patch("/api/engine/personas/luna", json={"appearance": {"cor": "x"}}).status_code == 400


def test_api_denies_without_token(personas_dir):
    api = client(engine_app(personas_dir), authorized=False)
    assert api.get("/api/engine/personas").status_code == 401
    assert api.get("/api/engine/personas/luna").status_code == 401
