"""T10의 로컬 준비·비교 도구. 실무 DWG는 저장소에 넣지 않는다.

준비는 스냅샷 DXF만 읽는다. CAD 적용은 기존 apply_service/MCP가 담당한다.
"""
import argparse
import cmath
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

import ezdxf
from ezdxf import bbox
from ezdxf.math import Matrix44, Vec3

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.state import entity_state

TOLERANCE = 0.01
CORE_LAYERS = {"WA1": "CON", "w1": "WAL", "A-STAIR": "CIR", "A-SYMBOL": "DIM",
               "A-ROOM": "ROM", "WIN1": "DOR", "0": "DOR"}
DETAIL_LAYERS = {"HEO_BATH": "TOL", "HEO_BED": "FUR", "HEO_DOOR": "DOR", "HEO_WIN": "WIN"}
TARGET_BOUNDS = (7000, 15000, 65000, 40000)


def segment_distance(point, a, b):
    px, py = point[0], point[1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = max(0, min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / den)) if den else 0
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def ellipse_polyline(e, tolerance=TOLERANCE / 4):
    """매개변수 보간 오차 상한 ||r''|| h²/8 ≤ a h²/8을 사용한다."""
    if tolerance <= 0:
        raise ValueError("곡선 허용 오차는 양수여야 함")
    a = e.dxf.major_axis.magnitude
    start, end = e.dxf.start_param, e.dxf.end_param
    span = (end - start) % math.tau or math.tau
    n = max(4, math.ceil(span / math.sqrt(8 * tolerance / a)))
    points = [list(v)[:2] for v in e.vertices(start + span * i / n for i in range(n + 1))]
    return points, {"segments": n, "max_error_bound_mm": a * (span / n) ** 2 / 8,
                    "method": "sup_norm_second_derivative_times_step_squared_over_8"}


def block_place(e, delta=(0, 0)):
    sx, sy = e.dxf.get("xscale", 1), e.dxf.get("yscale", 1)
    if abs(abs(sx) - 1) > 1e-9 or abs(abs(sy) - 1) > 1e-9:
        raise ValueError("상세 블록의 비단위 배율은 준비 범위 밖")
    p = {"at": [e.dxf.insert.x + delta[0], e.dxf.insert.y + delta[1]]}
    angle = e.dxf.get("rotation", 0)
    if sx < 0 and sy < 0:
        angle += 180
    elif sx < 0:
        p["mirror"] = "x"
    elif sy < 0:
        p["mirror"] = "y"
    if angle % 360:
        p["rotation"] = angle % 360
    return p


def room_counts(doc):
    counts = Counter(e.dxf.text for e in doc.modelspace().query('TEXT[layer=="ROM"]'))
    return {key: counts[f"객실({key})"] for key in ("A", "B")}


def align_axes(source, reference):
    if any(set(source[axis]) != set(reference[axis]) for axis in ("X", "Y")):
        raise ValueError("축 이름·수가 서로 다름")
    delta = tuple(source[a][min(source[a])] - reference[a][min(source[a])] for a in ("X", "Y"))
    rows = []
    for axis, shift in zip(("X", "Y"), delta):
        for name, value in source[axis].items():
            actual = reference[axis][name] + shift
            rows.append({"axis": name, "source": value, "reference": reference[axis][name],
                         "aligned": actual, "residual_mm": abs(value - actual)})
    if max(row["residual_mm"] for row in rows) > TOLERANCE:
        raise ValueError("전체 축 정렬 잔차가 0.01 mm를 넘음")
    return delta, rows


def within(e, bounds=TARGET_BOUNDS, cache=None):
    b = bbox.extents([e], fast=True, cache=cache)
    return (b.has_data and b.extmin.x >= bounds[0] and b.extmin.y >= bounds[1]
            and b.extmax.x <= bounds[2] and b.extmax.y <= bounds[3])


def select_core(doc, bounds=TARGET_BOUNDS):
    cache = bbox.Cache()
    return [e for e in doc.modelspace() if e.dxf.layer in CORE_LAYERS and within(e, bounds, cache)]


def text_anchor(e):
    return e.dxf.align_point if (e.dxf.get("halign", 0) or e.dxf.get("valign", 0)) else e.dxf.insert


def geometry(e):
    """핸들·ARCAID 태그·표준 속성을 제외한 WCS 형상. OCS 반전도 정규화한다."""
    t = e.dxftype()
    def p(v):
        return [float(v[0]), float(v[1])]
    if t == "LINE":
        return {"type": t, "points": sorted([p(e.dxf.start), p(e.dxf.end)])}
    if t == "LWPOLYLINE":
        pts = []
        sign = 1 if e.dxf.extrusion.z >= 0 else -1
        for x, y, b in e.get_points("xyb"):
            v = e.ocs().to_wcs(Vec3(x, y, e.dxf.elevation))
            pts.append([v.x, v.y, float(b) * sign])
        return {"type": t, "points": pts, "closed": bool(e.closed)}
    if t in ("ARC", "CIRCLE"):
        c = e.ocs().to_wcs(e.dxf.center)
        out = {"type": t, "center": p(c), "radius": float(e.dxf.radius)}
        if t == "ARC":
            angles = []
            for a in (e.dxf.start_angle, e.dxf.end_angle):
                v = e.ocs().to_wcs(e.dxf.center + Vec3.from_deg_angle(a, e.dxf.radius)) - c
                angles.append(math.degrees(math.atan2(v.y, v.x)) % 360)
            if e.dxf.extrusion.z < 0:
                angles.reverse()
            out["angles"] = angles
        return out
    if t == "HATCH":
        loops = []
        for path in e.paths:
            if not hasattr(path, "vertices"):
                raise ValueError("곡선 해치 경계는 별도 준비 필요")
            loops.append([[float(x), float(y), float(b)] for x, y, b in path.vertices])
        return {"type": t, "loops": loops, "pattern": e.dxf.pattern_name,
                "scale": e.dxf.get("pattern_scale", 1), "angle": e.dxf.get("pattern_angle", 0),
                "hatch_style": e.dxf.get("hatch_style", 0),
                "roles": ["external" if p.path_type_flags & 1 else "outermost" if p.path_type_flags & 16
                          else "default" for p in e.paths]}
    if t == "TEXT":
        return {"type": t, "text": e.dxf.text, "at": p(text_anchor(e))}
    if t == "INSERT":
        return {"type": t, "name": e.dxf.name, "at": p(e.dxf.insert),
                "attribs": sorted([a.dxf.tag, a.dxf.text] for a in e.attribs)}
    if t == "DIMENSION":
        return {"type": t, "p1": p(e.dxf.defpoint2), "p2": p(e.dxf.defpoint3),
                "angle": e.dxf.get("angle", 0) % 180}
    raise ValueError(f"지원하지 않는 비교 객체: {t}")


def _rounded(obj, digits=6):
    if isinstance(obj, (float, int)) and not isinstance(obj, bool):
        return round(float(obj), digits) + 0.0
    if isinstance(obj, (list, tuple)):
        return [_rounded(v, digits) for v in obj]
    if isinstance(obj, dict):
        return {k: _rounded(v, digits) for k, v in obj.items()}
    return obj


def signature(e, properties=True):
    record = {"geometry": geometry(e)}
    if properties:
        record["state"] = entity_state(e)
    return json.dumps(_rounded(record), sort_keys=True, ensure_ascii=False)


def compare_documents(expected, actual, properties=True):
    a = Counter(signature(e, properties) for e in expected.modelspace())
    b = Counter(signature(e, properties) for e in actual.modelspace())
    missing, extra = a - b, b - a
    return {"equal": not missing and not extra, "expected_count": sum(a.values()),
            "actual_count": sum(b.values()), "missing": sum(missing.values()), "extra": sum(extra.values()),
            "missing_examples": list(missing)[:3], "extra_examples": list(extra)[:3]}


def decompose(e):
    if e.dxftype() == "INSERT":
        for child in e.virtual_entities():
            yield from decompose(child)
    else:
        yield e


def entity_item(e, layer, curve_proofs):
    """독립 DXF 객체를 지원 명세로 변환한다. 지원하지 않는 형상은 중단한다."""
    t = e.dxftype()
    if t == "ELLIPSE":
        points, proof = ellipse_polyline(e)
        curve_proofs.append({"handle": e.dxf.get("handle"), **proof})
        return {"kind": "polyline", "layer": layer, "points": points, "closed": False}
    g = geometry(e)
    if t == "LINE":
        return {"kind": "line", "layer": layer, "start": g["points"][0], "end": g["points"][1]}
    if t == "LWPOLYLINE":
        return {"kind": "polyline", "layer": layer, "points": [p[:2] for p in g["points"]],
                "bulges": [p[2] for p in g["points"]], "closed": g["closed"]}
    if t in ("ARC", "CIRCLE"):
        out = {"kind": t.lower(), "layer": layer, "center": g["center"], "radius": g["radius"]}
        if t == "ARC":
            out.update(start_angle=g["angles"][0], end_angle=g["angles"][1])
        return out
    if t == "TEXT":
        updn = e.dxf.text in ("UP", "DN")
        text_layer = layer if layer in ("TXT", "ROM", "REM", "SYM", "LEV") else "TXT"
        return {"kind": "text", "layer": "TXT" if updn else text_layer,
                "role": "stair_updn" if updn else "general", "content": e.dxf.text,
                "at": g["at"], "align": "TC" if updn else "MC", "rotation": e.dxf.get("rotation", 0)}
    if t == "HATCH":
        if any(p[2] for loop in g["loops"] for p in loop):
            raise ValueError("곡선 해치 경계는 명세에서 지원하지 않음")
        return {"kind": "hatch", "layer": layer, "pattern": g["pattern"],
                "pattern_scale": g["scale"], "pattern_angle": g["angle"],
                "boundary": [p[:2] for p in g["loops"][0]],
                "holes": [[p[:2] for p in loop] for loop in g["loops"][1:]]}
    raise ValueError(f"지원하지 않는 명세 변환: {t}")


def expected_record(e, delta=(0, 0)):
    """기대값은 명세 전개를 거치지 않고 비교본 원래 객체에서 읽는다."""
    if e.dxftype() == "DIMENSION":
        record = geometry(e)
        for name in ("p1", "p2"):
            record[name] = [record[name][i] + delta[i] for i in (0, 1)]
        return record
    e = e.copy()
    e.transform(Matrix44.translate(delta[0], delta[1], 0))
    if e.dxftype() == "ELLIPSE":
        return {"type": "ELLIPSE", "center": list(e.dxf.center)[:2],
                "major": list(e.dxf.major_axis)[:2], "ratio": e.dxf.ratio,
                "start": e.dxf.start_param, "end": e.dxf.end_param,
                "normal": e.dxf.extrusion.z}
    return geometry(e)


def _difference(a, b):
    if isinstance(a, (float, int)) and not isinstance(a, bool):
        return abs(a - b) if isinstance(b, (float, int)) else math.inf
    if type(a) is not type(b):
        return math.inf
    if isinstance(a, dict):
        return max((_difference(a[k], b[k]) for k in a), default=0) if a.keys() == b.keys() else math.inf
    if isinstance(a, list):
        if len(a) != len(b):
            return math.inf
        # 좌표의 유클리드 거리로 대각 방향의 편차도 확인한다.
        if len(a) == 2 and all(isinstance(x, (float, int)) for x in a + b):
            return math.hypot(a[0] - b[0], a[1] - b[1])
        return max((_difference(x, y) for x, y in zip(a, b)), default=0)
    return 0 if a == b else math.inf


def _segment_curve(a, b):
    """bulge 구간을 정확한 원호 매개변수로 표현한다(직선의 2차미분은 0)."""
    p, q = complex(*a[:2]), complex(*b[:2]); bulge = a[2]
    if not bulge:
        return lambda t: p + (q - p) * t, 0.0, 0.0, 0.0
    if p == q:
        raise ValueError("끝점이 같은 유효하지 않은 bulge 구간")
    center = (p + q) / 2 + (q - p) * 1j * (1 - bulge * bulge) / (4 * bulge)
    radial = p - center; angle = 4 * math.atan(bulge)
    def point(t):
        if t == 0:
            return p
        if t == 1:
            return q
        u = angle * t
        return p + radial * complex(-2 * math.sin(u / 2) ** 2, math.sin(u))
    return point, abs(radial) * angle * angle, cmath.phase(radial), angle


def _polyline_error(expected, got):
    if got["type"] != "LWPOLYLINE" or expected["closed"] != got["closed"]:
        return math.inf
    a, b = expected["points"], got["points"]
    if len(a) != len(b):
        return math.inf
    error = max((math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(a, b)), default=0)
    for i in range(len(a) if expected["closed"] else len(a) - 1):
        j = (i + 1) % len(a)
        f, af, phase_f, angle_f = _segment_curve(a[i], a[j])
        g, ag, phase_g, angle_g = _segment_curve(b[i], b[j])
        mid = abs(f(0.5) - g(0.5))
        error = max(error, mid)
        if error > TOLERANCE:
            return error
        # D=f-g의 2차미분 상한. 같은 원호는 차이가 0이므로 과도한 표본을 만들지 않는다.
        phase_delta = abs((phase_f - phase_g + math.pi) % math.tau - math.pi)
        bound = min(af + ag, abs(af - ag) + min(af, ag) * (phase_delta + abs(angle_f - angle_g)))
        n = max(1, math.ceil(math.sqrt(bound / (8 * (TOLERANCE / 10)))))
        observed = max(abs(f(k / n) - g(k / n)) for k in range(n + 1))
        # 구간 사이도 선형 보간 오차 ||D''|| h²/8 이하이므로 전 곡선의 거리 상한이다.
        error = max(error, observed + bound / (8 * n * n))
    return error


def geometry_error(expected, actual):
    if expected["type"] == "ELLIPSE":
        if actual.dxftype() != "LWPOLYLINE":
            return math.inf
        actual_geometry = geometry(actual)
        pts = actual_geometry["points"]; n = len(pts) - 1
        if any(p[2] != 0 for p in pts):
            return math.inf
        span = (expected["end"] - expected["start"]) % math.tau or math.tau
        if actual.closed and abs(span - math.tau) > 1e-8:
            return math.inf
        if n < 4:
            return math.inf
        cx, cy = expected["center"]; mx, my = expected["major"]
        ratio, sign = expected["ratio"], expected["normal"]
        vertex_error = 0
        for i, (x, y, _) in enumerate(pts):
            t = expected["start"] + span * i / n
            ex = cx + mx * math.cos(t) - my * ratio * sign * math.sin(t)
            ey = cy + my * math.cos(t) + mx * ratio * sign * math.sin(t)
            vertex_error = max(vertex_error, math.hypot(x - ex, y - ey))
        return vertex_error + math.hypot(mx, my) * (span / n) ** 2 / 8
    got = geometry(actual)
    if expected["type"] == "LWPOLYLINE":
        return _polyline_error(expected, got)
    if expected["type"] == "HATCH" and got["type"] == "HATCH":
        if expected["hatch_style"] != got["hatch_style"]:
            return math.inf
        # 외부 루프를 섬으로 바꾸거나 섬을 external로 바꾸면 채움 의미가 달라진다.
        # 원본 default 섬 → 엔진 outermost 섬은 지원되는 표준화다.
        for roles in (expected["roles"], got["roles"]):
            if not roles or roles[0] not in ("external", "default") or any(r == "external" for r in roles[1:]):
                return math.inf
        expected = dict(expected, roles=["outer"] + ["island"] * (len(expected["roles"]) - 1))
        got = dict(got, roles=["outer"] + ["island"] * (len(got["roles"]) - 1))
        def canonical(loop):
            if len(loop) > 1 and loop[0] == loop[-1]:
                loop = loop[:-1]
            candidates = []
            for seq in (loop, list(reversed(loop))):
                for i in range(len(seq)):
                    candidates.append(seq[i:] + seq[:i])
            return min(candidates)
        def loops(record):
            outer, *holes = [canonical(p) for p in record["loops"]]
            return [outer] + sorted(holes)
        expected = dict(expected, loops=loops(expected)); got = dict(got, loops=loops(got))
    if expected["type"] in ("ARC", "CIRCLE") and got["type"] == expected["type"]:
        error = (math.dist(expected["center"], got["center"]) + abs(expected["radius"] - got["radius"]))
        if expected["type"] == "ARC":
            start, end = expected["angles"]; actual_start, actual_end = got["angles"]
            start_error = abs((start - actual_start + 180) % 360 - 180)
            sweep_error = abs((end - start) % 360 - (actual_end - actual_start) % 360)
            error += math.radians(start_error + sweep_error) * expected["radius"]
        return error
    return _difference(expected, got)


def verify_geometry(expectation, actual):
    failures, errors = [], []
    for key, record in expectation.items():
        error = geometry_error(record, actual[key]) if key in actual else math.inf
        if error > TOLERANCE:
            failures.append({"path": key, "error_mm": error if math.isfinite(error) else None})
        if math.isfinite(error):
            errors.append(error)
    extras = sorted(set(actual) - set(expectation))
    return {"passed": not failures and not extras, "expected_count": len(expectation),
            "actual_count": len(actual), "max_error_mm": max(errors, default=0),
            "failure_count": len(failures), "failures": failures[:20], "extra_paths": extras[:20]}


def verify_baseline(expectation, doc):
    keys = list(expectation)
    actual = {keys[i] if i < len(keys) else f"unexpected/{i}": e for i, e in enumerate(doc.modelspace())}
    return verify_geometry(expectation, actual)


def compress_places(places):
    """같은 방향의 연속 등간격 배치를 repeat로 압축한다. 번호의 간격도 보존한다."""
    places = sorted(places, key=lambda p: (p.get("mirror", ""), p.get("rotation", 0), p["at"][1], p["at"][0]))
    out, i = [], 0
    while i < len(places):
        p = dict(places[i]); count = 1
        if i + 1 < len(places):
            q = places[i + 1]
            step = [q["at"][j] - p["at"][j] for j in (0, 1)]
            dn = q["vars"]["room"]["start"] - p["vars"]["room"]["start"]
            for j in range(i + 1, len(places)):
                q = places[j]; n = j - i
                if (q.get("mirror") != p.get("mirror") or q.get("rotation", 0) != p.get("rotation", 0)
                        or any(abs(q["at"][k] - p["at"][k] - n * step[k]) > 1e-5 for k in (0, 1))
                        or q["vars"]["room"]["start"] != p["vars"]["room"]["start"] + n * dn):
                    break
                count += 1
            if count > 1:
                p["repeat"] = {"count": count, "step": step}
                p["vars"] = {"room": {"start": p["vars"]["room"]["start"], "step": dn}}
        out.append(p); i += count
    return out


def room_chunks(doc):
    """비교본의 연속된 객실 작성 묶음을 읽는다. 타입·기구 수가 다르면 추측하지 않는다."""
    out, pending, room_type = [], [], None
    active = False
    for e in doc.modelspace():
        if not active:
            active = (e.dxf.layer == "WAL1" or
                      (e.dxftype() == "INSERT" and e.dxf.name in DETAIL_LAYERS))
        if not active:
            continue
        pending.append(e)
        if e.dxftype() == "TEXT" and e.dxf.text in ("객실(A)", "객실(B)"):
            room_type = e.dxf.text[-2]
        elif room_type and e.dxftype() == "TEXT" and "㎡" in e.dxf.text:
            details = Counter(x.dxf.name for x in pending if x.dxftype() == "INSERT")
            if details != Counter({k: 1 for k in DETAIL_LAYERS}):
                raise ValueError(f"객실 상세 블록 누락·초과: {details}")
            out.append({"type": room_type, "entities": pending})
            pending, room_type, active = [], None, False
    if room_type:
        raise ValueError("객실 면적 문자가 없어 묶음 확인 불가")
    return out


def axes(doc, layer):
    found = {"X": [], "Y": []}
    for e in doc.modelspace().query("LINE"):
        if e.dxf.layer != layer:
            continue
        a, b = e.dxf.start, e.dxf.end
        if not (7000 <= min(a.x, b.x) <= max(a.x, b.x) <= 65000):
            continue
        if abs(a.x - b.x) < 1e-4 and abs(a.y - b.y) >= 13000:
            found["X"].append((a.x, e))
        elif abs(a.y - b.y) < 1e-4 and abs(a.x - b.x) >= 50000:
            found["Y"].append((a.y, e))
    grid = {axis: {f"{axis}{i + 1}": x for i, (x, _) in enumerate(sorted(values, key=lambda p: p[0]))}
            for axis, values in found.items()}
    if (len(grid["X"]), len(grid["Y"])) != (16, 4):
        raise ValueError("당산동 기준 축은 X16/Y4이어야 함")
    return grid, {axis: [e for _, e in sorted(values, key=lambda p: p[0])] for axis, values in found.items()}


def _spec(std, op, items, grid):
    return {"schema_version": 1, "standard": std.ref, "op": op, "units": "mm", "scale": 200,
            "grid": grid, "items": items}


def _depth(items):
    return max((1 + _depth(x["items"]) for x in items if x["kind"] == "group"), default=0)


def reference_annotations(ref, delta, grid):
    """비교본의 실제 주석에서 의미·기점을 고정한다. 생성 결과를 읽지 않는다."""
    expected = {}
    for e in ref.modelspace():
        t, key = e.dxftype(), None
        if t == "TEXT" and e.dxf.layer in ("ROM", "LEV") and 7000 < text_anchor(e).x < 65000:
            key = f"text-{e.dxf.handle}"
        elif t == "INSERT" and e.dxf.name in ("축열버블-상", "축열버블-좌"):
            values = [a.dxf.text for a in e.attribs if a.dxf.tag == "축열"]
            if len(values) != 1:
                raise ValueError("축열 버블 속성을 확인하지 못함")
            if values[0] in grid["X"] or values[0] in grid["Y"]:
                key = values[0]
        elif t == "DIMENSION" and 7000 < e.dxf.defpoint2.x < 65000:
            if e.dxf.layer == "DIM1":
                key = f"dim-{e.dxf.handle}"
            elif e.dxf.layer == "DIM":
                axis = "Y" if abs(e.dxf.get("angle", 0) % 180 - 90) < 1e-4 else "X"
                values = sorted(grid[axis].values()); index = 1 if axis == "Y" else 0
                a, b = sorted((e.dxf.defpoint2[index], e.dxf.defpoint3[index]))
                if abs(b - a - (values[-1] - values[0])) < TOLERANCE:
                    key = f"{axis.lower()}-overall"
                else:
                    ix = next((i for i, v in enumerate(values[:-1]) if abs(v - a) < TOLERANCE), None)
                    if ix is None:
                        raise ValueError("비교본 외부 치수의 축 기점을 확인하지 못함")
                    key = f"{axis.lower()}-chain.{ix + 1}"
        if key:
            if key in expected:
                raise ValueError(f"주석 기대값 중복: {key}")
            expected[key] = expected_record(e, delta)
    return expected


def file_identity(path):
    path = Path(path).resolve(); st = path.stat()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mtime_ns": st.st_mtime_ns, "size": st.st_size}


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def prepare(source_dxf, reference_dxf, out, std, source_dwg=None, reference_dwg=None):
    """확인용 기준·명세·고정 기대 manifest. 실제 CAD에 쓰지 않는다."""
    import time
    from ezdxf.addons import Importer
    from src.engine.layout import expand
    from src.engine.spec import parse_spec, validate_spec, MAX_OBJECTS, MAX_DEPTH
    from src.engine.write import build_dxf
    from src.engine.check import check_standard
    from src.engine.results import Status
    start = time.perf_counter(); out = Path(out); out.mkdir(parents=True, exist_ok=False)
    source, ref = ezdxf.readfile(source_dxf), ezdxf.readfile(reference_dxf)
    grid, axis_entities = axes(source, "A-CEN"); refgrid, _ = axes(ref, "CEN")
    delta, axis_rows = align_axes(grid, refgrid)
    manifest = {"standard": std.ref, "scale": 200, "source_snapshot": file_identity(source_dxf),
                "reference_snapshot": file_identity(reference_dxf), "translation_mm": delta, "axes": axis_rows,
                "source_drawing_id": source.header.get("$FINGERPRINTGUID"),
                "reference_drawing_id": ref.header.get("$FINGERPRINTGUID"), "selection": [],
                "baseline_expectation": {}, "room_expectation": {}, "curves": [],
                "reference_modelspace_count": len(ref.modelspace()), "historical_count": 943}
    for key, path in (("source_file", source_dwg), ("reference_file", reference_dwg)):
        if path:
            manifest[key] = file_identity(path)
    for key, path in (("source_snapshot", source_dxf), ("reference_snapshot", reference_dxf)):
        sidecar = Path(path).with_suffix(".json")
        if sidecar.exists():
            manifest[key]["metadata"] = json.loads(sidecar.read_text(encoding="utf-8"))
    manifest["annotation_expectation"] = reference_annotations(ref, delta, refgrid)
    s = std.profile.scale.factors(200)["s"]
    spacing = std.dimensions.spacing.model_dump()
    # 축선 끝만 표준 간격으로 연장한다. 정밀 축좌표는 유지한다.
    overhang = spacing["cen_overhang"] * s
    xs, ys = list(grid["X"].values()), list(grid["Y"].values())
    base_items = []
    for axis in ("X", "Y"):
        for name, e in zip(grid[axis], axis_entities[axis]):
            coord = grid[axis][name]
            a, b = ([coord, min(ys) - overhang], [coord, max(ys) + overhang]) if axis == "X" else \
                   ([min(xs) - overhang, coord], [max(xs) + overhang, coord])
            base_items.append({"kind": "line", "id": name, "layer": "CEN", "start": a, "end": b})
            manifest["baseline_expectation"][name] = {"type": "LINE", "points": sorted([a, b])}
            manifest["selection"].append({"handle": e.dxf.handle, "type": "LINE", "from": "A-CEN",
                                          "to": "CEN", "id": name, "change": "축좌표 보존, 끝만 표준 연장"})
    for e in select_core(source):
        layer = CORE_LAYERS[e.dxf.layer]
        if e.dxftype() == "INSERT" and e.dxf.layer == "w1":
            layer = "CIR"  # 승강기 내부 상세
        children = list(decompose(e))
        for i, child in enumerate(children):
            iid = f"core-{e.dxf.handle}-{i}"
            item = entity_item(child, layer, manifest["curves"]); item["id"] = iid
            base_items.append(item)
            manifest["baseline_expectation"][iid] = expected_record(child)
        manifest["selection"].append({"handle": e.dxf.handle, "type": e.dxftype(), "from": e.dxf.layer,
                                      "to": "TXT" if e.dxftype() == "TEXT" and e.dxf.text in ("UP", "DN") else layer,
                                      "expanded_count": len(children)})
    built = build_dxf(_spec(std, "t10-baseline-preparation", base_items, grid), std, out / "baseline-objects.dxf")
    if built.status is not Status.SUCCESS:
        raise ValueError(built.model_dump(mode="json"))
    gen = ezdxf.readfile(out / "baseline-objects.dxf")
    baseline = ezdxf.readfile(std.path(std.profile.template.dxf))
    if len(baseline.modelspace()):
        raise ValueError("표준 템플릿에 예상 밖 모형 객체가 있음")
    imp = Importer(gen, baseline); imp.import_entities(gen.modelspace()); imp.finalize()
    baseline.header["$INSUNITS"] = std.profile.insunits
    baseline.header["$LTSCALE"] = 200 * std.profile.scale.ltscale_per_k
    baseline.header["$FINGERPRINTGUID"] = "{" + str(uuid.uuid4()).upper() + "}"
    baseline.saveas(out / "baseline.dxf")
    checked = check_standard(out / "baseline.dxf", std, 200)
    manifest["baseline_check"] = checked.model_dump(mode="json")
    manifest["baseline_geometry"] = verify_baseline(manifest["baseline_expectation"], baseline)
    # 객실 기대값은 비교본 원시 객체에서 명세 변환과 별도로 저장한다.
    chunks = room_chunks(ref)
    if Counter(c["type"] for c in chunks) != Counter(A=25, B=2):
        raise ValueError("비교본의 객실이 A25/B2가 아님")
    variants, used = {}, set()
    for room_no, chunk in enumerate(chunks):
        entities = chunk["entities"]; used.update(e.dxf.handle for e in entities)
        pose = next(e for e in entities if e.dxftype() == "INSERT" and e.dxf.name == "HEO_BATH")
        inv = pose.matrix44(); inv.inverse()
        items = []
        for j, e in enumerate(entities):
            if e.dxftype() == "TEXT":
                continue
            iid = f"shape{j}"
            if e.dxftype() == "INSERT":
                children = list(ref.blocks.get(e.dxf.name))
                child_items = []
                for n, child in enumerate(children):
                    item = entity_item(child, DETAIL_LAYERS[e.dxf.name], manifest["curves"])
                    item["id"] = f"e{n}"; child_items.append(item)
                # 문·창은 욕실 기준점과 다른 삽입점이다. 지역 오프셋을 보존한다.
                for basis in (Vec3(1, 0, 0), Vec3(0, 1, 0)):
                    direction = inv.transform_direction(e.matrix44().transform_direction(basis))
                    if not direction.isclose(basis, abs_tol=1e-7):
                        raise ValueError("객실 상세의 상대 회전·배율은 별도 준비가 필요함")
                offset = inv.transform(e.dxf.insert)
                items.append({"kind": "group", "id": iid, "items": child_items,
                              "place": [{"at": [offset.x, offset.y]}]})
                for n, child in enumerate(decompose(e)):
                    manifest["room_expectation"][f"room/{room_no}/{iid}[0]/e{n}"] = expected_record(child, delta)
            else:
                local = e.copy(); local.transform(inv)
                item = entity_item(local, e.dxf.layer, manifest["curves"]); item["id"] = iid; items.append(item)
                manifest["room_expectation"][f"room/{room_no}/{iid}"] = expected_record(e, delta)
        key = chunk["type"] + hashlib.sha256(json.dumps(_rounded(items, 4), sort_keys=True).encode()).hexdigest()[:12]
        entry = variants.setdefault(key, {"kind": "group", "id": key, "path": "room/{room}",
                                           "items": items, "place": []})
        place = block_place(pose, delta); place["vars"] = {"room": {"start": room_no}}
        entry["place"].append(place)
    room_items = list(variants.values())
    for item in room_items:
        item["place"] = compress_places(item["place"])
    # 객실 그룹 밖에서 추가된 기둥·벽 해치 등 전체 형상.
    for e in ref.modelspace():
        if e.dxf.handle in used or e.dxf.layer not in ("CON", "HAT", "WAL1", "FUR") or not within(e):
            continue
        child = e.copy(); child.transform(Matrix44.translate(*delta, 0))
        iid = f"ref-{e.dxf.handle}"; item = entity_item(child, e.dxf.layer, manifest["curves"])
        item["id"] = iid; room_items.append(item)
        manifest["room_expectation"][iid] = expected_record(e, delta)
    annotation_items = []
    for e in ref.modelspace().query("TEXT"):
        if e.dxf.layer not in ("ROM", "LEV") or not (7000 < text_anchor(e).x < 65000):
            continue
        child = e.copy(); child.transform(Matrix44.translate(*delta, 0))
        item = entity_item(child, e.dxf.layer, manifest["curves"]); item["id"] = f"text-{e.dxf.handle}"
        annotation_items.append(item)
    top_end = max(ys) + overhang; left_end = min(xs) - overhang
    first_y = top_end + spacing["first_row_from_cen_end"] * s
    outer_y = first_y + spacing["row_gap"] * s
    first_x = left_end - spacing["first_row_from_cen_end"] * s
    outer_x = first_x - spacing["row_gap"] * s
    annotation_items += [
        {"kind": "dim_chain", "id": "x-chain", "style": "DIM330", "points": [[x, top_end] for x in xs], "direction": "x", "line_at": first_y},
        {"kind": "dim", "id": "x-overall", "style": "DIM330", "p1": [min(xs), top_end], "p2": [max(xs), top_end], "direction": "x", "line_at": outer_y},
        {"kind": "dim_chain", "id": "y-chain", "style": "DIM330", "points": [[left_end, y] for y in ys], "direction": "y", "line_at": first_x},
        {"kind": "dim", "id": "y-overall", "style": "DIM330", "p1": [left_end, min(ys)], "p2": [left_end, max(ys)], "direction": "y", "line_at": outer_x}]
    for name, x in grid["X"].items():
        annotation_items.append({"kind": "block", "id": name, "name": "축열버블-상", "at": [x, outer_y], "attribs": {"축열": name}})
    for name, y in grid["Y"].items():
        annotation_items.append({"kind": "block", "id": name, "name": "축열버블-좌", "at": [outer_x, y], "attribs": {"축열": name}})
    for e in ref.modelspace().query("DIMENSION"):
        if e.dxf.layer != "DIM1" or not (7000 < e.dxf.defpoint2.x < 65000):
            continue
        p1, p2 = e.dxf.defpoint2, e.dxf.defpoint3
        direction = "y" if abs((e.dxf.get("angle", 0) % 180) - 90) < 1e-4 else "x"
        annotation_items.append({"kind": "dim", "id": f"dim-{e.dxf.handle}", "style": "DIM270",
                                 "p1": [p1.x + delta[0], p1.y + delta[1]], "p2": [p2.x + delta[0], p2.y + delta[1]],
                                 "direction": direction, "line_at": e.dxf.defpoint.x + delta[0] if direction == "y" else e.dxf.defpoint.y + delta[1]})
    manifest["specs"] = {}
    for op, items in (("t10-rooms", room_items), ("t10-annotations", annotation_items)):
        spec = _spec(std, op, items, grid); parsed = parse_spec(spec)
        problems = validate_spec(parsed, std)
        if problems:
            raise ValueError([v.model_dump(mode="json") for v in problems])
        count = len(expand(parsed, std, 200))
        manifest["specs"][op] = {"objects": count, "max_objects": MAX_OBJECTS, "depth": _depth(items), "max_depth": MAX_DEPTH}
        write_json(out / f"{op}.json", spec)
    manifest["room_counts"] = {"A": 25, "B": 2}
    manifest["room_variants"] = len(variants)
    manifest["prepare_seconds"] = time.perf_counter() - start
    manifest["baseline_ready"] = (not checked.violations and not checked.unchecked and manifest["baseline_geometry"]["passed"])
    manifest["artifact_hashes"] = {name: file_identity(out / name)["sha256"] for name in
                                   ("baseline.dxf", "t10-rooms.json", "t10-annotations.json")}
    write_json(out / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_snapshot", type=Path); parser.add_argument("reference_snapshot", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-dwg", type=Path); parser.add_argument("--reference-dwg", type=Path)
    args = parser.parse_args()
    from src.engine.standards import load_standard
    result = prepare(args.source_snapshot, args.reference_snapshot, args.out, load_standard(), args.source_dwg, args.reference_dwg)
    print(json.dumps({k: result[k] for k in ("baseline_ready", "baseline_geometry", "specs", "room_counts", "room_variants", "prepare_seconds")}, ensure_ascii=False))
    return 0 if result["baseline_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
