"""IdentityValidator: confere se a imagem gerada e a mesma pessoa das fotos
de referencia da persona e devolve a nota composta com cada metrica.

- Rosto (ArcFace): comparado com ate 3 fotos de rosto ativas (principal,
  FACE, PROFILE); vale a mais parecida - uma foto de perfil cobre a imagem
  gerada de perfil.
- Estrutura: proporcoes dos 5 pontos do rosto contra a foto principal (so
  com os dois rostos de frente).
- Idade: a estimada contra a idade aparente da persona (ou a da foto).
- Tracos marcantes: palavras-chave da persona na descricao da imagem.
- Cabelo e corpo: sem modelo no pod - "nao medido".

Quem decide aceitar ou nao e o QualityGate; aqui so se mede.
"""
from __future__ import annotations

from app.core.persona import PersonaProfile
from app.core.validation.analysis import DetectedFace, FaceAnalyzer, FeatureChecker
from app.core.validation.config import IdentityValidationConfig
from app.core.validation.scoring import ScoreEngine
from app.core.validation.types import IdentityValidationResult
from app.providers.base import ProviderImage, ReferenceImage

MAX_REFERENCES = 3


class IdentityValidator:
    def __init__(
        self,
        analyzer: FaceAnalyzer,
        config: IdentityValidationConfig,
        features: FeatureChecker | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.config = config
        self.features = features

    async def check_ready(self) -> list[str]:
        return await self.analyzer.check_ready()

    async def validate(
        self, references: list[ReferenceImage], image: ProviderImage, persona: PersonaProfile
    ) -> IdentityValidationResult:
        engine = ScoreEngine(self.config)
        refs = references[:MAX_REFERENCES]
        reference_face = await self.analyzer.reference_face(refs[0]) if refs else None

        best: DetectedFace | None = None
        per_reference: dict[str, float] = {}
        for ref in refs:
            faces = [f for f in await self.analyzer.generated_faces(image, ref) if f.similarity is not None]
            if not faces:
                continue
            face = max(faces, key=lambda f: f.similarity)  # type: ignore[arg-type,return-value]
            per_reference[ref.reference_id] = round(face.similarity, 4)  # type: ignore[arg-type]
            if best is None or face.similarity > best.similarity:  # type: ignore[operator]
                best = face

        face_metric = engine.face_similarity(best.similarity if best else None)
        face_metric.raw["per_reference"] = per_reference
        target_age = persona.identity.apparent_age or (reference_face.age if reference_face else None)
        metrics = {
            "face_similarity": face_metric,
            "facial_structure": engine.facial_structure(reference_face, best),
            "hair": engine.not_measured("hair", "sem modelo de cabelo no pod"),
            "body": engine.not_measured("body", "sem modelo de corpo/proporcoes no pod"),
            "age": engine.age(best.age if best else None, target_age),
            "distinctive_features": await self._distinctive(engine, image, persona, best),
        }

        hard: list[str] = []
        if best is None and self.config.require_face:
            hard.append("face_not_found")
        sex = persona.identity.sex or (reference_face.sex if reference_face else None)
        if self.config.check_sex and sex and best and best.sex and best.sex != sex:
            hard.append("sex_mismatch")

        identity, coverage = engine.composite(metrics)
        if not face_metric.measured:
            # Sem o rosto nao ha como dizer que e ela, por melhor que o resto esteja.
            identity = None
        return IdentityValidationResult(
            identity_score=identity,
            face_similarity=face_metric.score,
            facial_structure_score=metrics["facial_structure"].score,
            appearance_score=engine.appearance(metrics),
            distinctive_features_score=metrics["distinctive_features"].score,
            coverage=coverage,
            face_found=best is not None,
            metrics=metrics,
            hard_failures=hard,
        )

    async def _distinctive(self, engine: ScoreEngine, image: ProviderImage, persona: PersonaProfile,
                           face: DetectedFace | None):
        keywords = persona.identity.distinctive_keywords
        if not keywords or self.features is None or face is None:
            return engine.distinctive(None, None)
        found = await self.features.check(image, keywords)
        return engine.distinctive(*found) if found else engine.distinctive(None, None)
