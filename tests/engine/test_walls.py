"""벽 합치기: 접합부 선·개구부·변환·해치·입력 상한을 실제 전개와 DXF로 확인한다."""
import ezdxf
import pytest

from src.engine.check import check_standard
from src.engine.layout import expand
from src.engine.spec import SpecError, parse_spec, spec_hash, validate_spec
from src.engine.standards import load_standard
from src.engine.write import build_dxf


@pytest.fixture(scope="module")
def std():
    return load_standard()


def rect(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def wall(parts=(), openings=(), **kw):
    return {"kind": "wall", "layer": "CON", "parts": list(parts), "openings": list(openings), **kw}


def spec(items):
    return parse_spec({"schema_version": 1, "standard": "heo-2026@2026.1", "op": "walls", "scale": 100,
                       "items": items})


def run(std, items, warnings=None):
    parsed = spec(items)
    assert validate_spec(parsed, std) == []
    return expand(parsed, std, 100, warnings=warnings)


def area(loop):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(loop, loop[1:] + loop[:1])) / 2


def room():
    return [rect(0, 0, 3000, 100), rect(0, 3900, 3000, 4000),
            rect(0, 0, 100, 4000), rect(2900, 0, 3000, 4000)]


@pytest.mark.parametrize("parts, vertices, size", [
    ([rect(0, 0, 6000, 200), rect(0, 0, 200, 4000)], 6, 1_960_000),
    ([rect(0, 0, 6000, 200), rect(2900, 0, 3100, 4000)], 8, 1_960_000),
    ([rect(0, 1900, 6000, 2100), rect(2900, 0, 3100, 4000)], 12, 1_960_000),
])
def test_junction_has_only_union_boundary(std, parts, vertices, size):
    got = run(std, [wall(parts)])
    assert len(got) == 1 and got[0].closed
    assert len(got[0].points) == vertices
    assert area(got[0].points) == size
    assert got[0].oid == "walls:wall/CON/1/outer"


def test_room_outer_and_hole(std):
    got = run(std, [wall(room())])
    assert [p.oid for p in got] == ["walls:wall/CON/1/outer", "walls:wall/CON/1/hole1"]
    assert [area(p.points) for p in got] == [12_000_000, -10_640_000]


def test_repeated_mirrored_rooms_merge_shared_wall(std):
    got = run(std, [{"kind": "group", "id": "room", "path": "floor/room/{i}",
                     "items": [wall(room())], "place": [
                         {"repeat": {"count": 2, "step": [2900, 0]}},
                         {"at": [0, 10000], "mirror": "y", "repeat": {"count": 2, "step": [2900, 0]}}]}])
    assert len(got) == 6
    assert sum(p.oid.endswith("/outer") for p in got) == 2
    assert all(p.path == "floor/room" for p in got)


def test_opening_only_group_cuts_redrawn_shared_wall(std):
    got = run(std, [wall([rect(0, 0, 6000, 200)]), wall([rect(0, 0, 6000, 200)]),
                    {"kind": "group", "items": [wall(openings=[rect(0, -10, 900, 210)])],
                     "place": [{"at": [2000, 0]}]}])
    assert len(got) == 2
    assert [area(p.points) for p in got] == [400_000, 620_000]
    assert max(x for x, y in got[0].points) == 2000
    assert min(x for x, y in got[1].points) == 2900


@pytest.mark.parametrize("rotation", [23.76, 89.9999999, -0.0000001])
def test_rotated_group_merges(std, rotation):
    got = run(std, [{"kind": "group", "items": [wall([rect(0, 0, 6000, 200), rect(0, 0, 200, 4000)])],
                     "place": [{"rotation": rotation, "at": [12000, -7000]}]}])
    assert len(got) == 1 and len(got[0].points) == 6
    assert area(got[0].points) == pytest.approx(1_960_000, abs=0.02)


def test_different_directions_warn_only_when_bounds_touch(std, tmp_path):
    items = [wall([rect(0, 0, 6000, 200)]),
             {"kind": "group", "items": [wall([rect(0, 0, 4000, 200)])], "place": [{"rotation": 23.76}]}]
    warnings = []
    assert len(run(std, items, warnings)) == 2
    assert len(warnings) == 1 and "방향이 다른 벽 접합부는 합치지 않음" in warnings[0]
    built = build_dxf(spec(items), std, tmp_path / "directions.dxf")
    assert built.status.value == "success" and built.warnings == warnings
    items[1]["place"][0]["at"] = [20000, 0]
    warnings = []
    assert len(run(std, items, warnings)) == 2 and warnings == []


def test_diagonal_contact_remains_two_regions(std):
    got = run(std, [wall([rect(0, 0, 100, 100), rect(100, 100, 200, 200)])])
    assert len(got) == 2 and all(area(p.points) == 10_000 for p in got)
    assert all(len(p.points) == 4 for p in got)


def test_same_layer_column_merges_but_material_boundary_remains(std):
    got = run(std, [wall([rect(0, 0, 6000, 200)], layer="con"),
                    wall([rect(-100, -100, 400, 400)]), wall([rect(0, 0, 6000, 200)], layer="WAL")])
    assert len(got) == 2 and {p.layer for p in got} == {"CON", "WAL"}
    assert area(got[0].points) == 1_370_000


def test_concave_polygon_and_coordinate_snap(std):
    polygon = [[0, 0], [6000, 0], [6000, 200], [200, 200], [200, 4000], [0, 4000]]
    got = run(std, [wall([polygon, rect(6000.0005, 0, 7000, 200)])])
    assert len(got) == 1 and len(got[0].points) == 6 and area(got[0].points) == 2_160_000


@pytest.mark.parametrize("polygon", [
    [[0, 0], [100, 0], [100, 100], [10, 90]],
    [[0, 0], [100, 0], [100, 100]],
    [[0, 0], [100, 0], [200, 0], [300, 0]],
    [[0, 0], [0, 0], [100, 100], [0, 100]],
])
def test_invalid_polygon_returns_spec_wall(std, polygon, tmp_path):
    built = build_dxf(spec([wall([polygon])]), std, tmp_path / "invalid.dxf")
    assert built.status.value == "failed" and {v.code for v in built.violations} == {"SPEC_WALL"}
    assert not (tmp_path / "invalid.dxf").exists()


def test_empty_wall_and_unmatched_opening_rejected(std, tmp_path):
    for items in ([wall()], [wall(openings=[rect(0, 0, 100, 100)])],
                  [wall([rect(0, 0, 100, 100)]), wall(openings=[rect(0, 0, 100, 100)], layer="WAL")]):
        built = build_dxf(spec(items), std, tmp_path / "invalid.dxf")
        assert built.status.value == "failed" and {v.code for v in built.violations} == {"SPEC_WALL"}


@pytest.mark.parametrize("hatch", [None, {"layer": "HAT", "pattern": "ANSI31"}])
def test_conflicting_hatch_settings_rejected(std, hatch, tmp_path):
    built = build_dxf(spec([wall(room(), hatch={"layer": "HAT", "pattern": "SOLID"}),
                            wall([rect(0, 0, 200, 200)], hatch=hatch)]), std, tmp_path / "invalid.dxf")
    assert {v.code for v in built.violations} == {"SPEC_WALL"}


def test_grid_limit_rejected_before_allocation(std, tmp_path):
    parts = [rect(i * 3, i * 3, i * 3 + 1, i * 3 + 1) for i in range(1002)]
    built = build_dxf(spec([wall(parts)]), std, tmp_path / "large.dxf")
    assert built.status.value == "failed" and {v.code for v in built.violations} == {"SPEC_LIMIT"}
    assert "op를 나눌" in built.violations[0].message


def test_actual_object_limit_is_checked_after_openings(std, monkeypatch):
    from src.engine import layout

    parsed = spec([wall([rect(0, 0, 6000, 200)], [rect(2000, -10, 2900, 210)])])
    assert validate_spec(parsed, std) == []
    monkeypatch.setattr(layout, "MAX_OBJECTS", 1)
    with pytest.raises(SpecError) as error:
        expand(parsed, std, 100)
    assert {v.code for v in error.value.violations} == {"SPEC_LIMIT"}


def test_requested_hatch_uses_union_loops_and_build_is_deterministic(std, tmp_path):
    parsed = spec([wall(room() + [rect(10000, 0, 10100, 100)],
                        hatch={"layer": "HAT", "pattern": "SOLID"})])
    first = build_dxf(parsed, std, tmp_path / "first.dxf")
    second = build_dxf(parsed, std, tmp_path / "second.dxf")
    assert first.status.value == second.status.value == "success"
    assert first.meta.spec_hash == second.meta.spec_hash == spec_hash(parsed)
    assert first.manifest == second.manifest
    assert first.meta.by_kind == {"hatch": 2, "polyline": 3}
    assert "wall" in first.scope.supported
    doc = ezdxf.readfile(tmp_path / "first.dxf")
    assert sorted(len(h.paths) for h in doc.modelspace().query("HATCH")) == [1, 2]
    assert all(p.closed for p in doc.modelspace().query("LWPOLYLINE"))
    checked = check_standard(tmp_path / "first.dxf", std, 100)
    assert checked.violations == [] and checked.unchecked == []


def test_wall_coordinates_and_hatch_are_validated(std):
    parsed = spec([{"kind": "group", "items": [wall([rect("X1", 0, 100, 100)],
                    hatch={"layer": "NO-LAYER", "pattern": "NO-PATTERN"})], "place": [{}]}])
    assert {v.code for v in validate_spec(parsed, std)} == {"SPEC_GRID", "SPEC_LAYER", "SPEC_HATCH"}


def test_nested_islands_are_assigned_to_smallest_containing_outer(std):
    got = run(std, [wall([rect(0, 0, 1000, 1000)], [rect(100, 100, 900, 900)]),
                    wall([rect(200, 200, 800, 300), rect(200, 700, 800, 800),
                          rect(200, 200, 300, 800), rect(700, 200, 800, 800)])])
    # 개구부는 같은 op의 모든 조각을 지운다. 중첩 영역은 처음부터 고리 조각으로 준다.
    assert len(got) == 2
    parts = [rect(0, 0, 1000, 100), rect(0, 900, 1000, 1000),
             rect(0, 0, 100, 1000), rect(900, 0, 1000, 1000),
             rect(200, 200, 800, 300), rect(200, 700, 800, 800),
             rect(200, 200, 300, 800), rect(700, 200, 800, 800)]
    got = run(std, [wall(parts)])
    assert [area(p.points) for p in got] == [1_000_000, -640_000, 360_000, -160_000]


def test_region_path_uses_only_contributing_parts(std):
    got = run(std, [wall([rect(0, 0, 100, 100)], path="floor/a/wall"),
                    wall([rect(1000, 0, 1100, 100)], path="floor/b/wall")])
    assert [p.path for p in got] == ["floor/a/wall", "floor/b/wall"]


def test_duplicate_wall_ids_are_rejected(std):
    with pytest.raises(SpecError) as error:
        run(std, [wall([rect(0, 0, 100, 100)], id="same"), wall([rect(100, 0, 200, 100)], id="same")])
    assert {v.code for v in error.value.violations} == {"SPEC_ID"}


def test_unmatched_opening_direction_rejected(std, tmp_path):
    built = build_dxf(spec([wall([rect(0, 0, 1000, 100)]),
                            {"kind": "group", "items": [wall(openings=[rect(0, 0, 200, 200)])],
                             "place": [{"rotation": 23.76}]}]), std, tmp_path / "invalid.dxf")
    assert {v.code for v in built.violations} == {"SPEC_WALL"}


def test_wall_grid_references_and_explicit_closed_loop(std):
    parsed = spec([wall([[["X1", 0], ["X2", 0], ["X2", 100], ["X1", 100], ["X1", 0]]])])
    parsed.grid.X = {"X1": 1000, "X2": 2000}
    assert validate_spec(parsed, std) == []
    got = expand(parsed, std, 100)
    assert len(got) == 1 and area(got[0].points) == 100_000


@pytest.mark.parametrize("side, rotation", [(50, 1), (10, 23.76)])
def test_small_rotated_parts_keep_the_same_direction(std, side, rotation):
    warnings = []
    got = run(std, [{"kind": "group", "items": [wall([rect(i * side, 0, (i + 1) * side, side)
                                                         for i in range(4)])],
                     "place": [{"rotation": rotation, "at": [12000, -7000]}]}], warnings)
    assert len(got) == 1 and len(got[0].points) == 4 and warnings == []
    assert area(got[0].points) == pytest.approx(4 * side * side, abs=0.001)


@pytest.mark.parametrize("mirror, angle", [(None, 30.76), ("x", 30.76), ("y", 210.76)])
def test_wall_hatch_pattern_follows_group_rotation(std, mirror, angle):
    got = run(std, [{"kind": "group", "items": [wall(room(), hatch={
        "layer": "HAT", "pattern": "ANSI31", "pattern_scale": 50, "pattern_angle": 7})],
        "place": [{"rotation": 23.76, "mirror": mirror}]}])
    hatch = next(p for p in got if p.kind == "hatch")
    assert hatch.pattern_angle == angle and hatch.pattern_scale == 50


def test_disconnected_wall_hatches_keep_each_group_angle(std):
    got = run(std, [{"kind": "group", "items": [wall([rect(0, 0, 6000, 200)], hatch={
        "layer": "HAT", "pattern": "ANSI31", "pattern_angle": 7})],
        "place": [{"at": [0, 0]}, {"at": [10000, 0], "rotation": 23.76}]}])
    assert [p.pattern_angle for p in got if p.kind == "hatch"] == [7, 30.76]


@pytest.mark.parametrize("pattern, fails", [("ANSI31", True), ("SOLID", False)])
def test_merged_region_rejects_conflicting_effective_hatch_angles(std, pattern, fails):
    items = [{"kind": "group", "items": [wall([rect(0, 0, 1000, 100)], hatch={
        "layer": "HAT", "pattern": pattern})], "place": [{}, {"rotation": 90}]}]
    if fails:
        with pytest.raises(SpecError) as error:
            run(std, items)
        assert {v.code for v in error.value.violations} == {"SPEC_WALL"}
    else:
        got = run(std, items)
        assert len(got) == 2 and sum(p.kind == "hatch" for p in got) == 1


def test_opening_only_repeat_has_preflight_work_limit(std):
    item = wall(openings=[rect(0, 0, 100, 100)])
    for i in range(4):
        item = {"kind": "group", "items": [item], "place": [{"repeat": {"count": 1000}}]}
    parsed = spec([wall([rect(0, 0, 6000, 200)]), item])
    assert {v.code for v in validate_spec(parsed, std)} == {"SPEC_LIMIT"}
