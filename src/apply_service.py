"""보호된 적용 경로 조율(MCP 쪽, COM 스레드에서 실행): draw_batch·check_standard(fix)·apply_changes

구조 결정 D5 순서: 적용 직전 확인 → Undo 묶음 안에서 적용(COM 호출만) → 결과 확인 → 기준 상태 → 검사 → 보고.
- 확인을 통과하지 못하면 아무것도 바꾸지 않는다.
- 묶음 안에서 실패하면 묶음을 닫고 UNDO 1 → 스냅샷 핸들 집합으로 복귀를 확인한다. 이 op 태그 객체가 남았으면
  지우고 다시 확인한다. 원래 객체가 사라졌거나 모르는 객체가 남으면 unverified로 보고하고, 이후 보호된 변경을
  막는다(GUARD). 사용자가 확인한 뒤 resume_after_unverified=True로 풀린다.
- 판정은 엔진(src/engine/apply.py)이 스냅샷 DXF로 한다. hooks는 시험 전용이다(MCP 툴은 넘기지 않는다).
- apply_changes는 대상 밖 도형 지문·종속 핸들·태그도 확인한다. after로 확인된 대상은 역변경하고, before로 돌아와
  state만 남은 대상은 원래 태그만 복원한다. 삭제 객체를 다시 넣거나 태그로 지우지 않는다.
  적용 기록은 사후 확인 뒤 묶음 밖 마지막에만 쓴다.
"""
import time
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import ezdxf

try:
    from .engine.apply import (FIX_PROPERTIES, ApplyMeta, ApplyResult, changed_since, fixed_value_ok, judge_rerun,
                               plan_fixes, restore_status, stale_states, used_definition_conflicts, verify_inserted)
    from .engine.check import check_standard
    from .engine.changes import (ChangeError, ChangeRecord, RecordError, RecordTarget, change_hash, change_state,
                                 judge_changes, parse_changes, read_records, record_strings, resolve,
                                 same_state, state_digest, verify_applied, verify_restored)
    from .engine.definitions import compare_definitions
    from .engine.results import Applied, Assumption, Checked, ResultIds, Scope, Status, Unchecked, Violation
    from .engine.snapshot import SnapshotError, load_meta
    from .engine.spec import SpecError, parse_spec, spec_hash, validate_spec
    from .engine.state import TAG_KEYS, arcaid_tags, state_hash
    from .engine.write import build_dxf, build_layer_dxf
    from .snapshot_service import snapshot_active
except ImportError:
    from engine.apply import (FIX_PROPERTIES, ApplyMeta, ApplyResult, changed_since, fixed_value_ok, judge_rerun,
                              plan_fixes, restore_status, stale_states, used_definition_conflicts, verify_inserted)
    from engine.check import check_standard
    from engine.changes import (ChangeError, ChangeRecord, RecordError, RecordTarget, change_hash, change_state,
                                judge_changes, parse_changes, read_records, record_strings, resolve,
                                same_state, state_digest, verify_applied, verify_restored)
    from engine.definitions import compare_definitions
    from engine.results import Applied, Assumption, Checked, ResultIds, Scope, Status, Unchecked, Violation
    from engine.snapshot import SnapshotError, load_meta
    from engine.spec import SpecError, parse_spec, spec_hash, validate_spec
    from engine.state import TAG_KEYS, arcaid_tags, state_hash
    from engine.write import build_dxf, build_layer_dxf
    from snapshot_service import snapshot_active

LIST_LIMIT = 20
PROP_VALUES = {"color": 256, "linetype": "ByLayer", "lineweight": -1}


@dataclass
class Hooks:
    """시험 전용 실패·변경 주입 지점(ctl을 받는다)"""
    before_apply: Optional[Callable] = None   # 적용 직전 확인(다시 읽기) 전에
    after_change: Optional[Callable] = None   # 묶음 안에서 Import·속성 쓰기 직후
    before_change: Optional[Callable] = None  # B2 판정 뒤, 묶음을 열기 전(사람 편집 경쟁 시험)
    after_nth_change: Optional[Callable] = None  # B2 대상 변경 직후(ctl, n), n은 1부터
    before_setxdata: Optional[Callable] = None
    before_end_undo: Optional[Callable] = None
    before_inverse: Optional[Callable] = None
    record_write: Optional[Callable] = None
    record_readback: Optional[Callable] = None


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
    def __init__(self, problems: List[str], message: str = "넣은 결과가 생성 DXF와 다름"):
        self.problems = problems
        super().__init__(f"{message} {len(problems)}건")


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
    r = ctl.export_dxf(str(folder / name), raise_on_error=True)
    if not r["ok"]:
        raise RuntimeError(f"스냅샷 내보내기 실패: {r['error']}")
    return ezdxf.readfile(r["path"])


def _result(status: Status, summary: str, scope: Scope, ids: ResultIds, meta: ApplyMeta, folder: Optional[Path] = None,
            **kw) -> ApplyResult:
    if status is Status.UNVERIFIED and GUARD.reason is None:
        GUARD.reason = f"{meta.op}: {summary}"
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


def _uncertain_failure(error: BaseException) -> bool:
    """시간 초과·연결 끊김·취소는 복귀 확인과 무관하게 후속 변경을 막는다."""
    import asyncio
    return (isinstance(error, (TimeoutError, ConnectionError, KeyboardInterrupt, SystemExit, asyncio.CancelledError))
            or getattr(error, "hresult", None) in (-2147417848, -2147023174, -2147023170)
            or any(s in str(error).casefold() for s in ("timeout", "시간 초과", "타임아웃", "연결 끊", "취소", "disconnected")))


def _unexpected_result(error, scope, ids, meta, writes_attempted):
    status = Status.UNVERIFIED if writes_attempted or _uncertain_failure(error) else Status.FAILED
    # 결과 저장도 실패했을 수 있으므로 다시 같은 파일을 쓰지 않는다. GUARD는 _result가 먼저 건다.
    return _result(status, f"적용 경로에서 예외가 나 결과를 확인하지 못함: {error}", scope, ids, meta)


def _uncertain_undo(result):
    return (not result.get("ok", False) or result.get("pending", False) or result.get("cmdactive", 0) != 0
            or _uncertain_failure(RuntimeError(str(result.get("error") or ""))))


def _rollback(ctl, folder: Path, base_handles, op: str, failure: Exception, group_closed: bool, scope, ids, meta,
              warnings: List[str], extra_check=None, change_plan=None, base_doc=None, hooks=None,
              writes_attempted=None, target_doc=None) -> ApplyResult:
    """묶음 안 실패 뒤 되돌리고 복귀를 확인한다. extra_check(now_doc) → 남은 문제 목록(고치기의 값 복귀 확인)"""
    if change_plan is not None:
        return _rollback_changes(ctl, folder, change_plan, base_doc, failure, group_closed, scope, ids, meta,
                                 warnings, hooks, writes_attempted, target_doc)
    reason = str(failure)
    if isinstance(failure, _CheckFailed):
        warnings = warnings + failure.problems[:LIST_LIMIT]
    try:
        if group_closed:
            undo = ctl.undo_once()
            if not undo["ok"]:
                warnings.append(f"UNDO 1 보내기 실패: {undo['error']}")
            if _uncertain_undo(undo):
                GUARD.reason = GUARD.reason or f"{meta.op}: UNDO 완료 여부 확인 불가 {undo}"
        else:
            warnings.append("Undo 묶음을 닫지 못해 UNDO 1을 보내지 않음")
        now = _export(ctl, folder, "rollback")
        missing, left_tagged, left_other = restore_status(base_handles, now, op)
        if left_tagged and group_closed:   # 닫지 못한 묶음에는 추가 묶음도 열지 않는다
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
    if not problems and not _uncertain_failure(failure) and GUARD.reason is None:
        return _result(Status.RECOVERED, f"적용 실패, 원래 상태로 되돌림(확인함): {reason}", scope, ids, meta, folder,
                       warnings=warnings)
    GUARD.reason = GUARD.reason or f"{meta.op}: {reason} / {'; '.join(problems)}"
    return _result(Status.UNVERIFIED, f"적용 실패, 원래 상태로 돌아왔는지 확인하지 못함: {'; '.join(problems)}",
                   scope, ids, meta, folder, warnings=warnings + problems)


# ---------- apply_changes ----------

def _tag_strings(entity, state=None):
    if not entity.has_xdata("ARCAID"):
        return []
    strings = [t.value for t in entity.get_xdata("ARCAID") if t.code == 1000]
    if state is not None:
        strings = [s for s in strings if not s.startswith("state=")] + [f"state={state}"]
    return strings


def _inverse_props(target, change, base_doc):
    """요청에서 쓴 속성만 원래 값으로 돌린다(표기 대소문자도 원본에서 읽는다)."""
    e = base_doc.entitydb[target.handle]
    before = target.before
    out = {}
    for key in change.props.model_fields_set:
        if key in ("layer", "linetype"):
            out[key] = e.dxf.get(key, "0" if key == "layer" else "BYLAYER")
        elif key == "color":
            rgb = before["true_color"]
            out[key] = before["color"] if rgb is None else {"rgb": [(rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255]}
        elif key == "attribs":
            wanted = {k.casefold() for k in change.props.attribs}
            out[key] = {a["tag"]: a["text"] for a in before["attribs"] if a["tag"].casefold() in wanted}
        else:
            out[key] = before[key]
    return out


def _same_nonstate_tags(before, now):
    """옛 태그를 써도 되는지. 알려진 키 밖의 사람 수정도 덮어쓰지 않는다."""
    def tags(e):
        return [t for t in e.get_xdata("ARCAID") if not (t.code == 1000 and str(t.value).startswith("state="))] \
            if e.has_xdata("ARCAID") else None
    return tags(before) == tags(now)


def _rollback_changes(ctl, folder, plan, base_doc, failure, group_closed, scope, ids, meta, warnings, hooks,
                      writes_attempted, target_doc):
    """B2 복귀: UNDO → 확인된 비삭제 대상 역변경 → before에서 state만 다른 태그 복원. 다시 넣기는 하지 않는다."""
    if not writes_attempted:
        status = Status.UNVERIFIED if _uncertain_failure(failure) else Status.FAILED
        return _result(status, f"변경 쓰기 전 실패(UNDO 생략): {failure}", scope, ids, meta, folder, warnings=warnings)
    clock = _Clock()
    try:
        ctl.ensure_active(target_doc)
        ctl.doc = target_doc
        if group_closed:
            undo = ctl.undo_once()
            if not undo["ok"]:
                warnings.append(f"UNDO 1 보내기 실패: {undo.get('error')}")
            if _uncertain_undo(undo):
                GUARD.reason = GUARD.reason or f"{meta.op}: UNDO 완료 여부 확인 불가 {undo}"
        else:
            warnings.append("Undo 묶음을 닫지 못해 UNDO·추가 묶음·역변경을 하지 않음")
        now = _export(ctl, folder, "rollback")
        problems = verify_restored(plan, base_doc, now)
        if problems and group_closed:
            eligible = [t for t in plan.targets if t.action != "delete" and now.entitydb.get(t.handle) is not None
                        and same_state(t.after, change_state(now.entitydb[t.handle]))
                        and _same_nonstate_tags(base_doc.entitydb[t.handle], now.entitydb[t.handle])
                        and (not same_state(t.before, change_state(now.entitydb[t.handle]))
                             or _tag_strings(base_doc.entitydb[t.handle]) != _tag_strings(now.entitydb[t.handle]))]
            if eligible:
                if hooks.before_inverse:
                    hooks.before_inverse(ctl)
                changes = {c.id: c for c in plan.spec.changes}
                ctl.start_undo()
                try:
                    for t in eligible:
                        c = changes[t.change_id]
                        if t.action == "move":
                            ctl.move_handles([t.handle], [-v for v in c.delta])
                        else:
                            ctl.set_change_props(t.handle, **_inverse_props(t, c, base_doc))
                        if base_doc.entitydb[t.handle].has_xdata("ARCAID"):
                            ctl.set_xdata(t.handle, _tag_strings(base_doc.entitydb[t.handle]))
                finally:
                    inverse_closed = ctl.end_undo()
                if not inverse_closed:
                    raise RuntimeError("역변경 묶음을 닫지 못함")
                now = _export(ctl, folder, "rollback_inverse")
                problems = verify_restored(plan, base_doc, now)
            if problems:
                # 속성값은 돌아왔지만 INSERT의 state만 남기는 CAD에서 태그만 복원한다.
                tag_only = [t for t in plan.targets if t.action != "delete" and now.entitydb.get(t.handle) is not None
                            and same_state(t.before, change_state(now.entitydb[t.handle]))
                            and _same_nonstate_tags(base_doc.entitydb[t.handle], now.entitydb[t.handle])
                            and (arcaid_tags(base_doc.entitydb[t.handle]) or {}).get("state")
                            != (arcaid_tags(now.entitydb[t.handle]) or {}).get("state")]
                if tag_only:
                    ctl.start_undo()
                    try:
                        for t in tag_only:
                            ctl.set_xdata(t.handle, _tag_strings(base_doc.entitydb[t.handle]))
                    finally:
                        tags_closed = ctl.end_undo()
                    if not tags_closed:
                        raise RuntimeError("태그 복원 묶음을 닫지 못함")
                    warnings.append(f"UNDO가 INSERT의 ARCAID 태그를 되돌리지 않아 태그만 원래 값으로 복원함({len(tag_only)}개)")
                    now = _export(ctl, folder, "rollback_tags")
                    problems = verify_restored(plan, base_doc, now)
    except BaseException as error:
        problems = [f"되돌리기·확인 중 오류: {error}"]
        if _uncertain_failure(error):
            GUARD.reason = GUARD.reason or f"{meta.op}: {error}"
    meta.timings_ms["rollback_verify"] = round((time.perf_counter() - clock.t) * 1000)
    if not problems and not _uncertain_failure(failure) and GUARD.reason is None:
        return _result(Status.RECOVERED, f"변경 실패, 원래 상태로 되돌림(확인함): {failure}", scope, ids, meta, folder,
                       warnings=warnings)
    return _result(Status.UNVERIFIED, f"변경 실패, 복귀 확인 불가: {failure}", scope, ids, meta, folder,
                   warnings=warnings + problems[:LIST_LIMIT])


@dataclass
class _TargetUnchecked:
    """변경 대상 미검사와 전후 합집합을 구할 핸들(None은 전역 항목)."""
    unchecked: Unchecked
    handles: Optional[Set[str]]


def _target_violations(path, std, handles, k, snapshot):
    unchecked_handles = {}
    checked = check_standard(Path(path), std, k=k, snapshot=snapshot, _unchecked_handles=unchecked_handles)
    unchecked = []
    for u in checked.unchecked:
        item_handles = unchecked_handles.get((u.item, u.reason))
        if item_handles is None:
            unchecked.append(_TargetUnchecked(u, None))
        else:
            overlap = item_handles & handles
            if overlap:
                unchecked.append(_TargetUnchecked(u.model_copy(update={"count": len(overlap)}), overlap))
    if checked.status is not Status.SUCCESS or checked.truncated:
        unchecked.append(_TargetUnchecked(Unchecked(item="대상 새 위반 차분",
                                         reason=f"표준 검사 {checked.status.value} 또는 결과 잘림"), None))
    violations = Counter((v.code, v.handle) for v in checked.violations if v.handle in handles)
    return violations, unchecked


def _merge_target_unchecked(*checks) -> List[Unchecked]:
    """같은 항목·이유는 대상 핸들의 합집합으로 센다. 전역 개수는 전후를 더하지 않는다."""
    merged = {}
    for check in checks:
        for entry in check:
            u = entry.unchecked
            key = (u.item, u.reason)
            previous = merged.get(key)
            if previous is None:
                merged[key] = entry
            elif previous.handles is not None and entry.handles is not None:
                union = previous.handles | entry.handles
                merged[key] = _TargetUnchecked(u.model_copy(update={"count": len(union)}), union)
            else:
                counts = (previous.unchecked.count, u.count)
                count = None if None in counts else max(counts)
                merged[key] = _TargetUnchecked(u.model_copy(update={"count": count}), None)
    return [merged[key].unchecked for key in sorted(merged)]


def apply_changes(ctl, work: Path, std, spec, overwrite_manual: bool = False, hooks: Optional[Hooks] = None,
                  resume: bool = False, k: Optional[int] = None) -> ApplyResult:
    """기존 객체 이동·삭제·속성 변경. CAD UI의 동시 편집은 사후 사본으로 감지한다(잠글 수는 없다)."""
    hooks, clock = hooks or Hooks(), _Clock()
    scope = Scope(target="활성 도면", supported=["기존 객체 이동·삭제·속성 변경(apply_changes)"])
    ids, meta = ResultIds(standard=std.ref), ApplyMeta(op="?")
    assumptions = [Assumption(name="scale", value=std.profile.scale.default_k,
                              reason=f"요청에 축척이 없어 A3 1/{std.profile.scale.default_k}로 가정")] if k is None else []
    clock.ms = meta.timings_ms
    try:
        parsed = parse_changes(spec)
    except ChangeError as error:
        return _result(Status.FAILED, "변경 명세 형식 오류(바꾸지 않음)", scope, ids, meta, violations=error.violations)
    ids.request_id, meta.op, meta.spec_hash = parsed.op, parsed.op, change_hash(parsed)
    refusal = _precheck(ctl, resume)
    if refusal:
        return _result(Status.FAILED, refusal, scope, ids, meta)
    folder = _folder(work, parsed.op)
    doc, warnings = ctl.doc, []
    writes_attempted = 0
    try:
        state = ctl.doc_state(doc)
        meta.drawing = state["full_name"] or state["name"]
        base_meta = load_meta(work, parsed.target.snapshot_id)
        if (base_meta.method != "open_document"
                or os.path.normcase(os.path.abspath(base_meta.source_path)) != os.path.normcase(os.path.abspath(meta.drawing))):
            return _result(Status.CONFLICT, "기준 스냅샷은 활성 문서의 사본이 아님", scope, ids, meta, folder)
        if parsed.target.drawing_id and parsed.target.drawing_id != base_meta.drawing_id:
            return _result(Status.CONFLICT, "명세와 기준 스냅샷의 drawing_id가 다름", scope, ids, meta, folder)
        base_doc = ezdxf.readfile(base_meta.dxf_path)
        try:
            plan = resolve(parsed, base_doc, std, base_meta.snapshot_id)
        except ChangeError as error:
            return _result(Status.FAILED, "기준 사본에서 변경 거부(바꾸지 않음)", scope, ids, meta, folder,
                           violations=error.violations)
        if hooks.before_apply:
            hooks.before_apply(ctl)
        pre_meta, warnings = snapshot_active(ctl, work)
        meta.snapshot_before, ids.snapshot_id, ids.drawing_id = pre_meta.snapshot_id, pre_meta.snapshot_id, pre_meta.drawing_id
        if (pre_meta.drawing_id != base_meta.drawing_id
                or os.path.normcase(os.path.abspath(pre_meta.source_path)) != os.path.normcase(os.path.abspath(meta.drawing))):
            return _result(Status.CONFLICT, "기준 사본과 활성 도면의 출처·drawing_id가 다름", scope, ids, meta, folder)
        if pre_meta.insunits != std.profile.insunits:
            return _result(Status.FAILED, f"도면 단위가 mm가 아님($INSUNITS {pre_meta.insunits})", scope, ids, meta, folder)
        pre = ezdxf.readfile(pre_meta.dxf_path)
        try:
            records = read_records(pre)
        except RecordError as error:
            return _result(Status.CONFLICT, f"적용 기록 읽기 실패: {error}", scope, ids, meta, folder)
        decision = judge_changes(plan, pre, records, overwrite_manual)
        meta.decision = decision.action
        clock.lap("plan_precheck")
        if decision.action == "skip":
            return _result(Status.SUCCESS, f"{parsed.op}: 이미 완료(건너뜀)", scope, ids, meta, folder, warnings=warnings)
        if decision.action == "conflict":
            return _result(Status.CONFLICT, f"{parsed.op}: {decision.reason}", scope, ids, meta, folder,
                           violations=_conflicts(decision.reason, decision.details), warnings=warnings)
        handles = {t.handle for t in plan.targets}
        check_handles = handles | {a.dxf.handle for h in handles if pre.entitydb[h].dxftype() == "INSERT"
                                   for a in pre.entitydb[h].attribs}
        meta.k = std.profile.scale.default_k if k is None else k
        before_v, before_unchecked = _target_violations(pre_meta.dxf_path, std, check_handles, meta.k, pre_meta)
        clock.lap("check_before")

        # 판정 뒤 사람 편집은 막을 수 없다. 묶음 전에 주입해야 UNDO가 그 편집을 지우지 않는다.
        if hooks.before_change:
            hooks.before_change(ctl)
        ctl.ensure_active(doc)
        ctl.doc = doc
        ctl.start_undo()
        failure, closed = None, False
        try:
            changes = {c.id: c for c in parsed.changes}
            for n, t in enumerate(plan.targets, 1):
                c = changes[t.change_id]
                writes_attempted += 1  # 쓰기가 일부만 적용되고 예외가 날 수 있다. 성공 개수로 세지 않는다.
                if t.action == "move":
                    ctl.move_handles([t.handle], c.delta)
                elif t.action == "delete":
                    _, failed = ctl.delete_handles([t.handle], raise_on_error=True)
                    if failed:
                        raise RuntimeError(f"삭제 실패: {failed}")
                else:
                    ctl.set_change_props(t.handle, **c.props.model_dump(mode="json", exclude_unset=True))
                if hooks.after_nth_change:
                    hooks.after_nth_change(ctl, n)
            if hooks.after_change:
                hooks.after_change(ctl)
            s1 = _export(ctl, folder, "s1")
            for t in plan.targets:
                if t.action == "delete" or not arcaid_tags(pre.entitydb[t.handle]):
                    continue
                if hooks.before_setxdata:
                    hooks.before_setxdata(ctl)
                writes_attempted += 1
                ctl.set_xdata(t.handle, _tag_strings(pre.entitydb[t.handle], state_hash(s1.entitydb[t.handle])))
                meta.states_rewritten += 1
        except BaseException as error:
            failure = error
        try:
            if hooks.before_end_undo:
                hooks.before_end_undo(ctl)
            closed = ctl.end_undo()
        except BaseException as error:
            failure = failure or error
        clock.lap("apply")
        if failure is None and not closed:
            failure = RuntimeError("Undo 묶음을 닫지 못함")
        if failure is not None:
            return _rollback(ctl, folder, None, parsed.op, failure, closed, scope, ids, meta, warnings,
                             change_plan=plan, base_doc=pre, hooks=hooks, writes_attempted=writes_attempted, target_doc=doc)

        ctl.ensure_active(doc)
        ctl.doc = doc
        post_meta, post_warnings = snapshot_active(ctl, work)
        meta.snapshot_after = post_meta.snapshot_id
        warnings += post_warnings
        post = ezdxf.readfile(post_meta.dxf_path)
        problems = verify_applied(plan, pre, post)
        if post_meta.drawing_id != pre_meta.drawing_id or post_meta.source_path != pre_meta.source_path:
            problems.append("사후 사본의 도면 출처·drawing_id가 다름")
        clock.lap("verify_applied")
        if problems:
            return _rollback(ctl, folder, None, parsed.op, _CheckFailed(problems, "적용 뒤 확인 불일치"), True, scope, ids, meta,
                             warnings + problems[:LIST_LIMIT], change_plan=plan, base_doc=pre, hooks=hooks,
                             writes_attempted=writes_attempted, target_doc=doc)
        after_v, after_unchecked = _target_violations(post_meta.dxf_path, std, check_handles, meta.k, post_meta)
        unchecked = _merge_target_unchecked(before_unchecked, after_unchecked)
        new_violations = dict(Counter(code for (code, _), n in (after_v - before_v).items() for _ in range(n)))
        clock.lap("check_after")

        # B0 E13: UNDO는 XRECORD를 되돌리지 않는다. 확인이 끝난 변경의 기록만 마지막에 쓴다.
        record = ChangeRecord(op=parsed.op, input_hash=meta.spec_hash, result="applied",
                              targets={t.handle: RecordTarget(action=t.action, after_hash=None if t.action == "delete"
                                                              else state_digest(change_state(post.entitydb[t.handle])))
                                       for t in plan.targets})
        phase = "생성"
        try:
            ctl.ensure_active(doc)
            ctl.doc = doc
            entry = ctl.create_change_record(parsed.op, root_exists=post.rootdict.get("ARCAID") is not None)
            phase = "쓰기"
            if hooks.record_write:
                hooks.record_write(ctl)
            ctl.write_change_record(entry, record_strings(record))
            phase = "재읽기"
            if hooks.record_readback:
                hooks.record_readback(ctl)
            # B0 E13에서 검증한 Export 사본으로 같은 op의 전체 기록을 대조한다.
            readback = read_records(_export(ctl, folder, "record")).get(parsed.op)
            if isinstance(readback, RecordError):
                raise readback
            if readback != record:
                raise RecordError("쓴 적용 기록과 재읽은 값이 다름")
        except BaseException as error:
            clock.lap("record")
            suffix = "(기록이 남았을 수 있음)" if phase == "재읽기" else ""
            return _result(Status.UNVERIFIED, f"변경은 확인했지만 적용 기록 {phase} 실패{suffix}: {error}",
                           scope, ids, meta, folder, warnings=warnings, unchecked=unchecked, assumptions=assumptions)
        clock.lap("record")
        counts = Counter(t.action for t in plan.targets)
        check_text = f"새 위반 {sum(new_violations.values())}건"
        if unchecked:
            check_text += f", 미검사 {len(unchecked)}종"
        return _result(Status.SUCCESS, f"{parsed.op}: {len(handles)}개 변경, {check_text}", scope, ids, meta, folder,
                       applied=[Applied(action=a, count=n, handles=[t.handle for t in plan.targets if t.action == a][:LIST_LIMIT])
                                for a, n in counts.items()], checked=[Checked(item="변경 대상", count=len(handles))],
                       new_violations=new_violations, unchecked=unchecked, warnings=warnings,
                       assumptions=assumptions)
    except BaseException as error:
        return _unexpected_result(error, scope, ids, meta, writes_attempted)


# ---------- draw_batch ----------

DRAW_SCOPE = ["객체 명세 넣기(draw_batch)"]


def change_refusal(std, spec) -> Optional[ApplyResult]:
    """변경 명세 형식·표준이 다르면 CAD 연결 전에 거부한다."""
    scope = Scope(target="활성 도면", supported=["기존 객체 이동·삭제·속성 변경(apply_changes)"])
    ids, meta = ResultIds(standard=std.ref), ApplyMeta(op="?")
    try:
        parsed = parse_changes(spec)
    except ChangeError as error:
        return _result(Status.FAILED, "변경 명세 형식 오류(바꾸지 않음)", scope, ids, meta,
                       violations=error.violations)
    ids.request_id, meta.op, meta.spec_hash = parsed.op, parsed.op, change_hash(parsed)
    if parsed.standard != std.ref:
        return _result(Status.FAILED, "변경 명세의 표준이 다름(바꾸지 않음)", scope, ids, meta,
                       violations=[Violation(code="CHG_STANDARD", message="standard: 표준이 다름",
                                             current=parsed.standard, expected=std.ref)])
    return None


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
    warnings = list(warnings) + built.warnings
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
    writes_attempted = 0
    try:
        failure, new = None, []
        ctl.start_undo()
        try:
            writes_attempted += 1
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
                writes_attempted += 1
                ctl.set_xdata(handle, [f"{k}={tags[k]}" for k in TAG_KEYS if k in tags])
            meta.states_rewritten = len(stale)
        except BaseException as e:  # noqa: BLE001
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

    except BaseException as error:
        return _unexpected_result(error, scope, ids, meta, writes_attempted)


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
    writes_attempted = 0
    try:
        failure = None
        ctl.start_undo()
        try:
            for handle, values in per_handle.items():
                writes_attempted += 1
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
                writes_attempted += 1
                ctl.set_xdata(handle, [f"{k}={tags[k]}" for k in TAG_KEYS if k in tags])
            meta.states_rewritten = len(stale)
        except BaseException as e:  # noqa: BLE001
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

    except BaseException as error:
        return _unexpected_result(error, scope, ids, meta, writes_attempted)


# ---------- ensure_standard_layers ----------

def layer_refusal(std, names: Optional[List[str]]):
    """(만들 표준 레이어 이름 목록, 거부 결과 또는 None). 이름을 주지 않으면 그리는 표준 레이어 전부(CAD 없이 판단)"""
    if not names:
        return [x.name for x in std.layers if x.drawable], None
    wanted, bad = [], []
    for name in names:
        layer = std.layer(name)
        if layer is None or not layer.drawable:
            bad.append(name)
        elif layer.name not in wanted:
            wanted.append(layer.name)
    if bad:
        return wanted, _result(Status.FAILED, f"표준에 없거나 그리지 않는 레이어: {', '.join(bad)}(만들지 않음)",
                               Scope(target="활성 도면", supported=["표준 레이어 만들기"]),
                               ResultIds(standard=std.ref, request_id="layers"), ApplyMeta(op="layers"))
    return wanted, None


def ensure_layers(ctl, work: Path, std, names: Optional[List[str]] = None, hooks: Optional[Hooks] = None,
                  resume: bool = False) -> ApplyResult:
    """없는 표준 레이어를 표준 색·선종류·출력 여부로 만든다(D5 보호된 경로). 같은 이름이 다른 정의면 멈춘다(D3)"""
    hooks = hooks or Hooks()
    clock = _Clock()
    scope = Scope(target="활성 도면", supported=["표준 레이어 만들기"])
    ids = ResultIds(standard=std.ref, request_id="layers")
    meta = ApplyMeta(op="layers")
    clock.ms = meta.timings_ms
    wanted, refused = layer_refusal(std, names)
    if refused is not None:
        return refused
    refusal = _precheck(ctl, resume)
    if refusal:
        return _result(Status.FAILED, refusal, scope, ids, meta)
    folder = _folder(work, "layers")
    doc = ctl.doc
    state = ctl.doc_state(doc)
    meta.drawing = state["full_name"] or state["name"]
    try:
        snap0, warnings = snapshot_active(ctl, work)
    except SnapshotError as e:
        return _result(Status.FAILED, f"스냅샷 실패: {e}", scope, ids, meta, folder)
    meta.snapshot_before, ids.snapshot_id, ids.drawing_id = snap0.snapshot_id, snap0.snapshot_id, snap0.drawing_id
    s0 = ezdxf.readfile(snap0.dxf_path)
    existing = {x.dxf.name.casefold() for x in s0.layers}
    present = [w for w in wanted if w.casefold() in existing]
    conflicts = [v for v in compare_definitions(std, s0, layers=present, dimstyles=[], blocks=[], text_style=False)
                 if not v.code.endswith("_MISSING")]
    if conflicts:
        return _result(Status.CONFLICT, f"같은 이름 레이어가 표준과 다른 정의로 있음 {len(conflicts)}건(덮어쓰지 않고 멈춤, "
                                        "고칠지는 사람이 정함)", scope, ids, meta, folder, violations=conflicts)
    missing = [w for w in wanted if w.casefold() not in existing]
    if not missing:
        return _result(Status.SUCCESS, f"표준 레이어 {len(wanted)}개가 이미 있음", scope, ids, meta, folder,
                       checked=[Checked(item="표준 레이어", count=len(wanted))], warnings=warnings)
    dxf = build_layer_dxf(std, missing, folder / "layers.dxf")
    clock.lap("precheck")

    base = {e.dxf.handle for e in s0.modelspace()}
    try:
        ctl.ensure_active(doc)
    except RuntimeError as e:
        return _result(Status.FAILED, f"{e}(만들지 않음)", scope, ids, meta, folder)
    writes_attempted = 0
    try:
        failure = None
        ctl.start_undo()
        try:
            writes_attempted += 1
            ctl.import_dxf(dxf)
            if hooks.after_change:
                hooks.after_change(ctl)
            s1 = _export(ctl, folder, "s1")
            names_now = {x.dxf.name.casefold() for x in s1.layers}
            problems = [f"레이어 {m}가 생기지 않음" for m in missing if m.casefold() not in names_now]
            problems += [v.message for v in compare_definitions(std, s1, layers=missing, dimstyles=[], blocks=[],
                                                                text_style=False) if not v.code.endswith("_MISSING")]
            if {e.dxf.handle for e in s1.modelspace()} != base:
                problems.append("레이어를 만드는 동안 객체가 생기거나 없어짐")
            if problems:
                raise _CheckFailed(problems)
        except BaseException as e:  # noqa: BLE001
            failure = e
        closed = ctl.end_undo()
        clock.lap("apply")
        if failure is None and not closed:
            failure = RuntimeError("Undo 묶음을 닫지 못함")
        if failure is not None:
            def layers_gone(now_doc):
                left = [m for m in missing if now_doc.layers.has_entry(m)]
                return [f"되돌린 뒤에도 남은 레이어 {', '.join(left)}"] if left else []
            return _rollback(ctl, folder, base, "layers", failure, closed, scope, ids, meta, list(warnings),
                             extra_check=layers_gone)
        return _result(Status.SUCCESS, f"표준 레이어 {len(missing)}개 만듦({', '.join(missing)}), 이미 있음 {len(present)}개",
                       scope, ids, meta, folder, checked=[Checked(item="표준 레이어", count=len(wanted))],
                       applied=[Applied(action="레이어 만들기", count=len(missing))], warnings=warnings)

    except BaseException as error:
        return _unexpected_result(error, scope, ids, meta, writes_attempted)
