"""변경 명세 안내의 거부 코드·상한이 엔진 원본과 일치하는지 확인한다."""
import ast
from pathlib import Path
import re

from src.engine import changes

REFERENCE = (Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard"
             / "references" / "change-spec.md")


def test_change_reference_lists_all_engine_rejection_codes():
    source = ast.parse(Path(changes.__file__).read_text(encoding="utf-8"))
    codes = {node.value for node in ast.walk(source)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and re.fullmatch(r"CHG_[A-Z]+", node.value)}
    documented = set(re.findall(r"\bCHG_[A-Z]+\b", REFERENCE.read_text(encoding="utf-8")))
    assert documented == codes


def test_change_reference_limit_matches_engine():
    text = REFERENCE.read_text(encoding="utf-8")
    match = re.search(r"펼친 대상 상한은 ([\d,]+)개", text)
    assert match is not None, "변경 명세 안내에 펼친 대상 상한이 없음"
    assert int(match[1].replace(",", "")) == changes.MAX_CHANGE_OBJECTS
