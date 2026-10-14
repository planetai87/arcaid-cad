"""객체 명세(구조 결정 D1·D5): AI가 좌표 대신 배치 규칙을 담아 넘기는 JSON의 형식과 표준 대조

이 docstring이 명세 형식의 원본이다.

    {"schema_version": 1, "standard": "heo-2026@2026.1", "op": "dangsan-3f-rooms", "units": "mm", "scale": 100,
     "target": {"drawing_id": null, "snapshot_id": null},
     "grid": {"X": {"X1": 0, "X2": 3300}, "Y": {"Y1": 0, "Y2": 6000}},
     "items": [ ...객체 또는 group... ]}

- 단위는 mm, 좌표계는 모형 공간 WCS. scale은 A3 축척 분모 k(없으면 100으로 가정).
- 좌표: 숫자 또는 그리드 참조 "X2", "X2+300", "Y1-450"(x 자리에는 X축, y 자리에는 Y축).
  그리드 참조는 최상위 객체와 최상위 group의 place.at에서만 쓴다. group 안 좌표는 지역 좌표(숫자)다.
- 객체(kind): line(start, end) · polyline(points, bulges?, closed) · circle(center, radius) ·
  arc(center, radius, start_angle, end_angle) · text(layer, role, content, at, align, rotation) ·
  block(name, at, rotation, attribs) · hatch(layer, pattern, pattern_scale, pattern_angle, boundary, holes) ·
  dim(style, p1, p2, direction x|y, line_at) · dim_chain(style, points, direction, line_at) · group(items, place)
- 표준이 정하는 값은 명세에 쓰지 않는다: 색·선굵기(ByLayer), 문자 스타일(돋움), 문자 높이(role = text.yaml의 id,
  높이 × s), 블록 레이어·배율(blocks.yaml), 치수 레이어(스타일의 레이어)·DIMSCALE(k/2).
  linetype은 레이어의 object_linetypes(LIM의 PHANTOM·CENTER)만 줄 수 있다.
- group.place: [{at, rotation, mirror: x|y, repeat: {count, step}, vars: {이름: {start, step}}}]
  변환 순서는 대칭(지역 x 또는 y 좌표 부호 반전) → 회전 → 이동(at + i × step).
  vars와 {i}(그 배치 안의 번호, 0부터)는 content·attribs 값·id·path의 "{이름}"을 바꾼다.
- id는 객체를 식별하는 이름이고(없으면 kind+순번), path는 층·실 같은 의미 경로다(D9).
"""
import hashlib
import json
import math
import re
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from ezdxf.tools.pattern import ISO_PATTERN
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .results import Violation
from .standards import Standard

MAX_OBJECTS = 20000
MAX_DEPTH = 4
KINDS = ("line", "polyline", "circle", "arc", "text", "block", "hatch", "dim", "dim_chain", "group")
ALIGNS = ("L", "C", "R", "ML", "MC", "MR", "TL", "TC", "TR", "BL", "BC", "BR")
ID_PATTERN = r"^[^/:\[\]\s]{1,64}$"
AXIS_PATTERN = r"^[A-Za-z0-9_.]{1,16}$"
_REF = re.compile(r"^([A-Za-z0-9_.]{1,16})\s*(?:([+-])\s*(\d+(?:\.\d+)?))?$")

Coord = Union[float, str]
Point = Tuple[Coord, Coord]


class SpecError(Exception):
    """명세를 받을 수 없다(위반 목록을 담는다)"""

    def __init__(self, violations: List[Violation]):
        self.violations = violations
        super().__init__("; ".join(v.message for v in violations))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Item(_Model):
    id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    path: Optional[str] = Field(default=None, max_length=200)


class _Drawn(_Item):
    layer: str
    linetype: Optional[str] = None


class LineItem(_Drawn):
    kind: Literal["line"]
    start: Point
    end: Point


class PolylineItem(_Drawn):
    kind: Literal["polyline"]
    points: List[Point] = Field(min_length=2)
    bulges: Optional[List[float]] = None
    closed: bool = False

    @model_validator(mode="after")
    def _bulges_match(self):
        if self.bulges is not None and len(self.bulges) != len(self.points):
            raise ValueError(f"bulges는 점 수({len(self.points)})만큼 있어야 함")
        return self


class CircleItem(_Drawn):
    kind: Literal["circle"]
    center: Point
    radius: float = Field(gt=0)


class ArcItem(_Drawn):
    kind: Literal["arc"]
    center: Point
    radius: float = Field(gt=0)
    start_angle: float
    end_angle: float


class TextItem(_Item):
    kind: Literal["text"]
    layer: str
    role: str = Field(description="text.yaml의 높이 id(예: general)")
    content: str = Field(min_length=1, max_length=250)
    at: Point
    align: Literal[ALIGNS] = "L"
    rotation: float = 0


class BlockItem(_Item):
    kind: Literal["block"]
    name: str
    at: Point
    rotation: float = 0
    attribs: Dict[str, str] = Field(default_factory=dict)


class HatchItem(_Item):
    kind: Literal["hatch"]
    layer: str
    pattern: str
    pattern_scale: float = Field(default=1.0, gt=0)
    pattern_angle: float = 0
    boundary: List[Point]
    holes: List[List[Point]] = Field(default_factory=list)


class DimItem(_Item):
    kind: Literal["dim"]
    style: str
    p1: Point
    p2: Point
    direction: Literal["x", "y"]
    line_at: Coord


class DimChainItem(_Item):
    kind: Literal["dim_chain"]
    style: str
    points: List[Point] = Field(min_length=2)
    direction: Literal["x", "y"]
    line_at: Coord


class Repeat(_Model):
    count: int = Field(default=1, ge=1, le=1000)
    step: Tuple[float, float] = (0.0, 0.0)


class VarRule(_Model):
    start: int = 1
    step: int = 1


class Placement(_Model):
    at: Point = (0.0, 0.0)
    rotation: float = 0
    mirror: Optional[Literal["x", "y"]] = None
    repeat: Repeat = Field(default_factory=Repeat)
    vars: Dict[str, VarRule] = Field(default_factory=dict)


class GroupItem(_Item):
    kind: Literal["group"]
    items: List["Item"] = Field(min_length=1)
    place: List[Placement] = Field(min_length=1)


Item = Annotated[Union[LineItem, PolylineItem, CircleItem, ArcItem, TextItem, BlockItem, HatchItem, DimItem,
                       DimChainItem, GroupItem], Field(discriminator="kind")]
GroupItem.model_rebuild()


class Grid(_Model):
    X: Dict[Annotated[str, Field(pattern=AXIS_PATTERN)], float] = Field(default_factory=dict)
    Y: Dict[Annotated[str, Field(pattern=AXIS_PATTERN)], float] = Field(default_factory=dict)


class Target(_Model):
    drawing_id: Optional[str] = None
    snapshot_id: Optional[str] = None


class Spec(_Model):
    schema_version: Literal[1]
    standard: str = Field(description="표준 프로파일 id@version")
    op: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$", description="요청 ID")
    units: str = "mm"
    scale: Optional[int] = Field(default=None, ge=1, le=10000)
    target: Optional[Target] = None
    grid: Grid = Field(default_factory=Grid)
    items: List[Item] = Field(min_length=1)


# ---------- 읽기 ----------

def _loc(loc) -> str:
    out = ""
    prev_int = False
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
            prev_int = True
            continue
        if prev_int and part in KINDS:   # 판별 공용체가 loc에 넣는 kind 이름
            prev_int = False
            continue
        out += ("." if out else "") + str(part)
        prev_int = False
    return out


def spec_violation(code: str, where: str, message: str, current: Any = None, expected: Any = None) -> Violation:
    return Violation(code=code, message=f"{where}: {message}" if where else message, current=current,
                     expected=expected)


def parse_spec(data: Union[str, dict, Spec]) -> Spec:
    """JSON 문자열·dict를 명세로 읽는다. 형식이 틀리면 SpecError(SPEC_SCHEMA)"""
    if isinstance(data, Spec):
        return data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise SpecError([spec_violation("SPEC_SCHEMA", "", f"JSON 형식 오류: {e}")]) from None
    try:
        return Spec.model_validate(data)
    except ValidationError as e:
        out = []
        for err in e.errors():
            where = _loc(err["loc"])
            if err["type"] == "union_tag_invalid":
                tag = err.get("ctx", {}).get("tag")
                msg = f"지원하지 않는 객체 종류 {tag}(지원: {', '.join(KINDS)})"
            elif err["type"] == "extra_forbidden":
                msg = "명세에 없는 항목(색·선굵기·문자 스타일·높이·블록 레이어 등은 표준이 정한다)"
            else:
                msg = err["msg"]
            out.append(spec_violation("SPEC_SCHEMA", where, msg))
        raise SpecError(out) from None


def spec_hash(spec: Spec) -> str:
    """명세의 정준 JSON(기본값 포함, 키 정렬) sha256 앞 16자"""
    text = json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_coord(value: Coord) -> Tuple[Optional[str], float]:
    """좌표 → (그리드 축 이름 또는 None, 수치). 형식이 틀리면 ValueError"""
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("유한한 수가 아님")
        return None, float(value)
    text = str(value).strip()
    try:
        return None, float(text)
    except ValueError:
        pass
    m = _REF.match(text)
    if not m:
        raise ValueError(f"좌표 형식이 아님: {value}(예: X2, X2+300, Y1-450)")
    offset = float(m.group(3) or 0) * (-1 if m.group(2) == "-" else 1)
    return m.group(1), offset


# ---------- 표준 대조 ----------

class _Checker:
    def __init__(self, spec: Spec, std: Standard):
        self.spec, self.std = spec, std
        self.out: List[Violation] = []
        self.roles = {h.id: h for h in list(std.text.heights) + list(std.text.sheet_title)}
        self.dimstyles = {d.name.casefold() for d in std.dimensions.styles}

    def add(self, code, where, message, current=None, expected=None):
        self.out.append(spec_violation(code, where, message, current, expected))

    def coord(self, value, axis: str, where: str, in_group: bool):
        try:
            name, _ = parse_coord(value)
        except ValueError as e:
            self.add("SPEC_GRID", where, str(e), value)
            return
        if name is None:
            return
        if in_group:
            self.add("SPEC_GRID", where, "group 안 좌표는 지역 좌표(숫자)만 쓴다", value)
            return
        axes = getattr(self.spec.grid, axis)
        other = getattr(self.spec.grid, "Y" if axis == "X" else "X")
        if name in axes:
            return
        if name in other:
            self.add("SPEC_GRID", where, f"{'x' if axis == 'X' else 'y'} 자리에 {'Y' if axis == 'X' else 'X'}축 {name}",
                     value, sorted(axes))
        else:
            self.add("SPEC_GRID", where, f"grid에 없는 축 {name}", value, sorted(axes))

    def point(self, p, where, in_group):
        self.coord(p[0], "X", where, in_group)
        self.coord(p[1], "Y", where, in_group)

    def layer(self, name, linetype, where):
        layer = self.std.layer(name)
        if layer is None:
            self.add("SPEC_LAYER", where, f"표준에 없는 레이어 {name}", name)
            return None
        if not layer.drawable:
            self.add("SPEC_LAYER", where, f"그리지 않는 레이어 {layer.name}", name)
            return None
        if linetype and linetype.casefold() != "bylayer" and \
                linetype.casefold() not in {x.casefold() for x in layer.object_linetypes}:
            self.add("SPEC_LINETYPE", where, f"레이어 {layer.name}에 줄 수 없는 선종류 {linetype}", linetype,
                     layer.object_linetypes)
        return layer

    def walk(self, items, where: str, in_group: bool, mirrored: bool, depth: int) -> int:
        """객체를 검사하고 펼쳤을 때의 객체 수를 돌려준다"""
        total = 0
        for n, item in enumerate(items):
            w = f"{where}[{n}]"
            kind = item.kind
            if kind == "group":
                if depth >= MAX_DEPTH:
                    self.add("SPEC_SCHEMA", w, f"group은 {MAX_DEPTH}단까지 중첩한다")
                    continue
                instances = 0
                for p_n, place in enumerate(item.place):
                    self.point(place.at, f"{w}.place[{p_n}].at", in_group)
                    instances += place.repeat.count
                child_mirrored = mirrored or any(p.mirror for p in item.place)
                total += instances * self.walk(item.items, f"{w}.items", True, child_mirrored, depth + 1)
                continue
            total += len(item.points) - 1 if kind == "dim_chain" else 1
            if kind in ("line", "polyline", "circle", "arc"):
                self.layer(item.layer, item.linetype, w)
                for p in {"line": lambda: [item.start, item.end], "polyline": lambda: item.points,
                          "circle": lambda: [item.center], "arc": lambda: [item.center]}[kind]():
                    self.point(p, w, in_group)
            elif kind == "text":
                layer = self.layer(item.layer, None, w)
                role = self.roles.get(item.role)
                if role is None:
                    self.add("SPEC_TEXT_ROLE", w, f"text.yaml에 없는 문자 용도 {item.role}", item.role,
                             sorted(self.roles))
                elif layer is not None and layer.name.casefold() not in {x.casefold() for x in role.layers}:
                    self.add("SPEC_TEXT_ROLE", w, f"문자 용도 {item.role}는 레이어 {', '.join(role.layers)}에 쓴다",
                             item.layer, role.layers)
                self.point(item.at, w, in_group)
            elif kind == "block":
                bdef = self.std.block(item.name)
                if bdef is None:
                    self.add("SPEC_BLOCK", w, f"표준 블록이 아님: {item.name}", item.name)
                elif bdef.status != "standard":
                    self.add("SPEC_BLOCK", w, f"쓰지 않는 블록 {bdef.name}: {bdef.reason}", item.name)
                else:
                    tags = {a.tag.casefold() for a in bdef.attributes}
                    unknown = sorted(t for t in item.attribs if t.casefold() not in tags)
                    if unknown:
                        self.add("SPEC_BLOCK", w, f"블록 {bdef.name}에 없는 속성 {', '.join(unknown)}", unknown,
                                 [a.tag for a in bdef.attributes])
                    if mirrored and bdef.attributes:
                        self.add("SPEC_BLOCK", w, f"속성이 있는 블록 {bdef.name}은 대칭 배치하지 않는다(속성 문자가 뒤집힘)")
                self.point(item.at, w, in_group)
            elif kind == "hatch":
                self.layer(item.layer, None, w)
                if item.pattern.upper() not in ISO_PATTERN:
                    self.add("SPEC_HATCH", w, f"해치 패턴 이름이 아님: {item.pattern}", item.pattern)
                for loop_n, loop in enumerate([item.boundary] + list(item.holes)):
                    if len(loop) < 3:
                        self.add("SPEC_HATCH", w, f"해치 경계{'' if loop_n == 0 else f' 섬 {loop_n}'}는 점 3개 이상",
                                 len(loop), 3)
                    for p in loop:
                        self.point(p, w, in_group)
            elif kind in ("dim", "dim_chain"):
                if item.style.casefold() not in self.dimstyles:
                    self.add("SPEC_DIMSTYLE", w, f"표준 치수 스타일이 아님: {item.style}", item.style,
                             [d.name for d in self.std.dimensions.styles])
                for p in ([item.p1, item.p2] if kind == "dim" else item.points):
                    self.point(p, w, in_group)
                self.coord(item.line_at, "Y" if item.direction == "x" else "X", f"{w}.line_at", in_group)
        return total


def validate_spec(spec: Spec, std: Standard) -> List[Violation]:
    """명세를 표준과 대조한다. 빈 목록이면 쓸 수 있다"""
    c = _Checker(spec, std)
    if spec.standard != std.ref:
        c.add("SPEC_STANDARD", "standard", f"표준이 다름: 명세 {spec.standard}, 엔진 {std.ref}", spec.standard, std.ref)
    if spec.units != "mm":
        c.add("SPEC_UNITS", "units", "단위는 mm만 받는다", spec.units, "mm")
    total = c.walk(spec.items, "items", in_group=False, mirrored=False, depth=0)
    if total > MAX_OBJECTS:
        c.add("SPEC_LIMIT", "items", f"펼친 객체가 {total}개(상한 {MAX_OBJECTS})", total, MAX_OBJECTS)
    return c.out
