"""배치 전개(src/engine/layout.py) 테스트: 그리드 참조·반복·대칭·회전을 손으로 계산한 값과 비교한다"""
import math

import pytest

from src.engine.layout import expand
from src.engine.spec import SpecError, parse_spec
from src.engine.standards import load_standard


@pytest.fixture(scope="module")
def std():
    return load_standard()


def run(std, items, k=100, grid=None):
    spec = {"schema_version": 1, "standard": "heo-2026@2026.1", "op": "op", "units": "mm", "scale": k,
            "grid": grid or {"X": {"X1": 0, "X2": 3300}, "Y": {"Y1": 0, "Y2": 6000}}, "items": items}
    return {p.oid: p for p in expand(parse_spec(spec), std, k)}


def group(items, *place, gid="g", path=None):
    g = {"kind": "group", "id": gid, "items": items, "place": list(place) or [{}]}
    if path:
        g["path"] = path
    return g


def line(x1, y1, x2, y2, lid="l", layer="WAL"):
    return {"kind": "line", "id": lid, "layer": layer, "start": [x1, y1], "end": [x2, y2]}


def test_grid_reference_resolves(std):
    p = run(std, [{"kind": "line", "id": "c", "layer": "CEN", "start": ["X2+300", "Y1-450"], "end": ["X2", "Y2"]}])
    assert p["op:c"].points == [[3600.0, -450.0], [3300.0, 6000.0]]
    assert p["op:c"].path == "c" and p["op:c"].layer == "CEN"


def test_repeat_vars_paths_and_oids(std):
    text = {"kind": "text", "id": "name", "layer": "ROM", "role": "general", "content": "객실 {no}-{i}",
            "at": [1650, 3000], "align": "MC"}
    p = run(std, [group([text], {"at": ["X1", "Y1"], "repeat": {"count": 3, "step": [3300, 0]},
                                 "vars": {"no": {"start": 301}}},
                        {"at": [0, 20000], "repeat": {"count": 2, "step": [3300, 0]},
                         "vars": {"no": {"start": 310, "step": -1}}}, gid="room", path="3F/room/{no}")])
    got = [(oid, x.text, x.at, x.path) for oid, x in sorted(p.items())]
    assert got == [
        ("op:room[0]/name", "객실 301-0", [1650.0, 3000.0], "3F/room/301/name"),
        ("op:room[1]/name", "객실 302-1", [4950.0, 3000.0], "3F/room/302/name"),
        ("op:room[2]/name", "객실 303-2", [8250.0, 3000.0], "3F/room/303/name"),
        ("op:room[3]/name", "객실 310-0", [1650.0, 23000.0], "3F/room/310/name"),
        ("op:room[4]/name", "객실 309-1", [4950.0, 23000.0], "3F/room/309/name"),
    ]
    assert p["op:room[0]/name"].height == 135.0


def test_nested_transforms_compose(std):
    inner = group([line(0, 0, 10, 0)], {"at": [100, 0]}, gid="in")
    p = run(std, [group([inner], {"at": [1000, 0], "rotation": 90}, gid="out")])
    assert p["op:out[0]/in[0]/l"].points == [[1000.0, 100.0], [1000.0, 110.0]]
    assert p["op:out[0]/in[0]/l"].path == "out[0]/in[0]/l"


@pytest.mark.parametrize("place, center, angles", [
    ({"mirror": "x"}, [-1000.0, 0.0], (90.0, 180.0)),
    ({"mirror": "y"}, [1000.0, 0.0], (270.0, 0.0)),
    ({"rotation": 90}, [0.0, 1000.0], (90.0, 180.0)),
])
def test_arc_mirror_and_rotation(std, place, center, angles):
    arc = {"kind": "arc", "id": "a", "layer": "DOR", "center": [1000, 0], "radius": 900, "start_angle": 0,
           "end_angle": 90}
    a = run(std, [group([arc], place)])["op:g[0]/a"]
    assert a.center == center and (a.start_angle, a.end_angle) == angles and a.radius == 900


def test_polyline_bulge_flips_with_mirror(std):
    pl = {"kind": "polyline", "id": "p", "layer": "WAL", "points": [[0, 0], [1000, 0]], "bulges": [0.5, 0]}
    assert run(std, [group([pl], {"mirror": "x"})])["op:g[0]/p"].bulges == [-0.5, 0.0]
    assert run(std, [group([pl], {"rotation": 90})])["op:g[0]/p"].bulges == [0.5, 0.0]


@pytest.mark.parametrize("place, align, at, rotation, want_align", [
    ({"mirror": "x"}, "ML", [-100.0, 50.0], 0.0, "MR"),    # 좌우 대칭: 오른쪽 끝을 기준점에
    ({"mirror": "y"}, "TL", [100.0, -50.0], 0.0, "BL"),    # 상하 대칭: 아래를 기준점에
    ({"rotation": 180}, "MC", [-100.0, -50.0], 0.0, "MC"),  # 뒤집혀 읽히지 않게
    ({"rotation": 90}, "L", [-50.0, 100.0], 90.0, "L"),
])
def test_text_stays_readable(std, place, align, at, rotation, want_align):
    t = {"kind": "text", "id": "t", "layer": "TXT", "role": "general", "content": "A", "at": [100, 50], "align": align}
    got = run(std, [group([t], place)])["op:g[0]/t"]
    assert (got.at, got.rotation, got.align) == (at, rotation, want_align)


def _block_world(x, y, rot, xs, ys, q):
    r = math.radians(rot)
    lx, ly = q[0] * xs, q[1] * ys
    return (x + lx * math.cos(r) - ly * math.sin(r), y + lx * math.sin(r) + ly * math.cos(r))


@pytest.mark.parametrize("place", [{"mirror": "x"}, {"mirror": "y"}, {"rotation": 90, "mirror": "x"}])
def test_block_mirror_maps_geometry(std, place):
    blk = {"kind": "block", "id": "b", "name": "계단화살표", "at": [2000, 500], "rotation": 30}
    b = run(std, [group([blk], dict(place, at=[100, 200]))])["op:g[0]/b"]
    assert b.layer == "DIM" and abs(b.xscale) == 1.0 and b.yscale == 1.0 and b.xscale < 0
    th = math.radians(place.get("rotation", 0))
    for q in ((100, 0), (0, 100), (37, -12)):
        ox, oy = _block_world(2000, 500, 30, 1, 1, q)        # 지역 좌표의 원래 위치
        if place.get("mirror") == "x":
            ox = -ox
        else:
            oy = -oy
        want = (100 + ox * math.cos(th) - oy * math.sin(th), 200 + ox * math.sin(th) + oy * math.cos(th))
        got = _block_world(b.at[0], b.at[1], b.rotation, b.xscale, b.yscale, q)
        assert got == pytest.approx(want, abs=1e-6)


def _area(loop):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(loop, loop[1:] + loop[:1])) / 2


@pytest.mark.parametrize("place", [{}, {"mirror": "x"}, {"rotation": 45, "mirror": "y"}])
def test_hatch_loops_are_normalized(std, place):
    h = {"kind": "hatch", "id": "h", "layer": "HAT", "pattern": "ansi31", "pattern_scale": 50,
         "boundary": [[0, 0], [0, 2000], [2000, 2000], [2000, 0]],          # 시계 방향으로 줌
         "holes": [[[500, 500], [1500, 500], [1500, 1500], [500, 1500]]]}  # 반시계 방향으로 줌
    got = run(std, [group([h], place)])["op:g[0]/h"]
    assert got.pattern == "ANSI31" and len(got.loops) == 2
    assert _area(got.loops[0]) == pytest.approx(4_000_000) and _area(got.loops[1]) == pytest.approx(-1_000_000)


def test_dim_and_chain(std):
    chain = {"kind": "dim_chain", "id": "d", "style": "dim330", "points": [["X1", "Y2+450"], ["X2", "Y2+450"], [6600, 6450]],
             "direction": "x", "line_at": "Y2+1350"}
    p = run(std, [chain])
    assert sorted(p) == ["op:d.1", "op:d.2"]
    d1 = p["op:d.1"]
    assert (d1.name, d1.layer, d1.angle) == ("DIM330", "DIM", 0.0)
    assert d1.points == [[0.0, 6450.0], [3300.0, 6450.0], [1650.0, 7350.0]]
    dim = {"kind": "dim", "id": "v", "style": "DIM270", "p1": [0, 0], "p2": [0, 3000], "direction": "y", "line_at": -800}
    v = run(std, [group([dim], {"rotation": 90})])["op:g[0]/v"]
    assert v.layer == "DIM1" and v.angle == 0.0 and v.points == [[0.0, 0.0], [-3000.0, 0.0], [-1500.0, -800.0]]


def test_scale_sets_text_height_and_block_scale(std):
    items = [{"kind": "text", "id": "t", "layer": "ROM", "role": "general", "content": "A", "at": [0, 0]},
             {"kind": "block", "id": "b", "name": "축열버블-상", "at": [0, 0], "attribs": {"축열": "X1"}},
             {"kind": "block", "id": "lev", "name": "LEV", "at": [0, 0]},
             {"kind": "line", "id": "lim", "layer": "LIM", "linetype": "phantom", "start": [0, 0], "end": [1, 0]}]
    p = run(std, items, k=200)
    assert p["op:t"].height == 270.0 and p["op:t"].align == "L"
    assert (p["op:b"].xscale, p["op:b"].yscale, p["op:b"].attribs) == (2.0, 2.0, {"축열": "X1"})
    assert p["op:lev"].xscale == 1.0 and p["op:lev"].layer == "LEV"
    assert p["op:lim"].linetype == "PHANTOM"


def test_default_ids_and_determinism(std):
    items = [line(0, 0, 1, 0, lid=None), line(0, 1, 1, 1, lid=None)]
    for it in items:
        del it["id"]
    first = run(std, items)
    assert sorted(first) == ["op:line1", "op:line2"]
    assert [x.model_dump() for x in first.values()] == [x.model_dump() for x in run(std, items).values()]


def test_duplicate_oid_and_unknown_var_are_rejected(std):
    with pytest.raises(SpecError) as e:
        run(std, [line(0, 0, 1, 0, lid="same"), line(0, 1, 1, 1, lid="same")])
    assert [v.code for v in e.value.violations] == ["SPEC_ID"]
    text = {"kind": "text", "id": "t", "layer": "ROM", "role": "general", "content": "{nope}", "at": [0, 0]}
    with pytest.raises(SpecError) as e:
        run(std, [group([text])])
    assert [v.code for v in e.value.violations] == ["SPEC_SCHEMA"] and "nope" in e.value.violations[0].message
