"""FailureAnalyzer: o motivo provavel de uma reprovacao, a partir das
metricas. O RetryManager usa o tipo para escolher o que mudar."""
from __future__ import annotations

from app.core.validation.config import IdentityValidationConfig
from app.core.validation.types import Failure, IdentityValidationResult

# Abaixo disto uma metrica/parte conta como falha.
WEAK = 0.7
PART_FAILURES = {
    "eyes": ("eye_mismatch", "Distancia entre os olhos diferente da persona."),
    "nose": ("nose_mismatch", "Nariz com proporcao diferente da persona."),
    "mouth": ("mouth_mismatch", "Boca com largura diferente da persona."),
    "proportions": ("facial_structure_mismatch", "Proporcoes do rosto diferentes da persona."),
}
METRIC_FAILURES = {
    "hair": ("hairstyle_mismatch", "Cabelo diferente da persona."),
    "body": ("body_mismatch", "Corpo/proporcoes diferentes da persona."),
}
_ORDER = {"high": 0, "medium": 1, "low": 2}


def severity(gap: float) -> str:
    return "high" if gap > 0.3 else "medium" if gap > 0.15 else "low"


class FailureAnalyzer:
    def __init__(self, config: IdentityValidationConfig) -> None:
        self.config = config

    def analyze(self, result: IdentityValidationResult, threshold: float) -> list[Failure]:
        failures: list[Failure] = []
        if "face_not_found" in result.hard_failures:
            failures.append(Failure("face_not_found", "high", "Nenhum rosto na imagem: nao da para conferir a identidade."))
        if "sex_mismatch" in result.hard_failures:
            failures.append(Failure("sex_mismatch", "high", "O rosto encontrado e do outro sexo."))
        if result.identity_score is None:
            if not failures:
                failures.append(Failure("low_identity_confidence", "high", "A identidade nao pode ser medida."))
            return failures

        metrics = result.metrics
        face = result.face_similarity
        if face is not None and face < max(threshold, self.config.min_face_similarity):
            failures.append(Failure(
                "facial_structure_mismatch", severity(threshold - face),
                f"Rosto {face * 100:.0f}% parecido com a persona (ArcFace {metrics['face_similarity'].raw.get('cosine')}).",
            ))
        structure = metrics.get("facial_structure")
        if structure is not None and structure.measured:
            for part, value in sorted(structure.raw.get("parts", {}).items(), key=lambda kv: kv[1]):
                if value < WEAK:
                    kind, text = PART_FAILURES[part]
                    if not any(f.failure_type == kind for f in failures):
                        failures.append(Failure(kind, severity(1 - value), f"{text} ({value * 100:.0f}%)"))
        age = metrics.get("age")
        if age is not None and age.measured and age.score < WEAK:  # type: ignore[operator]
            failures.append(Failure("age_mismatch", severity(1 - age.score),  # type: ignore[operator]
                                    f"Idade estimada {age.raw.get('generated'):.0f}, esperada {age.raw.get('target'):.0f}."))
        for name, (kind, text) in METRIC_FAILURES.items():
            metric = metrics.get(name)
            if metric is not None and metric.measured and metric.score < WEAK:  # type: ignore[operator]
                failures.append(Failure(kind, severity(1 - metric.score), text))  # type: ignore[operator]
        distinctive = metrics.get("distinctive_features")
        if distinctive is not None and distinctive.measured and distinctive.raw.get("missing"):
            failures.append(Failure(
                "distinctive_feature_missing", severity(1 - distinctive.score),  # type: ignore[operator]
                f"Faltou: {', '.join(distinctive.raw['missing'])}.",
            ))
        if result.coverage < self.config.min_coverage:
            failures.append(Failure("low_identity_confidence", "medium",
                                    f"So {result.coverage * 100:.0f}% do peso da nota foi medido."))
        if not failures:
            failures.append(Failure("low_identity_confidence", severity(threshold - result.identity_score),
                                    f"Nota {result.identity_score * 100:.1f}% abaixo do limiar {threshold * 100:.1f}%."))
        return sorted(failures, key=lambda f: _ORDER[f.severity])
