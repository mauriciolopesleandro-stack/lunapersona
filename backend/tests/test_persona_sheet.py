import json

import pytest

from app.core.persona.references import MasterReferenceProtectedError, ReferenceManager
from app.core.persona.sheet import (
    MasterIntegrityError,
    PersonaSheetError,
    PersonaSheetMissingError,
    PersonaSheetRepository,
    parse_sheet,
)
from app.persona_manager.manager import PersonaManager
from tests.conftest import SHEET
from tests.helpers import client, engine_app, png


def real() -> dict:
    return json.loads(SHEET.read_text(encoding="utf-8"))


def test_real_sheet_parses_with_required_profiles():
    sheet = parse_sheet(real(), SHEET)
    assert sheet.persona_id == "luna" and sheet.persona_version == "1.0"
    assert set(sheet.masters) >= {"master_face", "master_body"}
    assert sheet.pipeline_version == "luna-v1.0"
    assert sheet.validation["face_identity"]["threshold"] == 0.55
    assert sheet.age["target"] == 27 and sheet.age["known_drift"]
    assert sheet.data["identity"]["face"]["asymmetries"]["value"] == "UNKNOWN"
    # a master de corpo existe so para validar, nunca para o Qwen
    assert "PROIBIDO" in sheet.data["master_references"]["master_body"]["purpose"]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(schema_version=2), "schema_version"),
    (lambda d: d.update(persona_version="v1"), "persona_version"),
    (lambda d: d.update(persona_version="1.1"), "versioning"),
    (lambda d: d.pop("validation_profile"), "Secoes faltando"),
    (lambda d: d["master_references"].pop("master_face"), "master_face"),
    (lambda d: d["master_references"]["master_body"].update(file="../x.png"), "invalido"),
    (lambda d: d["validation_profile"]["face_identity"].update(threshold=1.5), "threshold"),
])
def test_invalid_sheets_rejected(mutate, message):
    data = real()
    mutate(data)
    with pytest.raises(PersonaSheetError, match=message):
        parse_sheet(data, SHEET)


def test_missing_sheet_is_explicit_error(personas_dir):
    with pytest.raises(PersonaSheetMissingError):
        PersonaSheetRepository(personas_dir).get("luna")


def test_master_read_checks_hash(engine_dir):
    sheet = PersonaSheetRepository(engine_dir).get("luna")
    assert sheet.read_master("master_face") == b"face-master"
    (engine_dir / "luna" / sheet.master("master_face").file).write_bytes(b"outra pessoa")
    with pytest.raises(MasterIntegrityError):
        sheet.read_master("master_face")


def test_repository_has_no_write_methods():
    assert not [m for m in dir(PersonaSheetRepository) if m.startswith(("save", "write", "update", "set"))]


# --- protecao das masters ------------------------------------------------


def manager(engine_dir) -> ReferenceManager:
    return ReferenceManager(PersonaManager(engine_dir), PersonaSheetRepository(engine_dir).protected_reference_ids)


def test_masters_cannot_be_removed_replaced_or_changed(engine_dir):
    refs = manager(engine_dir)
    master_id = PersonaSheetRepository(engine_dir).get("luna").master("master_face").reference_id
    with pytest.raises(MasterReferenceProtectedError):
        refs.remove("luna", master_id)
    with pytest.raises(MasterReferenceProtectedError):
        refs.replace("luna", master_id, "nova.png", png())
    with pytest.raises(MasterReferenceProtectedError):
        refs.update("luna", master_id, {"active": False})
    # o rotulo pode mudar
    assert refs.update("luna", master_id, {"label": "rosto oficial"}).label == "rosto oficial"


def test_non_master_reference_still_removable(engine_dir):
    refs = manager(engine_dir)
    extra = refs.add("luna", "extra.png", png(tag=b"x"), "FACE")
    refs.remove("luna", extra.id)
    assert extra.id not in {r.id for r in refs.list("luna")}


def test_api_returns_409_for_master(engine_dir):
    app = engine_app(engine_dir)
    app.state.persona_sheets = PersonaSheetRepository(engine_dir)
    app.state.reference_manager = manager(engine_dir)
    master_id = app.state.persona_sheets.get("luna").master("master_body").reference_id
    response = client(app).delete(f"/api/engine/personas/luna/references/{master_id}")
    assert response.status_code == 409
