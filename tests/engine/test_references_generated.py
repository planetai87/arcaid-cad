"""스킬 references의 표가 표준 YAML에서 생성된 그대로인지(손으로 고치지 않았는지) 확인한다"""
from pathlib import Path

import pytest

from src.engine.references import TARGETS, apply_regions, region_names
from src.engine.standards import load_standard

REFS = Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard" / "references"


@pytest.mark.parametrize("filename", sorted(TARGETS))
def test_reference_tables_are_generated_and_current(filename):
    text = (REFS / filename).read_text(encoding="utf-8")
    assert region_names(text) == TARGETS[filename], "생성 구역 표시가 없거나 다름"
    assert apply_regions(text, load_standard()) == text, (
        f"{filename}의 생성 표가 YAML과 다름: python tools/standards/gen_references.py --write")


def test_hand_edit_inside_region_is_detected():
    text = (REFS / "layers.md").read_text(encoding="utf-8")
    edited = text.replace("| CEN | 1 RED |", "| CEN | 3 GREEN |", 1)
    assert edited != text
    assert apply_regions(edited, load_standard()) == text


def test_unknown_region_name_rejected():
    with pytest.raises(ValueError, match="nope"):
        apply_regions("<!-- arcaid:generated nope -->\nx\n<!-- arcaid:end -->\n", load_standard())


def test_text_roles_are_retrievable_from_generated_table():
    # 높이만 제시하면 소비자가 유효한 명세의 role을 찾을 수 없다.
    std = load_standard().model_copy(deep=True)
    std.text.heights[0].id = "test_title_role"
    text = apply_regions("<!-- arcaid:generated text-heights -->\n<!-- arcaid:end -->", std)
    assert "test_title_role" in text


@pytest.mark.parametrize("region, value, expected", [
    ("dimension-spacing", "spacing", "2345"),
    ("dimension-styles", "style", "TEST_DIM"),
    ("sheet-title", "sheet", "test_sheet_role"),
    ("scale-rules", "scale", "125"),
])
def test_annotation_tables_follow_profile_changes(region, value, expected):
    # 숫자·ID를 renderer에 복제하면 프로파일 갱신 후 틀린 지침이 남는다.
    std = load_standard().model_copy(deep=True)
    if value == "spacing":
        std.dimensions.spacing.first_row_from_cen_end = 2345
    elif value == "style":
        std.dimensions.styles[0].name = "TEST_DIM"
    elif value == "sheet":
        std.text.sheet_title[0].id = "test_sheet_role"
    else:
        std.profile.scale.default_k = 125
    text = apply_regions(f"<!-- arcaid:generated {region} -->\n<!-- arcaid:end -->", std)
    assert expected in text


def test_block_reference_teaches_batch_attributes_without_legacy_insertion():
    std = load_standard()
    text = apply_regions("<!-- arcaid:generated blocks -->\n<!-- arcaid:end -->", std)
    assert "attribs" in text and "축열" in text
    assert "-INSERT" not in text and "insert_block" not in text
