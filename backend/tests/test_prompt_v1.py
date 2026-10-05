import json

from app.core.generation.negative import NegativePromptBuilder
from app.core.generation.prompt_builder import DEFAULT_CLOTHES, PromptBuilder, strip_trigger
from app.core.persona import PersonaRepository
from app.core.persona.sheet import PersonaSheetRepository, parse_sheet
from tests.conftest import SHEET
from tests.fakes import GLOBAL_NEGATIVE


def setup(engine_dir):
    return (PersonaSheetRepository(engine_dir).get("luna"), PersonaRepository(engine_dir).get("luna"),
            PromptBuilder(NegativePromptBuilder(GLOBAL_NEGATIVE)))


def test_trigger_only_from_sheet_at_the_start(engine_dir):
    sheet, profile, builder = setup(engine_dir)
    p = builder.build(sheet, profile, "Lunavox sign in a cafe, LUNA VOX written on the wall, sitting")
    assert p.subject == "lunavox, a woman"
    assert p.text.lower().count("lunavox") == 1 and p.text.startswith("lunavox, a woman")
    assert "vox" not in p.scene.lower()


def test_strip_trigger_variants():
    assert strip_trigger("a Luna-Vox poster, lunav ox", "lunavox") == "a poster"
    assert strip_trigger("walking in the park", "lunavox") == "walking in the park"


def test_leak_directive_only_when_enabled(engine_dir):
    sheet, profile, builder = setup(engine_dir)
    directive = sheet.generation["prompt_rules"]["trigger_leak_directive"]
    assert directive not in builder.build(sheet, profile, "street").directives
    data = json.loads(SHEET.read_text(encoding="utf-8"))
    data["generation_profile"]["prompt_rules"]["apply_trigger_leak_directive"] = True
    enabled = parse_sheet(data, sheet.path)
    assert directive in builder.build(enabled, profile, "street").directives


def test_retry_directives_and_default_clothes(engine_dir):
    sheet, profile, builder = setup(engine_dir)
    p = builder.build(sheet, profile, "at a kiosk", directives=["only one woman in the photo"])
    assert DEFAULT_CLOTHES in p.appearance
    assert p.text.endswith("only one woman in the photo")
    assert DEFAULT_CLOTHES not in builder.build(sheet, profile, "wearing a red dress at a kiosk").appearance


def test_negative_layers_are_separate_and_versioned(engine_dir):
    sheet, profile, builder = setup(engine_dir)
    p = builder.build(sheet, profile, "street")
    neg = p.negative
    assert "duplicated person" in neg.global_terms
    assert "tattoos" in neg.persona_terms
    assert "second copy of the same woman" in neg.scene_terms
    assert neg.version.startswith("negative-v1.0+") and p.version == "prompt-v1.0"
    # nada do negativo entra no texto positivo
    assert "tattoos" not in p.text
    single = builder.build(sheet, profile, "street", single_subject=False).negative
    assert single.scene_terms == []
