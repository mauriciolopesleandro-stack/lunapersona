import pytest

from app.core.persona.sheet import PersonaSheetRepository
from app.core.validation.checks import (
    FAIL,
    INFORMATIONAL,
    NOT_COMPARABLE,
    PASS,
    UNKNOWN,
    AgeValidator,
    AnatomyValidator,
    BodyConsistencyValidator,
    FaceIdentityValidator,
    PoseValidator,
    SubjectCountValidator,
    TriggerLeakValidator,
    ValidationContext,
)
from app.core.validation.engine import STATUS_FAIL, STATUS_PASS, STATUS_PASS_WITH_UNKNOWN, ValidationEngine
from app.core.validation.geometry import pose_distance, ratio_deviation
from app.providers.base import ProviderImage
from tests.fakes import DUPLICATE, GOOD, LOW_FACE, STANDING, WALKING, WITH_EXTRA, analysis, body, face

IMG = ProviderImage("fake", "img", "http://x")


@pytest.fixture
def sheet(engine_dir):
    return PersonaSheetRepository(engine_dir).get("luna")


def ctx(sheet, a, **kw):
    return ValidationContext(sheet=sheet, image=IMG, analysis=a, **kw)


async def test_face_identity(sheet):
    ok = await FaceIdentityValidator().check(ctx(sheet, GOOD))
    assert ok.status == PASS and ok.threshold == 0.55 and ok.score == 0.68
    low = await FaceIdentityValidator().check(ctx(sheet, LOW_FACE))
    assert low.status == FAIL and low.failure_type == "face_identity_low"
    none = await FaceIdentityValidator().check(ctx(sheet, analysis([])))
    assert none.status == FAIL and none.failure_type == "face_not_found"
    # exatamente no limiar passa
    assert (await FaceIdentityValidator().check(ctx(sheet, analysis([face(0.55)])))).status == PASS
    # limiar do pedido vence o da ficha
    assert (await FaceIdentityValidator().check(ctx(sheet, LOW_FACE, threshold_override=0.4))).status == PASS


async def test_subject_count_separates_duplicate_from_other_people(sheet):
    dup = await SubjectCountValidator().check(ctx(sheet, DUPLICATE))
    assert dup.status == FAIL and dup.failure_type == "persona_duplicated"
    extra = await SubjectCountValidator().check(ctx(sheet, WITH_EXTRA))
    assert extra.status == PASS
    assert extra.evidence["other_faces"] == 1 and extra.evidence["background_bodies"] == 1
    # garcom grande, com rosto que nao e ela: permitido
    waiter = analysis([face(0.67), face(0.05, bbox=(600.0, 100.0, 690.0, 220.0))], [body(), body()])
    assert (await SubjectCountValidator().check(ctx(sheet, waiter))).status == PASS


async def test_subject_count_does_not_invent_verdict_for_faceless_body(sheet):
    faceless = analysis([face(0.66)], [body(), body()])
    result = await SubjectCountValidator().check(ctx(sheet, faceless))
    assert result.status == PASS and result.confidence == "LOW"
    assert "nao e verificavel" in result.reason


async def test_anatomy_is_unknown_without_detector(sheet):
    result = await AnatomyValidator().check(ctx(sheet, GOOD))
    assert result.status == UNKNOWN and result.blocking


class Detector:
    def __init__(self, result):
        self.result = result

    async def inspect(self, image, a):
        return self.result


async def test_anatomy_with_detector(sheet):
    fail = await AnatomyValidator(Detector({"hard_failures": ["duplicated torso"]})).check(ctx(sheet, GOOD))
    assert fail.status == FAIL and fail.failure_type == "anatomy"
    ok = await AnatomyValidator(Detector({"hard_failures": [], "verified": True})).check(ctx(sheet, GOOD))
    assert ok.status == PASS


async def test_pose(sheet):
    assert (await PoseValidator().check(ctx(sheet, GOOD))).status == INFORMATIONAL
    same = await PoseValidator().check(ctx(sheet, GOOD, requested_pose=STANDING))
    assert same.status == PASS and same.score == 0.0
    wrong = await PoseValidator().check(ctx(sheet, GOOD, requested_pose=WALKING))
    assert wrong.status == FAIL and wrong.failure_type == "pose_mismatch"


async def test_body_only_compared_in_matching_pose(sheet):
    other_pose = await BodyConsistencyValidator().check(ctx(sheet, GOOD, master_body_pose=WALKING))
    assert other_pose.status == NOT_COMPARABLE and other_pose.evidence["pose_match"] is False
    no_master = await BodyConsistencyValidator().check(ctx(sheet, GOOD))
    assert no_master.status == NOT_COMPARABLE
    same_pose = await BodyConsistencyValidator().check(ctx(sheet, analysis([face(0.7)], [body(WALKING)]),
                                                           master_body_pose=WALKING))
    assert same_pose.status in (PASS, FAIL) and same_pose.evidence["pose_match"] is True
    assert same_pose.score is not None


async def test_age_is_informational(sheet):
    young = await AgeValidator().check(ctx(sheet, analysis([face(0.7, age=22)])))
    assert young.status == INFORMATIONAL and young.evidence["inside"] is False and not young.blocking


class Reader:
    def __init__(self, text):
        self.text = text

    async def read(self, image):
        return self.text


async def test_trigger_leak(sheet):
    assert (await TriggerLeakValidator().check(ctx(sheet, GOOD))).status == UNKNOWN
    assert (await TriggerLeakValidator(Reader("CAFE LUNA VOX")).check(ctx(sheet, GOOD))).status == FAIL
    assert (await TriggerLeakValidator(Reader("CAFE PAULISTA")).check(ctx(sheet, GOOD))).status == PASS


def engine():
    return ValidationEngine([FaceIdentityValidator(), SubjectCountValidator(), AnatomyValidator(), PoseValidator(),
                             BodyConsistencyValidator(), AgeValidator(), TriggerLeakValidator()])


async def test_engine_unknown_is_not_pass(sheet):
    report = await engine().run(ctx(sheet, GOOD))
    assert report.status == STATUS_PASS_WITH_UNKNOWN and report.accepted
    assert report.unverified == ["anatomy"]
    assert set(report.to_dict()["checks"]) == {"face_identity", "subject_count", "anatomy", "pose",
                                               "body_consistency", "age", "trigger_leak"}
    for check in report.to_dict()["checks"].values():
        assert {"status", "score", "threshold", "evidence", "confidence", "reason"} <= set(check)


async def test_engine_fail_and_full_pass(sheet):
    report = await engine().run(ctx(sheet, DUPLICATE))
    assert report.status == STATUS_FAIL and "persona_duplicated" in report.failures
    verified = ValidationEngine([FaceIdentityValidator(), AnatomyValidator(Detector({"verified": True}))])
    assert (await verified.run(ctx(sheet, GOOD))).status == STATUS_PASS


def test_geometry():
    assert pose_distance(STANDING, STANDING) == 0.0
    assert pose_distance(STANDING, WALKING) > 0.25
    assert ratio_deviation({"a": 1.1, "b": 0.9, "c": 1.0}, {"a": 1.0, "b": 1.0, "c": 1.0}) == pytest.approx(0.0667, abs=1e-3)
    assert ratio_deviation({"a": 1.0}, {"a": 1.0}) is None
