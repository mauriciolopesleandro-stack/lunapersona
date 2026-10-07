"""GenerationEngine V2: so a INTERFACE para a geracao normal conviver com Replacement e FaceSwap.

Nao muda nada da producao: delega ao GenerationOrchestrator V1 (Persona Sheet -> PromptBuilder ->
negativos -> Z-Image + LoRA da Luna -> validacao) exatamente como antes. Nenhum modelo, workflow ou
parametro e alterado aqui.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.generation.request import GenerationRequest


@dataclass
class GenerationResult:
    job_id: str
    status: str
    image_url: str | None
    validation: dict[str, Any] | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_job(cls, job: dict[str, Any]) -> "GenerationResult":
        result = job.get("result") or {}
        return cls(job_id=job.get("job_id") or job.get("id", ""), status=job.get("status", ""),
                   image_url=result.get("image_url"), validation=result.get("validation"), raw=job)


class GenerationEngine:
    engine = "generation"

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator  # GenerationOrchestrator V1 (producao), sem alteracao

    def submit(self, req: GenerationRequest) -> dict[str, Any]:
        return self.orchestrator.prepare(req)

    async def run(self, job_id: str) -> GenerationResult:
        return GenerationResult.from_job(await self.orchestrator.run(job_id))


__all__ = ["GenerationEngine", "GenerationRequest", "GenerationResult"]
