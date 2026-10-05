"""Logs estruturados do Persona Engine: uma linha JSON por evento
(generation_started, identity_validation_completed, generation_rejected...).
Campos com nome de segredo saem mascarados."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("luna.engine")

EVENTS = {
    "generation_started",
    "generation_completed",
    "identity_validation_started",
    "identity_validation_completed",
    "generation_rejected",
    "regeneration_started",
    "generation_accepted",
    "generation_failed",
    "generation_error",
}
_SECRET = re.compile(r"token|secret|password|api_key|apikey|authorization|credential", re.I)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "***" if _SECRET.search(str(k)) else _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def log_event(event: str, **fields: Any) -> dict[str, Any]:
    if event not in EVENTS:
        raise ValueError(f"Evento desconhecido: {event}")
    record = {"event": event, **_clean(fields)}
    log.info(json.dumps(record, ensure_ascii=False, default=str))
    return record
