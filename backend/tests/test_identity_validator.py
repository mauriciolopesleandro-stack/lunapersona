import pytest

from app.core.persona import PersonaProfile
from app.core.validation.analysis import DetectedFace
from app.core.validation.config import (
    IdentityValidationConfig,
    InvalidValidationConfigError,
    ThresholdPolicy,
)
from app.core.validation.scoring import ScoreEngine, face_ratios
from app.core.validation.validator import IdentityValidator
from app.providers.base import ProviderImage, ReferenceImage
from app.validation_backends.comfyui import to_face
from tests.conftest import REPO

# Rosto de frente "padrao": olhos a 100 px, nariz 60 px abaixo, boca 110 px.
KPS = [(100, 100), (200, 100), (150, 160), (115, 210), (185, 210)]


def face(sim=None, sex="F", age=27.0, yaw=0.0, kps=KPS, scale=1.0) -> DetectedFace:
    pts = [(x * scale, y * scale) for x, y in kps]
    return DetectedFace(bbox=(50 * scale, 40 * scale, 250 * scale, 280 * scale), det_score=0.9,
                        sex=sex, age=age, yaw=yaw, similarity=sim, kps=pts)


class FakeAnalyzer:
    def __init__(self, reference: DetectedFace | None, generated: list[DetectedFace] | dict[str, list[DetectedFace]]):
        self.reference = reference
        self.generated = generated

    async def check_ready(self):
        return []

    async def reference_face(self, reference):
        return self.reference

    async def generated_faces(self, image, reference):
        if isinstance(self.generated, dict):
            return self.generated.get(reference.reference_id, [])
        return self.generated


class FakeFeatures:
    def __init__(self, found, missing):
        self.result = (found, missing)

    async def check(self, image, keywords):
        return self.result


REF = ReferenceImage("r1", "r1.png", b"x", "PRIMARY", 1.0)
IMG = ProviderImage("fake", "img1", "http://fake/img1.png")


def persona(**identity) -> PersonaProfile:
    p = PersonaProfile(id="luna", name="Luna")
    p.identity.sex = identity.get("sex", "F")
    p.identity.apparent_age = identity.get("age", 27)
    p.identity.distinctive_keywords = identity.get("keywords", [])
    return p


async def validate(reference, generated, config=None, features=None, refs=None, **persona_kw):
    validator = IdentityValidator(FakeAnalyzer(reference, generated), config or IdentityValidationConfig(), features)
    return await validator.validate(refs or [REF], IMG, persona(**persona_kw))


# --- ScoreEngine ---------------------------------------------------------


def test_similarity_calibration():
    engine = ScoreEngine(IdentityValidationConfig())
    assert engine.face_similarity(0.60).score == 1.0
    assert engine.face_similarity(0.15).score == 0.0
    assert engine.face_similarity(0.375).score == pytest.approx(0.5)
    assert engine.face_similarity(0.9).score == 1.0  # nao passa de 1


def test_face_ratios_ignore_face_size():
    assert face_ratios(face()) == pytest.approx(face_ratios(face(scale=2.0)))


def test_structure_detects_which_part_changed():
    engine = ScoreEngine(IdentityValidationConfig())
    wide_mouth = [*KPS[:3], (95, 210), (205, 210)]
    metric = engine.facial_structure(face(), face(kps=wide_mouth))
    parts = metric.raw["parts"]
    assert parts["mouth"] < 0.5 and parts["eyes"] == 1.0 and parts["nose"] == 1.0


def test_structure_not_measured_in_profile():
    metric = ScoreEngine(IdentityValidationConfig()).facial_structure(face(), face(yaw=0.6))
    assert not metric.measured and "virado" in metric.note


def test_age_tolerance():
    engine = ScoreEngine(IdentityValidationConfig())
    assert engine.age(30, 27).score == 1.0
    assert engine.age(37, 27).score == pytest.approx(0.5)
    assert engine.age(50, 27).score == 0.0


def test_composite_renormalizes_over_measured_metrics():
    engine = ScoreEngine(IdentityValidationConfig())
    metrics = {
        "face_similarity": engine.metric("face_similarity", 1.0),
        "facial_structure": engine.metric("facial_structure", 0.5),
        "hair": engine.not_measured("hair", "x"),
        "body": engine.not_measured("body", "x"),
        "age": engine.not_measured("age", "x"),
        "distinctive_features": engine.not_measured("distinctive_features", "x"),
    }
    score, coverage = engine.composite(metrics)
    assert score == pytest.approx((0.4 * 1.0 + 0.2 * 0.5) / 0.6, abs=1e-4)
    assert coverage == pytest.approx(0.6)


# --- configuracao --------------------------------------------------------


def test_repo_config_loads_and_weights_are_configurable():
    config = IdentityValidationConfig.load(REPO / "config" / "identity_validation.json")
    assert config.weights["face_similarity"] == 0.40 and config.default_threshold == 0.90
    custom = IdentityValidationConfig.from_dict({"weights": {"face_similarity": 1.0}})
    assert custom.weights["facial_structure"] == 0.0


@pytest.mark.parametrize("data", [
    {"weights": {"face_similarity": -1}},
    {"weights": {"olhos": 0.5}},
    {"similarity_floor": 0.7, "similarity_ceiling": 0.6},
    {"default_threshold": 1.5},
])
def test_bad_config_rejected(data):
    with pytest.raises(InvalidValidationConfigError):
        IdentityValidationConfig.from_dict(data)


def test_threshold_precedence():
    policy = ThresholdPolicy(IdentityValidationConfig(default_threshold=0.9, provider_thresholds={"comfyui": 0.8}))
    assert policy.resolve(None, None, "outro") == 0.9
    assert policy.resolve(None, None, "comfyui") == 0.8
    assert policy.resolve(None, 0.7, "comfyui") == 0.7
    assert policy.resolve(0.6, 0.7, "comfyui") == 0.6


# --- validador -----------------------------------------------------------


async def test_same_person_scores_high():
    result = await validate(face(), [face(sim=0.62, age=28)])
    assert result.face_found and not result.hard_failures
    assert result.face_similarity == 1.0 and result.facial_structure_score == 1.0
    assert result.identity_score == pytest.approx(1.0)
    assert result.metrics["hair"].measured is False


async def test_different_person_scores_low():
    other = [*KPS[:2], (150, 185), (100, 235), (200, 235)]
    result = await validate(face(), [face(sim=0.08, kps=other, age=45)])
    assert result.face_similarity == 0.0
    assert result.identity_score < 0.3


async def test_picks_the_persona_among_several_faces():
    result = await validate(face(), [face(sim=0.05, sex="M"), face(sim=0.55)])
    assert result.metrics["face_similarity"].raw["cosine"] == 0.55
    assert not result.hard_failures


async def test_no_face_is_hard_failure_without_score():
    result = await validate(face(), [])
    assert result.hard_failures == ["face_not_found"]
    assert result.identity_score is None and not result.face_found


async def test_sex_mismatch_is_hard_failure():
    result = await validate(face(), [face(sim=0.5, sex="M")])
    assert "sex_mismatch" in result.hard_failures


async def test_best_of_several_references():
    refs = [REF, ReferenceImage("r2", "r2.png", b"y", "PROFILE", 0.8)]
    result = await validate(face(), {"r1": [face(sim=0.2, yaw=0.6)], "r2": [face(sim=0.58, yaw=0.6)]}, refs=refs)
    assert result.metrics["face_similarity"].raw["per_reference"] == {"r1": 0.2, "r2": 0.58}
    assert result.face_similarity > 0.9


async def test_distinctive_features_measured_when_configured():
    result = await validate(face(), [face(sim=0.6)], features=FakeFeatures(["necklace"], ["choker"]),
                            keywords=["necklace", "choker"])
    assert result.distinctive_features_score == 0.5
    assert result.metrics["distinctive_features"].raw["missing"] == ["choker"]


def test_luna_faces_payload_parses():
    parsed = to_face({"bbox": [1, 2, 3, 4], "score": 0.8, "sex": "F", "age": 26, "yaw": 0.1, "sim": 0.44,
                      "kps": [[1, 2]] * 5})
    assert parsed.similarity == 0.44 and parsed.age == 26.0 and len(parsed.kps) == 5
    # LunaFaces antigo (sem kps) continua valendo
    assert to_face({"bbox": [1, 2, 3, 4]}).kps == []
