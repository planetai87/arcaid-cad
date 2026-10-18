"""B1 변경 명세와 판정(0-B). CAD 조작·기록 쓰기는 B2가 맡는다.

이 docstring이 변경 명세·상태·적용 기록 형식의 원본이다.

    {"schema_version": 1, "standard": "heo-2026@2026.1", "op": "room-07-shift",
     "target": {"drawing_id": null, "snapshot_id": "snap-1"},
     "changes": [{"id": "c1", "select": {"op": "draw-1", "path_prefix": "room/7/"},
                  "action": "move", "delta": [600, 0]}]}

- select는 handles(16진 핸들 목록), oids("op:경로" 목록), op + path_prefix 중 정확히 하나다.
  핸들은 대문자로 정규화한다. 각 고르기는 하나 이상 맞아야 하며 한 객체를 두 번 고를 수 없다.
  대상 oid가 복사돼 둘 이상이면 고르는 방식과 관계없이 CHG_DUPLICATE로 거부한다(D5).
- action은 move(delta: 유한한 dx, dy), delete, set(props)이다. 다른 action의 필드는 받지 않는다.
  props는 layer, color(ACI 1~256 또는 {"rgb": [0~255, 0~255, 0~255]}), linetype,
  lineweight(DXF 선굵기 정수 또는 -3/-2/-1), text, attribs{태그: 문자열} 중 하나 이상이다.
  null은 받지 않는다. 빈 text·속성값은 지우기를 뜻한다. 속성 태그는 대소문자를 가리지 않는다.
- 모형 공간 LINE·LWPOLYLINE·TEXT·INSERT만, extrusion=(0,0,1)만 다룬다. 펼친 상한은 1,000개다.
  내장 MTEXT가 있는 여러 줄 속성은 MTEXT 범위 밖이므로 CHG_KIND로 거부한다.
  관계·레이어·속성 검사는 요청 전체에 적용한다. 표준 대조는 Standard.ref로 한다.
  snapshot_id를 반드시 대조한다. drawing_id는 B2가 사본 출처와 함께 검사할 보조 정보다.
- Target은 확정 핸들·변경 id·action·before·after·oid를 담는다. delete의 after는 None이다.
  ChangePlan.spec에 B2가 쓸 delta·props와 입력 해시의 원본을 보관한다.
- change_state는 반올림하지 않는다. 3D 점은 [x,y,z], 폴리선 points는 [x,y,시작폭,끝폭,bulge]다.
  연속 수치는 float로 통일한다(생략된 0 기본값과 명시된 0.0의 기록 해시가 같아야 한다).
  true_color가 None이 아니면 color는 None으로 정규화한다(ACI는 CAD가 정하는 파생값).
  공통 표시 속성·extrusion, 형상, 문자·속성의 정렬·내용·속성 flags,
  INSERT 변환·행/열 개수·간격, state 외 ARCAID 태그를 담는다.
  문자·속성은 왼쪽 정렬(0,0)이면 insert, fit/aligned(halign 3/5)이면 두 점,
  나머지 정렬이면 align_point만 담는다(insert는 CAD가 계산하는 파생값).
  move는 이 모든 점에 delta를 더한다. Z·폭·bulge·정렬은 보존한다.
- same_state/diff_fields는 점의 유클리드 거리와 길이 값에 tol(mm, 기본 0.01)을 쓴다.
  각도는 360도 순환 차에 tol(도), 배율·문자 폭비·bulge는 1e-9, 이산값·문자열·태그는 정확히 비교한다.
  레이어·선종류·블록·스타일 이름만 CAD 관례에 따라 casefold한다. diff_fields는 최상위 필드명 목록이다.
  state_digest는 원시 상태 정준 JSON의 sha256 앞 16자이며 기존 state.state_hash와 별개다.
- 기록은 그룹 1 문자열 ["arcaid-change-record/1", JSON의 250자 조각...]이다.
  JSON: {op, in: 입력해시, targets: {핸들: {action, after_hash}}, result: "applied"}.
  기록 모델의 op 문자열은 유니코드도 보존한다. 요청 op의 제한은 ChangeSpec이 검사한다.
  after_hash는 B2가 적용 뒤 사후 사본에서 읽은 실제 change_state로 계산한다(계산값 after가 아님).
  nullable(B0 E13 호환)이나 비삭제 skip에는 그 해시와 현재 상태의 해시가 반드시 일치해야 한다.
  기록 대상·action·입력이 같고, 현재 상태가 계산값 after와도 허용오차 안일 때만 skip한다.
  삭제 skip은 기록과 전체 도면에서의 실제 부재가 모두 필요하다. 손상·다른 버전의 기록은 conflict다.
- 기록이 없으면 모두 before일 때만 apply한다. 엔진 태그 state와 기존 state_hash가 다르면
  manual에 핸들을 담고 기본 거부한다. overwrite_manual도 읽은 뒤 바뀐 객체는 허용하지 않는다.
  apply 직전 recheck로 현재 레이어·선종류·관계·속성을 다시 검사한다. 기록은 묶음 밖에서 마지막에 쓴다.
- B2 CAD 실측: GstarCAD는 수정·복귀 뒤 Export에서 SEQEND 핸들을 재발급하므로 핸들·연결·지문 비교에서 제외한다.
"""
import copy
import hashlib
import json
import math
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional, Tuple, Union

from ezdxf.lldxf.const import VALID_DXF_LINEWEIGHTS
from ezdxf.entities import DXFGraphic
from ezdxf.lldxf.tagwriter import TagCollector
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator, model_validator

from .apply import _cut
from .results import Violation
from .spec import ID_PATTERN, spec_violation
from .standards import Standard
from .state import TAG_KEYS, arcaid_tags, state_hash

MAX_CHANGE_OBJECTS = 1000
RECORD_HEADER = "arcaid-change-record/1"
OP_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
KINDS = ("LINE", "LWPOLYLINE", "TEXT", "INSERT")
Action = Literal["move", "delete", "set"]
Name = Annotated[str, Field(min_length=1)]
Handle = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]+$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class ChangeError(Exception):
    """요청 전체를 거부한다(위반 목록을 담는다)."""

    def __init__(self, violations: List[Violation]):
        self.violations = violations
        super().__init__("; ".join(v.message for v in violations))


class RecordError(Exception):
    """적용 기록이 손상됐거나 지원하지 않는 형식이다."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Select(_Model):
    handles: Optional[List[Handle]] = Field(default=None, min_length=1)
    oids: Optional[List[Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}:.+$")]]] = Field(default=None, min_length=1)
    op: Optional[str] = Field(default=None, pattern=OP_PATTERN)
    path_prefix: Optional[str] = None

    @model_validator(mode="after")
    def _one_selector(self):
        fields = self.model_fields_set
        if fields not in ({"handles"}, {"oids"}, {"op", "path_prefix"}) or any(
                getattr(self, k) is None for k in fields):
            raise ValueError("handles, oids, op+path_prefix 중 정확히 하나가 필요함")
        if self.handles:
            self.handles = [h.upper() for h in self.handles]
        return self


class RGB(_Model):
    rgb: Tuple[Annotated[StrictInt, Field(ge=0, le=255)],
               Annotated[StrictInt, Field(ge=0, le=255)], Annotated[StrictInt, Field(ge=0, le=255)]]


class Props(_Model):
    layer: Optional[Name] = None
    color: Optional[Union[Annotated[StrictInt, Field(ge=1, le=256)], RGB]] = None
    linetype: Optional[Name] = None
    lineweight: Optional[StrictInt] = None
    text: Optional[str] = None
    attribs: Optional[Dict[Name, str]] = Field(default=None, min_length=1)

    @field_validator("lineweight")
    @classmethod
    def _lineweight(cls, value):
        if value is not None and value not in (*VALID_DXF_LINEWEIGHTS, -3, -2, -1):
            raise ValueError("DXF 선굵기 값이 아님")
        return value

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("props는 null이 아닌 속성이 하나 이상 필요함")
        if self.attribs and len({k.casefold() for k in self.attribs}) != len(self.attribs):
            raise ValueError("같은 속성 태그를 두 번 지정함")
        return self


class Change(_Model):
    id: str = Field(pattern=ID_PATTERN)
    select: Select
    action: Action
    delta: Optional[Tuple[float, float]] = None
    props: Optional[Props] = None

    @model_validator(mode="after")
    def _action_fields(self):
        want = {"delta"} if self.action == "move" else {"props"} if self.action == "set" else set()
        if self.model_fields_set & {"delta", "props"} != want or any(getattr(self, k) is None for k in want):
            raise ValueError("move에는 delta만, set에는 props만, delete에는 둘 다 쓰지 않음")
        return self


class ChangeTarget(_Model):
    drawing_id: Optional[str] = None
    snapshot_id: str = Field(min_length=1)


class ChangeSpec(_Model):
    schema_version: Literal[1]
    standard: str
    op: str = Field(pattern=OP_PATTERN)
    target: ChangeTarget
    changes: List[Change] = Field(min_length=1)


class Target(_Model):
    handle: str
    change_id: str
    action: Action
    before: Dict[str, Any]
    after: Optional[Dict[str, Any]]
    oid: Optional[str] = None


class ChangePlan(_Model):
    spec: ChangeSpec
    targets: List[Target]


class ChangeDecision(_Model):
    action: Literal["apply", "skip", "conflict"]
    reason: str
    details: List[str] = Field(default_factory=list)
    manual: List[str] = Field(default_factory=list, description="수동 수정 엔진 객체의 핸들")


def parse_changes(data: Union[str, dict, ChangeSpec]) -> ChangeSpec:
    """JSON 문자열·dict를 읽는다. 형식 위반은 ChangeError(CHG_SCHEMA)."""
    if isinstance(data, ChangeSpec):
        return data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ChangeError([spec_violation("CHG_SCHEMA", "", f"JSON 형식 오류: {e}")]) from None
    try:
        return ChangeSpec.model_validate(data)
    except ValidationError as e:
        raise ChangeError([spec_violation("CHG_SCHEMA", ".".join(map(str, err["loc"])),
                                         "변경 명세 형식이 맞지 않음: " + err["type"]) for err in e.errors()]) from None


def _digest(value) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def change_hash(spec: ChangeSpec) -> str:
    """기본값을 포함한 정준 JSON sha256 앞 16자(spec.spec_hash와 같은 방식)."""
    return _digest(spec.model_dump(mode="json"))


# ---------- 원시 상태와 변경 계산 ----------

def _point(value) -> list:
    return [float(v) for v in value]


def _text_state(e) -> dict:
    halign, valign = e.dxf.get("halign", 0), e.dxf.get("valign", 0)
    out = dict(text=e.dxf.text, height=float(e.dxf.height), rotation=float(e.dxf.get("rotation", 0)),
               width=float(e.dxf.get("width", 1)), oblique=float(e.dxf.get("oblique", 0)), halign=halign, valign=valign,
               style=e.dxf.get("style", "Standard").casefold(), text_generation_flag=e.dxf.get("text_generation_flag", 0),
               extrusion=_point(e.dxf.get("extrusion", (0, 0, 1))))
    if halign in (3, 5) or (halign, valign) == (0, 0):
        out["insert"] = _point(e.dxf.insert)
    if halign in (3, 5) or (halign, valign) != (0, 0):
        out["align_point"] = _point(e.dxf.get("align_point", (0, 0, 0)))
    if e.dxftype() == "ATTRIB":
        out.update(flags=e.dxf.get("flags", 0), multiline=e.has_embedded_mtext_entity)
    return out


def change_state(e) -> dict:
    """변경 비교용 원시 상태. state.state_hash의 0-A 형식은 바꾸지 않는다."""
    kind = e.dxftype()
    out = dict(type=kind, layer=e.dxf.get("layer", "0").casefold(), color=e.dxf.get("color", 256),
               true_color=e.dxf.get("true_color"), linetype=e.dxf.get("linetype", "BYLAYER").casefold(),
               lineweight=e.dxf.get("lineweight", -1), extrusion=_point(e.dxf.get("extrusion", (0, 0, 1))))
    if out["true_color"] is not None:
        out["color"] = None
    if kind == "LINE":
        out.update(start=_point(e.dxf.start), end=_point(e.dxf.end))
    elif kind == "LWPOLYLINE":
        out.update(points=[list(map(float, p)) for p in e.get_points("xyseb")], closed=bool(e.closed),
                   const_width=float(e.dxf.get("const_width", 0)), elevation=float(e.dxf.get("elevation", 0)))
    elif kind == "TEXT":
        out.update(_text_state(e))
    elif kind == "INSERT":
        out.update(name=e.dxf.name.casefold(), insert=_point(e.dxf.insert), xscale=float(e.dxf.get("xscale", 1)),
                   yscale=float(e.dxf.get("yscale", 1)), zscale=float(e.dxf.get("zscale", 1)),
                   rotation=float(e.dxf.get("rotation", 0)),
                   row_count=e.dxf.get("row_count", 1), column_count=e.dxf.get("column_count", 1),
                   row_spacing=float(e.dxf.get("row_spacing", 0)), column_spacing=float(e.dxf.get("column_spacing", 0)),
                   attribs=sorted([dict(tag=a.dxf.tag, **_text_state(a)) for a in e.attribs], key=lambda a: a["tag"]))
    tags = arcaid_tags(e) or {}
    out["tags"] = {k: tags[k] for k in TAG_KEYS if k != "state" and k in tags}
    return out


def apply_to_state(before: dict, change: Change) -> Optional[dict]:
    """상태 사본에만 변경을 계산한다. delete면 None."""
    if change.action == "delete":
        return None
    after = copy.deepcopy(before)
    if change.action == "move":
        dx, dy = change.delta

        def move_points(state):
            for key in ("start", "end", "insert", "align_point"):
                if key in state:
                    state[key][0] += dx
                    state[key][1] += dy
            for p in state.get("points", []):
                p[0] += dx
                p[1] += dy
            for a in state.get("attribs", []):
                move_points(a)
        move_points(after)
    else:
        props = change.props
        for key in ("layer", "linetype", "lineweight", "text"):
            value = getattr(props, key)
            if value is not None:
                after[key] = value.casefold() if key in ("layer", "linetype") else value
        if props.color is not None:
            if isinstance(props.color, RGB):
                r, g, b = props.color.rgb
                after.update(color=None, true_color=(r << 16) | (g << 8) | b)
            else:
                after.update(color=props.color, true_color=None)
        if props.attribs:
            values = {k.casefold(): v for k, v in props.attribs.items()}
            for a in after["attribs"]:
                if a["tag"].casefold() in values:
                    a["text"] = values[a["tag"].casefold()]
    return after


def _within(distance: float, tol: float) -> bool:
    # 이진 부동소수점의 경계 오차만 허용한다.
    return distance <= tol or math.isclose(distance, tol, rel_tol=0, abs_tol=1e-12)


def _equal(a, b, key: str, tol: float) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        if key == "tags":
            return a == b
        return a.keys() == b.keys() and all(_equal(a[k], b[k], k, tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        if key in ("start", "end", "insert", "align_point"):
            return _within(math.dist(a, b), tol)
        if key == "points":
            return all(len(p) == len(q) and _within(math.dist(p[:2], q[:2]), tol)
                       and all(_within(abs(p[i] - q[i]), tol) for i in (2, 3))
                       and _within(abs(p[4] - q[4]), 1e-9) for p, q in zip(a, b))
        return all(_equal(x, y, key, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if key in ("rotation", "oblique"):
            return _within(abs((a - b + 180) % 360 - 180), tol)
        if key in ("xscale", "yscale", "zscale", "width"):
            return _within(abs(a - b), 1e-9)
        if key in ("height", "const_width", "elevation", "row_spacing", "column_spacing"):
            return _within(abs(a - b), tol)
    return a == b


def diff_fields(a: Optional[dict], b: Optional[dict], tol: float = 0.01) -> List[str]:
    """허용오차 밖인 최상위 필드명. 부재 여부가 다르면 ['entity']."""
    if a is None or b is None:
        return [] if a is b else ["entity"]
    return sorted(k for k in a.keys() | b.keys() if k not in a or k not in b or not _equal(a[k], b[k], k, tol))


def same_state(a: Optional[dict], b: Optional[dict], tol: float = 0.01) -> bool:
    """원시 상태가 허용오차 안에서 같은지."""
    return not diff_fields(a, b, tol)


def state_digest(state: Optional[dict]) -> str:
    """원시 상태 정준 JSON 해시. after_hash에는 B2가 사후 사본에서 읽은 실제 change_state를 쓴다."""
    return _digest(state)


# ---------- 적용·복귀 직접 확인 ----------

def _raw(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return [_raw(v) for v in value]


def fingerprint(e) -> str:
    """SEQEND를 제외한 DXF 속성·XDATA·속성/정점을 종류 제한·반올림 없이 지문화한다."""
    # TagCollector에는 HATCH 경계처럼 dxf 이름공간 밖의 데이터도 포함된다.
    tags, seqend = [], False
    for t in TagCollector.dxftags(e):
        if t.code == 0:
            seqend = t.value == "SEQEND"
        if not seqend:
            tags.append((t.code, _raw(t.value)))
    return _digest(tags)


def entity_tree(e) -> list:
    """SEQEND를 뺀 대상과 종속 속성·정점(삭제 때 없어져야 할 핸들)."""
    entities = [e] + list(e.all_sub_entities() if hasattr(e, "all_sub_entities") else [])
    return [x for x in entities if x.dxftype() != "SEQEND"]


def drawing_entities(doc) -> dict:
    """SEQEND를 뺀 공간·블록 정의 전체의 도형과 종속 핸들. 사전·표는 변경 기록과 구분한다."""
    out = {}
    for e in doc.entitydb.values():
        if e.is_alive and isinstance(e, DXFGraphic):
            out.update((x.dxf.handle, x) for x in entity_tree(e) if x.is_alive)
    return out


def _arcaid_data(e, include_state: bool):
    if not e.has_xdata("ARCAID"):
        return None
    return [(t.code, _raw(t.value)) for t in e.get_xdata("ARCAID")
            if include_state or not (t.code == 1000 and str(t.value).startswith("state="))]


def _verify_changes(plan: ChangePlan, base_doc, now_doc, restored: bool) -> List[str]:
    base, now = drawing_entities(base_doc), drawing_entities(now_doc)
    target_handles = {x.dxf.handle for t in plan.targets for x in entity_tree(base[t.handle])}
    deleted = {x.dxf.handle for t in plan.targets if t.action == "delete"
               for x in entity_tree(base[t.handle])} if not restored else set()
    problems = []
    expected = set(base) - deleted
    if expected != set(now):
        problems.extend(f"핸들 집합 불일치: 없음 {h}" for h in sorted(expected - set(now)))
        problems.extend(f"핸들 집합 불일치: 추가 {h}" for h in sorted(set(now) - expected))
    msp = {e.dxf.handle: e for e in now_doc.modelspace()}
    for t in plan.targets:
        e = msp.get(t.handle)
        if not restored and t.action == "delete":
            original = now_doc.entitydb.get(t.handle)
            if original is not None and original.is_alive:
                problems.append(f"{t.handle}: 삭제 대상이 남음")
            continue
        wanted = change_state(base[t.handle]) if restored else t.after
        fields = diff_fields(wanted, change_state(e) if e is not None else None)
        if fields:
            problems.append(f"{t.handle}: 상태 불일치 {', '.join(fields)}")
        if e is not None:
            if _arcaid_data(base[t.handle], restored) != _arcaid_data(e, restored):
                problems.append(f"{t.handle}: ARCAID 태그 불일치")
            original_children, current_children = entity_tree(base[t.handle])[1:], entity_tree(e)[1:]
            if [x.dxf.handle for x in original_children] != [x.dxf.handle for x in current_children]:
                problems.append(f"{t.handle}: 종속 핸들 연결 불일치")
            for child in original_children:
                current = now.get(child.dxf.handle)
                if current is not None and _arcaid_data(child, True) != _arcaid_data(current, True):
                    problems.append(f"{child.dxf.handle}: 종속 객체 ARCAID 태그 불일치")
            if not restored and arcaid_tags(base[t.handle]) and (arcaid_tags(e) or {}).get("state") != state_hash(e):
                problems.append(f"{t.handle}: 기준 상태 미갱신")
    for h in sorted((set(base) & set(now)) - target_handles):
        if fingerprint(base[h]) != fingerprint(now[h]):
            problems.append(f"{h}: 대상이 아닌 객체 변경")
    return problems


def verify_applied(plan: ChangePlan, base_doc, now_doc) -> List[str]:
    """기록 없이 after·삭제 부재·태그·대상 밖 지문·전체 도형 핸들을 직접 확인한다."""
    return _verify_changes(plan, base_doc, now_doc, False)


def verify_restored(plan: ChangePlan, base_doc, now_doc) -> List[str]:
    """before·전체 ARCAID 태그(state 포함)·종속 핸들·대상 밖 지문으로 복귀를 확인한다."""
    return _verify_changes(plan, base_doc, now_doc, True)


# ---------- 대상·거부 검사 ----------

def relations(doc) -> Dict[str, str]:
    """반응자·그룹·해치 source boundary 역검사. 확장 사전 자체는 관계가 아니다."""
    out = {}
    for e in doc.entitydb.values():
        if not e.is_alive:
            continue
        if e.get_reactors():
            out[e.dxf.handle] = "반응자가 있음"
        if e.dxftype() == "HATCH":
            for path in e.paths:
                for handle in path.source_boundary_objects:
                    out[handle.upper()] = "해치 경계"
    for name, group in doc.groups:
        for e in group:
            out[e.dxf.handle] = f"그룹 {name}의 구성원"
    return out


def _checks(e, change: Change, doc, related: Dict[str, str]) -> List[Violation]:
    out = []
    handle, kind = e.dxf.handle, e.dxftype()

    def add(code, message, current=None, expected=None):
        v = spec_violation(code, f"{change.id}/{handle}", message, current, expected)
        v.handle = handle
        out.append(v)

    if kind not in KINDS:
        add("CHG_KIND", f"지원하지 않는 종류 {kind}", kind, list(KINDS))
        return out
    entities = [e] + (list(e.attribs) if kind == "INSERT" else [])
    if kind == "INSERT" and any(a.has_embedded_mtext_entity for a in e.attribs):
        add("CHG_KIND", "여러 줄 속성의 내장 MTEXT는 지원하지 않음")
    if any(tuple(x.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1) for x in entities):
        add("CHG_KIND", "extrusion은 (0,0,1)만 지원함")
    for x in entities:
        if x.dxf.handle in related:
            add("CHG_RELATION", related[x.dxf.handle])
    props = change.props
    if props and props.text is not None and kind != "TEXT":
        add("CHG_KIND", "text는 TEXT에만 지정함")
    if props and props.attribs is not None and kind != "INSERT":
        add("CHG_KIND", "attribs는 INSERT에만 지정함")
    layers = {e.dxf.get("layer", "0").casefold()}
    if props and props.layer:
        layers.add(props.layer.casefold())
    for name in sorted(layers):
        if name not in doc.layers:
            add("CHG_LAYER", f"없는 레이어 {name}", name)
            continue
        layer = doc.layers.get(name)
        if layer.is_locked() or layer.is_frozen() or layer.is_off():
            add("CHG_LAYER", f"잠김·동결·꺼짐 레이어 {name}", name)
    if props and props.linetype and props.linetype not in doc.linetypes:
        add("CHG_LINETYPE", f"없는 선종류 {props.linetype}", props.linetype)
    if kind == "INSERT" and props and props.attribs:
        attrs = {}
        for a in e.attribs:
            attrs.setdefault(a.dxf.tag.casefold(), []).append(a)
        definition = doc.blocks.get(e.dxf.name)
        constants = {a.dxf.tag.casefold() for a in definition.query("ATTDEF") if a.dxf.get("flags", 0) & 2} if definition else set()
        for key in props.attribs:
            found = attrs.get(key.casefold(), [])
            if not found:
                add("CHG_ATTRIB", f"없는 속성 태그 {key}", key)
            elif len(found) != 1 or key.casefold() in constants or any(a.dxf.get("flags", 0) & 2 for a in found):
                add("CHG_ATTRIB", f"상수 또는 중복 속성 태그 {key}", key)
    return out


def _oid_handles(entities: Iterable) -> Dict[str, List[str]]:
    out = {}
    for e in entities:
        if not e.is_alive:
            continue
        oid = (arcaid_tags(e) or {}).get("oid")
        if oid:
            out.setdefault(oid, []).append(e.dxf.handle)
    return out


def _identity_violations(plan: ChangePlan, doc) -> List[Violation]:
    by_oid = _oid_handles(doc.entitydb.values())
    out = []
    for t in plan.targets:
        if t.oid and len(by_oid.get(t.oid, [])) > 1:
            v = spec_violation("CHG_DUPLICATE", t.change_id, f"같은 oid가 둘 이상임(복사): {t.oid}", by_oid[t.oid])
            v.handle = t.handle
            out.append(v)
    return out


def resolve(spec: ChangeSpec, base_doc, std: Standard, snapshot_id: str) -> ChangePlan:
    """기준 사본에서 대상을 확정하고 before·after를 계산한다. 한 위반도 전체 거부한다."""
    out, targets = [], []
    if spec.standard != std.ref:
        out.append(spec_violation("CHG_STANDARD", "standard", "표준이 다름", spec.standard, std.ref))
    if spec.target.snapshot_id != snapshot_id:
        out.append(spec_violation("CHG_TARGET", "target.snapshot_id", "기준 스냅샷이 다름", spec.target.snapshot_id, snapshot_id))
    msp = {e.dxf.handle: e for e in base_doc.modelspace()}
    oid_map = _oid_handles(msp.values())
    identities = _oid_handles(base_doc.entitydb.values())
    related, seen, ids, count = relations(base_doc), set(), set(), 0
    for change in spec.changes:
        if change.id in ids:
            out.append(spec_violation("CHG_DUPLICATE", change.id, "중복 변경 id"))
        ids.add(change.id)
        select = change.select
        handles = []
        if select.handles is not None:
            handles = select.handles
        elif select.oids is not None:
            for oid in select.oids:
                found = oid_map.get(oid, [])
                if not found:
                    out.append(spec_violation("CHG_SELECT", change.id, f"없는 oid {oid}"))
                handles.extend(found)
        else:
            for handle, e in msp.items():
                tags = arcaid_tags(e) or {}
                if tags.get("op") == select.op and tags.get("path", "").startswith(select.path_prefix):
                    handles.append(handle)
        if not handles:
            out.append(spec_violation("CHG_SELECT", change.id, "고른 객체가 없음"))
        count += len(handles)
        for handle in handles:
            if handle not in msp:
                out.append(spec_violation("CHG_SELECT", change.id, f"없거나 모형 공간 밖인 핸들 {handle}"))
                continue
            if handle in seen:
                out.append(spec_violation("CHG_DUPLICATE", change.id, f"두 번 고른 핸들 {handle}"))
                continue
            seen.add(handle)
            e = msp[handle]
            oid = (arcaid_tags(e) or {}).get("oid")
            if oid and len(identities[oid]) > 1:
                v = spec_violation("CHG_DUPLICATE", change.id, f"같은 oid가 둘 이상임(복사): {oid}", identities[oid])
                v.handle = handle
                out.append(v)
                continue
            violations = _checks(e, change, base_doc, related)
            out.extend(violations)
            if not violations:
                before = change_state(e)
                targets.append(Target(handle=handle, change_id=change.id, action=change.action, before=before,
                                      after=apply_to_state(before, change), oid=oid))
    if count > MAX_CHANGE_OBJECTS:
        out.append(spec_violation("CHG_LIMIT", "changes", f"펼친 대상이 {count}개(상한 {MAX_CHANGE_OBJECTS})", count, MAX_CHANGE_OBJECTS))
    if out:
        raise ChangeError(out)
    return ChangePlan(spec=spec, targets=targets)


def recheck(plan: ChangePlan, now_doc) -> List[Violation]:
    """현재 사본에서 확정 대상의 종류·레이어·선종류·관계·속성을 다시 검사한다."""
    msp = {e.dxf.handle: e for e in now_doc.modelspace()}
    related = relations(now_doc)
    changes = {c.id: c for c in plan.spec.changes}
    out = _identity_violations(plan, now_doc)
    for target in plan.targets:
        e = msp.get(target.handle)
        if e is None:
            v = spec_violation("CHG_SELECT", target.change_id, "확정 대상이 없거나 모형 공간 밖임")
            v.handle = target.handle
            out.append(v)
        else:
            out.extend(_checks(e, changes[target.change_id], now_doc, related))
    return out


# ---------- 적용 기록 읽기와 판정 ----------

class RecordTarget(_Model):
    action: Action
    after_hash: Optional[Digest]


class ChangeRecord(_Model):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)

    op: Name
    input_hash: Digest = Field(alias="in")
    targets: Dict[Handle, RecordTarget]
    result: Literal["applied"]


def record_strings(record: ChangeRecord) -> List[str]:
    """그룹 1 문자열: 버전 머리말 + JSON 250자 조각. 기록 쓰기는 B2가 한다."""
    payload = json.dumps(record.model_dump(mode="json", by_alias=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return [RECORD_HEADER] + [payload[i:i + 250] for i in range(0, len(payload), 250)]


def parse_record(strings: List[str]) -> ChangeRecord:
    """그룹 1 문자열을 읽는다. 손상·버전 불일치는 RecordError로 보존한다."""
    if not strings or strings[0] != RECORD_HEADER:
        raise RecordError("적용 기록 머리말·버전이 다름")
    try:
        data = json.loads("".join(strings[1:]))
        if not isinstance(data, dict) or "in" not in data:
            raise ValueError("기록에는 in 키가 필요함")
        return ChangeRecord.model_validate(data)
    except (ValueError, TypeError):
        raise RecordError("적용 기록 JSON·형식 손상") from None


def read_records(doc) -> Dict[str, Union[ChangeRecord, RecordError]]:
    """루트 ARCAID 사전의 op별 XRECORD. 읽기 오류를 기록 없음으로 처리하지 않는다."""
    root = doc.rootdict.get("ARCAID")
    if root is None:
        return {}
    if root.dxftype() != "DICTIONARY":
        raise RecordError("ARCAID 루트가 사전이 아님")
    out = {}
    for op, entry in root.items():
        try:
            if not hasattr(entry, "dxftype") or entry.dxftype() != "XRECORD":
                raise RecordError("op의 적용 기록이 XRECORD가 아님")
            rec = parse_record([tag.value for tag in entry.tags if tag.code == 1])
            if rec.op != op:
                raise RecordError("사전 키와 기록 op가 다름")
            out[op] = rec
        except RecordError as e:
            out[op] = e
    return out


def judge_changes(plan: ChangePlan, now_doc, records: Dict[str, Union[ChangeRecord, RecordError]],
                  overwrite_manual: bool = False) -> ChangeDecision:
    """기록·현재 상태·수동 수정 여부로 apply/skip/conflict를 판정한다. 문서는 바꾸지 않는다."""
    msp = {e.dxf.handle: e for e in now_doc.modelspace()}
    now = {t.handle: change_state(msp[t.handle]) if t.handle in msp else None for t in plan.targets}
    manual = [t.handle for t in plan.targets if t.handle in msp and arcaid_tags(msp[t.handle])
              and arcaid_tags(msp[t.handle]).get("state") != state_hash(msp[t.handle])]

    def decision(action, reason, details=None):
        return ChangeDecision(action=action, reason=reason, details=_cut(details or []), manual=manual)

    duplicates = _identity_violations(plan, now_doc)
    if duplicates:
        return decision("conflict", "같은 oid가 둘 이상임(복사)", [v.message for v in duplicates])
    record = records.get(plan.spec.op)
    if isinstance(record, RecordError):
        return decision("conflict", "적용 기록이 손상됐거나 버전이 다름", [str(record)])
    if record is not None:
        if record.op != plan.spec.op or record.input_hash != change_hash(plan.spec):
            return decision("conflict", "같은 op에 다른 입력 또는 기록 op 불일치")
        expected = {t.handle: t.action for t in plan.targets}
        if {h: t.action for h, t in record.targets.items()} != expected:
            return decision("conflict", "적용 기록 대상·action 불일치")
        changed = []
        for t in plan.targets:
            current = now[t.handle]
            entity = now_doc.entitydb.get(t.handle)
            deletion_left = t.action == "delete" and entity is not None and entity.is_alive
            digest_differs = t.action != "delete" and (
                current is None or record.targets[t.handle].after_hash != state_digest(current))
            if deletion_left or not same_state(t.after, current) or digest_differs:
                changed.append(f"{t.handle}: 적용 뒤 바뀜")
        if changed:
            return decision("conflict", "적용 뒤 바뀐 대상", changed)
        if manual and not overwrite_manual:
            return decision("conflict", "손으로 고친 엔진 객체", manual)
        return decision("skip", "이미 완료")
    changed = [f"{t.handle}: {', '.join(diff_fields(t.before, now[t.handle]))}" for t in plan.targets
               if not same_state(t.before, now[t.handle])]
    if changed:
        return decision("conflict", "읽은 뒤 바뀌었거나 없어진 대상", changed)
    if manual and not overwrite_manual:
        return decision("conflict", "손으로 고친 엔진 객체", manual)
    violations = recheck(plan, now_doc)
    if violations:
        return decision("conflict", "현재 사본에서 변경 거부", [f"{v.code}: {v.message}" for v in violations])
    return decision("apply", "대상 모두 기준 상태와 같음")
