"""보호된 적용 판정(구조 결정 D5): 스냅샷(ezdxf 문서)으로 재실행·충돌·넣은 결과·고칠 대상을 판정한다

CAD 없이 돈다. CAD 조작과 순서는 src/apply_service.py가 맡는다.
- judge_rerun: 같은 op로 넣은 객체를 보고 적용·건너뜀(이미 완료)·충돌을 정한다
- used_definition_conflicts: 명세가 쓰는 이름의 정의가 대상 도면에서 표준과 다른지(D3·T0 E5)
- verify_inserted: 넣은 뒤 CAD 사본의 객체가 생성 DXF와 같은지(0.01mm)
- stale_states: 기준 상태(state)가 지금 객체와 다른 것(넣은 뒤 고쳐 쓸 것)
- restore_status: 되돌린 뒤 핸들 집합이 원래와 같은지
- plan_fixes·changed_since: 표준 위반 고치기(fix)의 대상과 적용 직전 비교
"""
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .check import CheckResult
from .definitions import compare_definitions
from .results import EngineResult, Unchecked, Violation
from .standards import Standard
from .state import arcaid_tags, entity_state, state_hash

TOL = 0.01
FIX_PROPERTIES = ("layer", "color", "linetype", "lineweight")
DETAIL_LIMIT = 20


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RerunDecision(_Model):
    action: Literal["apply", "skip", "conflict"]
    reason: str
    details: List[str] = Field(default_factory=list)


class FixTarget(_Model):
    handle: str
    property: Literal["layer", "color", "linetype", "lineweight"]
    value: Any
    before: Dict[str, Any] = Field(description="읽을 때의 레이어·색·트루컬러·선종류·선굵기")
    oid: Optional[str] = Field(default=None, description="엔진 객체면 oid")
    manual: bool = Field(default=False, description="엔진 객체인데 기준 상태와 달라 사람이 고친 것으로 보임")


class ApplyMeta(_Model):
    op: str
    decision: Optional[str] = None
    spec_hash: Optional[str] = None
    k: Optional[int] = None
    drawing: Optional[str] = None
    snapshot_before: Optional[str] = None
    snapshot_after: Optional[str] = None
    gen_dxf: Optional[str] = None
    result_path: Optional[str] = None
    states_rewritten: int = 0
    timings_ms: Dict[str, int] = Field(default_factory=dict)


class ApplyResult(EngineResult):
    """draw_batch·check_standard(fix)·apply_changes 결과: D2 공통 항목 + 적용 정보"""
    meta: Optional[ApplyMeta] = None
    new_violations: Dict[str, int] = Field(default_factory=dict, description="이번 적용으로 생긴 위반(코드별)")
    remaining_fixes: Dict[str, int] = Field(default_factory=dict, description="고치지 않고 남긴 고칠 목록(속성별)")
    warnings: List[str] = Field(default_factory=list)


class CheckFixResult(CheckResult):
    """check_standard 결과. fix=true면 본문은 고친 뒤 다시 검사한 결과이고 fix에 적용 결과가 들어 있다"""
    fix: Optional[ApplyResult] = Field(default=None, description="fix=true일 때 보호된 경로 적용 결과")


def _cut(items: List[str]) -> List[str]:
    return items[:DETAIL_LIMIT] + ([f"외 {len(items) - DETAIL_LIMIT}개"] if len(items) > DETAIL_LIMIT else [])


# ---------- 재실행 ----------

def tagged(doc, op: str) -> Dict[str, list]:
    """이 op로 넣은 모형 공간 객체: oid → 객체 목록"""
    out = defaultdict(list)
    for e in doc.modelspace():
        tags = arcaid_tags(e)
        if tags and tags.get("op") == op:
            out[tags.get("oid", "")].append(e)
    return dict(out)


def judge_rerun(doc, op: str, spec_hash: str, expected_oids: Set[str]) -> RerunDecision:
    """같은 op의 객체가 없으면 적용, 모두 그대로면 건너뜀, 그 밖에는 충돌(D5 '재실행')"""
    found = tagged(doc, op)
    if not found:
        return RerunDecision(action="apply", reason="이 op로 넣은 객체 없음")
    entities = [e for es in found.values() for e in es]
    other = sorted({oid for oid, es in found.items() for e in es if arcaid_tags(e).get("in") != spec_hash})
    if other:
        return RerunDecision(action="conflict", reason="같은 op에 다른 입력(명세가 바뀜). 이전 적용과 요청을 확인할 것",
                             details=_cut(other))
    missing, extra = sorted(expected_oids - set(found)), sorted(set(found) - expected_oids)
    if missing or extra:
        return RerunDecision(action="conflict", reason="일부만 있음(지워졌거나 남는 객체). 완료로 보지 않고 멈춤",
                             details=_cut([f"빠짐 {x}" for x in missing] + [f"남음 {x}" for x in extra]))
    copies = sorted(oid for oid, es in found.items() if len(es) > 1)
    if copies:
        return RerunDecision(action="conflict", reason="같은 oid가 둘 이상(복사된 엔진 객체)", details=_cut(copies))
    no_state = sorted(arcaid_tags(e)["oid"] for e in entities if not arcaid_tags(e).get("state"))
    if no_state:
        return RerunDecision(action="conflict", reason="기준 상태 없음(적용이 끝나지 않은 객체)", details=_cut(no_state))
    manual = sorted(arcaid_tags(e)["oid"] for e in entities if arcaid_tags(e)["state"] != state_hash(e))
    if manual:
        return RerunDecision(action="conflict", reason="손으로 고친 엔진 객체(덮어쓰지 않음)", details=_cut(manual))
    return RerunDecision(action="skip", reason="이미 완료")


# ---------- 적용 직전 확인 ----------

def used_definition_conflicts(std: Standard, target_doc, generated_doc) -> List[Violation]:
    """생성 DXF가 쓰는 레이어·문자 스타일·치수 스타일·블록 중, 대상 도면에 표준과 다른 정의로 있는 것"""
    msp = list(generated_doc.modelspace())
    layers = {e.dxf.layer for e in msp}
    dimstyles = {e.dxf.dimstyle for e in msp if e.dxftype() == "DIMENSION"}
    blocks = {e.dxf.name for e in msp if e.dxftype() == "INSERT"}
    uses_text = any(e.dxftype() in ("TEXT", "MTEXT", "DIMENSION") for e in msp)
    return [v for v in compare_definitions(std, target_doc, layers=layers, dimstyles=dimstyles, blocks=blocks,
                                           text_style=uses_text)
            if not v.code.endswith("_MISSING")]


def _close(a, b, tol: float = TOL) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol + 1e-9
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    return a == b


def _diff_keys(a: dict, b: dict, tol: float = TOL) -> List[str]:
    return sorted(k for k in a.keys() | b.keys() if not _close(a.get(k), b.get(k), tol))


def verify_inserted(generated_doc, new_entities: Iterable, tol: float = TOL) -> List[str]:
    """넣은 뒤 새로 생긴 객체가 생성 DXF와 같은지. 문제 목록(비면 통과)"""
    want = {arcaid_tags(e)["oid"]: e for e in generated_doc.modelspace()}
    got: Dict[str, list] = defaultdict(list)
    problems = []
    for e in new_entities:
        tags = arcaid_tags(e)
        if not tags or "oid" not in tags:
            problems.append(f"태그 없는 새 객체 {e.dxftype()} {e.dxf.handle}")
            continue
        got[tags["oid"]].append(e)
    for oid in sorted(want.keys() - got.keys()):
        problems.append(f"빠짐 {oid}")
    for oid in sorted(got.keys() - want.keys()):
        problems.append(f"모르는 oid {oid}")
    for oid in sorted(want.keys() & got.keys()):
        if len(got[oid]) > 1:
            problems.append(f"두 번 들어감 {oid}")
            continue
        keys = _diff_keys(entity_state(want[oid], verify=True), entity_state(got[oid][0], verify=True), tol)
        if keys:
            problems.append(f"다름 {oid}: {', '.join(keys)}")
    return problems


def stale_states(entities: Iterable) -> Dict[str, str]:
    """태그의 state가 지금 객체와 다른 엔진 객체: 핸들 → 새 state"""
    out = {}
    for e in entities:
        tags = arcaid_tags(e)
        if tags and "oid" in tags:
            now = state_hash(e)
            if tags.get("state") != now:
                out[e.dxf.handle] = now
    return out


def restore_status(base_handles: Set[str], now_doc, op: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """되돌린 뒤: (사라진 원래 핸들, 남은 이 op 객체 핸들, 남은 태그 없는·다른 객체 핸들)"""
    now = {e.dxf.handle: e for e in now_doc.modelspace()}
    missing = base_handles - now.keys()
    left_tagged, left_other = set(), set()
    for h in now.keys() - base_handles:
        tags = arcaid_tags(now[h])
        (left_tagged if tags and tags.get("op") == op else left_other).add(h)
    return missing, left_tagged, left_other


# ---------- 고치기(fix) ----------

def props(e) -> Dict[str, Any]:
    return {"layer": e.dxf.get("layer", "0"), "color": e.dxf.get("color", 256), "true_color": e.dxf.get("true_color"),
            "linetype": e.dxf.get("linetype", "BYLAYER"), "lineweight": e.dxf.get("lineweight", -1)}


def plan_fixes(check_full: CheckResult, doc, properties: Iterable[str] = FIX_PROPERTIES
               ) -> Tuple[List[FixTarget], List[Unchecked]]:
    """고칠 목록 중 객체 속성 하나를 바꾸는 것만 대상으로. 대상 도면에 없는 레이어로 옮기기는 고치지 않는다"""
    properties = set(properties)
    layers = {x.dxf.name.casefold() for x in doc.layers}
    targets, no_layer = [], 0
    for fix in check_full.fixes:
        if fix.property not in properties:
            continue
        for h in fix.handles:
            e = doc.entitydb.get(h)
            if e is None:
                continue
            if fix.property == "layer" and str(fix.value).casefold() not in layers:
                no_layer += 1
                continue
            tags = arcaid_tags(e)
            oid = tags.get("oid") if tags else None
            manual = bool(oid) and tags.get("state") != state_hash(e)
            targets.append(FixTarget(handle=h, property=fix.property, value=fix.value, before=props(e), oid=oid,
                                     manual=manual))
    unchecked = [Unchecked(item="대상 도면에 없는 레이어로 옮기기", reason="레이어를 먼저 만들어야 함(ensure_standard_layers)",
                           count=no_layer)] if no_layer else []
    return targets, unchecked


def changed_since(base_doc, now_doc, handles: Iterable[str], tol: float = TOL) -> List[str]:
    """읽을 때(base)와 지금(now) 사이에 바뀌었거나 없어진 객체 핸들(D5 '적용 직전 확인')"""
    out = []
    for h in handles:
        a, b = base_doc.entitydb.get(h), now_doc.entitydb.get(h)
        if a is None or b is None or not b.is_alive or not _close(entity_state(a), entity_state(b), tol):
            out.append(h)
    return out


def fixed_value_ok(target: FixTarget, e) -> bool:
    """고친 뒤 값이 맞는지"""
    p = props(e)
    if target.property == "color":
        return p["color"] == 256 and p["true_color"] is None
    if target.property == "linetype":
        return str(p["linetype"]).casefold() == "bylayer"
    if target.property == "lineweight":
        return p["lineweight"] == -1
    return str(p["layer"]).casefold() == str(target.value).casefold()
