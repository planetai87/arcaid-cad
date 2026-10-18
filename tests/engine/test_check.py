"""표준 검사(src/engine/check.py) 테스트: 위반을 심은 시험 도면마다 골든 파일과 정확히 일치해야 한다

골든은 핸들 대신 check_cases의 이름표로 적는다. 다시 만들려면 ARCAID_UPDATE_GOLDEN=1로 돌린 뒤
바뀐 내용을 심은 위반 목록과 한 줄씩 대조하고 커밋한다.
"""
import json
import os
from collections import Counter
from pathlib import Path

import ezdxf
import pytest

from check_cases import CASES, clean
from src.engine.check import DEFINITION_CODES, check_standard
from src.engine.results import Status
from src.engine.standards import load_standard

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def std():
    return load_standard()


def labeled(result, labels):
    """결과를 이름표로 바꾼 비교용 dict(핸들 → 이름표, 도면 단위 위반은 '도면')"""
    names = {h: n for n, h in labels.items()}

    def name(handle):
        return "도면" if handle is None else names.get(handle, f"?{handle}")

    data = {
        "violations": sorted([v.code, name(v.handle), v.current, v.expected, v.fixable] for v in result.violations),
        "unchecked": sorted([u.item, u.count] for u in result.unchecked),
        "fixes": sorted([f.property, f.value, sorted(name(h) for h in f.handles)] for f in result.fixes),
    }
    return json.loads(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str))


def golden(name, actual):
    path = GOLDEN / f"check_{name}.json"
    if os.environ.get("ARCAID_UPDATE_GOLDEN") == "1":
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def test_clean_drawing_has_no_violations(tmp_path, std):
    path, _ = clean(tmp_path / "clean.dxf")
    r = check_standard(path, std, k=100)
    assert r.status is Status.SUCCESS
    assert r.violations == [] and r.unchecked == [] and r.fixes == []
    assert r.is_complete() and r.assumptions == []
    assert r.meta.k == 100 and r.meta.k_assumed is False


@pytest.mark.parametrize("case", sorted(CASES))
def test_planted_violations_match_golden(tmp_path, std, case):
    path, labels = CASES[case](tmp_path / f"{case}.dxf")
    r = check_standard(path, std, k=100)
    actual = labeled(r, labels)
    assert actual == golden(case, actual)
    assert r.violation_counts == {code: sum(1 for v in r.violations if v.code == code)
                                  for code in {v.code for v in r.violations}}


def test_violation_has_location_and_fixes_group_handles(tmp_path, std):
    path, labels = CASES["entities"](tmp_path / "entities.dxf")
    r = check_standard(path, std, k=100)
    red = next(v for v in r.violations if v.handle == labels["red"] and v.code == "COLOR_NOT_BYLAYER")
    assert red.location == [0.0, 400.0] and red.expected == 256 and red.fixable
    color = next(f for f in r.fixes if f.property == "color")
    assert color.value == 256 and color.count == len(color.handles) == 4


def test_definition_mismatches_are_never_fixable(tmp_path, std):
    path, _ = CASES["definitions"](tmp_path / "definitions.dxf")
    r = check_standard(path, std, k=100)
    assert {v.code for v in r.violations} <= DEFINITION_CODES
    assert r.violations and not any(v.fixable for v in r.violations) and r.fixes == []


def test_scale_given_vs_assumed(tmp_path, std):
    path, labels = clean(tmp_path / "k200.dxf", k=200)
    ok = check_standard(path, std, k=200)
    assert ok.violations == [] and ok.is_complete()
    assumed = check_standard(path, std)
    assert assumed.meta.k == 100 and assumed.meta.k_assumed
    assert [(a.name, a.value) for a in assumed.assumptions] == [("scale", 100)]
    assert any("1/200" in w for w in assumed.warnings)
    actual = labeled(assumed, labels)
    assert actual == golden("scale_assumed", actual)


def test_units_not_mm_skips_text_heights(tmp_path, std):
    path, _ = clean(tmp_path / "m.dxf", insunits=6)
    r = check_standard(path, std, k=100)
    assert [v.code for v in r.violations] == ["UNITS_NOT_MM"]
    (u,) = r.unchecked
    assert u.item == "문자 높이" and u.count == 2 and not r.is_complete()


def test_limit_truncates_summary_but_full_file_has_all(tmp_path, std):
    path, _ = CASES["entities"](tmp_path / "entities.dxf")
    full = tmp_path / "work" / "snapshots" / "entities.check.json"
    r = check_standard(path, std, k=100, full_out=full, limit=1)
    everything = check_standard(path, std, k=100)
    assert r.truncated and r.full_path == str(full)
    assert all(n == 1 for n in Counter(v.code for v in r.violations).values())
    assert r.violation_counts == everything.violation_counts
    assert all(len(f.handles) <= 1 for f in r.fixes)
    saved = json.loads(full.read_text(encoding="utf-8"))
    assert len(saved["violations"]) == len(everything.violations) and not saved["truncated"]


def test_text_contents_are_not_in_messages(tmp_path, std):
    path, _ = CASES["text"](tmp_path / "text.dxf")
    r = check_standard(path, std, k=100)
    assert not any("큰" in v.message or "A-101" in v.message for v in r.violations)


def test_unreadable_file_fails(tmp_path, std):
    bad = tmp_path / "bad.dxf"
    bad.write_text("not a dxf", encoding="utf-8")
    r = check_standard(bad, std, k=100)
    assert r.status is Status.FAILED and not r.is_complete()


def test_unchecked_handles_are_internal_and_keep_public_result(tmp_path, std):
    path, labels = clean(tmp_path / "unchecked.dxf")
    doc = ezdxf.readfile(path)
    texts = [doc.modelspace().add_text("미검사", dxfattribs={"layer": "0"}) for _ in range(2)]
    doc.saveas(path)
    handles = {}
    public = check_standard(path, std, k=100)
    internal = check_standard(path, std, k=100, _unchecked_handles=handles)
    assert internal.model_dump(mode="json") == public.model_dump(mode="json")
    (u,) = internal.unchecked
    assert u.item == "높이 규칙이 없는 레이어의 문자" and u.count == 2
    key = (u.item, u.reason)
    assert handles[key] == {e.dxf.handle for e in texts}
    assert len(handles[key] & {texts[0].dxf.handle}) == 1
    assert not handles[key] & {labels["room"]}
    assert set(public.model_dump()["unchecked"][0]) == {"item", "reason", "count"}


def test_units_unchecked_is_global_in_internal_handles(tmp_path, std):
    path, _ = clean(tmp_path / "global.dxf", insunits=6)
    handles = {}
    r = check_standard(path, std, k=100, _unchecked_handles=handles)
    (u,) = r.unchecked
    assert handles[(u.item, u.reason)] is None
    assert u.count == 2
