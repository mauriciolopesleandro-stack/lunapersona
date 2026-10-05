"""Leitura e gravacao de JSON no volume. Sem banco (decisao do projeto): os
dados do Persona Engine ficam em personas/<id>/, que o volume_sync.py ja
copia entre os volumes. Gravacao atomica para um desligamento no meio nao
deixar arquivo pela metade."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ids viram nome de pasta: so isso evita "../" e afins.
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
