from app.core.generation import retry_policy
from app.core.generation.retry_policy import RELOCK_FACE, REGENERATE_SCENE, RetryPolicy


def test_face_fail_relocks_face_first_then_regenerates():
    policy = RetryPolicy(face_relock_before_regenerate=1)
    first = policy.decide(1, ["face_identity_low"], scene_seed=10, face_seed=11, relocks_done=0, directives=[])
    assert first.strategy == RELOCK_FACE and first.scene_seed == 10 and first.face_seed != 11
    second = policy.decide(2, ["face_identity_low"], 10, first.face_seed, relocks_done=1, directives=[])
    assert second.strategy == REGENERATE_SCENE and second.scene_seed != 10


def test_duplicate_and_missing_face_add_directives():
    d = RetryPolicy().decide(1, ["persona_duplicated", "face_not_found"], 5, 6, 0, [])
    assert d.strategy == REGENERATE_SCENE
    assert "only one woman in the photo, no mirrors" in d.directives and "her face clearly visible" in d.directives
    again = RetryPolicy().decide(2, ["persona_duplicated"], d.scene_seed, d.face_seed, 0, d.directives)
    assert again.directives.count("only one woman in the photo, no mirrors") == 1


def test_other_failures_regenerate_with_new_seed():
    for failure in ("pose_mismatch", "anatomy", "body_mismatch", "trigger_leak", "provider_error"):
        d = RetryPolicy().decide(1, [failure], 100, 101, 0, [])
        assert d.strategy == REGENERATE_SCENE and d.scene_seed != 100 and d.reason and d.attempt_number == 2


def test_seeds_are_deterministic():
    a = RetryPolicy().decide(2, ["anatomy"], 100, 101, 0, [])
    b = RetryPolicy().decide(2, ["anatomy"], 100, 101, 0, [])
    assert a == b


def test_policy_never_touches_lora():
    # A decisao nao tem como carregar forca de LoRA nem de identidade.
    fields = set(retry_policy.RetryDecision.__dataclass_fields__)
    assert not {f for f in fields if "lora" in f or "strength" in f}
