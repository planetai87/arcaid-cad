"""표준 검사(구조 결정 D2·D3): DXF 사본을 회사 표준 프로파일과 대조한다

- 도면 설정: 단위($INSUNITS), 선종류 축척($LTSCALE = k)
- 정의: 대상 도면의 같은 이름 레이어·문자 스타일·치수 스타일·표준 블록이 표준과 같은지(D3, 고칠지는 사람이 정함)
- 객체(모형 공간): 레이어, ByLayer 색·선종류·선굵기, 문자 스타일·높이, 치수 스타일·레이어·DIMSCALE·재정의,
  주석 객체, 표준 블록의 레이어·배율, 안 쓰는 블록
정답이 하나로 정해지는 위반만 fixable이고, 고칠 목록(fixes)으로 묶는다. 검사는 읽기 전용이고 고치지 않는다(T6).
메시지에는 문자 내용을 넣지 않는다(D8).
"""
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ezdxf
from pydantic import BaseModel, ConfigDict, Field

from .definitions import compare_definitions, is_annotative
from .read import UNITS
from .results import (Assumption, Checked, EngineResult, OutOfScope, ResultIds, Scope, Status, Unchecked,
                      Violation)
from .snapshot import SnapshotMeta
from .standards import Standard

HEIGHT_RTOL = 0.001          # 문자 높이 허용오차(0.1%)
NUM_TOL = 1e-6
TEXT_TYPES = ("TEXT", "MTEXT")
UNREAD_TEXT_TYPES = ("MULTILEADER", "ACAD_TABLE")   # 문자를 담지만 0단계에서 읽지 않는 객체
DEFINITION_CODES = {"LAYER_COLOR", "LAYER_LINETYPE", "LAYER_PLOT", "TEXTSTYLE_FONT", "DIMSTYLE_ANNOTATIVE",
                    "DIMSTYLE_VAR", "DIMSTYLE_TEXTSTYLE", "BLOCK_ATTRIB", "BLOCK_EXTENTS"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fix(_Model):
    """고칠 목록 한 묶음: 같은 속성을 같은 값으로 고칠 객체들"""
    property: str = Field(description="layer | color | linetype | lineweight | style | height | dimstyle | "
                                      "dimscale | dimstyle_override.<변수> | $LTSCALE")
    value: Any
    count: int = Field(ge=0)
    handles: List[str] = Field(default_factory=list, description="객체 핸들(도면 설정이면 빈 목록)")


class CheckMeta(_Model):
    source: str
    dxf_path: str
    snapshot_id: Optional[str] = None
    drawing_id: Optional[str] = None
    k: int = Field(description="검사한 A3 축척 분모")
    k_assumed: bool = Field(description="요청에 축척이 없어 기본값으로 가정했는지")
    s: float
    insunits: int
    units: str
    modelspace_count: int


class CheckResult(EngineResult):
    """check_standard 결과: D2 공통 항목 + 코드별 개수·고칠 목록"""
    meta: Optional[CheckMeta] = None
    violation_counts: Dict[str, int] = Field(default_factory=dict)
    fixes: List[Fix] = Field(default_factory=list)
    truncated: bool = Field(default=False, description="violations·fixes.handles를 코드별 상한까지만 담았는지")
    warnings: List[str] = Field(default_factory=list)
    full_path: Optional[str] = None


def _close(a: Any, b: Any, rtol: float = 0.0) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= max(NUM_TOL, rtol * abs(float(b)))
    return str(a).casefold() == str(b).casefold()


def _num(v: float) -> float:
    return round(float(v), 6)


def _location(e) -> Optional[List[float]]:
    t = e.dxftype()
    try:
        if t == "LINE":
            p = e.dxf.start
        elif t in ("TEXT", "MTEXT", "INSERT"):
            p = e.dxf.insert
        elif t in ("CIRCLE", "ARC", "ELLIPSE"):
            p = e.dxf.center
        elif t == "LWPOLYLINE":
            p = next(iter(e.get_points("xy")), None)
        elif t == "DIMENSION":
            p = e.dxf.defpoint
        elif t == "POINT":
            p = e.dxf.location
        else:
            return None
        return None if p is None else [_num(p[0]), _num(p[1])]
    except Exception:  # noqa: BLE001  (위치를 못 구하면 생략)
        return None


class _Collector:
    """위반·미검사 항목을 모은다. 위반마다 고칠 속성(있으면)을 함께 둔다"""

    def __init__(self):
        self.items: List[Tuple[Violation, Optional[str], Any]] = []   # (위반, 고칠 속성, 고칠 값)
        self.unchecked: Counter = Counter()

    def add(self, code: str, message: str, e=None, current: Any = None, expected: Any = None,
            fix: Optional[str] = None, fix_value: Any = None):
        v = Violation(code=code, message=message, handle=None if e is None else e.dxf.handle,
                      location=None if e is None else _location(e), current=current, expected=expected,
                      fixable=fix is not None)
        self.items.append((v, fix, expected if fix_value is None else fix_value))

    def skip(self, item: str, reason: str):
        self.unchecked[(item, reason)] += 1


def _allowed_heights(std: Standard, s: float) -> Dict[str, List[float]]:
    """레이어(소문자) → 허용 문자 높이(× s)"""
    out: Dict[str, set] = {}
    for h in list(std.text.heights) + list(std.text.sheet_title):
        for layer in h.layers:
            out.setdefault(layer.casefold(), set()).add(_num(h.height * s))
    return {k: sorted(v) for k, v in out.items()}


def _check_layer(c: _Collector, std: Standard, e, layer_name: str):
    layer = std.layer(layer_name)
    if layer is None:
        c.add("LAYER_NOT_STANDARD", f"표준에 없는 레이어 {layer_name}의 객체", e, layer_name)
    elif not layer.drawable:
        c.add("LAYER_NOT_DRAWABLE", f"그리지 않는 레이어 {layer.name}의 객체", e, layer_name)


def _check_bylayer(c: _Collector, std: Standard, e, layer_name: str):
    if e.dxf.hasattr("true_color"):
        r, g, b = e.rgb
        c.add("COLOR_NOT_BYLAYER", "색이 ByLayer가 아님(트루컬러)", e, f"#{r:02X}{g:02X}{b:02X}", 256, "color")
    else:
        color = e.dxf.get("color", 256)
        if color != 256:
            c.add("COLOR_NOT_BYLAYER", "색이 ByLayer가 아님", e, color, 256, "color")
    linetype = e.dxf.get("linetype", "BYLAYER")
    if linetype.casefold() != "bylayer":
        layer = std.layer(layer_name)
        allowed = {x.casefold() for x in (layer.object_linetypes if layer else [])}
        if linetype.casefold() not in allowed:
            c.add("LINETYPE_NOT_BYLAYER", "선종류가 ByLayer가 아님", e, linetype, "BYLAYER", "linetype")
    lineweight = e.dxf.get("lineweight", -1)
    if lineweight != -1:
        c.add("LINEWEIGHT_NOT_BYLAYER", "선굵기가 ByLayer가 아님", e, lineweight, -1, "lineweight")


def _check_text(c: _Collector, std: Standard, e, layer_name: str, heights: Dict[str, List[float]], mm: bool):
    style = e.dxf.get("style", "Standard")
    want = std.text.style.name
    if style.casefold() != want.casefold():
        c.add("TEXT_STYLE", "문자 스타일이 표준이 아님", e, style, want, "style")
    if not mm:
        c.skip("문자 높이", "도면 단위가 mm가 아님")
        return
    if e.dxftype() == "MTEXT":
        if "\\H" in e.text:
            c.skip("서식 코드로 높이를 바꾼 MTEXT", "문자 안 높이 서식(\\H)은 읽지 않음")
            return
        height = e.dxf.char_height
    else:
        height = e.dxf.height
    candidates = heights.get(layer_name.casefold())
    if not candidates:
        c.skip("높이 규칙이 없는 레이어의 문자", "text.yaml에 그 레이어의 문자 높이가 없음")
    elif not any(_close(height, h, HEIGHT_RTOL) for h in candidates):
        if len(candidates) == 1:
            c.add("TEXT_HEIGHT", f"{layer_name} 문자 높이가 표준과 다름", e, _num(height), candidates[0], "height")
        else:
            c.add("TEXT_HEIGHT", f"{layer_name} 문자 높이가 표준 높이 중 어느 것과도 다름", e, _num(height),
                  candidates)


def _check_dimension(c: _Collector, std: Standard, e, layer_name: str, k: int, annotative: bool):
    styles = {d.name.casefold(): d for d in std.dimensions.styles}
    by_layer = {d.layer.casefold(): d for d in std.dimensions.styles}
    name = e.dxf.get("dimstyle", "Standard")
    want = styles.get(name.casefold())
    if want is None:
        _check_layer(c, std, e, layer_name)
        ref = by_layer.get(layer_name.casefold())
        if ref is not None:
            c.add("DIM_STYLE", "치수 스타일이 표준이 아님", e, name, ref.name, "dimstyle")
        else:
            c.add("DIM_STYLE", "치수 스타일이 표준이 아님(레이어로 정할 수 없음)", e, name,
                  [d.name for d in std.dimensions.styles])
    else:
        ref = want
        if layer_name.casefold() != want.layer.casefold():
            # DIM330을 DIM1에 두면 스타일과 레이어 중 무엇이 틀렸는지 모른다
            ambiguous = layer_name.casefold() in by_layer
            c.add("DIM_LAYER", f"치수 스타일 {want.name}는 {want.layer} 레이어", e, layer_name, want.layer,
                  None if ambiguous else "layer")
    try:
        override = e.override()
        overrides = dict(override.dimstyle_attribs)
        dimscale = override.get("dimscale")
    except Exception:  # noqa: BLE001
        c.skip("재정의를 읽지 못한 치수", "치수 스타일 재정의(XDATA)를 읽지 못함")
        return
    if not annotative:
        expected = k * std.profile.scale.dimscale_per_k
        if dimscale is None or not _close(dimscale, expected):
            c.add("DIM_SCALE", "치수 축척(DIMSCALE)이 k/2가 아님", e, dimscale, expected, "dimscale")
    if ref is None:
        return
    for var, value in sorted(overrides.items()):
        if var == "dimscale" or var not in ref.vars:
            continue
        if not _close(value, ref.vars[var]):
            c.add("DIM_OVERRIDE", f"치수 스타일 값 {var}를 재정의함", e, {var: value}, {var: ref.vars[var]},
                  f"dimstyle_override.{var}", ref.vars[var])


def _check_insert(c: _Collector, std: Standard, e, layer_name: str, s: float):
    bdef = std.block(e.dxf.name)
    if bdef is None:
        _check_layer(c, std, e, layer_name)
        return
    if bdef.status == "unused":
        _check_layer(c, std, e, layer_name)
        c.add("BLOCK_NOT_USED", f"쓰지 않는 블록 {bdef.name}", e, bdef.name, None)
        return
    if layer_name.casefold() != bdef.layer.casefold():
        c.add("BLOCK_LAYER", f"블록 {bdef.name}는 {bdef.layer} 레이어", e, layer_name, bdef.layer, "layer")
    factor = bdef.scale_factor(s)
    xs, ys = abs(e.dxf.get("xscale", 1)), abs(e.dxf.get("yscale", 1))
    if not (_close(xs, factor, HEIGHT_RTOL) and _close(ys, factor, HEIGHT_RTOL)):
        c.add("BLOCK_SCALE", f"블록 {bdef.name} 배율이 규칙({bdef.scale})과 다름", e, [_num(xs), _num(ys)],
              _num(factor))


def _fixes(items: List[Tuple[Violation, Optional[str], Any]]) -> List[Fix]:
    groups: Dict[Tuple[str, str], Fix] = {}
    for v, prop, value in items:
        if prop is None:
            continue
        key = (prop, json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))
        fix = groups.get(key)
        if fix is None:
            fix = groups[key] = Fix(property=prop, value=value, count=0)
        fix.count += 1
        if v.handle is not None:
            fix.handles.append(v.handle)
    return list(groups.values())


def _sort_key(v: Violation):
    return v.code, -1 if v.handle is None else int(v.handle, 16)


def _truncate(result: CheckResult, limit: int) -> CheckResult:
    seen: Counter = Counter()
    kept = []
    for v in result.violations:
        seen[v.code] += 1
        if seen[v.code] <= limit:
            kept.append(v)
    fixes = [f.model_copy(update={"handles": f.handles[:limit]}) for f in result.fixes]
    truncated = len(kept) < len(result.violations) or any(len(f.handles) > limit for f in result.fixes)
    return result.model_copy(update={"violations": kept, "fixes": fixes, "truncated": truncated})


def check_standard(dxf_path: Path, std: Standard, k: Optional[int] = None, snapshot: Optional[SnapshotMeta] = None,
                   full_out: Optional[Path] = None, limit: Optional[int] = None) -> CheckResult:
    """DXF 사본을 표준과 대조한다. k = A3 축척 분모(없으면 기본값으로 가정)

    limit: 코드별로 돌려줄 위반 수(None = 전부). full_out에는 항상 전부를 쓴다.
    """
    dxf_path = Path(dxf_path)
    scope = Scope(target=str(dxf_path), supported=["도면 단위·LTSCALE", "정의(레이어·문자 스타일·치수 스타일·표준 블록)",
                                                   "레이어", "ByLayer 색·선종류·선굵기", "문자 스타일·높이",
                                                   "치수 스타일·레이어·DIMSCALE·재정의", "주석 객체", "블록 레이어·배율"])
    ids = ResultIds(standard=std.ref, snapshot_id=snapshot.snapshot_id if snapshot else None)
    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as e:  # noqa: BLE001  (깨진 파일·없는 파일·형식 오류)
        return CheckResult(status=Status.FAILED, summary=f"DXF를 읽지 못함: {e}", scope=scope, ids=ids)

    rules = std.profile.scale
    k_assumed = k is None
    k = rules.default_k if k is None else k
    s = rules.factors(k)["s"]
    insunits = int(doc.header.get("$INSUNITS", 0))
    units = UNITS.get(insunits, (f"코드 {insunits}", None))[0]
    mm = insunits == std.profile.insunits
    msp = list(doc.modelspace())
    ids.drawing_id = (snapshot.drawing_id if snapshot else None) or doc.header.get("$FINGERPRINTGUID")
    meta = CheckMeta(source=snapshot.source_path if snapshot else str(dxf_path), dxf_path=str(dxf_path),
                     snapshot_id=ids.snapshot_id, drawing_id=ids.drawing_id, k=k, k_assumed=k_assumed, s=s,
                     insunits=insunits, units=units, modelspace_count=len(msp))
    c = _Collector()
    assumptions, warnings = [], []

    # 도면 설정
    ltscale = doc.header.get("$LTSCALE", 1.0)
    if not mm:
        c.add("UNITS_NOT_MM", "도면 단위가 mm가 아님", current=f"{units}({insunits})", expected="mm(4)")
    if not _close(ltscale, k * rules.ltscale_per_k):
        c.add("LTSCALE", "선종류 축척(LTSCALE)이 k가 아님", current=ltscale, expected=k * rules.ltscale_per_k,
              fix="$LTSCALE")
    if k_assumed:
        assumptions.append(Assumption(name="scale", value=k, reason=f"요청에 축척이 없어 A3 1/{k}로 가정"))
        hinted = sorted({round(x) for x in (ltscale / rules.ltscale_per_k,
                                            doc.header.get("$DIMSCALE", 0) / rules.dimscale_per_k)
                         if x and round(x) != k})
        if hinted:
            warnings.append(f"축척을 받지 않아 1/{k}로 검사함. 도면 설정(LTSCALE {ltscale:g}, DIMSCALE "
                            f"{doc.header.get('$DIMSCALE', 0):g})은 " + "·".join(f"1/{x}" for x in hinted)
                            + "을 가리킴. 축척을 확인하고 scale을 주어 다시 검사할 것")

    # 같은 이름, 다른 정의(없는 정의는 위반 아님). 고칠지는 사람이 정한다(D3)
    for v in compare_definitions(std, doc):
        if v.code.endswith("_MISSING"):
            continue
        c.items.append((v.model_copy(update={"fixable": False,
                                             "message": v.message + "(고칠지는 사람이 정함)"}), None, None))

    # 객체
    heights = _allowed_heights(std, s)
    counts = Counter()
    for e in msp:
        t = e.dxftype()
        layer_name = e.dxf.get("layer", "0")
        annotative = is_annotative(e)
        if t == "DIMENSION":
            counts["치수"] += 1
            _check_dimension(c, std, e, layer_name, k, annotative)
        elif t == "INSERT":
            counts["블록 참조"] += 1
            _check_insert(c, std, e, layer_name, s)
        else:
            _check_layer(c, std, e, layer_name)
        _check_bylayer(c, std, e, layer_name)
        if annotative:
            c.add("ANNOTATIVE", "주석 객체(표준은 비주석)", e, True, False)
        if t in TEXT_TYPES:
            counts["문자"] += 1
            _check_text(c, std, e, layer_name, heights, mm)
        elif t in UNREAD_TEXT_TYPES:
            c.skip(f"{t} 문자", "0단계는 TEXT·MTEXT 문자만 검사")

    violations = sorted((v for v, _, _ in c.items), key=_sort_key)
    order = {id(v): i for i, v in enumerate(violations)}
    items = sorted(c.items, key=lambda it: order[id(it[0])])
    fixes = _fixes(items)
    unchecked = [Unchecked(item=item, reason=reason, count=n) for (item, reason), n in sorted(c.unchecked.items())]
    paperspace = sum(len(layout) for layout in doc.layouts if not layout.is_modelspace)
    out_of_scope = [
        OutOfScope(item="레이아웃", reason=f"종이 공간 객체 {paperspace}개는 검사하지 않음"),
        OutOfScope(item="블록 정의 내부 객체", reason="표준 블록은 정의 비교(BLOCK_ATTRIB·BLOCK_EXTENTS)로만 봄"),
        OutOfScope(item="Xref 내부 객체", reason="0단계 범위 밖(D4)"),
        OutOfScope(item="해치 패턴·출력 스타일(CTB)", reason="0단계 표준 파일 범위 밖(D3)"),
        OutOfScope(item="객체가 용도에 맞는 레이어에 있는지", reason="의미 판단은 0단계 범위 밖"),
        OutOfScope(item="치수·축열 간격 배치", reason="0단계 검사 항목 아님"),
    ]
    checked = [Checked(item="모형 공간 객체", count=len(msp)), Checked(item="문자", count=counts["문자"]),
               Checked(item="치수", count=counts["치수"]), Checked(item="블록 참조", count=counts["블록 참조"]),
               Checked(item="표준 정의", count=len(std.layers) + 1 + len(std.dimensions.styles)
                       + sum(1 for b in std.blocks if b.status == "standard"))]
    violation_counts = dict(sorted(Counter(v.code for v in violations).items()))
    fixable = sum(f.count for f in fixes)
    summary = (f"위반 {len(violations)}건({len(violation_counts)}종, 고칠 목록 {fixable}건), "
               f"미검사 {len(unchecked)}종, 축척 1/{k}" + ("(가정)" if k_assumed else ""))
    result = CheckResult(status=Status.SUCCESS, summary=summary, scope=scope, checked=checked,
                         violations=violations, unchecked=unchecked, out_of_scope=out_of_scope,
                         assumptions=assumptions, ids=ids, meta=meta, violation_counts=violation_counts,
                         fixes=fixes, warnings=warnings)

    if full_out is not None:
        full_out = Path(full_out)
        try:
            full_out.parent.mkdir(parents=True, exist_ok=True)
            full_out.write_text(result.model_dump_json(), encoding="utf-8")
            result.full_path = str(full_out)
        except OSError as e:
            result.warnings.append(f"전체 검사 결과 파일을 쓰지 못함({full_out}): {e}")
    if limit is not None:
        result = _truncate(result, limit)
    return result
