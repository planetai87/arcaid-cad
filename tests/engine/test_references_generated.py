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
