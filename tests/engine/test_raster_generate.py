"""범용 생성기(tools/raster/generate.py): measure.yaml → 미리보기 명세·ops 폴더.

이미지 없이 measure 기록을 직접 만든다. 관측은 frame 지역 좌표의 선(축·좌표)으로 주고 WCS 점·각도를 계산해
넣는다(측정 도구가 기록하는 형식과 같음). 기대 형상은 손으로 정한 면선 위치에서 온다.
"""
import copy
import json
import math

import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from src.engine.layout import expand
from src.engine.spec import parse_spec, validate_spec
from src.engine.standards import load_standard
from tools.raster import generate as gen

STD = load_standard()


# ---------- 시험 도구 ----------

def _est(v):
    return {"value": v, "source": "추정", "reason": "시험"}


def _frame(x, y, deg):
    return {"x": _est(x), "y": _est(y), "deg": _est(deg)}


def _wcs(frame, u, v):
    x0, y0, deg = frame
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return x0 + c * u - s * v, y0 + s * u + c * v


def _obs(axis, coord, frame=(0.0, 0.0, 0.0), skew=0.0, at=0.0):
    """frame 지역 좌표의 선: axis u면 v=coord를 지나 u 방향으로 뻗음(at은 선 위 다른 좌표). skew는 기울기(도)"""
    ang = (0.0 if axis == "u" else 90.0) + skew
    u, v = (at, coord) if axis == "u" else (coord, at)
    x, y = _wcs(frame, u, v)
    return {"kind": "line", "tool": "profile", "frame": list(frame), "axis": axis, "coord": coord,
            "angle_local_deg": ang, "angle_wcs_deg": (frame[2] + ang) % 180.0, "point_wcs": [x, y],
            "width_px": 2.0, "tol_mm": 10.0, "flags": [], "command": ["profile"], "pick": 0,
            "image_sha256": "0" * 64, "calib_sha256": "0" * 64}


def _measure(frames=None):
    return {"schema_version": 1, "kind": "measure", "units": "mm",
            "image": {"path": "plan.png", "sha256": "0" * 64},
            "calib": {"file": "calib.json", "file_sha256": "0" * 64, "px_per_mm": 0.06, "beta_deg": 0.0,
                      "origin_px": [0.0, 0.0], "err_mm": 1.0},
            "output": {"standard": "heo-2026@2026.1", "k": 100},
            "frames": frames or {}, "grids": {}, "legend": [], "regions": {},
            "observations": {}, "elements": {}, "interpretations": {}, "questions": []}


def _confirm(data, eid, **attrs):
    for k, v in attrs.items():
        data["interpretations"].setdefault(eid, {})[k] = {"value": v, "status": "사용자확정", "by": "user"}


def _wall(data, eid, faces, ends, frame_name=None, frame=(0.0, 0.0, 0.0), material="RC", skew=(0, 0, 0, 0),
          axis="u", etype="wall"):
    """axis u: 면선 v=faces(가로 벽), 끝선 u=ends. axis v: 면선 u=faces(세로 벽), 끝선 v=ends"""
    other = "v" if axis == "u" else "u"
    obs = data["observations"]
    for role, ax, c, sk in (("f1", axis, faces[0], skew[0]), ("f2", axis, faces[1], skew[1]),
                            ("s", other, ends[0], skew[2]), ("e", other, ends[1], skew[3])):
        obs[f"{eid}-{role}"] = _obs(ax, c, frame, sk)
    assert eid not in data["elements"], eid                    # 시험 데이터의 id 겹침(덮어쓰기) 방지
    el = {"type": etype, "refs": {r: f"{eid}-{r}" for r in ("f1", "f2", "s", "e")}}
    if frame_name:
        el["frame"] = frame_name
    data["elements"][eid] = el
    if material:
        _confirm(data, eid, material=material)


def _build(data, prefix="t"):
    return gen.build(data, prefix)


def _expanded(spec):
    s = parse_spec(json.dumps(spec))
    assert validate_spec(s, STD) == []
    return expand(s, STD, s.scale or 100)


def _area_shape(prims, layer):
    """닫힌 polyline(바깥 반시계·구멍 시계)을 부호 면적으로 합친 형상"""
    shapes = []
    for p in prims:
        if p.kind == "polyline" and p.closed and p.layer == layer:
            shapes.append(Polygon(p.points))
    outers = [s for s in shapes if s.exterior.is_ccw]
    holes = [s for s in shapes if not s.exterior.is_ccw]
    return unary_union(outers).difference(unary_union(holes)) if outers else Polygon()


def _same_shape(a, b, tol=1e-3):
    return a.symmetric_difference(b).area <= tol * max(a.length, b.length, 1.0)


# ---------- 벽: 직교·회전 frame ----------

def test_orthogonal_wall_becomes_an_engine_wall():
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0))
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    [op] = r["ops"]
    walls = [it for it in op["items"] if it["kind"] == "wall"]
    assert len(walls) == 1 and walls[0]["layer"] == "CON"
    shape = _area_shape(_expanded(op), "CON")
    assert _same_shape(shape, Polygon([(0, 0), (5000, 0), (5000, 200), (0, 200)]))


def test_wall_in_a_rotated_frame_goes_into_a_rotated_group_with_local_coordinates():
    fr = (2200.0, 6540.0, 23.76)
    data = _measure({"wing": _frame(*fr)})
    _wall(data, "EW", faces=(0.0, 150.0), ends=(0.0, 4000.0), frame_name="wing", frame=fr)
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    [op] = r["ops"]
    [group] = [it for it in op["items"] if it["kind"] == "group"]
    assert group["place"] == [{"at": [2200.0, 6540.0], "rotation": 23.76}]
    [wall] = [it for it in group["items"] if it["kind"] == "wall"]
    assert sorted(map(tuple, wall["parts"][0])) == [(0.0, 0.0), (0.0, 150.0), (4000.0, 0.0), (4000.0, 150.0)]
    want = Polygon([_wcs(fr, u, v) for u, v in ((0, 0), (4000, 0), (4000, 150), (0, 150))])
    assert _same_shape(_area_shape(_expanded(op), "CON"), want)


def test_faces_measured_slightly_off_axis_snap_to_the_frame():
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0), skew=(0.1, -0.1, 0.1, 0.0))
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    wall = next(it for it in r["ops"][0]["items"] if it["kind"] == "wall")
    for x, y in wall["parts"][0]:
        assert x in (0.0, 5000.0) and y in (0.0, 200.0)


def test_faces_more_than_half_a_degree_off_the_frame_are_refused():
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0), skew=(0.8, 0.8, 0, 0))
    r = _build(data)
    assert r["status"] == "error"
    assert any("EA" in p and "frame" in p for p in r["problems"])


@pytest.mark.parametrize("material,layer", [("RC", "CON"), ("조적", "WAL"), ("건식", "WAL1")])
def test_material_decides_the_wall_layer(material, layer):
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0), material=material)
    r = _build(data)
    assert next(it for it in r["ops"][0]["items"] if it["kind"] == "wall")["layer"] == layer


@pytest.mark.parametrize("faces,ends", [((0.0, 0.0), (0.0, 5000.0)), ((0.0, 1200.0), (0.0, 5000.0)),
                                        ((0.0, 200.0), (100.0, 100.0))])
def test_walls_with_no_or_huge_thickness_or_no_length_are_refused(faces, ends):
    data = _measure()
    _wall(data, "EA", faces=faces, ends=ends)
    assert _build(data)["status"] == "error"


def test_missing_material_or_unknown_type_is_an_error():
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0), material=None)
    data["elements"]["EB"] = {"type": "curtain", "refs": {}}
    r = _build(data)
    assert r["status"] == "error"
    assert any("EA" in p and "material" in p for p in r["problems"])
    assert any("EB" in p and "curtain" in p for p in r["problems"])


def test_pending_interpretation_or_open_question_gives_preview_only():
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0), material=None)
    data["interpretations"]["EA"] = {"material": {"value": "RC", "status": "확인필요", "by": "main"}}
    r = _build(data)
    assert r["status"] == "preview" and r["preview"] is not None
    assert any("EA.material" in p for p in r["pending"])
    _confirm(data, "EA", material="RC")
    data["questions"] = [{"id": "Q1", "text": "벽 끝?"}]
    r = _build(data)
    assert r["status"] == "preview" and any("Q1" in p for p in r["pending"])


# ---------- 개구부: 창·문·개구부 ----------

def _opening(data, eid, etype, host, ends, axis="v", frame=(0.0, 0.0, 0.0), skew=(0, 0), refs=None, **interp):
    """host 벽을 가로지르는 끝선 두 개(axis v면 u=ends)"""
    obs = data["observations"]
    for role, c, sk in (("s", ends[0], skew[0]), ("e", ends[1], skew[1])):
        obs[f"{eid}-{role}"] = _obs(axis, c, frame, sk)
    assert eid not in data["elements"], eid
    el = {"type": etype, "refs": {"s": f"{eid}-s", "e": f"{eid}-e", **(refs or {})}, "links": {"host": host}}
    data["elements"][eid] = el
    if interp:
        _confirm(data, eid, **interp)


def _host_wall():
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0))
    return data


def _lines(items, layer):
    out = []
    for it in items:
        if it["kind"] == "group":
            out += _lines(it["items"], layer)
        elif it["kind"] == "line" and it["layer"] == layer:
            out.append(sorted([tuple(it["start"]), tuple(it["end"])]))
    return sorted(out)


def test_window_cuts_its_host_wall_and_draws_frame_and_glass_lines():
    data = _host_wall()
    _opening(data, "EW", "window", "EA", (1000.0, 2500.0))
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    [op] = r["ops"]
    want = Polygon([(0, 0), (5000, 0), (5000, 200), (0, 200)]).difference(
        Polygon([(1000, 0), (2500, 0), (2500, 200), (1000, 200)]))
    assert _same_shape(_area_shape(_expanded(op), "CON"), want)
    assert _lines(op["items"], "WIN") == sorted([
        [(1000.0, 0.0), (2500.0, 0.0)], [(1000.0, 200.0), (2500.0, 200.0)], [(1000.0, 100.0), (2500.0, 100.0)],
        [(1000.0, 0.0), (1000.0, 200.0)], [(2500.0, 0.0), (2500.0, 200.0)]])
    assert r["manifest"]["EW"] == op["op"]


def test_window_glass_line_follows_its_own_observation():
    data = _host_wall()
    data["observations"]["EW-g"] = _obs("u", 60.0)
    _opening(data, "EW", "window", "EA", (1000.0, 2500.0), refs={"glass": "EW-g"})
    lines = _lines(_build(data)["ops"][0]["items"], "WIN")
    assert [(1000.0, 60.0), (2500.0, 60.0)] in lines and [(1000.0, 100.0), (2500.0, 100.0)] not in lines


def _arcs(items):
    out = []
    for it in items:
        if it["kind"] == "group":
            out += _arcs(it["items"])
        elif it["kind"] == "arc":
            out.append((tuple(it["center"]), it["radius"], it["start_angle"], it["end_angle"]))
    return out


@pytest.mark.parametrize("hinge,swing,angle,leaf,arc", [
    ("s", "f2", 90, [(3000.0, 200.0), (3000.0, 1100.0)], ((3000.0, 200.0), 900.0, 0.0, 90.0)),
    ("e", "f2", 90, [(3900.0, 200.0), (3900.0, 1100.0)], ((3900.0, 200.0), 900.0, 90.0, 180.0)),
    ("s", "f1", 90, [(3000.0, -900.0), (3000.0, 0.0)], ((3000.0, 0.0), 900.0, 270.0, 0.0)),
    ("s", "f2", 180, [(2100.0, 200.0), (3000.0, 200.0)], ((3000.0, 200.0), 900.0, 0.0, 180.0)),
])
def test_single_door_leaf_and_swing(hinge, swing, angle, leaf, arc):
    data = _host_wall()
    _opening(data, "ED", "door", "EA", (3000.0, 3900.0), hinge=hinge, swing=swing, angle=angle)
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    items = r["ops"][0]["items"]
    assert _lines(items, "DOR") == [leaf]
    assert _arcs(items) == [arc]


def test_double_door_has_two_half_leaves():
    data = _host_wall()
    _opening(data, "ED", "door", "EA", (3000.0, 4000.0), swing="f2", leaves=2)
    items = _build(data)["ops"][0]["items"]
    assert _lines(items, "DOR") == [[(3000.0, 200.0), (3000.0, 700.0)], [(4000.0, 200.0), (4000.0, 700.0)]]
    assert sorted(_arcs(items)) == [((3000.0, 200.0), 500.0, 0.0, 90.0), ((4000.0, 200.0), 500.0, 90.0, 180.0)]


def test_plain_opening_only_cuts_the_wall():
    data = _host_wall()
    _opening(data, "EO", "opening", "EA", (1000.0, 2000.0))
    [op] = _build(data)["ops"]
    want = Polygon([(0, 0), (5000, 0), (5000, 200), (0, 200)]).difference(
        Polygon([(1000, 0), (2000, 0), (2000, 200), (1000, 200)]))
    assert _same_shape(_area_shape(_expanded(op), "CON"), want)
    assert _lines(op["items"], "WIN") == [] and _lines(op["items"], "DOR") == []


def test_door_needs_swing_and_hinge_interpretations():
    data = _host_wall()
    _opening(data, "ED", "door", "EA", (3000.0, 3900.0))
    r = _build(data)
    assert r["status"] == "error" and any("ED" in p and "swing" in p for p in r["problems"])


@pytest.mark.parametrize("case", ["no_host", "host_not_wall", "outside", "other_frame", "missing_host"])
def test_openings_with_a_bad_host_are_refused(case):
    data = _measure({"wing": _frame(0.0, 0.0, 30.0)})
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0))
    _opening(data, "EW", "window", "EA", (4500.0, 5500.0) if case == "outside" else (1000.0, 2000.0))
    if case == "no_host":
        del data["elements"]["EW"]["links"]
    elif case == "host_not_wall":
        _opening(data, "EX", "opening", "EA", (3000.0, 3500.0))
        data["elements"]["EW"]["links"]["host"] = "EX"
    elif case == "other_frame":
        data["elements"]["EW"]["frame"] = "wing"
    elif case == "missing_host":
        data["elements"]["EW"]["links"]["host"] = "NOPE"
    r = _build(data)
    assert r["status"] == "error"
    assert any(p.startswith("EW") for p in r["problems"])


def test_openings_in_a_rotated_wall_stay_in_the_wall_frame_group():
    fr = (1000.0, 500.0, 30.0)
    data = _measure({"wing": _frame(*fr)})
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 5000.0), frame_name="wing", frame=fr)
    _opening(data, "EW", "window", "EA", (1000.0, 2500.0), frame=fr)
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    [op] = r["ops"]
    [group] = op["items"]
    assert group["kind"] == "group" and [it["kind"] for it in group["items"]].count("line") == 5
    want = Polygon([_wcs(fr, u, v) for u, v in ((0, 0), (5000, 0), (5000, 200), (0, 200))]).difference(
        Polygon([_wcs(fr, u, v) for u, v in ((1000, 0), (2500, 0), (2500, 200), (1000, 200))]))
    assert _same_shape(_area_shape(_expanded(op), "CON"), want)


# ---------- 방향이 섞인 묶음: 생성기가 합친다 ----------

SLANT = (3000.0, 0.0, 30.0)


def _l_junction(window=False):
    """WCS 가로 벽 EA(0~3100) 끝에 30° 사선 벽 EB가 겹쳐 붙는다"""
    data = _measure({"slant": _frame(*SLANT)})
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 3100.0))
    _wall(data, "EB", faces=(0.0, 200.0), ends=(0.0, 3000.0), frame_name="slant", frame=SLANT)
    if window:
        _opening(data, "EW", "window", "EB", (1000.0, 2000.0), frame=SLANT)
    return data


def _pieces_union():
    a = Polygon([(0, 0), (3100, 0), (3100, 200), (0, 200)])
    b = Polygon([_wcs(SLANT, u, v) for u, v in ((0, 0), (3000, 0), (3000, 200), (0, 200))])
    return a.union(b)


def test_walls_of_two_directions_join_into_one_outline_without_a_seam():
    r = _build(_l_junction())
    assert r["status"] == "ready", r["problems"]
    [op] = r["ops"]
    assert not [it for it in op["items"] if it["kind"] == "wall"]
    prims = _expanded(op)
    outlines = [p for p in prims if p.kind == "polyline" and p.layer == "CON"]
    assert len(outlines) == 1 and outlines[0].closed
    assert _same_shape(_area_shape(prims, "CON"), _pieces_union())


def test_window_on_a_slanted_wall_is_cut_from_the_joined_outline():
    r = _build(_l_junction(window=True))
    assert r["status"] == "ready", r["problems"]
    [op] = r["ops"]
    hole = Polygon([_wcs(SLANT, u, v) for u, v in ((1000, 0), (2000, 0), (2000, 200), (1000, 200))])
    assert _same_shape(_area_shape(_expanded(op), "CON"), _pieces_union().difference(hole))
    assert len(_lines(op["items"], "WIN")) == 5


def test_engine_and_generator_paths_give_the_same_orthogonal_outline():
    data = _host_wall()
    _wall(data, "EC", faces=(0.0, 250.0), ends=(200.0, 3000.0), axis="v")       # 세로 벽(L자)
    _opening(data, "EW", "window", "EA", (1000.0, 2500.0))
    engine = _build(data)
    forced = gen.build(data, "t", force_union=True)
    assert [it["kind"] for it in engine["ops"][0]["items"]].count("wall") == 1
    assert not [it for it in forced["ops"][0]["items"] if it["kind"] == "wall"]
    assert _same_shape(_area_shape(_expanded(engine["ops"][0]), "CON"),
                       _area_shape(_expanded(forced["ops"][0]), "CON"))


def _ring(hatch):
    """벽 네 개로 닫힌 고리(안쪽이 구멍)"""
    data = _measure()
    _wall(data, "EA", faces=(0.0, 200.0), ends=(0.0, 3000.0))
    _wall(data, "EB", faces=(2800.0, 3000.0), ends=(0.0, 3000.0))
    _wall(data, "EC", faces=(0.0, 200.0), ends=(0.0, 3000.0), axis="v")
    _wall(data, "ED", faces=(2800.0, 3000.0), ends=(0.0, 3000.0), axis="v")
    for eid in ("EA", "EB", "EC", "ED"):
        _confirm(data, eid, hatch=hatch)
    return data


def test_hatch_on_a_joined_outline_is_a_solid_hatch_with_holes():
    r = gen.build(_ring(True), "t", force_union=True)
    assert r["status"] == "ready", r["problems"]
    [hatch] = [it for it in r["ops"][0]["items"] if it["kind"] == "hatch"]
    assert hatch["layer"] == "HAT" and hatch["pattern"] == "SOLID" and len(hatch["holes"]) == 1
    shape = Polygon(hatch["boundary"], hatch["holes"])
    want = Polygon([(0, 0), (3000, 0), (3000, 3000), (0, 3000)], [[(200, 200), (2800, 200), (2800, 2800), (200, 2800)]])
    assert _same_shape(shape, want)
    assert _same_shape(_area_shape(_expanded(r["ops"][0]), "CON"), want)


def test_wall_without_hatch_request_has_no_hatch():
    for force in (False, True):
        r = gen.build(_ring(False), "t", force_union=force)
        spec = json.dumps(r["ops"][0])
        assert '"hatch"' not in spec


def test_hatch_requests_must_agree_within_a_cluster():
    data = _l_junction()
    _confirm(data, "EA", hatch=True)
    r = _build(data)
    assert r["status"] == "error" and any("해치" in p for p in r["problems"])


def test_a_wall_strip_thinner_than_a_millimetre_is_refused():
    data = _host_wall()
    _opening(data, "EO", "opening", "EA", (1000.0, 4999.5))
    r = _build(data)
    assert r["status"] == "error" and any("1 mm" in p for p in r["problems"])


def test_an_opening_reaching_into_another_cluster_is_refused():
    data = _host_wall()
    _wall(data, "EC", faces=(0.0, 200.0), ends=(1800.0, 2200.0), material="조적")   # 다른 레이어 벽이 겹침
    _opening(data, "EW", "window", "EA", (1000.0, 2500.0))
    r = _build(data)
    assert r["status"] == "error" and any(p.startswith("EW") and "EC" in p for p in r["problems"])


def test_hatch_request_on_an_engine_wall_uses_the_wall_hatch():
    r = _build(_ring(True))
    [wall] = [it for it in r["ops"][0]["items"] if it["kind"] == "wall"]
    assert wall["hatch"] == {"layer": "HAT", "pattern": "SOLID"}
    assert [p.kind for p in _expanded(r["ops"][0])].count("hatch") == 1


# ---------- 선: 마감선·일반 선·교점 꼭짓점 ----------

def _seg_el(data, eid, etype, line, ends, axis="u", frame=(0.0, 0.0, 0.0), frame_name=None, region=None, **interp):
    other = "v" if axis == "u" else "u"
    obs = data["observations"]
    obs[f"{eid}-l"] = _obs(axis, line, frame)
    obs[f"{eid}-s"] = _obs(other, ends[0], frame)
    obs[f"{eid}-e"] = _obs(other, ends[1], frame)
    assert eid not in data["elements"], eid
    el = {"type": etype, "refs": {"l": f"{eid}-l", "s": f"{eid}-s", "e": f"{eid}-e"}}
    if frame_name:
        el["frame"] = frame_name
    if region:
        el["region"] = region
    data["elements"][eid] = el
    if interp:
        _confirm(data, eid, **interp)


def _all(items, kind):
    out = []
    for it in items:
        if it["kind"] == "group":
            out += _all(it["items"], kind)
        elif it["kind"] == kind:
            out.append(it)
    return out


def test_finish_line_runs_between_its_end_lines_on_fin():
    data = _measure()
    _seg_el(data, "F1", "finish", 300.0, (100.0, 2100.0))
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    [line] = _all(r["ops"][0]["items"], "line")
    assert line["layer"] == "FIN" and (line["start"], line["end"]) == ([100.0, 300.0], [2100.0, 300.0])


@pytest.mark.parametrize("meaning,layer", [("대지경계", "LIM"), ("보선", "HID"), ("난간", "STL1"), ("HID", "HID")])
def test_line_layer_comes_from_its_interpretation(meaning, layer):
    data = _measure()
    _seg_el(data, "L1", "line", 0.0, (0.0, 1000.0), layer=meaning)
    [line] = _all(_build(data)["ops"][0]["items"], "line")
    assert line["layer"] == layer


def test_line_linetype_is_limited_to_the_layer_object_linetypes():
    data = _measure()
    _seg_el(data, "L1", "line", 0.0, (0.0, 1000.0), layer="대지경계", linetype="PHANTOM")
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    assert _all(r["ops"][0]["items"], "line")[0]["linetype"] == "PHANTOM"
    _confirm(data, "L1", linetype="DASHED")
    assert _build(data)["status"] == "error"


def test_unknown_line_layer_is_refused():
    data = _measure()
    _seg_el(data, "L1", "line", 0.0, (0.0, 1000.0), layer="무엇")
    r = _build(data)
    assert r["status"] == "error" and any("L1" in p and "무엇" in p for p in r["problems"])


def _shaft(data, eid="SH", corners=((0.0, 0.0), (2000.0, 0.0), (2000.0, 2500.0), (0.0, 2500.0)), etype="polygon",
           frame=(0.0, 0.0, 0.0)):
    """꼭짓점 i = 선 v{i}a(가로, y) × v{i}b(세로, x)"""
    refs = {}
    for i, (x, y) in enumerate(corners, 1):
        data["observations"][f"{eid}-v{i}a"] = _obs("u", y, frame)
        data["observations"][f"{eid}-v{i}b"] = _obs("v", x, frame)
        refs[f"v{i}a"], refs[f"v{i}b"] = f"{eid}-v{i}a", f"{eid}-v{i}b"
    data["elements"][eid] = {"type": etype, "refs": refs}
    _confirm(data, eid, layer="샤프트")


def test_polygon_vertices_are_crossings_of_two_observations():
    data = _measure()
    _shaft(data)
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    [pl] = _all(r["ops"][0]["items"], "polyline")
    assert pl["layer"] == "CIR" and pl["closed"] is True
    assert pl["points"] == [[0.0, 0.0], [2000.0, 0.0], [2000.0, 2500.0], [0.0, 2500.0]]


def test_open_polyline_keeps_vertex_order():
    data = _measure()
    _shaft(data, etype="polyline")
    [pl] = _all(_build(data)["ops"][0]["items"], "polyline")
    assert pl["closed"] is False and len(pl["points"]) == 4


def test_vertex_from_lines_crossing_at_a_shallow_angle_is_refused():
    data = _measure()
    _shaft(data)
    data["observations"]["SH-v2b"] = _obs("u", 0.0, skew=10.0)       # v2a(가로)와 10°로 만남
    r = _build(data)
    assert r["status"] == "error" and any("SH" in p and "15" in p for p in r["problems"])


def test_self_crossing_polygon_is_refused():
    data = _measure()
    _shaft(data, corners=((0.0, 0.0), (2000.0, 2500.0), (2000.0, 0.0), (0.0, 2500.0)))
    r = _build(data)
    assert r["status"] == "error" and any("SH" in p for p in r["problems"])


def test_lines_without_a_host_go_into_one_op_per_region():
    data = _measure()
    _seg_el(data, "F1", "finish", 300.0, (100.0, 2100.0), region="A")
    _seg_el(data, "F2", "finish", 900.0, (100.0, 2100.0), region="B")
    _seg_el(data, "F3", "finish", 1500.0, (100.0, 2100.0))
    r = _build(data)
    assert [o["op"] for o in r["ops"]] == ["t-A", "t-B", "t-misc"]
    assert r["manifest"] == {"F1": "t-A", "F2": "t-B", "F3": "t-misc"}


def test_line_in_a_rotated_frame_goes_into_its_frame_group():
    fr = (500.0, 500.0, 30.0)
    data = _measure({"slant": _frame(*fr)})
    _seg_el(data, "F1", "finish", 300.0, (100.0, 2100.0), frame=fr, frame_name="slant")
    [op] = _build(data)["ops"]
    [group] = op["items"]
    assert group["kind"] == "group" and group["place"] == [{"at": [500.0, 500.0], "rotation": 30.0}]
    [prim] = _expanded(op)
    assert prim.points[0] + prim.points[1] == pytest.approx([*_wcs(fr, 100, 300), *_wcs(fr, 2100, 300)], abs=1e-6)


# ---------- CLI·산출물: 미리보기·ops 폴더·verify·bind ----------

def _plan():
    """WCS 벽 고리 + 창 + 문 + 고리에 붙은 30° 사선 벽(생성기 합치기), 따로 떨어진 30° 회전 동(엔진 wall),
    대지경계선"""
    wing = (8000.0, 0.0, 30.0)
    data = _ring(False)
    data["frames"].update({"slant": _frame(*SLANT), "wing": _frame(*wing)})
    _wall(data, "ES", faces=(0.0, 200.0), ends=(0.0, 1500.0), frame_name="slant", frame=SLANT)
    _opening(data, "EW", "window", "EA", (1000.0, 2000.0))
    _opening(data, "DR", "door", "EC", (800.0, 1700.0), axis="u", hinge="s", swing="f2")
    _wall(data, "WA", faces=(0.0, 200.0), ends=(0.0, 4000.0), frame_name="wing", frame=wing, material="조적")
    _wall(data, "WB", faces=(0.0, 200.0), ends=(0.0, 2500.0), frame_name="wing", frame=wing, material="조적",
          axis="v")
    _seg_el(data, "L1", "line", -500.0, (-500.0, 8000.0), layer="대지경계", linetype="PHANTOM")
    return data


def _write_measure(tmp_path, data):
    import yaml
    path = tmp_path / "measure.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _run_cli(tmp_path, capsys, data, *extra):
    path = _write_measure(tmp_path, data)
    out = tmp_path / "run"
    rc = gen.main([str(path), "--prefix", "t", "--out", str(out), *extra])
    return rc, out, json.loads(capsys.readouterr().out)


def test_cli_writes_a_preview_and_an_ops_folder_that_passes_ops_verify(tmp_path, capsys):
    from tools.ops import ops
    rc, out, report = _run_cli(tmp_path, capsys, _plan())
    assert rc == 0 and report["status"] == "ready", report
    assert (out / "preview" / "preview.json").exists()
    manifest = json.loads((out / "preview" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ready" and manifest["elements"]["EW"] == "t-wall-CON-1"
    assert ops.main(["verify", str(out / "ops")]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert [r["op"] for r in verified["ops"]] == ["t-wall-CON-1", "t-wall-WAL-1", "t-misc"]
    con, wal = (json.loads((out / "ops" / f"{op}.json").read_text(encoding="utf-8")) for op in
                ("t-wall-CON-1", "t-wall-WAL-1"))
    assert "wall" not in json.dumps([it["kind"] for it in con["items"]])                 # 사선 벽이 섞여 합침
    assert [it["kind"] for it in wal["items"][0]["items"]] == ["wall"]                  # 회전 동은 엔진 wall
    assert ops.main(["bind", str(out / "ops"), "--out", str(out / "bound"), "--drawing-id", "d1",
                     "--snapshot-id", "s1"]) == 0
    bound = json.loads(capsys.readouterr().out)
    assert bound["ok"] and bound["target"] == {"drawing_id": "d1", "snapshot_id": "s1"}


def test_cli_writes_only_a_preview_while_questions_are_open(tmp_path, capsys):
    data = _plan()
    data["questions"] = [{"id": "Q1", "text": "창인가"}]
    rc, out, report = _run_cli(tmp_path, capsys, data)
    assert rc == 0 and report["status"] == "preview"
    assert (out / "preview" / "preview.json").exists() and not (out / "ops").exists()


def test_cli_refuses_errors_and_writes_nothing(tmp_path, capsys):
    data = _plan()
    data["elements"]["EW"]["links"]["host"] = "NOPE"
    rc, out, report = _run_cli(tmp_path, capsys, data)
    assert rc == 1 and report["status"] == "error" and not out.exists()


def test_cli_refuses_a_measure_that_fails_record_validation_or_its_lock(tmp_path, capsys):
    data = _plan()
    data["elements"]["L1"]["refs"]["l"] = "gone"
    rc, _, report = _run_cli(tmp_path, capsys, data)
    assert rc == 1 and any("gone" in p for p in report["problems"])
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"lock": "0" * 64}), encoding="utf-8")
    rc, _, report = _run_cli(tmp_path, capsys, _plan(), "--lock", str(lock))
    assert rc == 1 and any("잠금" in p for p in report["problems"])


def test_cli_refuses_a_run_folder_that_is_not_empty(tmp_path, capsys):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "x.txt").write_text("x", encoding="utf-8")
    rc, _, report = _run_cli(tmp_path, capsys, _plan())
    assert rc == 1 and any("비어" in p for p in report["problems"])


def test_output_does_not_depend_on_input_order():
    data = _plan()
    shuffled = copy.deepcopy(data)
    for key in ("observations", "elements", "interpretations"):
        shuffled[key] = dict(reversed(list(shuffled[key].items())))
    assert json.dumps(_build(data), ensure_ascii=False) == json.dumps(_build(shuffled), ensure_ascii=False)


def test_ops_together_draw_the_same_shapes_as_the_preview():
    r = _build(_plan())
    preview = _expanded(r["preview"])
    split = [p for spec in r["ops"] for p in _expanded(spec)]
    for layer in ("CON", "WAL"):
        assert _same_shape(_area_shape(preview, layer), _area_shape(split, layer))
    key = lambda p: (p.kind, p.layer, json.dumps(p.points), p.center, p.radius)       # noqa: E731
    assert sorted(map(key, (p for p in preview if p.kind != "polyline"))) == \
        sorted(map(key, (p for p in split if p.kind != "polyline")))


def test_preview_builds_and_passes_the_strict_standard_check(tmp_path, capsys):
    from src import cli
    rc, out, _ = _run_cli(tmp_path, capsys, _plan())
    dxf = tmp_path / "preview.dxf"
    assert cli.main(["build", str(out / "preview" / "preview.json"), "-o", str(dxf)]) == 0
    capsys.readouterr()
    assert cli.main(["check", str(dxf), "--scale", "100", "--strict"]) == 0, capsys.readouterr().out


def test_bad_element_ids_are_caught_by_the_engine_checks():
    data = _measure()
    _seg_el(data, "F/1", "finish", 300.0, (100.0, 2100.0))
    r = _build(data)
    assert r["status"] == "error" and any("SPEC_" in p for p in r["problems"])


def test_fractional_coordinates_in_a_rotated_frame_stay_orthogonal_after_rounding():
    fr = (1234.5678, -987.6543, 17.123456)
    data = _measure({"odd": _frame(*fr)})
    _wall(data, "EA", faces=(0.12345, 200.98765), ends=(-3.33333, 4567.89012), frame_name="odd", frame=fr)
    _wall(data, "EB", faces=(-3.33333, 250.5), ends=(0.12345, 3000.4444), frame_name="odd", frame=fr, axis="v")
    r = _build(data)
    assert r["status"] == "ready", r["problems"]
    assert [it["kind"] for it in r["ops"][0]["items"][0]["items"]] == ["wall"]


@pytest.mark.parametrize("value,want", [("false", False), ("true", True), (False, False), (True, True)])
def test_hatch_request_written_by_the_interpret_cli_as_text_is_read_as_yes_or_no(value, want):
    # record.py interpret --value는 문자열을 저장한다. bool("false")는 참이므로 그대로 쓰면 안 된다
    r = _build(_ring(value))
    [wall] = [it for it in r["ops"][0]["items"] if it["kind"] == "wall"]
    assert ("hatch" in wall) is want


def test_unreadable_hatch_request_is_refused():
    r = _build(_ring("아마"))
    assert r["status"] == "error" and any("hatch" in p for p in r["problems"])


def test_output_signature_is_the_same_on_every_platform():
    # CI(Ubuntu·Windows, Python 3.10·3.12)에서 shapely·GEOS 결과가 같은지 본다. 합친 좌표는 0.001 mm 격자라
    # 같아야 한다. 생성 규칙을 일부러 바꾼 경우에만 새 값으로 고친다(값은 아래 식으로 다시 계산)
    import hashlib
    ops = _build(_plan())["ops"]
    signature = hashlib.sha256(json.dumps(ops, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert signature == "35f1be0df83bc693526429eab2f2bf064001ae206169e9f77a85230b3813e75f"
