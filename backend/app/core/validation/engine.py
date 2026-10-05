"""ValidationEngine: roda os validadores independentes e decide.

  FAIL              algum validador bloqueante falhou
  PASS_WITH_UNKNOWN nada falhou, mas algo bloqueante ficou UNKNOWN (ex.: anatomia sem detector)
  PASS              tudo medido e aprovado

UNKNOWN nunca vira PASS: a imagem sai aceita com a lista do que nao foi verificado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.validation.checks import FAIL, UNKNOWN, WARN, CheckResult, ValidationContext

STATUS_PASS = "PASS"
STATUS_PASS_WITH_UNKNOWN = "PASS_WITH_UNKNOWN"
STATUS_FAIL = "FAIL"


class Validator(Protocol):
    name: str

    async def check(self, ctx: ValidationContext) -> CheckResult: ...


@dataclass
class ValidationReport:
    status: str
    checks: dict[str, CheckResult]
    failures: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    seconds: float = 0.0
    # V1.1: medidos e abaixo do desejado, sem bloquear (ex.: pele, idade).
    warnings: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.status != STATUS_FAIL

    @property
    def face_score(self) -> float | None:
        check = self.checks.get("face_identity")
        return check.score if check else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": {k: c.to_dict() for k, c in self.checks.items()},
            "failures": self.failures,
            "unverified": self.unverified,
            "warnings": self.warnings,
            "seconds": self.seconds,
        }


class ValidationEngine:
    def __init__(self, validators: list[Validator]) -> None:
        self.validators = validators

    async def run(self, ctx: ValidationContext, seconds: float = 0.0) -> ValidationReport:
        checks: dict[str, CheckResult] = {}
        for validator in self.validators:
            checks[validator.name] = await validator.check(ctx)
        failures = [c.failure_type or c.name for c in checks.values() if c.blocking and c.status == FAIL]
        unverified = [c.name for c in checks.values() if c.blocking and c.status == UNKNOWN]
        warnings = [c.name for c in checks.values() if c.status == WARN or (not c.blocking and c.status == FAIL)]
        status = STATUS_FAIL if failures else STATUS_PASS_WITH_UNKNOWN if unverified else STATUS_PASS
        return ValidationReport(status, checks, failures, unverified, round(seconds, 2), warnings)
