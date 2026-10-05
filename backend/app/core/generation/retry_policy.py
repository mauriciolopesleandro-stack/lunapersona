"""RetryPolicy do Persona Engine V1: a proxima tentativa depende do TIPO de falha.

Nunca mexe na forca da LoRA (a auditoria mostrou: 1.3 nao melhorou o rosto e
envelheceu a persona). Sementes derivadas de forma deterministica, para a
geracao poder ser reproduzida.

  face_identity_low    refaz so o Face Lock (nova semente no Qwen), mantendo a cena;
                       se ja refez, regenera a cena
  face_not_found       regenera a cena pedindo o rosto visivel
  persona_duplicated   regenera a cena com "so uma mulher"
  pose_mismatch        regenera a cena (mesmo controle de pose; forcas acima de 0.8 nao foram testadas)
  anatomy              regenera a cena (nova semente)
  body_mismatch        regenera a cena (nova semente)
  trigger_leak         regenera a cena (nova semente)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SEED_STEP = 7919
RELOCK_FACE = "RELOCK_FACE"
REGENERATE_SCENE = "REGENERATE_SCENE"

DIRECTIVES = {
    "face_not_found": "her face clearly visible",
    "persona_duplicated": "only one woman in the photo, no mirrors",
}


@dataclass
class RetryDecision:
    attempt_number: int
    strategy: str
    reason: str
    failures: list[str]
    scene_seed: int
    face_seed: int
    directives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetryPolicy:
    def __init__(self, face_relock_before_regenerate: int = 1) -> None:
        self.face_relocks = face_relock_before_regenerate

    def decide(
        self, attempt: int, failures: list[str], scene_seed: int, face_seed: int,
        relocks_done: int, directives: list[str],
    ) -> RetryDecision:
        """attempt = tentativa que acabou de falhar; devolve o plano da proxima."""
        nxt = attempt + 1
        only_face = set(failures) == {"face_identity_low"}
        if only_face and relocks_done < self.face_relocks:
            return RetryDecision(nxt, RELOCK_FACE, "Rosto abaixo do limiar: refaz so o Face Lock, mantendo a cena.",
                                 failures, scene_seed, (face_seed + SEED_STEP) % (2**32), list(directives))
        added = [DIRECTIVES[f] for f in failures if f in DIRECTIVES]
        merged = list(dict.fromkeys([*directives, *added]))
        reason = "Regenera a cena com nova semente" + (f" e diretivas {added}" if added else "") + f" (falhas: {', '.join(failures)})."
        new_scene = (scene_seed + SEED_STEP * nxt) % (2**32)
        return RetryDecision(nxt, REGENERATE_SCENE, reason, failures, new_scene, (new_scene + 1) % (2**32), merged)
