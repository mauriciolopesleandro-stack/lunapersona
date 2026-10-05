"""QualityGate: decide se uma imagem medida pode ser aceita.

ACEITA so se tudo vale ao mesmo tempo:
- nenhuma falha grave (sem rosto, sexo trocado);
- a identidade foi medida e cobre ao menos min_coverage do peso;
- o rosto sozinho tem ao menos min_face_similarity;
- nota composta >= limiar. Nota exatamente no limiar ACEITA (comparacao
  com 4 casas, sem ruido de ponto flutuante).
"""
from __future__ import annotations

from dataclasses import replace

from app.core.validation.config import IdentityValidationConfig
from app.core.validation.types import ACCEPT, REJECT, IdentityValidationResult

HARD_REASONS = {
    "face_not_found": "Nenhum rosto encontrado na imagem.",
    "sex_mismatch": "O rosto encontrado nao e do mesmo sexo da persona.",
}


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


class QualityGate:
    def __init__(self, config: IdentityValidationConfig) -> None:
        self.config = config

    def decide(self, result: IdentityValidationResult, threshold: float) -> IdentityValidationResult:
        reasons = [HARD_REASONS.get(h, h) for h in result.hard_failures]
        score = result.identity_score
        if score is None:
            if not result.hard_failures:
                reasons.append("A identidade nao pode ser medida.")
        else:
            if result.coverage < self.config.min_coverage:
                reasons.append(f"Medicao insuficiente: so {_pct(result.coverage)} do peso foi medido.")
            face = result.face_similarity
            if face is not None and round(face, 4) < round(self.config.min_face_similarity, 4):
                reasons.append(f"Rosto pouco parecido ({_pct(face)}; minimo {_pct(self.config.min_face_similarity)}).")
            if round(score, 4) < round(threshold, 4):
                reasons.append(f"Nota de identidade {_pct(score)} abaixo do limiar {_pct(threshold)}.")
        return replace(result, status=REJECT if reasons else ACCEPT, reasons=reasons)
