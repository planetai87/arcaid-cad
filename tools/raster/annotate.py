"""범용 생성기의 주석·기호(C2): measure.yaml 요소 → 명세 항목. generate.py가 부른다(따로 실행하지 않음).

위치는 관측에서 온다. 점 역할(p·t·q·box)은 measure.py blob의 점 관측(외곽 상자와 그 중심)이고, 선 역할은 C1과 같은
선 관측이다. 외곽 상자(bbox)는 요소와 같은 frame에서 잰 것만 쓴다. 문자 내용·번호·간격은 Element.values(출처 있는 값),
이산 속성은 interpretations다. 표준이 정하지 않은 기호 치수(단면 표식·계단 기호 간격 등)는 assumptions로 보고한다.

요소 지원표(C2). 크기 × s(s = k/100)는 표준 기호 크기다.
  type      refs                               values                      해석                           출력·레이어
  text      p(점) 또는 l·r·b·t(선 4개, 실 중심) content(원본표기)           role 실명|일반|제목|창호부호,   ROM·TXT·TIT1 general·main_title MC,
                                                                          창호부호면 layer WIN|DOR       창호부호 small_tag
  mark      p                                  content                     layer DOR|WIN|SYM              원 r 200·s + 문자(DOR·WIN small_tag,
                                                                                                         SYM general)
  level     p(기호 상자: 좌하단 = 삽입점), t     content                     -                              LEV 블록 + LEV general 문자
  north     p(상자 중심), dir(화살표 축 선)       -                           flip true|false                방위 블록, 회전 = dir 각도(+180) − 90
  section   l(단면선), p(기호), q(끝 깃발)        num·sheet·seg_a·seg_b       look left|right(p→q 기준)      SYM 원 r 300·s·구분선·삼각형·채움
                                                                                                         (삼각형 − 원)·문자, 실선 구간, 깃발
  fixture   box(점의 상자)                      -                           kind 변기|세면기|소변기|욕조,  TOL 정형 형상(상자에 맞춤)
                                                                          back u+|u-|v+|v-(벽 쪽)
  pattern   l, s·e                             pitch·radius·first          shape scallop, side            기준선 + 반원 호 group repeat
                                                                          left|right(s→e 기준), layer
  stair     ao·h1·h2·bo(u 위치), at0·t2·at1,    -                           up a|b, a_wall·b_wall·         stair.md U형: CIR 디딤선·난간·옆선,
            bt0·bt1, land(v 위치)                                          land_wall(선택)                DIM 보행선·계단화살표, SYM1 절단선,
                                                                                                         TXT UP/DN
  grid      -(머리말 grids.<frame 또는 wcs>)     -                           bubbles(기본 상좌; 하·우는     CEN 축선, DIM330 축 체인·전체,
                                                                          사용자 확정 표준 예외)          축열버블 블록(하·우는 원·선·문자)
  sheet     -                                  name, number(기본 A-000)    title_under true|false         도곽 블록 + 표제, 아래 제목(선택)
계단 frame: 디딤선이 u 방향이고 참이 +v 쪽, run A가 u가 작은 쪽이다. 디딤판 깊이 T = t2 − at0, 단수는 (끝 − 첫)/T + 1이
정수에서 0.25 넘게 벗어나면 거부한다. 거부: 직선·곡선 계단, 등분 표기·개구부·외벽 치수 단, unused 블록.
"""
import math

from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

from tools.raster.common import _cross, _o, _Refused, _seg, _yes_no

ANNO_TYPES = ("text", "mark", "level", "north", "section", "fixture", "pattern")
TEXT_ROLE = {"실명": ("ROM", "general"), "일반": ("TXT", "general"), "제목": ("TIT1", "main_title"),
             "창호부호": (None, "small_tag")}
TAG_LAYERS = ("WIN", "DOR")
MARK_LAYERS = ("DOR", "WIN", "SYM")
FIXTURES = ("변기", "세면기", "소변기", "욕조")
MIN_FIXTURE_MM = 100.0
BUBBLE_SIDES = set("상하좌우")
TREAD_RESIDUAL = 0.25
TAN30 = math.tan(math.radians(30.0))


def _pt(p):
    return [_o(p[0]), _o(p[1])]


def _text(iid, layer, role, content, at, align="MC", rotation=0.0):
    item = {"kind": "text", "id": iid, "layer": layer, "role": role, "content": content, "at": _pt(at), "align": align}
    if rotation:
        item["rotation"] = _o(rotation)
    return item


def _poly(iid, layer, pts, closed=True, bulges=None):
    item = {"kind": "polyline", "id": iid, "layer": layer, "points": [_pt(p) for p in pts], "closed": closed}
    if bulges:
        item["bulges"] = bulges
    return item


def _circle(iid, layer, c, r):
    return {"kind": "circle", "id": iid, "layer": layer, "center": _pt(c), "radius": _o(r)}


def _block(iid, name, at, rotation=0.0, attribs=None):
    item = {"kind": "block", "id": iid, "name": name, "at": _pt(at)}
    if rotation:
        item["rotation"] = _o(rotation)
    if attribs:
        item["attribs"] = attribs
    return item


def _norm_deg(a):
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def _add(p, d, k=1.0):
    return p[0] + d[0] * k, p[1] + d[1] * k


# ---------- 문자·표식·레벨·방위 ----------

def anchor(g, eid, el, frame):
    """문자 기준점: 점 관측 p의 상자 중심, 또는 선 l·r·b·t로 둘러싼 칸의 중심"""
    refs = el.get("refs", {})
    if "p" in refs:
        return g.point(eid, el, "p", frame)[0]
    if all(r in refs for r in "lrbt"):
        l, r, b, t = (g.ref(eid, el, x, frame) for x in "lrbt")
        pts = [_cross(l, b), _cross(r, b), _cross(r, t), _cross(l, t)]
        return sum(x for x, _ in pts) / 4, sum(y for _, y in pts) / 4
    raise _Refused("위치는 p(점 관측) 또는 l·r·b·t(선 4개)")


def text(g, eid, el, frame):
    role = g.interp(eid, "role")
    if role not in TEXT_ROLE:
        raise _Refused(f"role은 {'|'.join(TEXT_ROLE)}(받은 것 {role})")
    layer, rid = TEXT_ROLE[role]
    if layer is None:
        layer = g.interp(eid, "layer")
        if layer not in TAG_LAYERS:
            raise _Refused(f"창호부호 layer는 {'|'.join(TAG_LAYERS)}(받은 것 {layer})")
    return [_text(eid, layer, rid, g.content(eid, el, "content"), anchor(g, eid, el, frame))]


def mark(g, eid, el, frame):
    layer = g.interp(eid, "layer")
    if layer not in MARK_LAYERS:
        raise _Refused(f"layer는 {'|'.join(MARK_LAYERS)}(받은 것 {layer})")
    c = g.point(eid, el, "p", frame)[0]
    role = "small_tag" if layer in TAG_LAYERS else "general"
    return [_circle(f"{eid}.circle", layer, c, 200.0 * g.s),
            _text(f"{eid}.text", layer, role, g.content(eid, el, "content"), c)]


def level(g, eid, el, frame):
    _, box = g.point(eid, el, "p", frame, box=True)
    t = g.point(eid, el, "t", frame)[0]
    return [_block(f"{eid}.symbol", "LEV", (box[0], box[1])),
            _text(f"{eid}.text", "LEV", "general", g.content(eid, el, "content"), t)]


def north(g, eid, el, frame):
    c = g.point(eid, el, "p", frame)[0]
    d = g.ref(eid, el, "dir", frame).d
    flip = _yes_no(g.interp(eid, "flip"), "flip")
    arrow = math.degrees(math.atan2(d[1], d[0])) + (180.0 if flip else 0.0)
    return [_block(f"{eid}.symbol", "방위", c, _norm_deg(arrow - 90.0))]


# ---------- 단면 표식 ----------

def section(g, eid, el, frame):
    """단면선 l 위의 기호(원·구분선·바라보는 쪽 삼각형, 원 밖만 채움)·번호, 실선 구간 두 개, 끝 깃발(지상6층 형식)"""
    line = g.ref(eid, el, "l", frame)
    look = g.interp(eid, "look")
    if look not in ("left", "right"):
        raise _Refused(f"look은 left|right(받은 것 {look})")
    p0, d = line.p, line.d

    def proj(q):
        t = (q[0] - p0[0]) * d[0] + (q[1] - p0[1]) * d[1]
        return t, (p0[0] + d[0] * t, p0[1] + d[1] * t)
    tp, c = proj(g.point(eid, el, "p", frame)[0])
    tq, e = proj(g.point(eid, el, "q", frame)[0])
    if abs(tq - tp) < 1.0:
        raise _Refused("기호 p와 끝 q가 단면선 위 같은 자리")
    sign = 1.0 if tq > tp else -1.0
    t = (d[0] * sign, d[1] * sign)
    u = (-t[1], t[0]) if look == "left" else (t[1], -t[0])
    seg_a, seg_b = g.number(eid, el, "seg_a"), g.number(eid, el, "seg_b")
    if seg_a <= 0 or seg_b <= 0:
        raise _Refused("seg_a·seg_b(실선 구간 길이)는 0보다 커야 함")
    s = g.s
    r = 300.0 * s
    tri = [_add(c, t, -1.35 * r), _add(c, t, 1.35 * r), _add(c, u, 1.35 * r)]
    fill = Polygon(tri).difference(Point(c).buffer(r, quad_segs=16))
    pieces = [fill] if fill.geom_type == "Polygon" else list(fill.geoms)
    pieces = sorted((orient(q, 1.0) for q in pieces if q.area > 1e-6),
                    key=lambda q: (_o(q.bounds[0]), _o(q.bounds[1])))
    rot = math.degrees(math.atan2(u[1], u[0])) - 90.0
    items = [_circle(f"{eid}.circle", "SYM", c, r), _seg(f"{eid}.divider", "SYM", _add(c, t, -r), _add(c, t, r)),
             _poly(f"{eid}.triangle", "SYM", tri)]
    for n, q in enumerate(pieces, 1):
        items.append({"kind": "hatch", "id": f"{eid}.fill{n}", "layer": "SYM", "pattern": "SOLID",
                      "boundary": [_pt(p) for p in list(q.exterior.coords)[:-1]]})
    items += [_text(f"{eid}.num", "SYM", "general", g.content(eid, el, "num"), _add(c, u, 0.45 * r), rotation=rot),
              _text(f"{eid}.sheet", "SYM", "general", g.content(eid, el, "sheet"), _add(c, u, -0.45 * r),
                    rotation=rot),
              _seg(f"{eid}.seg_a", "SYM", c, _add(c, t, seg_a)),
              _seg(f"{eid}.seg_b", "SYM", _add(e, t, -seg_b), e)]
    flag = [e, _add(e, t, 120.0 * s), _add(_add(e, t, 120.0 * s), u, 700.0 * s)]
    items += [_poly(f"{eid}.flag", "SYM", flag),
              {"kind": "hatch", "id": f"{eid}.flag_fill", "layer": "SYM", "pattern": "SOLID",
               "boundary": [_pt(p) for p in flag]}]
    g.assume(f"{eid}: 단면 표식 형상(원 300·s, 삼각형 1.35r, 깃발 120·700·s, 실선 구간 시작 = 기호 중심)은 "
             f"표준 블록이 없어 지상6층 형식을 따름")
    return items


# ---------- 위생기구 ----------

def fixture(g, eid, el, frame):
    """측정한 외곽 상자 안에 정형 형상. 기구 좌표 (a 벽을 따라, b 벽에서 앞으로)를 back에 맞춰 회전해 넣는다"""
    kind = g.interp(eid, "kind")
    if kind not in FIXTURES:
        raise _Refused(f"kind는 {'|'.join(FIXTURES)}(받은 것 {kind})")
    back = g.interp(eid, "back")
    _, (u0, v0, u1, v1) = g.point(eid, el, "box", frame, box=True)
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    maps = {"v-": (u1 - u0, v1 - v0, lambda a, b: (cu + a, v0 + b)),
            "v+": (u1 - u0, v1 - v0, lambda a, b: (cu - a, v1 - b)),
            "u-": (v1 - v0, u1 - u0, lambda a, b: (u0 + b, cv - a)),
            "u+": (v1 - v0, u1 - u0, lambda a, b: (u1 - b, cv + a))}
    if back not in maps:
        raise _Refused(f"back은 {'|'.join(maps)}(받은 것 {back})")
    W, D, f = maps[back]
    if min(W, D) < MIN_FIXTURE_MM:
        raise _Refused(f"외곽 상자 {W:.0f}×{D:.0f} mm가 {MIN_FIXTURE_MM:.0f} mm보다 작음")

    def rect(a0, b0, a1, b1):
        return [f(a0, b0), f(a1, b0), f(a1, b1), f(a0, b1)]
    h = W / 2
    items = []
    if kind == "변기":
        items.append(_poly(f"{eid}.tank", "TOL", rect(-h, 0.0, h, 0.236 * D)))
        bowl = [f(0.452 * W * math.cos(2 * math.pi * i / 24), 0.639 * D + 0.361 * D * math.sin(2 * math.pi * i / 24))
                for i in range(24)]
        items.append(_poly(f"{eid}.bowl", "TOL", bowl))
    elif kind == "세면기":
        inset = min(50.0, W / 4, D / 4)
        items += [_poly(f"{eid}.outer", "TOL", rect(-h, 0.0, h, D)),
                  _poly(f"{eid}.inner", "TOL", rect(-h + inset, inset, h - inset, D - inset)),
                  _circle(f"{eid}.drain", "TOL", f(0.0, D / 2), min(30.0, W / 8, D / 8))]
    elif kind == "소변기":
        if D - h <= 0:
            raise _Refused("소변기 상자 깊이가 폭의 반보다 작음")
        items.append(_poly(f"{eid}.outer", "TOL", rect(-h, 0.0, h, D - h), bulges=[0.0, 0.0, 1.0, 0.0]))
    else:
        inset = min(75.0, W / 6, D / 6)
        items += [_poly(f"{eid}.outer", "TOL", rect(-h, 0.0, h, D)),
                  _poly(f"{eid}.inner", "TOL", rect(-h + inset, inset, h - inset, D - inset))]
    g.assume(f"{eid}: {kind} 형상 비율은 정형(외곽 상자에 맞춤)")
    return items, [(u0, v0), (u1, v1)]


# ---------- 물결 패턴 ----------

def pattern(g, eid, el, frame):
    shape = g.interp(eid, "shape")
    if shape != "scallop":
        raise _Refused(f"shape는 scallop(받은 것 {shape})")
    side = g.interp(eid, "side")
    if side not in ("left", "right"):
        raise _Refused(f"side는 left|right(받은 것 {side})")
    layer = g.layer_of(eid)
    l, s, e = (g.ref(eid, el, r, frame) for r in ("l", "s", "e"))
    a, b = _cross(l, s), _cross(l, e)
    pitch, r, first = (g.number(eid, el, k) for k in ("pitch", "radius", "first"))
    if r <= 0 or pitch < 2 * r:
        raise _Refused(f"radius > 0, pitch ≥ 2·radius여야 함(pitch {pitch}, radius {r})")
    length = math.dist(a, b)
    if first < r or first + r > length:
        raise _Refused(f"first는 radius 이상, 기준선 길이 − radius 이하(first {first}, 길이 {length:.0f})")
    n = int((length - first - r) // pitch) + 1
    if n > 1000:
        raise _Refused(f"반복 {n}개가 1000개를 넘음")
    t = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    arc = {"kind": "arc", "id": "b", "layer": layer, "center": [0.0, 0.0], "radius": _o(r),
           "start_angle": 180.0 if side == "right" else 0.0, "end_angle": 360.0 if side == "right" else 180.0}
    return [_seg(f"{eid}.base", layer, a, b),
            {"kind": "group", "id": f"{eid}.scallop", "items": [arc],
             "place": [{"at": _pt(_add(a, t, first)), "rotation": _o(math.degrees(math.atan2(t[1], t[0]))),
                        "repeat": {"count": n, "step": [_o(t[0] * pitch), _o(t[1] * pitch)]}}]}], [a, b]


# ---------- 그리드 ----------

def _floor100(v):
    return math.floor(v / 100.0) * 100.0


def _ceil100(v):
    return math.ceil(v / 100.0) * 100.0


def _span(axes, shapes, s, dims):
    """축선 끝: 바깥 축 ± cen_overhang. 같은 frame 형상이 그보다 cen_overhang_beyond_wall_min 안쪽까지 나오면
    형상 ± 그 값을 100 단위로 바깥쪽에 맞춘다"""
    over, beyond = dims["cen_overhang"] * s, dims["cen_overhang_beyond_wall_min"] * s
    lo = min(axes) - over if axes else None
    hi = max(axes) + over if axes else None
    if shapes:
        if lo is None or min(shapes) - beyond < lo:
            lo = _floor100(min(shapes) - beyond)
        if hi is None or max(shapes) + beyond > hi:
            hi = _ceil100(max(shapes) + beyond)
    return lo, hi


def _axes(raw, word):
    axes = sorted(((float(v["value"]), name) for name, v in (raw or {}).items()), key=lambda x: (x[0], x[1]))
    for (a, na), (b, nb) in zip(axes, axes[1:]):
        if abs(a - b) < 1e-6:
            raise _Refused(f"{word} 축 {na}·{nb} 위치가 겹침({a})")
    return axes


def _order_warning(axes, word):
    nums = []
    for _, name in axes:
        digits = "".join(ch for ch in name if ch.isdigit())
        if not digits:
            return None
        nums.append(int(digits))
    if nums != sorted(nums):
        return f"{word} 축 번호가 위치 순서와 다름({', '.join(n for _, n in axes)})"
    return None


def grid(g, eid, el, frame, shapes):
    """축선(CEN)·치수(DIM330 축 체인 + 전체)·버블. 세로 축(X)은 지역 u, 가로 축(Y)은 지역 v 위치다"""
    key = el.get("frame") or "wcs"
    if key not in g.data["grids"]:
        raise _Refused(f"머리말 grids.{key}가 없음")
    xs, ys = _axes(g.data["grids"][key].get("X"), "X"), _axes(g.data["grids"][key].get("Y"), "Y")
    if not xs and not ys:
        raise _Refused(f"grids.{key}에 축이 없음")
    for axes, word in ((xs, "X"), (ys, "Y")):
        w = _order_warning(axes, word)
        if w:
            g.warnings.append(f"{eid}: {w}")
    bubbles = str(g.interp(eid, "bubbles", required=False, default="상좌"))
    if not set(bubbles) <= BUBBLE_SIDES:
        raise _Refused(f"bubbles는 상·하·좌·우 조합(받은 것 {bubbles})")
    if set(bubbles) & set("하우"):
        it = g.data["interpretations"].get(eid, {}).get("bubbles", {})
        if it.get("by") != "user":
            raise _Refused("하·우 버블은 표준 예외라 사용자 확정(by user)만 받음")
    s = g.s
    dims = g.std.dimensions.spacing.model_dump()
    us, vs = [p[0] for p in shapes], [p[1] for p in shapes]
    v_lo, v_hi = _span([y for y, _ in ys], vs, s, dims)
    u_lo, u_hi = _span([x for x, _ in xs], us, s, dims)
    if xs and v_lo is None:
        raise _Refused("가로 축(Y)도 형상도 없어 세로 축선 길이를 정하지 못함")
    if ys and u_lo is None:
        raise _Refused("세로 축(X)도 형상도 없어 가로 축선 길이를 정하지 못함")
    first, gap = dims["first_row_from_cen_end"] * s, dims["row_gap"] * s
    items = [_seg(f"{eid}.{n}.cen", "CEN", (x, v_lo), (x, v_hi)) for x, n in xs]
    items += [_seg(f"{eid}.{n}.cen", "CEN", (u_lo, y), (u_hi, y)) for y, n in ys]
    sides = [("상", xs, v_hi, 1.0, "x"), ("하", xs, v_lo, -1.0, "x"), ("좌", ys, u_lo, -1.0, "y"),
             ("우", ys, u_hi, 1.0, "y")]
    for side, axes, base, sign, direction in sides:
        if side not in bubbles or not axes:
            continue

        def at(pos, off):     # 축 위치 pos, 축선 끝에서 바깥으로 off
            return (pos, base + sign * off) if direction == "x" else (base + sign * off, pos)
        row1 = base + sign * first
        outer = row1
        if len(axes) >= 2:
            items.append({"kind": "dim_chain", "id": f"{eid}.{side}.axes", "style": "DIM330", "direction": direction,
                          "points": [_pt(at(p, 0.0)) for p, _ in axes], "line_at": _o(row1)})
        if len(axes) >= 3:
            outer = row1 + sign * gap
            items.append({"kind": "dim", "id": f"{eid}.{side}.total", "style": "DIM330", "direction": direction,
                          "p1": _pt(at(axes[0][0], 0.0)), "p2": _pt(at(axes[-1][0], 0.0)), "line_at": _o(outer)})
        off = abs(outer - base)
        for p, name in axes:
            if side in ("상", "좌"):
                items.append(_block(f"{eid}.{name}.bubble", "축열버블-상" if side == "상" else "축열버블-좌",
                                    at(p, off), attribs={"축열": name}))
            else:
                items += [_seg(f"{eid}.{name}.bubble_line", "DIM", at(p, off + 350.0 * s), at(p, off + 500.0 * s)),
                          _circle(f"{eid}.{name}.bubble", "DIM", at(p, off + 800.0 * s), 300.0 * s),
                          _text(f"{eid}.{name}.bubble_text", "DIM", "grid_bubble", name, at(p, off + 800.0 * s))]
    return items


# ---------- 계단(stair.md U형) ----------

def _runs(g, eid, el, frame, roles, axis):
    out = []
    for r in roles:
        line = g.ref(eid, el, r, frame)
        if line.axis != axis:
            raise _Refused(f"{r}는 frame {'세로' if axis == 'v' else '가로'} 선이어야 함(계단 frame: 디딤선이 u 방향)")
        out.append(line.coord)
    return out


def _treads(first, last, T, word):
    q = (last - first) / T
    n = round(q)
    if abs(q - n) > TREAD_RESIDUAL or n < 1:
        raise _Refused(f"{word} 디딤선 수가 정수가 아님((끝 − 첫)/T = {q:.2f})")
    return n + 1


def stair(g, eid, el, frame):
    ao, h1, h2, bo = _runs(g, eid, el, frame, ("ao", "h1", "h2", "bo"), "v")
    at0, t2, at1, bt0, bt1, land = _runs(g, eid, el, frame, ("at0", "t2", "at1", "bt0", "bt1", "land"), "u")
    if not ao < h1 < h2 < bo:
        raise _Refused("run 순서는 ao < h1 < h2 < bo(run A가 u가 작은 쪽)")
    T = t2 - at0
    if T <= 0:
        raise _Refused(f"디딤선 간격 T = t2 − at0 = {T:.1f}")
    n_a, n_b = _treads(at0, at1, T, "run A"), _treads(bt0, bt1, T, "run B")
    up = g.interp(eid, "up")
    if up not in ("a", "b"):
        raise _Refused(f"up은 a|b(받은 것 {up})")
    walls = {w: _yes_no(g.interp(eid, w, required=False, default=False), w) for w in ("a_wall", "b_wall", "land_wall")}
    y_top = max(at1, bt1)
    if land <= y_top:
        raise _Refused("참 끝선 land가 마지막 디딤선보다 앞에 있음")
    s = g.s
    c = (h1 + h2) / 2
    if abs((h2 - h1) - 250.0) > 20.0:
        g.warnings.append(f"{eid}: 가운데 난간 폭 {h2 - h1:.0f} mm(stair.md 틀 250으로 그림)")
    y0 = min(at0, bt0)
    xl, xr = (ao + c - 125.0) / 2, (c + 125.0 + bo) / 2
    y_land = y_top + (land - y_top) / 2
    if up == "b":
        x_up, y_up, last_up, x_dn, y_dn = xr, bt0, bt1, xl, at0
    else:
        x_up, y_up, last_up, x_dn, y_dn = xl, at0, at1, xr, bt0
    yc = y_up + 4.87 * T
    if yc >= last_up:
        raise _Refused(f"절단 위치 {yc:.0f}가 UP run 끝 디딤선 {last_up:.0f}을 넘음")

    def repeat(iid, a, b, count, y):
        return {"kind": "group", "id": iid, "items": [{**_seg("t", "CIR", (a, y), (b, y))}],
                "place": [{"at": [0.0, 0.0], "repeat": {"count": count, "step": [0.0, _o(T)]}}]}
    items = [repeat(f"{eid}.a.tread", ao, c - 125.0, n_a, at0), repeat(f"{eid}.b.tread", c + 125.0, bo, n_b, bt0),
             _poly(f"{eid}.rail", "CIR", [(c - 75, y0 - 100), (c - 75, y_top + 50), (c + 75, y_top + 50),
                                          (c + 75, y0 - 100), (c + 125, y0 - 100), (c + 125, y_top + 100),
                                          (c - 125, y_top + 100), (c - 125, y0 - 100)]),
             _seg(f"{eid}.rail.l", "CIR", (c - 50, y0), (c - 50, y_top)),
             _seg(f"{eid}.rail.r", "CIR", (c + 50, y0), (c + 50, y_top)),
             _seg(f"{eid}.rail.top", "CIR", (c - 75, y_top), (c + 75, y_top))]
    for run, x0, x1, first, last, n in (("a", c - 75, c - 50, at0, at1, n_a), ("b", c + 50, c + 75, bt0, bt1, n_b)):
        count = n - (1 if abs(last - y_top) < 1e-6 else 0)
        if count:
            items.append(repeat(f"{eid}.{run}.rail_tick", x0, x1, count, first))
    if not walls["a_wall"]:
        items.append(_seg(f"{eid}.a.side", "CIR", (ao, y0), (ao, land)))
    if not walls["b_wall"]:
        items.append(_seg(f"{eid}.b.side", "CIR", (bo, y0), (bo, land)))
    if not walls["land_wall"]:
        items.append(_seg(f"{eid}.land", "CIR", (ao, land), (bo, land)))
    items += [_poly(f"{eid}.walk.dn", "DIM", [(x_dn, y_dn), (x_dn, y_land), (x_up, y_land)], closed=False),
              _seg(f"{eid}.walk.up", "DIM", (x_up, y_up), (x_up, y_land))]
    # 절단선·화살표: UP run이 c 오른쪽인 기준 배치를 c 원점 group에 담고, up=a면 x 대칭
    xr_ = abs(x_up - c)
    out_wall = walls["b_wall"] if up == "b" else walls["a_wall"]
    x_out = (bo - c) if up == "b" else (c - ao)
    x_end = x_out if out_wall else x_out + 290.0 * s
    z, j = 65.6 * s, 11.8 * s
    sym = [_block("cut", "계단절단선Z", (xr_, yc)),
           _seg("cut.lower", "SYM1", (xr_ - z, yc - j), (-50.0, yc - j - (xr_ - z + 50.0) * TAN30)),
           _seg("cut.upper", "SYM1", (xr_ + z, yc + j), (x_end, yc + j + (x_end - xr_ - z) * TAN30)),
           _block("up", "계단화살표", (xr_, yc - 160.0 * s)),
           _block("dn", "계단화살표", (xr_, yc + 133.0 * s), 180.0)]
    place = {"at": [_o(c), 0.0]}
    if up == "a":
        place["mirror"] = "x"
    items.append({"kind": "group", "id": f"{eid}.symbols", "items": sym, "place": [place]})
    items += [_text(f"{eid}.up", "TXT", "stair_updn", "UP", (x_up, y_up - 100.0 * s), "TC"),
              _text(f"{eid}.dn", "TXT", "stair_updn", "DN", (x_dn, y_dn - 100.0 * s), "TC")]
    g.assume(f"{eid}: 계단 기호(절단 위치 4.87T, 화살표 160·133·s, 절단선 접합 11.8·s, 난간 틀 250)는 stair.md 가정")
    return items, [(ao, y0 - 100.0), (bo, land)]


# ---------- 도곽 ----------

def title_under(g, eid, el, box):
    """도면 아래 제목(지상6층 형식): 번호 원·번호·도면명·밑줄·축척"""
    s, k = g.s, g.k
    x0, y0 = box[0], box[1]
    ty = y0 - 1000.0 * s
    c = (x0 + 330.0 * s, ty)
    g.assume(f"{eid}: 아래 제목 배치(도면 왼쪽 아래, 원 330·s)는 지상6층 형식")
    return [_circle(f"{eid}.title.circle", "TIT", c, 330.0 * s),
            _text(f"{eid}.title.number", "TIT1", "main_title", g.content(eid, el, "title_number", default="1"), c),
            _text(f"{eid}.title.name", "TIT1", "main_title", g.content(eid, el, "name"),
                  (x0 + 1000.0 * s, ty + 120.0 * s), "BL"),
            _seg(f"{eid}.title.line", "TIT", (x0 + 660.0 * s, ty), (x0 + 8300.0 * s, ty)),
            _text(f"{eid}.title.scale", "TIT1", "sub_title", f"SCALE   A1 : 1/{k // 2}   A3 : 1/{k}",
                  (x0 + 1000.0 * s, ty - 150.0 * s), "TL")]


def sheet(g, eid, el, box):
    """도곽: 도면 외곽 box(x0, y0, x1, y1)를 그림 영역(35000×29700·s) 가운데에, 삽입점은 100 단위 내림"""
    s, k = g.s, g.k
    width, height, margin = 35000.0 * s, 29700.0 * s, 300.0 * s
    x0, y0, x1, y1 = box
    sx = _floor100((x0 + x1) / 2 - width / 2)
    sy = _floor100((y0 + y1) / 2 - height / 2)
    if x0 < sx + margin or x1 > sx + width - margin or y0 < sy + margin or y1 > sy + height - margin:
        raise _Refused(f"도면 외곽 {x1 - x0:.0f}×{y1 - y0:.0f} mm가 도곽 그림 영역 {width:.0f}×{height:.0f} mm"
                       f"(여백 {margin:.0f})에 안 들어감: k를 키우거나 나눠 그릴 것")
    items = [_block(f"{eid}.frame", "HEOarchitects_sheet", (sx, sy))]
    for st in g.std.text.sheet_title:
        if st.id == "drawing_name":
            content = g.content(eid, el, "name")
        elif st.id == "scale":
            content = st.format.replace("<k÷2>", str(k // 2)).replace("<k>", str(k))
        else:
            content = g.content(eid, el, "number", default=st.format)
        items.append(_text(f"{eid}.{st.id}", "TIT1", st.id, content,
                           (sx + st.insert[0] * s, sy + st.insert[1] * s), "L"))
    return items
