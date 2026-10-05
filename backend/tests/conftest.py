import shutil
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def personas_dir(tmp_path: Path) -> Path:
    """Copia do persona.json real da Luna (formato antigo, sem bloco engine)
    numa pasta temporaria - os testes nunca mexem em personas/."""
    target = tmp_path / "personas" / "luna"
    target.mkdir(parents=True)
    shutil.copy(REPO / "personas" / "luna" / "persona.json", target / "persona.json")
    return tmp_path / "personas"
