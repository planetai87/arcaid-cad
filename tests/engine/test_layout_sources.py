"""전개 객체를 원본 명세 항목과 group 인스턴스로 되짚는 참조를 검증한다."""
import json
import re
from pathlib import Path

import pytest

from src.engine.layout import expand
from src.engine.spec import parse_spec
from src.engine.standards import load_standard


@pytest.fixture(scope="module")
def std():
    return load_standard()


def spec(items):
    return parse_spec({"schema_version": 1, "standard": "heo-2026@2026.1", "op": "source",
                       "scale": 100, "items": items})


def sources(prim):
    return [source.model_dump() for source in prim.sources]


def rect(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def test_top_level_primitive_sources(std):
    items = [
        {"kind": "line", "layer": "WAL", "start": [0, 0], "end": [100, 0]},
        {"kind": "polyline", "layer": "WAL", "points": [[0, 0], [100, 0]]},
        {"kind": "circle", "layer": "WAL", "center": [0, 0], "radius": 50},
        {"kind": "arc", "layer": "DOR", "center": [0, 0], "radius": 50,
         "start_angle": 0, "end_angle": 90},
        {"kind": "text", "layer": "ROM", "role": "general", "content": "객실", "at": [0, 0]},
        {"kind": "block", "name": "계단화살표", "at": [0, 0]},
        {"kind": "hatch", "layer": "HAT", "pattern": "SOLID", "boundary": rect(0, 0, 100, 100)},
        {"kind": "dim", "style": "DIM330", "p1": [0, 0], "p2": [100, 0],
         "direction": "x", "line_at": 200},
    ]
    got = expand(spec(items), std, 100)
    assert [p.kind for p in got] == ["line", "polyline", "circle", "arc", "text", "block", "hatch", "dim"]
    assert [sources(p) for p in got] == [
        [{"item": "items[0]", "groups": []}],
        [{"item": "items[1]", "groups": []}],
        [{"item": "items[2]", "groups": []}],
        [{"item": "items[3]", "groups": []}],
        [{"item": "items[4]", "groups": []}],
        [{"item": "items[5]", "groups": []}],
        [{"item": "items[6]", "groups": []}],
        [{"item": "items[7]", "groups": []}],
    ]


def test_nested_groups_keep_place_and_repeat_indices_with_mirrors(std):
    parsed = spec([{
        "kind": "group", "id": "outer", "place": [
            {"at": [1000, 0], "mirror": "y", "repeat": {"count": 2, "step": [500, 0]}}],
        "items": [
            {"kind": "circle", "layer": "WAL", "center": [0, 0], "radius": 50},
            {"kind": "group", "id": "inner", "place": [
                {"at": [100, 0], "mirror": "x", "repeat": {"count": 2, "step": [10, 0]}},
                {"at": [200, 0], "mirror": "y", "repeat": {"count": 2, "step": [0, 10]}}],
             "items": [{"kind": "line", "id": "target", "path": "같은 표시 경로", "layer": "WAL",
                        "start": [0, 0], "end": [10, 0]}]},
        ],
    }])
    got = [p for p in expand(parsed, std, 100) if p.kind == "line"]
    # place의 반복 번호 i는 매 place마다 0부터 다시 시작하며 oid의 누적 번호와 다르다.
    expected = [
        ("source:outer[0]/inner[0]/target", 0, 0, 0, [1100, 0]),
        ("source:outer[0]/inner[1]/target", 0, 0, 1, [1110, 0]),
        ("source:outer[0]/inner[2]/target", 0, 1, 0, [1200, 0]),
        ("source:outer[0]/inner[3]/target", 0, 1, 1, [1200, -10]),
        ("source:outer[1]/inner[0]/target", 1, 0, 0, [1600, 0]),
        ("source:outer[1]/inner[1]/target", 1, 0, 1, [1610, 0]),
        ("source:outer[1]/inner[2]/target", 1, 1, 0, [1700, 0]),
        ("source:outer[1]/inner[3]/target", 1, 1, 1, [1700, -10]),
    ]
    assert len(got) == len(expected)
    for prim, (oid, outer_i, place, inner_i, start) in zip(got, expected):
        assert prim.oid == oid and prim.points[0] == start
        assert sources(prim) == [{"item": "items[0].items[1].items[0]", "groups": [
            {"group": "items[0]", "place": 0, "i": outer_i},
            {"group": "items[0].items[1]", "place": place, "i": inner_i},
        ]}]


def test_dim_chain_segments_refer_to_same_item(std):
    parsed = spec([{"kind": "group", "place": [{"mirror": "x"}], "items": [
        {"kind": "dim_chain", "id": "chain", "style": "DIM330", "points": [[0, 0], [100, 0], [300, 0]],
         "direction": "x", "line_at": 200}]}])
    got = expand(parsed, std, 100)
    assert [p.oid for p in got] == ["source:group1[0]/chain.1", "source:group1[0]/chain.2"]
    assert [sources(p) for p in got] == [
        [{"item": "items[0].items[0]", "groups": [{"group": "items[0]", "place": 0, "i": 0}]}],
        [{"item": "items[0].items[0]", "groups": [{"group": "items[0]", "place": 0, "i": 0}]}],
    ]


def test_merged_wall_sources_are_unique_sorted_and_region_specific(std):
    parsed = spec([
        {"kind": "group", "place": [
            {"at": [0, 1000]},
            {"repeat": {"count": 2, "step": [100, 0]}},
        ], "items": [{"kind": "wall", "layer": "CON",
                      "parts": [rect(0, 0, 100, 100), rect(0, 0, 50, 100)],
                      "hatch": {"layer": "HAT", "pattern": "SOLID"}}]},
        {"kind": "wall", "layer": "CON", "parts": [rect(200, 0, 300, 100)],
         "openings": [rect(220, 20, 280, 80)], "hatch": {"layer": "HAT", "pattern": "SOLID"}},
    ])
    got = expand(parsed, std, 100)
    assert [p.oid for p in got] == [
        "source:wall/CON/1/outer", "source:wall/CON/1/hole1", "source:wall/CON/1/hatch",
        "source:wall/CON/2/outer", "source:wall/CON/2/hatch",
    ]
    merged = [
        {"item": "items[0].items[0]", "groups": [{"group": "items[0]", "place": 1, "i": 0}]},
        {"item": "items[0].items[0]", "groups": [{"group": "items[0]", "place": 1, "i": 1}]},
        {"item": "items[1]", "groups": []},
    ]
    separate = [{"item": "items[0].items[0]", "groups": [{"group": "items[0]", "place": 0, "i": 0}]}]
    assert [sources(p) for p in got] == [merged, merged, merged, separate, separate]
    assert [sources(p) for p in expand(parsed, std, 100)] == [merged, merged, merged, separate, separate]


def test_dangsan_sources_resolve_to_real_items_and_group_placements(std):
    raw = json.loads((Path(__file__).parent / "specs" / "dangsan_typical.json").read_text(encoding="utf-8"))

    def resolve(path):
        assert re.fullmatch(r"items\[\d+\](?:\.items\[\d+\])*", path), path
        node = raw
        for index in re.findall(r"items\[(\d+)\]", path):
            node = node["items"][int(index)]
        return node

    got = expand(parse_spec(raw), std, 100)
    assert len(got) == 107
    for prim in got:
        assert prim.sources, prim.oid
        for source in prim.sources:
            item = resolve(source.item)
            assert item["kind"] == prim.kind or (item["kind"], prim.kind) in {
                ("dim_chain", "dim"), ("wall", "polyline"), ("wall", "hatch")}
            ancestors = [source.item[:m.start()] for m in re.finditer(r"\.items\[", source.item)]
            assert [group.group for group in source.groups] == ancestors
            for group in source.groups:
                node = resolve(group.group)
                assert node["kind"] == "group"
                place = node["place"][group.place]
                assert 0 <= group.i < place.get("repeat", {}).get("count", 1)
