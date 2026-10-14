"""배치 전개(구조 결정 D1): 명세의 group·place 규칙을 WCS 기본 객체(Prim) 목록으로 펼친다

- 변환은 여기서 2D로 직접 계산한다. ezdxf의 transform()은 대칭을 돌출 방향 (0,0,-1)로 표현해
  좌표가 OCS가 되고, 읽기·검사가 위치를 잘못 읽기 때문이다.
- 대칭 규칙: 문자는 읽는 방향을 유지한다(뒤집히거나 거꾸로 읽히지 않게 회전·정렬을 고른다).
  블록은 도형까지 대칭한다(음수 x 배율). 호는 대칭이면 시작·끝을 바꾸고, 폴리선 bulge는 부호를 바꾼다.
  해치는 바깥 루프를 반시계, 섬을 시계 방향으로 맞춘다. 해치 패턴은 대칭하지 않고 회전만 따른다.
- 표준이 정하는 값(문자 높이·블록 레이어·배율·치수 레이어)을 여기서 채운다.
"""
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .spec import Spec, SpecError, spec_violation, parse_coord
from .standards import Standard

XDATA_STR_MAX = 255
_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class Prim(BaseModel):
    """펼친 기본 객체(WCS, mm)"""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["line", "polyline", "circle", "arc", "text", "block", "hatch", "dim"]
    oid: str
    path: str
    layer: str
    linetype: Optional[str] = None
    points: List[List[float]] = Field(default_factory=list, description="line [시작, 끝] / polyline 꼭짓점 / dim [p1, p2, 치수선 점]")
    bulges: List[float] = Field(default_factory=list)
    closed: bool = False
    center: Optional[List[float]] = None
    radius: Optional[float] = None
    start_angle: Optional[float] = None
    end_angle: Optional[float] = None
    at: Optional[List[float]] = None
    text: Optional[str] = None
    height: Optional[float] = None
    align: Optional[str] = None
    rotation: float = 0.0
    name: Optional[str] = Field(default=None, description="블록 이름 또는 치수 스타일")
    xscale: Optional[float] = None
    yscale: Optional[float] = None
    attribs: Dict[str, str] = Field(default_factory=dict)
    pattern: Optional[str] = None
    pattern_scale: Optional[float] = None
    pattern_angle: Optional[float] = None
    loops: List[List[List[float]]] = Field(default_factory=list, description="해치 루프(첫째가 바깥)")
    angle: Optional[float] = Field(default=None, description="치수 측정 방향(도)")


def _r(v: float) -> float:
    v = round(float(v), 6)
    return 0.0 if v == 0 else v


def _deg(v: float) -> float:
    v = _r(v % 360.0)
    return 0.0 if v == 360.0 else v


def _dir(deg: float) -> Tuple[float, float]:
    a = math.radians(deg)
    return math.cos(a), math.sin(a)


def _ang(v: Tuple[float, float]) -> float:
    return math.degrees(math.atan2(v[1], v[0]))


@dataclass(frozen=True)
class _T:
    """2D 변환: x' = a·x + b·y + tx, y' = c·x + d·y + ty"""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    @property
    def det(self) -> float:
        return self.a * self.d - self.b * self.c

    def vec(self, v) -> Tuple[float, float]:
        return self.a * v[0] + self.b * v[1], self.c * v[0] + self.d * v[1]

    def pt(self, p) -> List[float]:
        x, y = self.vec(p)
        return [_r(x + self.tx), _r(y + self.ty)]

    def then(self, outer: "_T") -> "_T":
        """이 변환 뒤에 outer를 적용하는 변환"""
        return _T(outer.a * self.a + outer.b * self.c, outer.a * self.b + outer.b * self.d,
                  outer.c * self.a + outer.d * self.c, outer.c * self.b + outer.d * self.d,
                  outer.a * self.tx + outer.b * self.ty + outer.tx, outer.c * self.tx + outer.d * self.ty + outer.ty)


def _placement(at: Tuple[float, float], rotation: float, mirror: Optional[str]) -> _T:
    """대칭 → 회전 → 이동"""
    mx, my = (-1.0, 1.0) if mirror == "x" else (1.0, -1.0) if mirror == "y" else (1.0, 1.0)
    cos, sin = _dir(rotation)
    return _T(cos * mx, -sin * my, sin * mx, cos * my, at[0], at[1])


def _area(loop: List[List[float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(loop, loop[1:] + loop[:1])) / 2




def _split_align(align: str) -> Tuple[str, str]:
    return (align[0], align[1]) if len(align) == 2 else ("", align)


def _readable_text(t: _T, rotation: float, align: str) -> Tuple[float, str]:
    """변환 뒤에도 문자가 뒤집히거나 거꾸로 읽히지 않게 회전과 정렬을 정한다"""
    base = t.vec(_dir(rotation))
    up = t.vec(_dir(rotation + 90))
    d = _ang(base)
    d = (d + 180) % 360 - 180               # (-180, 180]
    rot = d if -90 < d <= 90 else d + 180 if d <= -90 else d - 180
    v, h = _split_align(align)
    if abs(((rot - d) + 180) % 360 - 180) > 1e-6:   # 글줄 방향을 뒤집었으면 좌우 정렬도 바꾼다
        h = {"L": "R", "R": "L"}.get(h, h)
    ux, uy = _dir(rot + 90)
    if up[0] * ux + up[1] * uy < 0:         # 위아래가 뒤집혔으면 상하 정렬도 바꾼다
        v = {"T": "B", "B": "T", "": "T"}.get(v, v)
    return _deg(rot), v + h


class _Expander:
    def __init__(self, spec: Spec, std: Standard, k: int):
        self.spec, self.std, self.k = spec, std, k
        self.s = std.profile.scale.factors(k)["s"]
        self.roles = {h.id: h for h in list(std.text.heights) + list(std.text.sheet_title)}
        self.dimstyles = {d.name.casefold(): d for d in std.dimensions.styles}
        self.out: List[Prim] = []

    # ---------- 값 풀기 ----------

    def coord(self, value, axis: str) -> float:
        name, offset = parse_coord(value)
        if name is None:
            return offset
        return getattr(self.spec.grid, axis)[name] + offset

    def point(self, p) -> Tuple[float, float]:
        return self.coord(p[0], "X"), self.coord(p[1], "Y")

    @staticmethod
    def subst(text: str, values: Dict[str, int], where: str) -> str:
        def repl(m):
            name = m.group(1)
            if name not in values:
                raise SpecError([spec_violation("SPEC_SCHEMA", where, f"알 수 없는 변수 {{{name}}}(쓸 수 있는 변수: "
                                                                  f"{', '.join(sorted(values)) or '없음'})")])
            return str(values[name])
        return _VAR.sub(repl, text)

    def layer_name(self, name: str) -> str:
        return self.std.layer(name).name

    def linetype(self, layer: str, linetype: Optional[str]) -> Optional[str]:
        if not linetype or linetype.casefold() == "bylayer":
            return None
        return next(x for x in self.std.layer(layer).object_linetypes if x.casefold() == linetype.casefold())

    # ---------- 펼치기 ----------

    def walk(self, items, t: _T, chain: List[str], parent_path: Optional[str], values: Dict[str, int], where: str):
        for n, item in enumerate(items):
            w = f"{where}[{n}]"
            iid = self.subst(item.id or f"{item.kind}{n + 1}", values, f"{w}.id")
            if item.kind == "group":
                self.group(item, iid, t, chain, parent_path, values, w)
                continue
            path = self.subst(item.path, values, f"{w}.path") if item.path else \
                (f"{parent_path}/{iid}" if parent_path else iid)
            oid = f"{self.spec.op}:{'/'.join(chain + [iid])}"
            self.primitive(item, oid, path, t, values, w)

    def group(self, item, gid, t, chain, parent_path, values, where):
        g = 0
        for place in item.place:
            at = self.point(place.at)
            for i in range(place.repeat.count):
                local = dict(values)
                local["i"] = i
                local.update({name: rule.start + i * rule.step for name, rule in place.vars.items()})
                offset = (at[0] + i * place.repeat.step[0], at[1] + i * place.repeat.step[1])
                child_t = _placement(offset, place.rotation, place.mirror).then(t)
                seg = f"{gid}[{g}]"
                path = self.subst(item.path, local, f"{where}.path") if item.path else \
                    (f"{parent_path}/{seg}" if parent_path else seg)
                self.walk(item.items, child_t, chain + [seg], path, local, f"{where}.items")
                g += 1

    def add(self, **kw):
        self.out.append(Prim(**kw))

    def primitive(self, item, oid, path, t: _T, values, where):
        kind = item.kind
        common = {"oid": oid, "path": path}
        flip = t.det < 0
        if kind in ("line", "polyline", "circle", "arc"):
            layer = self.layer_name(item.layer)
            common.update(layer=layer, linetype=self.linetype(layer, item.linetype))
        if kind == "line":
            self.add(kind=kind, points=[t.pt(self.point(item.start)), t.pt(self.point(item.end))], **common)
        elif kind == "polyline":
            bulges = item.bulges or [0.0] * len(item.points)
            self.add(kind=kind, points=[t.pt(self.point(p)) for p in item.points],
                     bulges=[_r(-b if flip else b) for b in bulges], closed=item.closed, **common)
        elif kind == "circle":
            self.add(kind=kind, center=t.pt(self.point(item.center)), radius=_r(item.radius), **common)
        elif kind == "arc":
            start, end = (_deg(_ang(t.vec(_dir(a)))) for a in (item.start_angle, item.end_angle))
            if flip:
                start, end = end, start
            self.add(kind=kind, center=t.pt(self.point(item.center)), radius=_r(item.radius), start_angle=start,
                     end_angle=end, **common)
        elif kind == "text":
            rotation, align = _readable_text(t, item.rotation, item.align)
            self.add(kind=kind, layer=self.layer_name(item.layer), at=t.pt(self.point(item.at)),
                     text=self.subst(item.content, values, f"{where}.content"),
                     height=_r(self.roles[item.role].height * self.s), align=align, rotation=rotation, **common)
        elif kind == "block":
            bdef = self.std.block(item.name)
            factor = _r(bdef.scale_factor(self.s))
            if flip:   # 블록 y축이 대칭된 y축을 따르도록 회전하고 x 배율을 음수로
                rotation, xscale = _deg(_ang(t.vec(_dir(item.rotation + 90))) - 90), -factor
            else:
                rotation, xscale = _deg(_ang(t.vec(_dir(item.rotation)))), factor
            tags = {a.tag.casefold(): a.tag for a in bdef.attributes}
            attribs = {tags[k.casefold()]: self.subst(v, values, f"{where}.attribs") for k, v in item.attribs.items()}
            self.add(kind=kind, layer=bdef.layer, at=t.pt(self.point(item.at)), name=bdef.name, rotation=rotation,
                     xscale=xscale, yscale=factor, attribs=attribs, **common)
        elif kind == "hatch":
            loops = []
            for n, loop in enumerate([item.boundary] + list(item.holes)):
                pts = [t.pt(self.point(p)) for p in loop]
                if (_area(pts) > 0) != (n == 0):   # 바깥은 반시계, 섬은 시계
                    pts.reverse()
                loops.append(pts)
            rot = _ang(t.vec(_dir(90))) - 90 if flip else _ang(t.vec(_dir(0)))
            self.add(kind=kind, layer=self.layer_name(item.layer), pattern=item.pattern.upper(),
                     pattern_scale=_r(item.pattern_scale), pattern_angle=_deg(item.pattern_angle + rot),
                     loops=loops, **common)
        elif kind in ("dim", "dim_chain"):
            style = self.dimstyles[item.style.casefold()]
            pts = [item.p1, item.p2] if kind == "dim" else item.points
            pts = [self.point(p) for p in pts]
            line_at = self.coord(item.line_at, "Y" if item.direction == "x" else "X")
            for j in range(len(pts) - 1):
                p1, p2 = pts[j], pts[j + 1]
                if item.direction == "x":
                    base, angle = ((p1[0] + p2[0]) / 2, line_at), 0.0
                else:
                    base, angle = (line_at, (p1[1] + p2[1]) / 2), 90.0
                d_oid, d_path = (oid, path) if kind == "dim" else (f"{oid}.{j + 1}", f"{path}.{j + 1}")
                self.add(kind="dim", oid=d_oid, path=d_path, layer=style.layer, name=style.name,
                         points=[t.pt(p1), t.pt(p2), t.pt(base)], angle=_r(_ang(t.vec(_dir(angle))) % 180.0))


def expand(spec: Spec, std: Standard, k: int) -> List[Prim]:
    """검증한 명세를 기본 객체 목록으로 펼친다. oid가 겹치거나 태그가 너무 길면 SpecError(SPEC_ID)"""
    ex = _Expander(spec, std, k)
    ex.walk(spec.items, _T(), [], None, {}, "items")
    seen, problems = set(), []
    for p in ex.out:
        if p.oid in seen:
            problems.append(spec_violation("SPEC_ID", p.oid, "같은 oid가 둘 이상(같은 단계의 id가 겹침)", p.oid))
        seen.add(p.oid)
        for key, value in (("oid", p.oid), ("path", p.path)):
            if len(f"{key}={value}") > XDATA_STR_MAX:
                problems.append(spec_violation("SPEC_ID", p.oid, f"{key}가 너무 김(XDATA 문자열 {XDATA_STR_MAX}자)", len(value)))
    if problems:
        raise SpecError(problems)
    return ex.out
