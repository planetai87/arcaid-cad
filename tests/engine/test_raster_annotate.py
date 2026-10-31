"""범용 생성기의 주석·기호(C2, tools/raster/annotate.py): 문자·표식·레벨·방위·단면 표식·위생기구·물결 패턴·
그리드(축선·치수·버블)·계단·도곽.

이미지 없이 measure 기록을 직접 만든다. 점 관측은 측정 도구 blob이 기록하는 형식(외곽 상자와 그 중심)이다.
기대값은 표준(dimensions.yaml·text.yaml·blocks.yaml)과 stair.md의 매뉴얼 예시 수치에서 온다.
비교는 엔진이 전개한 Prim(WCS)으로 한다.
"""
import json

import pytest
from shapely.geometry import Point, Polygon

from tests.engine.test_raster_generate import _confirm, _est, _expanded, _frame, _measure, _obs, _wall, _wcs
from tools.raster import generate as gen

WING = (2200.0, 6540.0, 23.76)


# ---------- 시험 도구 ----------

def _pt(data, name, center, half=(100.0, 100.0), frame=(0.0, 0.0, 0.0)):
    """blob 점 관측: frame 지역 좌표의 외곽 상자 중심 center, 반폭 half"""
    u, v = center
    x, y = _wcs(frame, u, v)
    data["observations"][name] = {
        "kind": "point", "tool": "blob", "frame": list(frame), "point_local": [u, v], "point_wcs": [x, y],
        "bbox_local": [u - half[0], v - half[1], u + half[0], v + half[1]], "tol_mm": 10.0, "flags": [],
        "command": ["blob"], "pick": None, "image_sha256": "0" * 64, "calib_sha256": "0" * 64}
    return name


def _line(data, name, axis, coord, frame=(0.0, 0.0, 0.0), skew=0.0):
    data["observations"][name] = _obs(axis, coord, frame, skew)
    return name


def _text_value(v):
    return {"value": v, "source": "원본표기", "text": str(v)}


def _el(data, eid, etype, refs=None, frame=None, values=None, links=None, region=None):
    assert eid not in data["elements"], eid
    el = {"type": etype, "refs": refs or {}}
    for key, val in (("frame", frame), ("links", links), ("region", region)):
        if val:
            el[key] = val
    if values:
        el["values"] = {k: v if isinstance(v, dict) else _text_value(v) for k, v in values.items()}
    data["elements"][eid] = el


def _ready(data):
    r = gen.build(data, "t")
    assert r["status"] == "ready", r["problems"]
    return r


def _op(r, name):
    return next(o for o in r["ops"] if o["op"] == name)


def _prims(r, name, eid=None, kind=None, layer=None):
    out = []
    for p in _expanded(_op(r, name)):
        if (eid is None or f"/{eid}" in p.oid or f":{eid}" in p.oid or f"/{eid}." in p.oid) and \
                (kind is None or p.kind == kind) and (layer is None or p.layer == layer):
            out.append(p)
    return out


def _close(a, b, tol=1e-3):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _error(data, *words):
    r = gen.build(data, "t")
    assert r["status"] == "error", r
    assert any(all(w in p for w in words) for p in r["problems"]), r["problems"]


# ---------- 문자 ----------

def test_room_name_goes_at_the_box_center_with_special_codes():
    data = _measure()
    _el(data, "T1", "text", {"p": _pt(data, "T1-p", (3000.0, 2000.0), (400.0, 100.0))}, values={"content": "사무소±"})
    _confirm(data, "T1", role="실명")
    r = _ready(data)
    [t] = _prims(r, "t-anno-misc", "T1", "text")
    assert t.layer == "ROM" and t.align == "MC" and _close(t.at, (3000.0, 2000.0))
    assert t.text == "사무소%%P" and t.height == pytest.approx(135.0)


def test_text_from_four_lines_goes_at_the_room_center():
    data = _measure()
    refs = {"l": _line(data, "l", "v", 1000.0), "r": _line(data, "r", "v", 5000.0),
            "b": _line(data, "b", "u", 0.0), "t": _line(data, "t", "u", 3000.0)}
    _el(data, "T1", "text", refs, values={"content": "거실"})
    _confirm(data, "T1", role="실명")
    [t] = _prims(_ready(data), "t-anno-misc", "T1", "text")
    assert _close(t.at, (3000.0, 1500.0))


@pytest.mark.parametrize("role,layer,height", [("일반", "TXT", 135.0), ("제목", "TIT1", 255.0)])
def test_text_role_decides_layer_and_height(role, layer, height):
    data = _measure()
    _el(data, "T1", "text", {"p": _pt(data, "T1-p", (0.0, 0.0))}, values={"content": "EV"})
    _confirm(data, "T1", role=role)
    [t] = _prims(_ready(data), "t-anno-misc", "T1", "text")
    assert t.layer == layer and t.height == pytest.approx(height)


def test_door_and_window_tags_need_a_layer_and_use_small_tag():
    data = _measure()
    _el(data, "T1", "text", {"p": _pt(data, "T1-p", (0.0, 0.0))}, values={"content": "AW1"})
    _confirm(data, "T1", role="창호부호")
    _error(data, "T1", "layer")
    _confirm(data, "T1", layer="WIN")
    [t] = _prims(_ready(data), "t-anno-misc", "T1", "text")
    assert t.layer == "WIN" and t.height == pytest.approx(105.0)


def test_text_in_a_rotated_frame_is_placed_once_by_the_frame_group():
    data = _measure({"wing": _frame(*WING)})
    _el(data, "T1", "text", {"p": _pt(data, "T1-p", _wcs(WING, 1000.0, 500.0))}, frame="wing",
        values={"content": "TPS"})
    _confirm(data, "T1", role="실명")
    r = _ready(data)
    [t] = _prims(r, "t-anno-misc", "T1", "text")
    assert _close(t.at, _wcs(WING, 1000.0, 500.0)) and t.rotation == pytest.approx(23.76)


def test_text_refuses_a_line_for_a_point_and_missing_content():
    data = _measure()
    _el(data, "T1", "text", {"p": _line(data, "T1-p", "u", 0.0)}, values={"content": "A"})
    _confirm(data, "T1", role="실명")
    _el(data, "T2", "text", {"p": _pt(data, "T2-p", (0.0, 0.0))})
    _confirm(data, "T2", role="실명")
    r = gen.build(data, "t")
    assert r["status"] == "error"
    assert any("T1" in p and "점 관측" in p for p in r["problems"])
    assert any("T2" in p and "content" in p for p in r["problems"])


# ---------- 표식·레벨·방위 ----------

@pytest.mark.parametrize("layer,k,role_height,radius", [("DOR", 100, 105.0, 200.0), ("SYM", 200, 270.0, 400.0)])
def test_mark_is_a_circle_with_a_tag(layer, k, role_height, radius):
    data = _measure()
    data["output"]["k"] = k
    _el(data, "M1", "mark", {"p": _pt(data, "M1-p", (7382.0, 823.0))}, values={"content": "갑"})
    _confirm(data, "M1", layer=layer)
    r = _ready(data)
    [c] = _prims(r, "t-anno-misc", "M1", "circle")
    [t] = _prims(r, "t-anno-misc", "M1", "text")
    assert c.layer == layer and c.radius == pytest.approx(radius) and _close(c.center, (7382.0, 823.0))
    assert t.text == "갑" and t.height == pytest.approx(role_height) and _close(t.at, (7382.0, 823.0))


def test_level_block_sits_at_the_symbol_box_corner_with_its_text():
    data = _measure()
    refs = {"p": _pt(data, "L-p", (5000.0, 3000.0), (500.0, 100.0)), "t": _pt(data, "L-t", (5400.0, 3300.0))}
    _el(data, "LV1", "level", refs, values={"content": "FL±0"})
    r = _ready(data)
    [b] = _prims(r, "t-anno-misc", "LV1", "block")
    [t] = _prims(r, "t-anno-misc", "LV1", "text")
    assert b.name == "LEV" and _close(b.at, (4500.0, 2900.0)) and b.xscale == pytest.approx(0.5)
    assert t.layer == "LEV" and t.text == "FL%%P0" and _close(t.at, (5400.0, 3300.0))


@pytest.mark.parametrize("flip,rotation", [(False, -60.0), (True, 120.0)])
def test_north_arrow_turns_with_its_measured_axis(flip, rotation):
    data = _measure()
    refs = {"p": _pt(data, "N-p", (20000.0, 15000.0), (1250.0, 1500.0)), "dir": _line(data, "N-d", "u", 0.0, skew=30.0)}
    _el(data, "N1", "north", refs)
    _confirm(data, "N1", flip="true" if flip else "false")
    [b] = _prims(_ready(data), "t-anno-misc", "N1", "block")
    assert b.name == "방위" and _close(b.at, (20000.0, 15000.0))
    assert (b.rotation - rotation) % 360 == pytest.approx(0.0, abs=1e-6)


# ---------- 단면 표식 ----------

def _section(data, look="left"):
    refs = {"l": _line(data, "S-l", "u", 4760.0), "p": _pt(data, "S-p", (-2830.0, 4900.0), (405.0, 405.0)),
            "q": _pt(data, "S-q", (11050.0, 5100.0), (60.0, 350.0))}
    _el(data, "SC1", "section", refs, values={"num": "1", "sheet": "A-304", "seg_a": 3430.0, "seg_b": 1900.0})
    _confirm(data, "SC1", look=look)


def test_section_mark_sits_on_the_cut_line_and_fills_outside_its_circle():
    data = _measure()
    _section(data)
    r = _ready(data)
    [c] = _prims(r, "t-anno-misc", "SC1", "circle")
    assert c.layer == "SYM" and c.radius == pytest.approx(300.0) and _close(c.center, (-2830.0, 4760.0))
    hatches = _prims(r, "t-anno-misc", "SC1", "hatch")
    assert all(h.layer == "SYM" and h.pattern == "SOLID" for h in hatches)
    fills = [Polygon(h.loops[0]) for h in hatches if ".fill" in h.oid]
    assert len(fills) == 3                         # 원이 삼각형 빗변을 두 번 넘어 밑변 양 끝·꼭짓점 세 조각
    disk = Point(-2830.0, 4760.0).buffer(300.0)
    tri = Polygon([(-2830.0 - 405, 4760.0), (-2830.0 + 405, 4760.0), (-2830.0, 4760.0 + 405)])
    assert all(f.is_valid and f.intersection(disk).area < 1.0 for f in fills)
    assert sum(f.area for f in fills) == pytest.approx(tri.difference(disk).area, rel=0.01)
    assert any(".flag_fill" in h.oid for h in hatches)
    texts = {t.text: t for t in _prims(r, "t-anno-misc", "SC1", "text")}
    assert _close(texts["1"].at, (-2830.0, 4760.0 + 135.0)) and _close(texts["A-304"].at, (-2830.0, 4760.0 - 135.0))
    assert texts["1"].layer == "SYM" and texts["1"].rotation == pytest.approx(0.0)
    lines = sorted((p.points for p in _prims(r, "t-anno-misc", "SC1", "line")), key=lambda q: q[0][0])
    assert [-2830.0 + 300, 4760.0] in [list(map(float, pt)) for ln in lines for pt in ln]  # 구분선 끝
    seg = [ln for ln in lines if _close(ln[0], (-2830.0, 4760.0)) or _close(ln[1], (-2830.0, 4760.0))]
    assert any(_close(sorted(ln)[1], (600.0, 4760.0)) for ln in seg)                        # 실선 구간 A
    assert any(_close(ln[0], (9150.0, 4760.0)) and _close(ln[1], (11050.0, 4760.0)) for ln in lines)


def test_section_looking_right_puts_the_triangle_on_the_other_side():
    data = _measure()
    _section(data, look="right")
    r = _ready(data)
    texts = {t.text: t for t in _prims(r, "t-anno-misc", "SC1", "text")}
    assert _close(texts["1"].at, (-2830.0, 4760.0 - 135.0))
    fills = [Polygon(h.loops[0]) for h in _prims(r, "t-anno-misc", "SC1", "hatch") if ".fill" in h.oid]
    assert all(f.centroid.y < 4760.0 for f in fills)


# ---------- 위생기구 ----------

def test_basin_fills_its_measured_box():
    data = _measure()
    _el(data, "F1", "fixture", {"box": _pt(data, "F1-b", (7452.0, 3590.0), (170.0, 260.0))})
    _confirm(data, "F1", kind="세면기", back="u+")
    r = _ready(data)
    polys = _prims(r, "t-anno-misc", "F1", "polyline")
    assert all(p.layer == "TOL" and p.closed for p in polys) and len(polys) == 2
    outer = max(polys, key=lambda p: Polygon(p.points).area)
    assert Polygon(outer.points).equals(Polygon([(7282, 3330), (7622, 3330), (7622, 3850), (7282, 3850)]))
    [drain] = _prims(r, "t-anno-misc", "F1", "circle")
    assert _close(drain.center, (7452.0, 3590.0)) and drain.radius == pytest.approx(30.0)


@pytest.mark.parametrize("back,tank_side", [("v-", "low"), ("v+", "high")])
def test_toilet_tank_goes_against_the_wall_side(back, tank_side):
    data = _measure()
    _el(data, "F1", "fixture", {"box": _pt(data, "F1-b", (0.0, 360.0), (210.0, 360.0))})
    _confirm(data, "F1", kind="변기", back=back)
    polys = _prims(_ready(data), "t-anno-misc", "F1", "polyline")
    tank = min(polys, key=lambda p: Polygon(p.points).area)
    ys = [p[1] for p in tank.points]
    assert (max(ys) <= 200.0) if tank_side == "low" else (min(ys) >= 520.0)
    for p in polys:
        assert all(-210.001 <= x <= 210.001 and -0.001 <= y <= 720.001 for x, y in p.points)


def test_fixture_refuses_a_tiny_box_and_a_box_from_another_frame():
    data = _measure({"wing": _frame(*WING)})
    _el(data, "F1", "fixture", {"box": _pt(data, "F1-b", (0.0, 0.0), (40.0, 40.0))})
    _confirm(data, "F1", kind="세면기", back="u+")
    _el(data, "F2", "fixture", {"box": _pt(data, "F2-b", (0.0, 0.0), (200.0, 200.0))}, frame="wing")
    _confirm(data, "F2", kind="세면기", back="u+")
    r = gen.build(data, "t")
    assert any("F1" in p and "100" in p for p in r["problems"])
    assert any("F2" in p and "frame" in p for p in r["problems"])


# ---------- 물결 패턴 ----------

def _scallop(data, pitch=200.0, side="right", end=2000.0):
    refs = {"l": _line(data, "P-l", "u", 0.0), "s": _line(data, "P-s", "v", 0.0), "e": _line(data, "P-e", "v", end)}
    _el(data, "P1", "pattern", refs, values={"pitch": pitch, "radius": 58.0, "first": 100.0})
    _confirm(data, "P1", shape="scallop", side=side, layer="WAL")


@pytest.mark.parametrize("side,start", [("right", 180.0), ("left", 0.0)])
def test_scallop_repeats_half_circles_along_its_base_line(side, start):
    data = _measure()
    _scallop(data, side=side)
    r = _ready(data)
    [base] = _prims(r, "t-anno-misc", "P1", "line")
    assert base.layer == "WAL" and _close(base.points[0], (0, 0)) and _close(base.points[1], (2000, 0))
    arcs = sorted(_prims(r, "t-anno-misc", "P1", "arc"), key=lambda a: a.center[0])
    assert len(arcs) == 10 and _close(arcs[0].center, (100, 0)) and _close(arcs[-1].center, (1900, 0))
    assert arcs[0].radius == pytest.approx(58.0) and arcs[0].start_angle == pytest.approx(start)


@pytest.mark.parametrize("pitch,end,word", [(0.0, 2000.0, "pitch"), (-5.0, 2000.0, "pitch"),
                                            (100.0, 2000.0, "pitch"), (120.0, 200000.0, "1000")])
def test_scallop_refuses_bad_spacing_or_too_many_repeats(pitch, end, word):
    data = _measure()
    _scallop(data, pitch=pitch, end=end)
    _error(data, "P1", word)


# ---------- 그리드 ----------

def _grid(data, frame_key="wcs", xs=(0.0, 6000.0, 9000.0), ys=(0.0, 5000.0), frame=None, bubbles=None, by="user"):
    data["grids"][frame_key] = {"X": {f"X{i + 1}": _est(x) for i, x in enumerate(xs)},
                                "Y": {f"Y{i + 1}": _est(y) for i, y in enumerate(ys)}}
    _el(data, "G1", "grid", frame=frame)
    if bubbles:
        data["interpretations"]["G1"] = {"bubbles": {"value": bubbles, "status": "사용자확정" if by == "user"
                                                     else "확정", "by": by}}


def _cen(r, name):
    [ln] = [p for p in _prims(r, "t-grid", kind="line", layer="CEN") if p.oid.endswith(f"{name}.cen")]
    return ln.points


@pytest.mark.parametrize("k", [100, 200])
def test_grid_lines_dims_and_bubbles_follow_the_standard_spacing(k):
    s = k / 100
    data = _measure()
    data["output"]["k"] = k
    _grid(data)
    r = _ready(data)
    assert _close(_cen(r, "X2")[0], (6000.0, -450.0 * s)) and _close(_cen(r, "X2")[1], (6000.0, 5000.0 + 450.0 * s))
    assert _close(_cen(r, "Y1")[0], (-450.0 * s, 0.0)) and _close(_cen(r, "Y1")[1], (9000.0 + 450.0 * s, 0.0))
    dims = _prims(r, "t-grid", kind="dim")
    top = sorted(d.points[2][1] for d in dims if d.angle == pytest.approx(0.0))
    row1, row2 = 5000.0 + 450.0 * s + 900.0 * s, 5000.0 + 450.0 * s + 1300.0 * s
    assert top[0] == pytest.approx(row1) and top[-1] == pytest.approx(row2)
    assert sum(1 for d in dims if d.angle == pytest.approx(0.0)) == 3          # 축 체인 2구간 + 전체 1
    assert all(d.name == "DIM330" for d in dims)
    left = [d for d in dims if d.angle == pytest.approx(90.0)]
    assert len(left) == 1 and left[0].points[2][0] == pytest.approx(-450.0 * s - 900.0 * s)   # 축 2개면 전체 없음
    blocks = {b.attribs["축열"]: b for b in _prims(r, "t-grid", kind="block")}
    assert blocks["X3"].name == "축열버블-상" and _close(blocks["X3"].at, (9000.0, row2))
    assert blocks["Y2"].name == "축열버블-좌" and _close(blocks["Y2"].at, (-450.0 * s - 900.0 * s, 5000.0))


def test_grid_lines_reach_past_walls_of_the_same_frame_only():
    data = _measure({"wing": _frame(*WING)})
    _grid(data)
    _wall(data, "EA", faces=(-200.0, 0.0), ends=(-300.0, 9800.0))
    _wall(data, "EW", faces=(0.0, 200.0), ends=(0.0, 30000.0), frame_name="wing", frame=WING)
    r = _ready(data)
    assert _close(_cen(r, "Y1")[0], (-600.0, 0.0)) and _close(_cen(r, "Y1")[1], (10100.0, 0.0))
    assert _close(_cen(r, "X1")[0], (0.0, -500.0)) and _close(_cen(r, "X1")[1], (0.0, 5450.0))


def test_bottom_bubbles_are_an_exception_only_the_user_can_approve():
    data = _measure()
    _grid(data, bubbles="하좌", by="main")
    _error(data, "G1", "표준 예외")
    data = _measure()
    _grid(data, bubbles="하좌")
    r = _ready(data)
    assert not [b for b in _prims(r, "t-grid", kind="block") if b.name == "축열버블-상"]
    outer = -450.0 - 1300.0
    circles = {round(c.center[0]): c for c in _prims(r, "t-grid", kind="circle")}
    assert _close(circles[6000].center, (6000.0, outer - 800.0)) and circles[6000].radius == pytest.approx(300.0)
    texts = {t.text: t for t in _prims(r, "t-grid", kind="text")}
    assert texts["X2"].layer == "DIM" and texts["X2"].height == pytest.approx(225.0) and texts["X2"].rotation == 0
    assert any(_close(sorted(ln.points)[0], (6000.0, outer - 500.0)) for ln in _prims(r, "t-grid", kind="line",
                                                                                       layer="DIM"))


def test_grid_in_a_rotated_frame_turns_with_its_frame():
    data = _measure({"wing": _frame(*WING)})
    _grid(data, frame_key="wing", xs=(0.0, 4000.0), ys=(0.0, 3000.0), frame="wing")
    r = _ready(data)
    assert _close(_cen(r, "X2")[0], _wcs(WING, 4000.0, -450.0))
    [b] = [b for b in _prims(r, "t-grid", kind="block") if b.attribs["축열"] == "X2"]
    assert b.rotation == pytest.approx(23.76) and _close(b.at, _wcs(WING, 4000.0, 3000.0 + 450.0 + 900.0))


@pytest.mark.parametrize("xs,ys,frame_key,word", [((0.0, 0.0), (0.0,), "wcs", "겹침"), ((), (), "wcs", "축"),
                                                  ((0.0, 100.0), (0.0,), "nope", "grids")])
def test_grid_refuses_duplicate_or_missing_axes(xs, ys, frame_key, word):
    data = _measure()
    _grid(data, frame_key=frame_key, xs=xs, ys=ys)
    _error(data, "G1", word)


# ---------- 계단(stair.md 매뉴얼 예시: c=−253.1, xL0=−1645.6, xR1=1114.4, y0=53292, T=270, n=13) ----------

def _stair(data, up="b", n_a=13, n_b=13, last_a=None):
    y0, T = 53292.0, 270.0
    lines = {"ao": ("v", -1645.6), "h1": ("v", -378.1), "h2": ("v", -128.1), "bo": ("v", 1114.4),
             "at0": ("u", y0), "t2": ("u", y0 + T), "at1": ("u", last_a or y0 + (n_a - 1) * T),
             "bt0": ("u", y0), "bt1": ("u", y0 + (n_b - 1) * T), "land": ("u", y0 + 12 * T + 1200.0)}
    refs = {role: _line(data, f"ST-{role}", ax, c) for role, (ax, c) in lines.items()}
    _el(data, "ST1", "stair", refs)
    _confirm(data, "ST1", up=up)


def test_stair_reproduces_the_manual_example():
    data = _measure()
    _stair(data)
    r = _ready(data)
    assert any("ST1" in a for a in r["assumptions"])
    treads = [p for p in _prims(r, "t-stair-ST1", kind="line", layer="CIR")
              if p.points[0][1] == p.points[1][1] and 900 < abs(p.points[1][0] - p.points[0][0]) < 2000]
    assert len(treads) == 26
    blocks = _prims(r, "t-stair-ST1", kind="block")
    [z] = [b for b in blocks if b.name == "계단절단선Z"]
    assert _close(z.at, (493.15, 54606.9), 0.05)
    arrows = sorted((b for b in blocks if b.name == "계단화살표"), key=lambda b: b.at[1])
    assert _close(arrows[0].at, (493.15, 54446.9), 0.05) and arrows[0].rotation % 360 == pytest.approx(0.0)
    assert _close(arrows[1].at, (493.15, 54739.9), 0.05) and arrows[1].rotation % 360 == pytest.approx(180.0)
    texts = {t.text: t for t in _prims(r, "t-stair-ST1", kind="text")}
    assert _close(texts["UP"].at, (493.15, 53192.0), 0.05) and _close(texts["DN"].at, (-1011.85, 53192.0), 0.05)
    assert texts["UP"].layer == "TXT" and texts["UP"].align == "TC" and texts["UP"].height == pytest.approx(150.0)
    [walk] = [p for p in _prims(r, "t-stair-ST1", kind="polyline", layer="DIM")]
    assert _close(walk.points[0], (-1011.85, 53292.0), 0.05) and _close(walk.points[-1], (493.15, 57132.0), 0.05)


def test_stair_going_up_on_the_left_mirrors_the_symbols():
    data = _measure()
    _stair(data, up="a")
    r = _ready(data)
    texts = {t.text: t for t in _prims(r, "t-stair-ST1", kind="text")}
    assert _close(texts["UP"].at, (-1011.85, 53192.0), 0.05) and _close(texts["DN"].at, (493.15, 53192.0), 0.05)
    [z] = [b for b in _prims(r, "t-stair-ST1", kind="block") if b.name == "계단절단선Z"]
    assert _close(z.at, (-1011.85, 54606.9), 0.05)


@pytest.mark.parametrize("kwargs,word", [({"last_a": 53292.0 + 11.5 * 270.0}, "디딤선"), ({"n_b": 3}, "절단")])
def test_stair_refuses_uneven_treads_and_a_cut_past_the_last_tread(kwargs, word):
    data = _measure()
    _stair(data, **kwargs)
    _error(data, "ST1", word)


def test_stair_refuses_runs_in_the_wrong_order():
    data = _measure()
    _stair(data)
    data["observations"]["ST-ao"] = _obs("v", 0.0)
    _error(data, "ST1", "run")


# ---------- 도곽 ----------

def _sheet(data, width=20000.0, height=15000.0, title_under=None):
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, width))
    _wall(data, "EB", faces=(0.0, 200.0), ends=(0.0, height), axis="v")
    _el(data, "SH", "sheet", values={"name": "1층 평면도"})
    if title_under is not None:
        _confirm(data, "SH", title_under=title_under)


def test_sheet_frame_centers_the_drawing_and_fills_the_title_block():
    data = _measure()
    _sheet(data)
    r = _ready(data)
    assert r["ops"][-1]["op"] == "t-sheet"
    [b] = _prims(r, "t-sheet", kind="block")
    assert b.name == "HEOarchitects_sheet" and _close(b.at, (-7500.0, -7400.0))
    texts = {t.text: t for t in _prims(r, "t-sheet", kind="text")}
    assert _close(texts["1층 평면도"].at, (-7500.0 + 37400.0, -7400.0 + 3671.0))
    assert texts["SCALE   A1 : 1/50   A3 : 1/100"].layer == "TIT1" and "A-000" in texts


def test_sheet_refuses_a_drawing_too_big_for_the_frame():
    data = _measure()
    _sheet(data, width=40000.0)
    _error(data, "SH", "k")


def test_title_under_the_drawing_is_inside_the_frame():
    data = _measure()
    _sheet(data, title_under="true")
    r = _ready(data)
    texts = {t.text: t for t in _prims(r, "t-sheet", kind="text")}
    [b] = _prims(r, "t-sheet", kind="block")
    title = [t for t in _prims(r, "t-sheet", kind="text") if t.text == "1층 평면도" and t.at[0] < b.at[0] + 35000]
    assert title and title[0].at[1] < 0 and title[0].at[1] > b.at[1] + 300.0
    assert "1" in texts


# ---------- op ----------

def test_annotation_ops_come_after_shapes_and_sheet_comes_last():
    data = _measure()
    data["output"]["k"] = 300                      # 계단(y 5만대)까지 한 도곽에 들어가게
    _sheet(data)
    _grid(data)
    _stair(data)
    _el(data, "T1", "text", {"p": _pt(data, "T1-p", (3000.0, 2000.0))}, values={"content": "사무소"}, region="A")
    _confirm(data, "T1", role="실명")
    names = [o["op"] for o in _ready(data)["ops"]]
    assert names[-4:] == ["t-anno-A", "t-grid", "t-stair-ST1", "t-sheet"]
    assert names[0].startswith("t-wall-")


def test_a_region_named_like_an_annotation_op_is_refused():
    data = _measure()
    _grid(data)
    for eid, ax in (("L1", "u"),):
        refs = {"l": _line(data, "L-l", "u", 0.0), "s": _line(data, "L-s", "v", 0.0), "e": _line(data, "L-e", "v", 10.0)}
        _el(data, eid, "line", refs, region="grid")
        _confirm(data, eid, layer="HID")
    _error(data, "op", "t-grid")


def test_grid_warns_when_axis_numbers_do_not_follow_positions():
    data = _measure()
    data["grids"]["wcs"] = {"X": {"X2": _est(0.0), "X1": _est(5000.0)}, "Y": {"Y1": _est(0.0)}}
    _el(data, "G1", "grid")
    r = _ready(data)
    assert any("G1" in w and "X" in w and "순서" in w for w in r["warnings"])


# ---------- CLI: 형상 + 주석 전체 ----------

def _full_plan():
    data = _measure({"wing": _frame(*WING)})
    data["output"]["k"] = 300
    _sheet(data, title_under="true")
    _grid(data, xs=(0.0, 10000.0, 20000.0), ys=(0.0, 15000.0))
    _stair(data)
    _section(data)
    _el(data, "T1", "text", {"p": _pt(data, "T1-p", (3000.0, 2000.0))}, values={"content": "사무소"}, region="A")
    _confirm(data, "T1", role="실명")
    _el(data, "F1", "fixture", {"box": _pt(data, "F1-b", (7452.0, 3590.0), (170.0, 260.0))}, region="A")
    _confirm(data, "F1", kind="세면기", back="u+")
    _el(data, "T2", "text", {"p": _pt(data, "T2-p", (1000.0, 500.0), frame=WING)}, frame="wing",
        values={"content": "TPS"}, region="A")
    _confirm(data, "T2", role="실명")
    return data


def test_cli_writes_annotation_ops_that_pass_ops_verify_and_the_strict_check(tmp_path, capsys):
    from src import cli
    from tests.engine.test_raster_generate import _run_cli
    from tools.ops import ops
    rc, out, report = _run_cli(tmp_path, capsys, _full_plan())
    assert rc == 0 and report["status"] == "ready", report
    assert any("ST1" in a for a in report["assumptions"])
    manifest = json.loads((out / "preview" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["elements"]["T1"] == "t-anno-A" and manifest["elements"]["SH"] == "t-sheet"
    assert any("SC1" in a for a in manifest["assumptions"])
    assert ops.main(["verify", str(out / "ops")]) == 0
    assert [r["op"] for r in json.loads(capsys.readouterr().out)["ops"]][-3:] == ["t-grid", "t-stair-ST1", "t-sheet"]
    dxf = tmp_path / "preview.dxf"
    assert cli.main(["build", str(out / "preview" / "preview.json"), "-o", str(dxf)]) == 0
    capsys.readouterr()
    assert cli.main(["check", str(dxf), "--scale", "300", "--strict"]) == 0, capsys.readouterr().out


def test_sheet_position_does_not_depend_on_element_order():
    a = _full_plan()
    b = _full_plan()
    b["elements"] = dict(reversed(list(b["elements"].items())))
    b["observations"] = dict(reversed(list(b["observations"].items())))
    sheet = [gen.build(d, "t")["ops"][-1] for d in (a, b)]
    assert json.dumps(sheet[0], sort_keys=True) == json.dumps(sheet[1], sort_keys=True)
