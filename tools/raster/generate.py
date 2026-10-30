"""범용 생성기(형상): measure.yaml → 미리보기 명세·ops 폴더(CAD 없음).

사용(저장소 루트):
  python tools/raster/generate.py measure.yaml --prefix p6f --out runs/<id> [--lock lock.json]

입력은 measure.yaml만이다. 숫자는 관측(refs)에서, 요소 사이 관계는 links에서, 이산 속성(재료·레이어·문 경첩 등)은
interpretations에서 온다. 관측 하나는 직선 하나(point_wcs·angle_wcs_deg)이고, 요소의 frame(없으면 WCS)
지역 좌표로 옮겨 frame 축과 0.5° 안이면 그 축에 맞춘다(벽 방향은 frame 각도가 정한다). 꼭짓점은 두 선의 교점이며,
두 선이 15° 미만으로 만나면 거부한다(교점 오차 ≈ 선 오차 / sin 교차각). 중간 좌표는 반올림하지 않고 출력만
소수 셋째 자리로 반올림한다(-0.0 정리).

요소 지원표(C1, 형상). 해석 값은 record.py interpret로 확정한다(문자열 "true"·"false"도 받음).
  type              refs(관측)                     links   해석                         출력·레이어
  wall·column       f1·f2(평행 면선), s·e(끝선)      -       material RC|조적|건식,       RC→CON, 조적→WAL, 건식→WAL1
                                                            hatch(선택, 기본 false)
  window            s·e(개구부 끝선), glass(선택)    host    -                            host 벽에서 빼고 WIN 선 5개
                                                                                         (면선 2·유리선·양 끝)
  door              s·e                            host    swing f1|f2, hinge s|e(편개),  host 벽에서 빼고 DOR 문짝선·호
                                                            leaves 1|2(기본 1),
                                                            angle 0~180(기본 90)
  opening           s·e                            host    -                            host 벽에서 빼기만
  finish            l(선), s·e                     -       -                            FIN 선
  line              l, s·e                         -       layer, linetype(선택)          해석 레이어
  polyline·polygon  v1a·v1b, v2a·v2b, ...(꼭짓점)   -       layer, linetype(선택)          열린·닫힌 polyline
  layer 해석은 뜻(승강기·샤프트→CIR, 난간→STL1, 보선→HID, 대지경계→LIM, 마감→FIN) 또는 표준 레이어 이름.
  linetype은 레이어의 object_linetypes만(엔진 검사).

벽 묶음과 op:
- 같은 레이어에서 1 mm 안에 닿는 벽 조각이 한 묶음 = op 하나(t-wall-<레이어>-<n>). 창·문·개구부는 host 묶음의
  op에 들어간다. 다른 묶음의 벽에 걸친 개구부는 거부한다. 벽이 아닌 선은 영역(region, 없으면 misc)별 op다.
- 묶음의 조각·개구부가 모두 직사각형이고 frame 각도가 mod 90으로 같으면 엔진 wall(회전 frame은 group 안
  지역 좌표)로 넘긴다. 아니면(사선 벽이 섞임) 생성기가 shapely로 합집합 − 개구부를 0.001 mm 격자에서 계산해
  닫힌 polyline(바깥 반시계·구멍 시계, 가장 작은 점부터)과 요청 시 SOLID hatch(HAT, 구멍 포함)로 낸다.
  엔진 wall도 같은 종류(닫힌 polyline·hatch)를 내지만 oid·경고는 엔진 규칙(op:wall/레이어/번호)을 따른다.
- 묶음마다 해치 요청이 같아야 하고, 개구부를 뺀 결과에 폭 1 mm 미만 조각이 남으면 거부한다.
- 명세 id는 요소 id와 역할(EW.f1, ED.leaf, CON.1.1.outer)로 정하고, 모든 op를 엔진 검사·전개로 확인한다.

거부(C1 밖): 곡면·두께가 변하는 벽, frame 축과 0.5° 넘게 어긋난 면선(frame을 그 벽 각도로 둘 것), 두께 0 이하
또는 1,000 mm 초과, 원·타원 기둥, 블록(문·창 블록은 표준에서 unused), host 구간 밖·다른 frame의 개구부,
SOLID 아닌 해치. 그리드·치수·버블·문자·계단·위생기구·표식·도곽·반복 패턴은 C2.

상태: 오류가 있으면 error(아무것도 쓰지 않음). 확인 필요 해석이나 미해결 질문이 있으면 preview(미리보기만).
둘 다 없으면 ready(미리보기 + ops 폴더). ops 폴더는 tools/ops/ops.py verify·bind로 확인·묶은 뒤 적용한다.
산출물: out/preview/preview.json·manifest.json, out/ops/<op>.json·hashes.json(ready일 때). bind는 out/bound로.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shapely import set_precision  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.geometry.polygon import orient  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from src.engine.layout import expand  # noqa: E402
from src.engine.spec import SpecError, parse_spec, validate_spec  # noqa: E402
from src.engine.standards import load_standard  # noqa: E402

SNAP_DEG = 0.5
MAX_THICK_MM = 1000.0
TOUCH_MM = 1.0
GRID_MM = 0.001
MIN_WIDTH_MM = 1.0
MIN_CROSS_DEG = 15.0
DIGITS = 3
MATERIAL_LAYER = {"RC": "CON", "조적": "WAL", "건식": "WAL1"}
MEANING_LAYER = {"승강기": "CIR", "샤프트": "CIR", "난간": "STL1", "보선": "HID", "대지경계": "LIM", "마감": "FIN"}
CONFIRMED = ("확정", "사용자확정")
WALL_TYPES = ("wall", "column")
OPENING_TYPES = ("window", "door", "opening")
LINE_TYPES = ("finish", "line", "polyline", "polygon")


class _Refused(ValueError):
    pass


def _o(v):
    """출력 좌표: 소수 셋째 자리, -0.0 정리"""
    v = round(float(v), DIGITS)
    return 0.0 if v == 0 else v


def _wrap(a):
    return (a + 90.0) % 180.0 - 90.0


def _yes_no(value, attr):
    """해석 값(record.py interpret --value는 문자열로 저장)을 참·거짓으로"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in ("true", "yes", "1", "예"):
        return True
    if text in ("false", "no", "0", "아니오"):
        return False
    raise _Refused(f"{attr}는 true|false(받은 것 {value})")


# ---------- 선·frame ----------

class _Frame:
    def __init__(self, name, x, y, deg):
        self.name, self.x, self.y, self.deg = name, x, y, deg
        self.c, self.s = math.cos(math.radians(deg)), math.sin(math.radians(deg))

    @property
    def identity(self):
        return self.x == 0 and self.y == 0 and self.deg == 0

    def local(self, x, y):
        dx, dy = x - self.x, y - self.y
        return self.c * dx + self.s * dy, -self.s * dx + self.c * dy

    def wcs(self, u, v):
        return self.x + self.c * u - self.s * v, self.y + self.s * u + self.c * v


class _Line:
    """frame 지역 좌표의 직선. axis u면 v=coord인 가로선, v면 u=coord인 세로선, None이면 점 p·방향 d"""
    def __init__(self, axis=None, coord=None, p=None, d=None):
        self.axis, self.coord, self.p, self.d = axis, coord, p, d
        if axis == "u":
            self.p, self.d = (0.0, coord), (1.0, 0.0)
        elif axis == "v":
            self.p, self.d = (coord, 0.0), (0.0, 1.0)


def _line(obs, frame):
    """관측 → frame 지역 좌표의 선. 같은 frame에서 잰 축 선이면 기록 좌표를 그대로 쓴다"""
    if [float(v) for v in obs["frame"]] == [frame.x, frame.y, frame.deg] and \
            abs(_wrap(obs["angle_local_deg"] - (0.0 if obs["axis"] == "u" else 90.0))) <= SNAP_DEG:
        return _Line(obs["axis"], float(obs["coord"]))
    pu, pv = frame.local(*obs["point_wcs"])
    a = obs["angle_wcs_deg"] - frame.deg
    if abs(_wrap(a)) <= SNAP_DEG:
        return _Line("u", pv)
    if abs(_wrap(a - 90.0)) <= SNAP_DEG:
        return _Line("v", pu)
    r = math.radians(a)
    return _Line(p=(pu, pv), d=(math.cos(r), math.sin(r)))


def _cross(a, b):
    """두 선의 교점. 교차각이 MIN_CROSS_DEG 미만이면 거부(교점 오차 ≈ 선 오차 / sin 교차각)"""
    if a.axis == "u" and b.axis == "v":
        return b.coord, a.coord
    if a.axis == "v" and b.axis == "u":
        return a.coord, b.coord
    det = a.d[0] * b.d[1] - a.d[1] * b.d[0]
    if abs(det) < math.sin(math.radians(MIN_CROSS_DEG)):
        raise _Refused(f"두 선이 {MIN_CROSS_DEG:.0f}° 미만으로 만나 교점을 정하지 않음")
    t = ((b.p[0] - a.p[0]) * b.d[1] - (b.p[1] - a.p[1]) * b.d[0]) / det
    return a.p[0] + t * a.d[0], a.p[1] + t * a.d[1]


# ---------- 요소 ----------

class _Piece:
    """벽 조각 하나: 지역 꼭짓점, 레이어, frame, 직사각형 여부"""
    def __init__(self, eid, layer, frame, local, rect, faces, hatch):
        self.eid, self.layer, self.frame, self.local, self.rect = eid, layer, frame, local, rect
        self.faces, self.hatch = faces, hatch
        self.wcs = Polygon([frame.wcs(u, v) for u, v in local])


class _Generator:
    def __init__(self, data, prefix, force_union=False, std=None):
        self.data, self.prefix, self.force_union = data, prefix, force_union
        self.std = std or load_standard()
        self.problems, self.pending, self.warnings = [], [], []
        self.frames = {}

    def frame(self, name):
        if name is None:
            return _Frame(None, 0.0, 0.0, 0.0)
        if name not in self.data["frames"]:
            raise _Refused(f"없는 frame {name}")
        if name not in self.frames:
            fr = self.data["frames"][name]
            self.frames[name] = _Frame(name, float(fr["x"]["value"]), float(fr["y"]["value"]),
                                       float(fr["deg"]["value"]))
        return self.frames[name]

    def interp(self, eid, attr, required=True, default=None):
        it = self.data["interpretations"].get(eid, {}).get(attr)
        if it is None:
            if required:
                raise _Refused(f"해석 {attr} 없음(interpret로 확정할 것)")
            return default
        note = f"{eid}.{attr}: 확인 필요({it.get('value')})"
        if it.get("status") not in CONFIRMED and note not in self.pending:
            self.pending.append(note)
        return it["value"]

    def ref(self, eid, el, role, frame):
        name = el.get("refs", {}).get(role)
        if name is None:
            raise _Refused(f"관측 참조 {role} 없음")
        if name not in self.data["observations"]:
            raise _Refused(f"없는 관측 {name}({role})")
        return _line(self.data["observations"][name], frame)

    def wall(self, eid, el):
        frame = self.frame(el.get("frame"))
        material = self.interp(eid, "material")
        if material not in MATERIAL_LAYER:
            raise _Refused(f"모르는 재료 {material}(지원: {', '.join(MATERIAL_LAYER)})")
        hatch = _yes_no(self.interp(eid, "hatch", required=False, default=False), "hatch")
        f1, f2, s, e = (self.ref(eid, el, r, frame) for r in ("f1", "f2", "s", "e"))
        if f1.axis is None or f2.axis is None or f1.axis != f2.axis:
            raise _Refused(f"면선 f1·f2가 frame 축과 {SNAP_DEG}° 넘게 어긋남(frame을 벽 각도로 둘 것)")
        thick = abs(f1.coord - f2.coord)
        if not 0 < thick <= MAX_THICK_MM:
            raise _Refused(f"벽 두께 {thick:.1f} mm(0 초과 {MAX_THICK_MM:.0f} 이하만)")
        local = [_cross(f1, s), _cross(f2, s), _cross(f2, e), _cross(f1, e)]
        if Polygon(local).area < 1e-6:
            raise _Refused("벽 길이가 0")
        rect = s.axis is not None and s.axis != f1.axis and e.axis == s.axis
        return _Piece(eid, MATERIAL_LAYER[material], frame, local, rect, (f1, f2), hatch)

    def opening(self, eid, el, pieces):
        """창·문·개구부: host 벽의 frame에서 면선과 끝선 s·e의 교점으로 개구부를, 종류별로 선·호를 만든다"""
        host = el.get("links", {}).get("host")
        if host is None:
            raise _Refused("links.host(붙는 벽) 없음")
        if host not in self.data["elements"]:
            raise _Refused(f"없는 host 요소 {host}")
        if self.data["elements"][host]["type"] not in WALL_TYPES:
            raise _Refused(f"host {host}가 벽이 아님({self.data['elements'][host]['type']})")
        if host not in pieces:
            raise _Refused(f"host {host}가 거부됨")
        piece = pieces[host]
        frame = piece.frame
        if el.get("frame", frame.name) != frame.name:
            raise _Refused(f"host {host}와 다른 frame({el.get('frame')})")
        f1, f2 = piece.faces
        s, e = self.ref(eid, el, "s", frame), self.ref(eid, el, "e", frame)
        local = [_cross(f1, s), _cross(f2, s), _cross(f2, e), _cross(f1, e)]
        poly = Polygon(local)
        if poly.area < 1e-6:
            raise _Refused("개구부 폭이 0")
        if poly.difference(Polygon(piece.local)).area > 1e-3:
            raise _Refused(f"host {host} 벽 구간 밖으로 나감")
        rect = s.axis is not None and s.axis != f1.axis and e.axis == s.axis
        kind = el["type"]
        items = []
        if kind == "window":
            glass = self.ref(eid, el, "glass", frame) if "glass" in el.get("refs", {}) else \
                _Line(f1.axis, (f1.coord + f2.coord) / 2)
            for role, line in (("f1", f1), ("f2", f2), ("glass", glass)):
                items.append(_seg(f"{eid}.{role}", "WIN", _cross(line, s), _cross(line, e)))
            items.append(_seg(f"{eid}.s", "WIN", local[0], local[1]))
            items.append(_seg(f"{eid}.e", "WIN", local[3], local[2]))
        elif kind == "door":
            items += self.door(eid, f1, f2, s, e)
        return _Opening(eid, host, frame, local, rect, items)

    def line_items(self, eid, el):
        """마감선·선(l·s·e)과 polyline·polygon(꼭짓점 i = v{i}a × v{i}b). 레이어는 해석, 마감선은 FIN"""
        frame = self.frame(el.get("frame"))
        kind = el["type"]
        if kind == "finish":
            layer = "FIN"
        else:
            meaning = self.interp(eid, "layer")
            meaning = str(meaning)
            layer = MEANING_LAYER.get(meaning) or (self.std.layer(meaning).name if self.std.layer(meaning) else None)
            if layer is None:
                raise _Refused(f"모르는 레이어 해석 {meaning}(뜻: {', '.join(MEANING_LAYER)} 또는 표준 레이어 이름)")
        linetype = self.interp(eid, "linetype", required=False)
        if kind in ("finish", "line"):
            l, s, e = (self.ref(eid, el, r, frame) for r in ("l", "s", "e"))
            item = _seg(eid, layer, _cross(l, s), _cross(l, e))
        else:
            pts, i = [], 1
            while f"v{i}a" in el.get("refs", {}):
                pts.append(_cross(self.ref(eid, el, f"v{i}a", frame), self.ref(eid, el, f"v{i}b", frame)))
                i += 1
            closed = kind == "polygon"
            if len(pts) < (3 if closed else 2):
                raise _Refused(f"꼭짓점(v1a·v1b, v2a·v2b, ...)이 {len(pts)}개")
            if closed and not Polygon(pts).is_valid:
                raise _Refused("다각형 변이 서로 교차함")
            item = {"kind": "polyline", "id": eid, "layer": layer, "points": [[_o(x), _o(y)] for x, y in pts],
                    "closed": closed}
        if linetype:
            item["linetype"] = linetype
        return frame, item

    def door(self, eid, f1, f2, s, e):
        swing = self.interp(eid, "swing")
        if swing not in ("f1", "f2"):
            raise _Refused(f"swing은 f1|f2(받은 것 {swing})")
        leaves = int(self.interp(eid, "leaves", required=False, default=1))
        if leaves not in (1, 2):
            raise _Refused(f"leaves는 1|2(받은 것 {leaves})")
        angle = float(self.interp(eid, "angle", required=False, default=90))
        if not 0 < angle <= 180:
            raise _Refused(f"angle은 0 초과 180 이하(받은 것 {angle})")
        face, other = (f1, f2) if swing == "f1" else (f2, f1)
        side = 1.0 if face.coord > other.coord else -1.0
        normal = (0.0, side) if face.axis == "u" else (side, 0.0)
        if leaves == 2:
            hinges = ("s", "e")
        else:
            hinge = self.interp(eid, "hinge")
            if hinge not in ("s", "e"):
                raise _Refused(f"hinge는 s|e(받은 것 {hinge})")
            hinges = (hinge,)
        ends = {"s": _cross(face, s), "e": _cross(face, e)}
        width = math.dist(ends["s"], ends["e"]) / leaves
        items = []
        for n, h in enumerate(hinges):
            p, q = ends[h], ends["e" if h == "s" else "s"]
            c = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            ccw = (q[0] - p[0]) * normal[1] - (q[1] - p[1]) * normal[0] > 0
            o = c + angle if ccw else c - angle
            tip = (p[0] + width * math.cos(math.radians(o)), p[1] + width * math.sin(math.radians(o)))
            tag = "" if n == 0 else str(n + 1)
            items.append(_seg(f"{eid}.leaf{tag}", "DOR", p, tip))
            a0, a1 = (c, o) if ccw else (o, c)
            items.append({"kind": "arc", "id": f"{eid}.arc{tag}", "layer": "DOR", "center": [_o(p[0]), _o(p[1])],
                          "radius": _o(width), "start_angle": _o(a0 % 360.0), "end_angle": _o(a1 % 360.0)})
        return items

    # ---------- 묶음·op ----------

    def clusters(self, pieces):
        parent = list(range(len(pieces)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i, a in enumerate(pieces):
            for j in range(i + 1, len(pieces)):
                b = pieces[j]
                if a.layer == b.layer and a.wcs.distance(b.wcs) <= TOUCH_MM:
                    parent[find(i)] = find(j)
        groups = {}
        for i, p in enumerate(pieces):
            groups.setdefault(find(i), []).append(p)
        return sorted(groups.values(), key=lambda g: (g[0].layer, *(_o(v) for v in _bounds(g)[:2]),
                                                      min(p.eid for p in g)))

    def cluster_items(self, cluster, openings, tag):
        """묶음 하나의 명세 항목. 한 방향(직사각형 조각·개구부, frame 각도 mod 90 같음)이면 엔진 wall,
        아니면 합집합 − 개구부를 닫힌 polyline으로 낸다. 어느 쪽이든 결과 형상을 먼저 검사한다"""
        if len({p.hatch for p in cluster}) > 1:
            raise _Refused("같은 벽 묶음에서 해치 요청이 다름(모두 같아야 함)")
        shape = set_precision(unary_union([p.wcs for p in cluster]), GRID_MM)
        if openings:
            shape = set_precision(shape.difference(unary_union([o.wcs for o in openings])), GRID_MM)
        polys = _polygons(shape)
        if not polys:
            raise _Refused("개구부를 빼고 남은 벽이 없음")
        opened = shape.buffer(-MIN_WIDTH_MM / 2, join_style="mitre", mitre_limit=10.0) \
            .buffer(MIN_WIDTH_MM / 2, join_style="mitre", mitre_limit=10.0)
        if shape.area - opened.area > 1.0:
            raise _Refused(f"폭 {MIN_WIDTH_MM:.0f} mm 미만 벽 조각이 남음(개구부 끝선 확인)")
        engine = not self.force_union and all(p.rect for p in cluster) and all(o.rect for o in openings) \
            and len({round(p.frame.deg % 90.0, 9) for p in cluster}) == 1
        if engine:
            return self.wall_items(cluster, openings)
        layer = cluster[0].layer
        items = []
        for n, poly in enumerate(polys, 1):
            rings = [_ring(poly.exterior.coords)] + sorted(_ring(r.coords) for r in poly.interiors)
            for i, ring in enumerate(rings):
                items.append({"kind": "polyline", "id": f"{tag}.{n}.{'outer' if i == 0 else f'hole{i}'}",
                              "layer": layer, "points": ring, "closed": True})
            if cluster[0].hatch:
                items.append({"kind": "hatch", "id": f"{tag}.{n}.hatch", "layer": "HAT", "pattern": "SOLID",
                              "boundary": rings[0], "holes": rings[1:]})
        by_frame = {}
        for o in sorted(openings, key=lambda o: o.eid):
            by_frame.setdefault(o.frame.name, (o.frame, []))[1].extend(o.items)
        for name in sorted(by_frame, key=lambda n: n or ""):
            frame, its = by_frame[name]
            if its:
                items += self.place(frame, its)
        return items

    def wall_items(self, cluster, openings):
        """한 방향 묶음: frame별로 엔진 wall 하나(회전 frame이면 group 안 지역 좌표)와 그 frame의 창·문 선"""
        by_frame = {}
        for p in sorted(cluster, key=lambda p: p.eid):
            by_frame.setdefault(p.frame.name, []).append(p)
        items = []
        for name in sorted(by_frame, key=lambda n: n or ""):
            ps = by_frame[name]
            eids = {p.eid for p in ps}
            cuts = [o for o in openings if o.host in eids]
            layer = ps[0].layer
            wall = {"kind": "wall", "id": f"wall.{layer}", "layer": layer,
                    "parts": [[[_o(u), _o(v)] for u, v in p.local] for p in ps]}
            if cuts:
                wall["openings"] = [[[_o(u), _o(v)] for u, v in o.local] for o in cuts]
            if any(p.hatch for p in ps):
                wall["hatch"] = {"layer": "HAT", "pattern": "SOLID"}
            items += self.place(ps[0].frame, [wall] + [it for o in cuts for it in o.items])
        return items

    @staticmethod
    def place(frame, items):
        if frame.identity:
            return items
        return [{"kind": "group", "id": f"F.{frame.name}", "items": items,
                 "place": [{"at": [_o(frame.x), _o(frame.y)], "rotation": frame.deg}]}]

    def check(self, o):
        """op 명세를 엔진처럼 읽고 표준 대조·전개해 본다(SPEC_ID·선종류·레이어 등)"""
        try:
            spec = parse_spec(json.dumps(self.spec(o["op"], o["items"]), ensure_ascii=False))
            violations = validate_spec(spec, self.std)
            if not violations:
                expand(spec, self.std, spec.scale or 100, warnings=self.warnings)
        except SpecError as e:
            violations = e.violations
        for v in violations[:5]:
            self.problems.append(f"{', '.join(o['elements'])}: 명세 {o['op']} {v.code}: {v.message}")

    def spec(self, op, items):
        out = self.data["output"]
        return {"schema_version": 1, "standard": out["standard"], "op": op, "units": "mm", "scale": out["k"],
                "items": items}

    def run(self):
        elements = self.data["elements"]
        pieces, openings, lines = {}, [], {}
        for eid in sorted(elements):
            el = elements[eid]
            try:
                if el["type"] in WALL_TYPES:
                    pieces[eid] = self.wall(eid, el)
                elif el["type"] in LINE_TYPES:
                    region = el.get("region") or "misc"
                    lines.setdefault(region, []).append((eid,) + self.line_items(eid, el))
                elif el["type"] not in OPENING_TYPES:
                    raise _Refused(f"C1에서 지원하지 않는 요소 종류 {el['type']}")
            except _Refused as e:
                self.problems.append(f"{eid}: {e}")
        for eid in sorted(elements):
            if elements[eid]["type"] in OPENING_TYPES:
                try:
                    openings.append(self.opening(eid, elements[eid], pieces))
                except _Refused as e:
                    self.problems.append(f"{eid}: {e}")
        pieces = list(pieces.values())
        for q in self.data["questions"]:
            if not q.get("answer"):
                self.pending.append(f"질문 {q['id']}: {q['text']}")
        ops = []
        clusters = self.clusters(pieces)
        for o in openings:
            for cluster in clusters:
                if any(p.eid == o.host for p in cluster):
                    continue
                hit = sorted(p.eid for p in cluster if p.wcs.intersection(o.wcs).area > 1e-3)
                if hit:
                    self.problems.append(f"{o.eid}: 다른 벽 묶음({', '.join(hit)})에 걸침")
        for cluster in clusters:
            layer = cluster[0].layer
            n = sum(1 for o in ops if o["layer"] == layer) + 1
            eids = {p.eid for p in cluster}
            mine = [o for o in openings if o.host in eids]
            try:
                items = self.cluster_items(cluster, mine, f"{layer}.{n}")
            except _Refused as e:
                self.problems.append(f"{', '.join(sorted(eids))}: {e}")
                continue
            ops.append({"op": f"{self.prefix}-wall-{layer}-{n}", "layer": layer, "items": items,
                        "elements": sorted(eids | {o.eid for o in mine})})
        for region in sorted(lines):
            by_frame = {}
            for eid, frame, item in lines[region]:
                by_frame.setdefault(frame.name, (frame, []))[1].append(item)
            items = []
            for name in sorted(by_frame, key=lambda n: n or ""):
                items += self.place(*by_frame[name])
            ops.append({"op": f"{self.prefix}-{region}", "layer": None, "items": items,
                        "elements": sorted(eid for eid, _, _ in lines[region])})
        for o in ops:
            self.check(o)
        if self.problems:
            return {"status": "error", "problems": self.problems, "pending": self.pending,
                    "warnings": self.warnings, "preview": None, "ops": [], "manifest": {}}
        specs = [self.spec(o["op"], o["items"]) for o in ops]
        preview = self.spec(f"{self.prefix}-preview",
                            [{"kind": "group", "id": o["op"], "items": o["items"], "place": [{"at": [0.0, 0.0]}]}
                             for o in ops])
        manifest = {eid: o["op"] for o in ops for eid in o["elements"]}
        return {"status": "preview" if self.pending else "ready", "problems": [], "pending": self.pending,
                "warnings": self.warnings, "preview": preview, "ops": specs, "manifest": manifest}


class _Opening:
    def __init__(self, eid, host, frame, local, rect, items):
        self.eid, self.host, self.frame, self.local, self.rect, self.items = eid, host, frame, local, rect, items
        self.wcs = Polygon([frame.wcs(u, v) for u, v in local])


def _polygons(shape):
    """합친 형상의 다각형들(바깥 반시계·구멍 시계), 왼쪽 아래부터"""
    if shape.is_empty:
        return []
    parts = [shape] if shape.geom_type == "Polygon" else [g for g in getattr(shape, "geoms", []) if g.geom_type == "Polygon"]
    parts = [orient(p.simplify(0), 1.0) for p in parts if p.area > 0]
    return sorted(parts, key=lambda p: (_o(p.bounds[0]), _o(p.bounds[1])))


def _ring(coords):
    """닫는 점을 빼고 가장 작은 (x, y) 점부터 시작하는 고리"""
    pts = [(_o(x), _o(y)) for x, y in list(coords)[:-1]]
    k = pts.index(min(pts))
    return [list(p) for p in pts[k:] + pts[:k]]


def _seg(iid, layer, a, b):
    return {"kind": "line", "id": iid, "layer": layer, "start": [_o(a[0]), _o(a[1])], "end": [_o(b[0]), _o(b[1])]}


def _bounds(pieces):
    xs = [b for p in pieces for b in p.wcs.bounds[0::2]]
    ys = [b for p in pieces for b in p.wcs.bounds[1::2]]
    return min(xs), min(ys), max(xs), max(ys)


_STD = []


def build(data, prefix, force_union=False):
    """measure dict → {status: ready|preview|error, problems, pending, warnings, preview, ops, manifest}.
    force_union은 시험용: 한 방향 묶음도 생성기 합치기로 낸다(엔진 wall과 같은 형상인지 대조)"""
    if not _STD:
        _STD.append(load_standard())
    return _Generator(data, prefix, force_union, _STD[0]).run()


def _dump(path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    path.write_text(text, encoding="utf-8")
    return text


def generate(measure_path, prefix, out, lock_path=None):
    """measure.yaml → out/preview/(preview.json·manifest.json), 준비되면 out/ops/(op별 명세·hashes.json)"""
    from tools.raster import record as rec
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        return {"status": "error", "problems": [f"실행 폴더가 비어 있지 않음(새 폴더를 쓸 것): {out}"]}
    try:
        checked = rec.validate(measure_path, lock_path)
        data = rec.load(measure_path)
    except rec.RecordError as e:
        return {"status": "error", "problems": [str(e)]}
    if checked["errors"]:
        return {"status": "error", "problems": [f"measure 검사: {e}" for e in checked["errors"]]}
    r = build(data, prefix)
    report = {k: r[k] for k in ("status", "problems", "pending", "warnings")}
    if r["status"] == "error":
        return report
    (out / "preview").mkdir(parents=True)
    _dump(out / "preview" / "preview.json", r["preview"])
    _dump(out / "preview" / "manifest.json", {
        "status": r["status"], "measure": str(Path(measure_path).resolve()), "prefix": prefix,
        "ops": [s["op"] for s in r["ops"]], "elements": r["manifest"], "pending": r["pending"],
        "warnings": r["warnings"], "estimates": checked["estimates"]})
    report["preview"] = str(out / "preview" / "preview.json")
    if r["status"] == "ready":
        from src.engine.spec import spec_hash
        (out / "ops").mkdir()
        listed = []
        for s in r["ops"]:
            text = _dump(out / "ops" / f"{s['op']}.json", s)
            listed.append({"op": s["op"], "bytes": len(text.encode("utf-8")), "spec_hash": spec_hash(parse_spec(text))})
        _dump(out / "ops" / "hashes.json", listed)
        report["ops"] = str(out / "ops")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="measure.yaml → 미리보기 명세·ops 폴더(형상)")
    parser.add_argument("measure")
    parser.add_argument("--prefix", required=True, help="op 이름 앞부분(예: p6f)")
    parser.add_argument("--out", required=True, help="새(빈) 실행 폴더")
    parser.add_argument("--lock", help="lock.json(주면 머리말 잠금을 대조)")
    args = parser.parse_args(argv)
    result = generate(args.measure, args.prefix, args.out, args.lock)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
