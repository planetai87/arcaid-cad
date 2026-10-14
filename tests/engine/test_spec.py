"""객체 명세(src/engine/spec.py) 테스트: 형식 오류와 표준 대조로 쓰기 전에 거부한다"""
import copy
import json
import re
from pathlib import Path

import pytest

from src.engine import ENGINE_VERSION
from src.engine.spec import SpecError, parse_spec, spec_hash, validate_spec
from src.engine.standards import load_standard

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def std():
    return load_standard()


def base_spec():
    return {
        "schema_version": 1, "standard": "heo-2026@2026.1", "op": "t5-test", "units": "mm", "scale": 100,
        "grid": {"X": {"X1": 0, "X2": 3300}, "Y": {"Y1": 0, "Y2": 6000}},
        "items": [
            {"kind": "line", "id": "cen", "layer": "CEN", "start": ["X1", "Y1-450"], "end": ["X1", "Y2+450"]},
            {"kind": "group", "id": "room", "path": "3F/room/{no}",
             "items": [
                 {"kind": "polyline", "id": "wall", "layer": "WAL",
                  "points": [[0, 0], [3300, 0], [3300, 6000], [0, 6000]], "closed": True},
                 {"kind": "text", "id": "name", "layer": "ROM", "role": "general", "content": "객실 {no}",
                  "at": [1650, 3000], "align": "MC"},
                 {"kind": "arc", "id": "door", "layer": "DOR", "center": [2300, 6000], "radius": 900,
                  "start_angle": 180, "end_angle": 270},
                 {"kind": "hatch", "id": "floor", "layer": "HAT", "pattern": "ANSI31", "pattern_scale": 50,
                  "boundary": [[0, 0], [1000, 0], [1000, 1000]], "holes": []},
             ],
             "place": [{"at": ["X1", "Y1"], "repeat": {"count": 2, "step": [3300, 0]}, "vars": {"no": {"start": 301}}}]},
            {"kind": "block", "id": "bubble", "name": "축열버블-상", "at": ["X1", "Y2+2150"], "attribs": {"축열": "X1"}},
            {"kind": "dim_chain", "id": "dims", "style": "DIM330", "points": [["X1", "Y2+450"], ["X2", "Y2+450"]],
             "direction": "x", "line_at": "Y2+1350"},
            {"kind": "line", "id": "site", "layer": "LIM", "linetype": "PHANTOM", "start": [0, -2000], "end": [3300, -2000]},
        ],
    }


def codes(spec, std):
    return [v.code for v in validate_spec(parse_spec(spec), std)]


def test_engine_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1) == ENGINE_VERSION


def test_valid_spec_passes(std):
    assert codes(base_spec(), std) == []


def test_json_text_is_accepted_and_hash_ignores_key_order(std):
    spec = base_spec()
    reordered = json.loads(json.dumps(spec, sort_keys=True))
    assert spec_hash(parse_spec(json.dumps(spec))) == spec_hash(parse_spec(reordered))
    changed = copy.deepcopy(spec)
    changed["items"][0]["end"] = ["X1", "Y2+500"]
    assert spec_hash(parse_spec(changed)) != spec_hash(parse_spec(spec))
    assert re.fullmatch(r"[0-9a-f]{16}", spec_hash(parse_spec(spec)))


def _schema_error(spec):
    with pytest.raises(SpecError) as e:
        parse_spec(spec)
    return e.value.violations


def test_unsupported_kind_is_schema_error():
    spec = base_spec()
    spec["items"].append({"kind": "mtext", "layer": "TXT", "content": "x", "at": [0, 0]})
    (v,) = _schema_error(spec)
    assert v.code == "SPEC_SCHEMA" and "mtext" in v.message and "items[5]" in v.message


def test_unknown_field_is_schema_error():
    spec = base_spec()
    spec["items"][0]["color"] = 1          # 색은 표준이 정한다(ByLayer)
    assert [v.code for v in _schema_error(spec)] == ["SPEC_SCHEMA"]


def _mutate(path, value):
    spec = base_spec()
    target = spec
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return spec


@pytest.mark.parametrize("path, value, code", [
    (("standard",), "heo-2026@2025.9", "SPEC_STANDARD"),
    (("units",), "m", "SPEC_UNITS"),
    (("items", 0, "layer"), "A-CEN", "SPEC_LAYER"),
    (("items", 0, "layer"), "0", "SPEC_LAYER"),
    (("items", 4, "linetype"), "DASHED", "SPEC_LINETYPE"),
    (("items", 1, "items", 1, "role"), "huge", "SPEC_TEXT_ROLE"),
    (("items", 1, "items", 1, "role"), "main_title", "SPEC_TEXT_ROLE"),   # ROM에는 쓰지 않는 높이
    (("items", 2, "name"), "실명", "SPEC_BLOCK"),                         # status: unused
    (("items", 2, "name"), "MY_BLOCK", "SPEC_BLOCK"),
    (("items", 2, "attribs"), {"번호": "1"}, "SPEC_BLOCK"),
    (("items", 3, "style"), "Standard", "SPEC_DIMSTYLE"),
    (("items", 1, "items", 3, "pattern"), "NO_SUCH", "SPEC_HATCH"),
    (("items", 1, "items", 3, "boundary"), [[0, 0], [1000, 0]], "SPEC_HATCH"),
    (("items", 0, "start"), ["X9", "Y1"], "SPEC_GRID"),
    (("items", 0, "start"), ["Y1", "Y1"], "SPEC_GRID"),                  # x 자리에 Y축
    (("items", 1, "items", 0, "points"), [["X1", 0], [3300, 0]], "SPEC_GRID"),   # 그룹 안은 지역 좌표
    (("items", 3, "line_at"), "X2", "SPEC_GRID"),                        # direction x면 line_at은 y(Y축)
])
def test_rejections(std, path, value, code):
    assert codes(_mutate(path, value), std) == [code]


def test_block_with_attributes_cannot_be_mirrored(std):
    spec = base_spec()
    spec["items"].append({"kind": "group", "id": "g", "items": [
        {"kind": "block", "id": "b", "name": "축열버블-상", "at": [0, 0], "attribs": {"축열": "X1"}}],
        "place": [{"mirror": "x"}]})
    assert codes(spec, std) == ["SPEC_BLOCK"]
    spec["items"][-1]["items"][0] = {"kind": "block", "id": "b", "name": "계단화살표", "at": [0, 0]}
    assert codes(spec, std) == []                                        # 속성 없는 블록은 대칭 가능


def test_object_limit(std):
    spec = base_spec()
    spec["items"][1]["place"][0]["repeat"]["count"] = 1000
    spec["items"][1]["place"].append({"at": [0, 0], "repeat": {"count": 1000, "step": [0, 1]}, "vars": {"no": {"start": 1}}})
    nested = {"kind": "group", "id": "outer", "items": [spec["items"][1]], "place": [{"repeat": {"count": 10, "step": [0, 10000]}}]}
    spec["items"][1] = nested
    assert "SPEC_LIMIT" in codes(spec, std)
