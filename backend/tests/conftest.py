import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

SHEET = REPO / "personas" / "luna" / "persona_sheet.json"
# Bytes de mentira no lugar das masters (as fotos reais nao ficam no git).
FAKE_MASTERS = {"master_face": b"face-master", "master_face_secondary": b"face-2",
                "master_body": b"body-master", "master_body_seated": b"body-seated"}


@pytest.fixture
def personas_dir(tmp_path: Path) -> Path:
    """Copia do persona.json real da Luna (formato antigo, sem bloco engine)
    numa pasta temporaria - os testes nunca mexem em personas/."""
    target = tmp_path / "personas" / "luna"
    target.mkdir(parents=True)
    shutil.copy(REPO / "personas" / "luna" / "persona.json", target / "persona.json")
    return tmp_path / "personas"


@pytest.fixture
def engine_dir(personas_dir: Path) -> Path:
    """personas_dir + a Persona Sheet real da Luna, com as masters trocadas por
    bytes de teste e os sha256 da ficha recalculados para eles."""
    data = json.loads(SHEET.read_text(encoding="utf-8"))
    refs = personas_dir / "luna" / "references"
    refs.mkdir(parents=True, exist_ok=True)
    for role, content in FAKE_MASTERS.items():
        master = data["master_references"][role]
        (personas_dir / "luna" / master["file"]).write_bytes(content)
        master["sha256"] = hashlib.sha256(content).hexdigest()
    # As masters tambem existem no index.json (aba Referencias).
    index = [{"id": data["master_references"][r]["reference_id"], "filename": Path(data["master_references"][r]["file"]).name,
              "original_filename": f"{r}.png", "uploaded_at": "2026-10-05T00:00:00+00:00", "label": "",
              "is_primary": r == "master_face"} for r in FAKE_MASTERS]
    (refs / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (personas_dir / "luna" / "persona_sheet.json").write_text(json.dumps(data), encoding="utf-8")
    return personas_dir
