"""Historico das geracoes do Persona Engine: um JSON por job em
personas/<persona>/engine/jobs/<job>.json (o volume_sync.py ja copia a
pasta personas/ entre os volumes).

Cada job guarda as tres "tabelas" da especificacao, ligadas por id:
- o proprio job (generation_jobs),
- results: uma por tentativa (generation_results, com job_id),
- failures: os motivos de cada reprovacao (generation_failures, com result_id),
mais retries: o que mudou de uma tentativa para a outra.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.storage import SAFE_ID, read_json, utcnow, write_json_atomic

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

RUNNING = "RUNNING"
ACCEPTED = "ACCEPTED"
FAILED = "FAILED"
ERROR = "ERROR"


class JobNotFoundError(LookupError):
    pass


class GenerationHistory:
    def __init__(self, personas_dir: Path) -> None:
        self.personas_dir = personas_dir

    def _path(self, persona_id: str, job_id: str) -> Path:
        return self.personas_dir / persona_id / "engine" / "jobs" / f"{job_id}.json"

    def save(self, job: dict[str, Any]) -> dict[str, Any]:
        job["updated_at"] = utcnow()
        write_json_atomic(self._path(job["persona_id"], job["id"]), job)
        return job

    def get(self, job_id: str) -> dict[str, Any]:
        if not _UUID.match(job_id or ""):
            raise JobNotFoundError(f"Geracao '{job_id}' nao encontrada.")
        for path in self.personas_dir.glob(f"*/engine/jobs/{job_id}.json"):
            if SAFE_ID.match(path.parents[2].name):
                return read_json(path)
        raise JobNotFoundError(f"Geracao '{job_id}' nao encontrada.")

    def list(self, persona_id: str, limit: int = 30) -> list[dict[str, Any]]:
        folder = self.personas_dir / persona_id / "engine" / "jobs"
        if not SAFE_ID.match(persona_id or "") or not folder.exists():
            return []
        jobs = [read_json(p) for p in folder.glob("*.json")]
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return jobs[:limit]

    def mark_interrupted(self) -> int:
        """Jobs RUNNING de um backend que reiniciou nunca vao terminar."""
        count = 0
        for path in self.personas_dir.glob("*/engine/jobs/*.json"):
            job = read_json(path)
            if job and job.get("status") == RUNNING:
                job["status"] = ERROR
                job["error"] = "O backend reiniciou no meio da geracao."
                self.save(job)
                count += 1
        return count
