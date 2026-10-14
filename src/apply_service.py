"""보호된 적용 경로 조율(MCP 쪽, COM 스레드에서 실행): draw_batch와 check_standard(fix)

구조 결정 D5 순서: 적용 직전 확인 → Undo 묶음 안에서 적용(COM 호출만) → 결과 확인 → 기준 상태 → 검사 → 보고.
- 확인을 통과하지 못하면 아무것도 바꾸지 않는다.
- 묶음 안에서 실패하면 묶음을 닫고 UNDO 1 → 스냅샷 핸들 집합으로 복귀를 확인한다. 이 op 태그 객체가 남았으면
  지우고 다시 확인한다. 원래 객체가 사라졌거나 모르는 객체가 남으면 unverified로 보고하고, 이후 보호된 변경을
  막는다(GUARD). 사용자가 확인한 뒤 resume_after_unverified=True로 풀린다.
- 판정은 엔진(src/engine/apply.py)이 스냅샷 DXF로 한다. hooks는 시험 전용이다(MCP 툴은 넘기지 않는다).
"""
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import ezdxf

try:
    from .engine.apply import (FIX_PROPERTIES, ApplyMeta, ApplyResult, changed_since, fixed_value_ok, judge_rerun,
                               plan_fixes, restore_status, stale_states, used_definition_conflicts, verify_inserted)
    from .engine.check import check_standard
    from .engine.results import Applied, Assumption, Checked, ResultIds, Scope, Status, Violation
    from .engine.snapshot import SnapshotError, load_meta
    from .engine.spec import SpecError, parse_spec, spec_hash, validate_spec
    from .engine.state import TAG_KEYS, arcaid_tags
    from .engine.write import build_dxf
    from .snapshot_service import snapshot_active
except ImportError:
    from engine.apply import (FIX_PROPERTIES, ApplyMeta, ApplyResult, changed_since, fixed_value_ok, judge_rerun,
                              plan_fixes, restore_status, stale_states, used_definition_conflicts, verify_inserted)
    from engine.check import check_standard
    from engine.results import Applied, Assumption, Checked, ResultIds, Scope, Status, Violation
    from engine.snapshot import SnapshotError, load_meta
    from engine.spec import SpecError, parse_spec, spec_hash, validate_spec
    from engine.state import TAG_KEYS, arcaid_tags
    from engine.write import build_dxf
    from snapshot_service import snapshot_active

LIST_LIMIT = 20
PROP_VALUES = {"color": 256, "linetype": "ByLayer", "lineweight": -1}


@dataclass
class Hooks:
    """시험 전용 실패·변경 주입 지점(ctl을 받는다)"""
    before_apply: Optional[Callable] = None   # 적용 직전 확인(다시 읽기) 전에
    after_change: Optional[Callable] = None   # 묶음 안에서 Import·속성 쓰기 직후


class ApplyGuard:
    """확인 불가로 끝난 뒤 후속 보호된 변경을 막는다(D5)"""

    def __init__(self):
        self.reason: Optional[str] = None

    def refusal(self, resume: bool) -> Optional[str]:
        if self.reason and not resume:
            return (f"이전 적용이 확인 불가로 끝나 보호된 변경을 멈춤: {self.reason}. "
                    "도면을 확인한 뒤 resume_after_unverified=true로 다시 부를 것")
        if resume:
            self.reason = None
        return None


GUARD = ApplyGuard()


class _CheckFailed(Exception):
    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__(f"넣은 결과가 생성 DXF와 다름 {len(problems)}건")


class _Clock:
    def __init__(self):
        self.t, self.ms = time.perf_counter(), {}

    def lap(self, name: str):
        now = time.perf_counter()
        self.ms[name] = round((now - self.t) * 1000)
        self.t = now


def _folder(work: Path, name: str) -> Path:
    path = Path(work) / "apply" / name / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _export(ctl, folder: Path, name: str):
    r = ctl.export_dxf(str(folder / name))
    if not r["ok"]:
        raise RuntimeError(f"스냅샷 내보내기 실패: {r['error']}")
    return ezdxf.readfile(r["path"])


def _result(status: Status, summary: str, scope: Scope, ids: ResultIds, meta: ApplyMeta, folder: Optional[Path] = None,
            **kw) -> ApplyResult:
    result = ApplyResult(status=status, summary=summary, scope=scope, ids=ids, meta=meta, **kw)
    if folder is not None:
        path = folder / "result.json"
        meta.result_path = str(path)
        path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    return result


def _conflicts(reason: str, details: List[str]) -> List[Violation]:
    return [Violation(code="APPLY_CONFLICT", message=f"{reason}: {d}") for d in details] or \
        [Violation(code="APPLY_CONFLICT", message=reason)]


def _precheck(ctl, resume: bool) -> Optional[str]:
    refusal = GUARD.refusal(resume)
    if refusal:
        return refusal
    if not ctl.is_running():
        return "CAD 미연결 또는 열린 도면 없음"
    busy = ctl.command_busy_reason()
    if busy:
        return f"{busy}. 명령을 마치거나 cancel_command로 취소한 뒤 다시 부를 것"
    return None


def _rollback(ctl, folder: Path, base_handles, op: str, failure: Exception, group_closed: bool, scope, ids, meta,
              warnings: List[str], extra_check=None) -> ApplyResult:
    """묶음 안 실패 뒤 되돌리고 복귀를 확인한다. extra_check(now_doc) → 남은 문제 목록(고치기의 값 복귀 확인)"""
    reason = str(failure)
    if isinstance(failure, _CheckFailed):
        warnings = warnings + failure.problems[:LIST_LIMIT]
    try:
        if group_closed:
            undo = ctl.undo_once()
            if not undo["ok"]:
                warnings.append(f"UNDO 1 보내기 실패: {undo['error']}")
        else:
            warnings.append("Undo 묶음을 닫지 못해 UNDO 1을 보내지 않음")
        now = _export(ctl, folder, "rollback")
        missing, left_tagged, left_other = restore_status(base_handles, now, op)
        if left_tagged:   # 이 op로 넣은 객체가 남았으면 태그로 찾아 지운다(T0 E6 대안)
            ctl.start_undo()
            try:
                deleted, failed = ctl.delete_handles(sorted(left_tagged))
            finally:
                ctl.end_undo()
            warnings.append(f"UNDO 뒤 남은 이 op 객체 {deleted}개를 태그로 지움" + (f", 못 지움 {failed}" if failed else ""))
            now = _export(ctl, folder, "rollback2")
            missing, left_tagged, left_other = restore_status(base_handles, now, op)
        problems = [f"사라진 원래 객체 {len(missing)}개"] if missing else []
        problems += [f"남은 이 op 객체 {len(left_tagged)}개"] if left_tagged else []
        problems += [f"남은 모르는 객체 {len(left_other)}개({', '.join(sorted(left_other)[:LIST_LIMIT])})"] if left_other else []
        if extra_check is not None:
            problems += extra_check(now)
    except Exception as e:  # noqa: BLE001  (연결 끊김 등: 복귀를 확인할 수 없다)
        problems = [f"되돌리기·확인 중 오류: {e}"]
    if not problems:
        return _result(Status.RECOVERED, f"적용 실패, 원래 상태로 되돌림(확인함): {reason}", scope, ids, meta, folder,
                       warnings=warnings)
    GUARD.reason = f"{meta.op}: {reason} / {'; '.join(problems)}"
    return _result(Status.UNVERIFIED, f"적용 실패, 원래 상태로 돌아왔는지 확인하지 못함: {'; '.join(problems)}",
                   scope, ids, meta, folder, warnings=warnings + problems)


# ---------- draw_batch ----------

DRAW_SCOPE = ["객체 명세 넣기(draw_batch)"]


def spec_refusal(std, spec) -> Optional[ApplyResult]:
    """명세가 형식·표준에 맞지 않으면 실패 결과(CAD 없이 판단), 맞으면 None"""
    scope, ids = Scope(target="활성 도면", supported=DRAW_SCOPE), ResultIds(standard=std.ref)
    try:
        parsed = parse_spec(spec)
    except SpecError as e:
        return _result(Status.FAILED, f"명세 형식 오류 {len(e.violations)}건(넣지 않음)", scope, ids,
                       ApplyMeta(op="?"), violations=e.violations)
    problems = validate_spec(parsed, std)
    if problems:
        ids.request_id = parsed.op
        return _result(Status.FAILED, f"명세가 표준과 맞지 않음 {len(problems)}건(넣지 않음)", scope, ids,
                       ApplyMeta(op=parsed.op, spec_hash=spec_hash(parsed)), violations=problems)
    return None


def draw_batch(ctl, work: Path, std, spec, hooks: Optional[Hooks] = None, resume: bool = False) -> ApplyResult:
    """객체 명세를 검증·작성해 활성 도면에 넣는다(D5 보호된 경로)"""
    hooks = hooks or Hooks()
    clock = _Clock()
    refused = spec_refusal(std, spec)
    if refused is not None:
        return refused
    parsed = parse_spec(spec)
    scope = Scope(target="활성 도면", supported=DRAW_SCOPE)
    ids = ResultIds(standard=std.ref, request_id=parsed.op)
    h = spec_hash(parsed)
    meta = ApplyMeta(op=parsed.op, spec_hash=h)
    clock.ms = meta.timings_ms   # 모델이 dict를 복사하므로 모델 쪽 dict에 바로 적는다
    refusal = _precheck(ctl, resume)
    if refusal:
        return _result(Status.FAILED, refusal, scope, ids, meta)

    folder = _folder(work, parsed.op)
    gen_path = folder / "gen.dxf"
    built = build_dxf(parsed, std, gen_path)
    meta.gen_dxf, meta.k = str(gen_path), built.meta.k if built.meta else None
    if built.status is not Status.SUCCESS:
        return _result(Status.FAILED, built.summary, scope, ids, meta, folder, violations=built.violations)
    gen_doc = ezdxf.readfile(gen_path)
    expected = {m.oid for m in built.manifest}
    clock.lap("build")

    # 적용 직전 확인
    if hooks.before_apply:
        hooks.before_apply(ctl)
    doc = ctl.doc
    state = ctl.doc_state(doc)
    meta.drawing = state["full_name"] or state["name"]
    try:
        snap0, warnings = snapshot_active(ctl, work)
    except SnapshotError as e:
        return _result(Status.FAILED, f"적용 전 스냅샷 실패: {e}", scope, ids, meta, folder)
    meta.snapshot_before, ids.snapshot_id, ids.drawing_id = snap0.snapshot_id, snap0.snapshot_id, snap0.drawing_id
    s0 = ezdxf.readfile(snap0.dxf_path)
    clock.lap("snapshot_before")
    if snap0.insunits != std.profile.insunits:
        return _result(Status.FAILED, f"도면 단위가 mm가 아니라 넣지 않음($INSUNITS {snap0.insunits}, Import는 단위를 "
                                      "환산하지 않음)", scope, ids, meta, folder)
    target = parsed.target
    if target and target.drawing_id and target.drawing_id != snap0.drawing_id:
        return _result(Status.CONFLICT, f"명세의 대상 도면({target.drawing_id})이 활성 도면({snap0.drawing_id})과 다름",
                       scope, ids, meta, folder)
    defs = used_definition_conflicts(std, s0, gen_doc)
    if defs:
        return _result(Status.CONFLICT, f"대상 도면의 같은 이름 정의가 표준과 다름 {len(defs)}건(덮어쓰지 않고 멈춤, "
                                        "고칠지는 사람이 정함)", scope, ids, meta, folder, violations=defs)
    decision = judge_rerun(s0, parsed.op, h, expected)
    meta.decision = decision.action
    if decision.action == "skip":
        return _result(Status.SUCCESS, f"{parsed.op}: 이미 완료(건너뜀, 객체 {len(expected)}개 그대로)", scope, ids, meta,
                       folder, checked=[Checked(item="이 op 객체", count=len(expected))], warnings=warnings)
    if decision.action == "conflict":
        return _result(Status.CONFLICT, f"{parsed.op}: {decision.reason}(바꾸지 않음)", scope, ids, meta, folder,
                       violations=_conflicts(decision.reason, decision.details), warnings=warnings)

    # Undo 묶음 안에서 넣기(COM 호출만)
    base = {e.dxf.handle for e in s0.modelspace()}
    try:
        ctl.ensure_active(doc)
    except RuntimeError as e:
        return _result(Status.FAILED, f"{e}(넣지 않음)", scope, ids, meta, folder)
    failure, new = None, []
    ctl.start_undo()
    try:
        ctl.import_dxf(gen_path)
        if hooks.after_change:
            hooks.after_change(ctl)
        s1 = _export(ctl, folder, "s1")
        new = [e for e in s1.modelspace() if e.dxf.handle not in base]
        problems = verify_inserted(gen_doc, new)
        if problems:
            raise _CheckFailed(problems)
        stale = stale_states(new)
        for handle, st in stale.items():   # 넣으면서 CAD가 값을 바꾼 객체만 기준 상태를 고쳐 쓴다
            tags = dict(arcaid_tags(s1.entitydb[handle]), state=st)
            ctl.set_xdata(handle, [f"{k}={tags[k]}" for k in TAG_KEYS if k in tags])
        meta.states_rewritten = len(stale)
    except Exception as e:  # noqa: BLE001
        failure = e
    closed = ctl.end_undo()
    clock.lap("apply")
    if failure is None and not closed:
        failure = RuntimeError("Undo 묶음을 닫지 못함")
    if failure is not None:
        return _rollback(ctl, folder, base, parsed.op, failure, closed, scope, ids, meta, list(warnings))

    # 결과 확인·검사
    try:
        snap2, _ = snapshot_active(ctl, work)
    except SnapshotError as e:
        return _result(Status.UNVERIFIED, f"넣었지만 사후 확인 스냅샷을 만들지 못함: {e}", scope, ids, meta, folder,
                       warnings=warnings)
    meta.snapshot_after = snap2.snapshot_id
    s2 = ezdxf.readfile(snap2.dxf_path)
    after = judge_rerun(s2, parsed.op, h, expected)
    if after.action != "skip":
        return _rollback(ctl, folder, base, parsed.op, RuntimeError(f"넣은 뒤 확인 실패: {after.reason}"), True,
                         scope, ids, meta, list(warnings) + after.details)
    new_handles = {e.dxf.handle for e in new}
    check = check_standard(Path(snap2.dxf_path), std, k=meta.k)
    new_violations = dict(Counter(v.code for v in check.violations if v.handle in new_handles))
    clock.lap("verify_check")
    kinds = ", ".join(f"{k} {n}" for k, n in built.meta.by_kind.items())
    summary = (f"{parsed.op}: 객체 {len(new)}개 넣음({kinds}), 기준 상태 고쳐 씀 {meta.states_rewritten}개, "
               f"새 위반 {sum(new_violations.values())}건")
    return _result(Status.SUCCESS, summary, scope, ids, meta, folder,
                   checked=[Checked(item="넣은 객체", count=len(new))],
                   applied=[Applied(action="넣기", count=len(new), handles=sorted(new_handles)[:LIST_LIMIT])],
                   assumptions=built.assumptions, new_violations=new_violations, warnings=warnings)


# ---------- check_standard(fix) ----------

def fix_standard(ctl, work: Path, std, k: Optional[int] = None, base_snapshot_id: Optional[str] = None,
                 overwrite_manual: bool = False, hooks: Optional[Hooks] = None, resume: bool = False) -> ApplyResult:
    """표준 위반 중 객체 속성 하나(레이어·색·선종류·선굵기)를 바꾸는 것을 고친다(D5 보호된 경로)"""
    hooks = hooks or Hooks()
    clock = _Clock()
    scope = Scope(target="활성 도면", supported=[f"고치기: {', '.join(FIX_PROPERTIES)}"])
    ids = ResultIds(standard=std.ref, request_id="fix")
    meta = ApplyMeta(op="fix")
    clock.ms = meta.timings_ms
    refusal = _precheck(ctl, resume)
    if refusal:
        return _result(Status.FAILED, refusal, scope, ids, meta)
    folder = _folder(work, "fix")
    doc = ctl.doc
    state = ctl.doc_state(doc)
    meta.drawing = state["full_name"] or state["name"]
    warnings: List[str] = []
    try:
        if base_snapshot_id:
            base_meta = load_meta(work, base_snapshot_id)
            if base_meta.method != "open_document" or base_meta.source_path not in (state["full_name"], state["name"]):
                return _result(Status.CONFLICT, f"스냅샷 {base_snapshot_id}은 활성 도면의 사본이 아님({base_meta.source_path})",
                               scope, ids, meta, folder)
        else:
            base_meta, warnings = snapshot_active(ctl, work)
    except SnapshotError as e:
        return _result(Status.FAILED, f"스냅샷 실패: {e}", scope, ids, meta, folder)
    meta.snapshot_before, ids.snapshot_id, ids.drawing_id = base_meta.snapshot_id, base_meta.snapshot_id, base_meta.drawing_id
    base_doc = ezdxf.readfile(base_meta.dxf_path)
    kk = std.profile.scale.default_k if k is None else k
    meta.k = kk
    assumptions = [Assumption(name="scale", value=kk, reason=f"요청에 축척이 없어 A3 1/{kk}로 가정")] if k is None else []
    full = check_standard(Path(base_meta.dxf_path), std, k=kk, snapshot=base_meta)
    targets, unchecked = plan_fixes(full, base_doc)
    remaining: Dict[str, int] = {}
    for f in full.fixes:   # 이번 경로로 고치지 않는 고칠 목록(DIMSCALE·문자 높이·스타일·LTSCALE 등)
        if f.property not in FIX_PROPERTIES:
            remaining[f.property] = remaining.get(f.property, 0) + f.count
    clock.lap("plan")
    if not targets:
        return _result(Status.SUCCESS, "고칠 대상 없음(객체 속성 위반 0건)", scope, ids, meta, folder, unchecked=unchecked,
                       assumptions=assumptions, remaining_fixes=remaining, warnings=warnings)
    manual = [t for t in targets if t.manual]
    if manual and not overwrite_manual:
        return _result(Status.CONFLICT, f"손으로 고친 엔진 객체 {len(manual)}개가 고칠 대상(덮어쓰지 않음, "
                                        "overwrite_manual=true일 때만 고침)", scope, ids, meta, folder,
                       violations=_conflicts("손으로 고친 엔진 객체", sorted({t.oid for t in manual}))[:LIST_LIMIT],
                       unchecked=unchecked, assumptions=assumptions, remaining_fixes=remaining)

    # 적용 직전 확인: 읽을 때와 지금 대상이 같은가
    if hooks.before_apply:
        hooks.before_apply(ctl)
    now = _export(ctl, folder, "now")
    handles = sorted({t.handle for t in targets})
    changed = changed_since(base_doc, now, handles)
    if changed:
        return _result(Status.CONFLICT, f"읽은 뒤 바뀐 대상 {len(changed)}개(바꾸지 않음). 다시 검사한 뒤 고칠 것", scope, ids,
                       meta, folder, violations=_conflicts("읽은 뒤 바뀜", changed[:LIST_LIMIT]),
                       assumptions=assumptions)
    base = {e.dxf.handle for e in now.modelspace()}
    layer_names = {x.dxf.name.casefold(): x.dxf.name for x in now.layers}
    per_handle: Dict[str, dict] = {}
    for t in targets:
        value = layer_names[str(t.value).casefold()] if t.property == "layer" else PROP_VALUES[t.property]
        per_handle.setdefault(t.handle, {})[t.property] = value
    clock.lap("precheck")

    try:
        ctl.ensure_active(doc)
    except RuntimeError as e:
        return _result(Status.FAILED, f"{e}(고치지 않음)", scope, ids, meta, folder)
    failure = None
    ctl.start_undo()
    try:
        for handle, values in per_handle.items():
            ctl.set_props(handle, **values)
        if hooks.after_change:
            hooks.after_change(ctl)
        s1 = _export(ctl, folder, "s1")
        problems = [f"{t.handle} {t.property}" for t in targets
                    if s1.entitydb.get(t.handle) is None or not fixed_value_ok(t, s1.entitydb[t.handle])]
        if {e.dxf.handle for e in s1.modelspace()} != base:
            problems.append("고치는 동안 객체가 생기거나 없어짐")
        if problems:
            raise _CheckFailed(problems)
        engine = [s1.entitydb[h] for h in handles if arcaid_tags(s1.entitydb[h])]
        stale = stale_states(engine)
        for handle, st in stale.items():   # 보호된 경로로 바꿨으니 기준 상태도 갱신한다
            tags = dict(arcaid_tags(s1.entitydb[handle]), state=st)
            ctl.set_xdata(handle, [f"{k}={tags[k]}" for k in TAG_KEYS if k in tags])
        meta.states_rewritten = len(stale)
    except Exception as e:  # noqa: BLE001
        failure = e
    closed = ctl.end_undo()
    clock.lap("apply")
    if failure is None and not closed:
        failure = RuntimeError("Undo 묶음을 닫지 못함")
    if failure is not None:
        def values_back(now_doc):
            back = changed_since(now, now_doc, handles)
            return [f"값이 원래대로 돌아오지 않은 대상 {len(back)}개"] if back else []
        return _rollback(ctl, folder, base, "fix", failure, closed, scope, ids, meta, warnings, extra_check=values_back)

    try:
        snap2, _ = snapshot_active(ctl, work)
    except SnapshotError as e:
        return _result(Status.UNVERIFIED, f"고쳤지만 사후 확인 스냅샷을 만들지 못함: {e}", scope, ids, meta, folder,
                       warnings=warnings)
    meta.snapshot_after = snap2.snapshot_id
    after = check_standard(Path(snap2.dxf_path), std, k=kk)
    before = {(v.code, v.handle) for v in full.violations}
    new_violations = dict(Counter(v.code for v in after.violations
                                  if v.handle in set(handles) and (v.code, v.handle) not in before))
    clock.lap("verify_check")
    by_prop = dict(Counter(t.property for t in targets))
    summary = (f"고침 {len(per_handle)}개 객체({', '.join(f'{p} {n}' for p, n in by_prop.items())}), "
               f"엔진 객체 기준 상태 갱신 {meta.states_rewritten}개, 남은 위반 {len(after.violations)}건")
    return _result(Status.SUCCESS, summary, scope, ids, meta, folder,
                   applied=[Applied(action=f"고치기 {p}", count=n,
                                    handles=sorted(t.handle for t in targets if t.property == p)[:LIST_LIMIT])
                            for p, n in by_prop.items()],
                   checked=[Checked(item="고칠 대상", count=len(targets))], unchecked=unchecked,
                   assumptions=assumptions, new_violations=new_violations, remaining_fixes=remaining, warnings=warnings)
