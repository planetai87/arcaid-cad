"""B5 실제 사본 통과 시험. CAD 실행은 Claude가 사람 확인 뒤 직렬로 수행한다.

ARCAID_CAD_TESTS=1, ARCAID_B5_PREPARED, ARCAID_B5_APPROVED_MANIFEST_SHA256 필요.
먼저 1단계 test_apply_changes_cad 원·호 관문을 통과해야 한다.
--basetemp는 C:/arcaid/work 아래 존재하지 않는 새 경로를 쓴다. MCP와 동시 실행 금지.
"""
import copy
import os
from pathlib import Path
import time

import ezdxf
import pytest

from src import apply_service as service
from src.engine.check import check_standard
from src.engine.results import Status
from src.engine.standards import load_standard
from src.snapshot_service import snapshot_active
from tools.acceptance import b5_dangsan as b5


@pytest.fixture(autouse=True)
def reset_guard():
    """상위 conftest의 자동 해제를 덮어쓴다. 불확실 상태는 사람 확인 없이 지우지 않는다."""
    assert service.GUARD.reason is None, "이전 unverified를 사람 확인 없이 해제하지 않음"
    yield


@pytest.fixture
def prepared(tmp_path):
    folder = os.environ.get("ARCAID_B5_PREPARED")
    approved = os.environ.get("ARCAID_B5_APPROVED_MANIFEST_SHA256")
    if not folder or not approved:
        pytest.skip("B5 입력·사람 확인 manifest 해시를 지정하지 않음")
    path = Path(folder).resolve()
    manifest = b5.load_prepared(path, approved)
    assert tmp_path.resolve().is_relative_to(Path("C:/arcaid/work").resolve())
    assert len(manifest["requests"]) == 3
    return path, manifest


def _user_state(ctl):
    return {d["full_name"] or d["name"]: (d["saved"], d["is_active"]) for d in ctl.list_drawings()}


class Run:
    def __init__(self, ctl, work, prepared, manifest):
        self.ctl, self.work, self.prepared, self.manifest = ctl, work, prepared, manifest
        self.std = load_standard()
        self.evidence = b5.Evidence(work / "b5-evidence.json", approved_manifest=b5.file_identity(prepared / "manifest.json"))
        self.ids = set()
        self.safe = True

    def snap(self, stage):
        self.evidence.add(stage + ":export-start")
        started = time.perf_counter()
        meta, warnings = snapshot_active(self.ctl, self.work / "work")
        self.evidence.add(stage + ":export", seconds=time.perf_counter()-started,
                          meta=meta.model_dump(mode="json"), warnings=warnings)
        assert b5.file_identity(meta.dxf_path)["sha256"] == meta.dxf_sha256
        return meta, ezdxf.readfile(meta.dxf_path)

    def prove(self, stage, proof):
        self.evidence.add(stage, proof=proof)
        assert proof["equal"], proof

    def open(self, label, kind):
        assert self.safe and service.GUARD.reason is None
        dxf, dwg = self.work / f"{label}.dxf", self.work / f"{label}.dwg"
        b5.copy_with_new_guid(self.prepared / f"{kind}-base.dxf", dxf)
        self.evidence.add(label + ":open-start", source=str(dxf))
        assert self.ctl.open_drawing(str(dxf))
        meta, doc = self.snap(label + ":initial")
        assert meta.drawing_id not in self.ids, "사본마다 다른 FINGERPRINTGUID 필요"
        self.ids.add(meta.drawing_id)
        for description in self.manifest["requests"]:
            if (description["op"] == b5.OPS[2]) != (kind == "human"):
                continue
            request = b5.bind_request(self.prepared, description["op"], meta.model_dump())
            result = b5.validate_runtime(doc, request, description)
            self.evidence.add(label + ":runtime-targets", result=result)
        # 최초 DWG 저장도 새 Export의 대상 검증이 끝난 뒤에만 한다.
        self.save(dwg)
        return dwg

    def save(self, dest):
        assert self.safe and service.GUARD.reason is None, "불확실 이후 저장 금지"
        self.evidence.add("save-start", path=str(dest))
        assert self.ctl.save_drawing(str(dest))
        self.evidence.add("save-done", identity=b5.file_identity(dest))

    def request(self, op, stage):
        meta, doc = self.snap(stage)
        return b5.bind_request(self.prepared, op, meta.model_dump()), doc

    def apply(self, request, stage, hooks=None):
        assert self.safe and service.GUARD.reason is None
        self.evidence.add(stage + ":apply-start", request=request)
        started = time.perf_counter()
        result = service.apply_changes(self.ctl, self.work / "work", self.std, request, hooks=hooks, k=200)
        self.evidence.add(stage + ":apply-done", seconds=time.perf_counter()-started, result=result.model_dump(mode="json"))
        if result.status is Status.UNVERIFIED or service.GUARD.reason:
            self.safe = False
            raise AssertionError(f"불확실: 이후 저장 금지: {result.summary}")
        return result

    def close(self):
        # 정상 시에도 자기 사본을 대조한다. finally의 전체 정리와 별개다.
        name = Path(self.ctl.doc_state(self.ctl.doc)["full_name"]).resolve()
        assert name.is_relative_to(self.work.resolve())
        assert self.ctl.close_drawing(False)

    def marker(self, label):
        """이전 작업 표시선과 실제 기존 적용 기록을 별도 Undo 작업으로 만든다."""
        ctl = self.ctl
        ctl.start_undo()
        try:
            ms = ctl._call(getattr, ctl.doc, "ModelSpace")
            obj = ctl._call_attr(ms, "AddLine", ctl._pt((100000, -100000, 7)), ctl._pt((101000, -100000, 7)))
            handle = str(ctl._call(getattr, obj, "Handle"))
        finally:
            assert ctl.end_undo()
        meta, before = self.snap(label + ":marker-before")
        request = dict(schema_version=1, standard=self.std.ref, op="b5-prior-marker",
                       target=dict(drawing_id=meta.drawing_id, snapshot_id=meta.snapshot_id),
                       changes=[dict(id="prior", select=dict(handles=[handle]), action="move", delta=[1, 0])])
        result = self.apply(request, label + ":marker")
        assert result.status is Status.SUCCESS and not result.new_violations and not result.unchecked
        _, after = self.snap(label + ":marker-after")
        self.prove(label + ":marker-proof", b5.compare_transition(before, after, request))
        return handle


def _success(run):
    finals, baselines = {}, {}
    for label in ("A", "B"):
        for kind, ops in (("engine", b5.OPS[:2]), ("human", b5.OPS[2:])):
            name = f"{label}-{kind}"
            dest = run.open(name, kind)
            _, initial = run.snap(name + ":baseline")
            baselines[name] = initial
            specs = []
            for op in ops:
                request, before = run.request(op, name + ":" + op + ":base")
                description = next(r for r in run.manifest["requests"] if r["op"] == op)
                b5.validate_runtime(before, request, description)
                result = run.apply(request, name + ":" + op)
                assert result.status is Status.SUCCESS and result.meta.decision == "apply", result.summary
                assert sum(a.count for a in result.applied) == description["count"]
                run.evidence.add(name + ":" + op + ":apply-scope",
                                 result=b5.verify_apply_result(kind, description, run.std, result.model_dump(mode="json")))
                _, after = run.snap(name + ":" + op + ":after")
                run.prove(name + ":" + op + ":independent", b5.compare_transition(before, after, request))
                specs.append(request)
            _, applied = run.snap(name + ":applied")
            for request in specs:
                result = run.apply(request, name + ":replay:" + request["op"])
                assert result.status is Status.SUCCESS and result.meta.decision == "skip", result.summary
                _, replayed = run.snap(name + ":replay-export")
                run.prove(name + ":skip-unchanged", b5.compare_unchanged(applied, replayed))
            meta, final = run.snap(name + ":check-source")
            checked = check_standard(Path(meta.dxf_path), run.std, 200)
            run.evidence.add(name + ":check", result=checked.model_dump(mode="json"))
            # 사람 도면의 기존 위반은 보존한다. 요청 범위의 차분은 각 apply 반환에서 0을 확인했다.
            b5.verify_standard_result(kind, checked.model_dump(mode="json"))
            run.save(dest)
            run.close()
            assert run.ctl.open_drawing(str(dest))
            reopened_meta, reopened = run.snap(name + ":reopened")
            assert reopened_meta.drawing_id == meta.drawing_id
            run.prove(name + ":save-reopen", b5.compare_unchanged(final, reopened, float_rel_tol=1e-12))
            finals[name] = reopened
            run.close()
    for kind in ("engine", "human"):
        run.prove(kind + ":A-B", b5.compare_copies(finals["A-" + kind], finals["B-" + kind],
                                                  baselines=(baselines["A-" + kind], baselines["B-" + kind])))


def _mutate(ctl, handle, action, change=None):
    """충돌 주입은 보호된 도구를 우회하는 직접 COM 호출이다."""
    if action in ("move", "partial"):
        ctl.move_handles([handle], change["delta"] if action == "partial" else (17, 23))
    elif action == "delete":
        count, failed = ctl.delete_handles([handle], raise_on_error=True)
        assert count == 1 and not failed
    else:
        obj = ctl._call_attr(ctl.doc, "HandleToObject", handle)
        ctl._call(setattr, obj, "TextString", "B5 수동 편집")


def _conflicts(run):
    cases = [("human", "after", action) for action in ("move", "text", "delete")]
    cases += [("engine", "before", "move"), ("engine", "after", "move"),
              ("engine", "after", "text"), ("engine", "after", "delete"),
              ("engine", "after", "partial"), ("engine", "applied", "move"),
              ("engine", "applied", "different-input")]
    for i, (kind, timing, action) in enumerate(cases):
        label = f"conflict-{i}-{kind}-{timing}-{action}"
        run.open(label, kind)
        run.marker(label)
        op = b5.OPS[2] if kind == "human" else (b5.OPS[1] if action in ("text", "delete") else b5.OPS[0])
        request, base = run.request(op, label + ":own-baseline")
        chosen = b5.targets(base, request)
        handle = next(h for h, c in chosen.items() if c["action"] == ("set" if action == "text" else "delete" if action == "delete" else "move"))
        change = chosen[handle]
        if timing == "before":
            _mutate(run.ctl, handle, action)
            # 이 사본 자체의 수정 후 기준: state 불일치 경로만 시험한다.
            request, base = run.request(op, label + ":manual-before-baseline")
        elif timing == "applied":
            result = run.apply(request, label + ":first-apply")
            assert result.status is Status.SUCCESS
            _, applied = run.snap(label + ":first-after")
            run.prove(label + ":first-proof", b5.compare_transition(base, applied, request))
            if action == "different-input":
                request = copy.deepcopy(request)
                request["changes"][0]["delta"][0] += 1
            else:
                _mutate(run.ctl, handle, action)
        else:
            _mutate(run.ctl, handle, action, change)
        _, immediately_before = run.snap(label + ":just-before")
        result = run.apply(request, label)
        assert result.status is Status.CONFLICT and result.meta.decision == "conflict", result.summary
        reason = ("같은 op에 다른 입력" if action == "different-input" else "적용 뒤 바뀐 대상" if timing == "applied"
                  else "손으로 고친 엔진 객체" if timing == "before" else "읽은 뒤 바뀌었거나 없어진 대상")
        assert reason in result.summary, result.summary
        if action != "different-input":
            assert any(handle in v.message for v in result.violations), (handle, result.violations)
        assert all(v.code == "APPLY_CONFLICT" for v in result.violations)
        run.evidence.add(label + ":reason-and-handle", expected_reason=reason, handle=handle)
        _, after = run.snap(label + ":after")
        run.prove(label + ":no-change", b5.compare_unchanged(immediately_before, after))
        if timing != "applied":
            assert op not in b5.records(after)
        assert b5.records(after)["b5-prior-marker"] == b5.records(immediately_before)["b5-prior-marker"]
        run.close()
    # 태그가 없는 사람 객체의 기준 이전 수정은 새 기준에 포함된다. 거짓 충돌로 처리하지 않는다.
    label = "human-manual-before-baseline"
    run.open(label, "human")
    request, doc = run.request(b5.OPS[2], label + ":initial")
    handle = next(h for h, c in b5.targets(doc, request).items() if c["action"] == "move")
    _mutate(run.ctl, handle, "move")
    request, base = run.request(b5.OPS[2], label + ":own-new-baseline")
    result = run.apply(request, label)
    assert result.status is Status.SUCCESS
    _, after = run.snap(label + ":after")
    run.prove(label, b5.compare_transition(base, after, request))
    run.close()


def _recoveries(run, monkeypatch):
    points = [(op, point) for op in b5.OPS[:2] for point in
              ("after-first", "after-last", "before-second-state", "after-last-state")]
    points += [(b5.OPS[2], p) for p in ("after-first", "after-last", "after-change")]
    for i, (op, point) in enumerate(points):
        label = f"recovery-{i}-{op}-{point}"
        kind = "human" if op == b5.OPS[2] else "engine"
        run.open(label, kind)
        marker = run.marker(label)
        request, before = run.request(op, label + ":own-baseline")
        count = len(b5.targets(before, request))
        state_count = sum(c["action"] != "delete" for c in b5.targets(before, request).values())
        fired = []
        def boom():
            fired.append(point)
            run.evidence.add(label + ":injected", point=point)
            raise RuntimeError("B5 시험 실패 주입: " + point)
        def nth(_ctl, n):
            if n == (1 if point == "after-first" else count):
                boom()
        states = []
        def before_state(_ctl):
            states.append(1)
            if len(states) == 2:
                boom()  # 첫 state를 실제 쓴 뒤 두 번째 쓰기 전
        hooks = service.Hooks()
        with monkeypatch.context() as patch:
            if point in ("after-first", "after-last"):
                hooks.after_nth_change = nth
            elif point == "before-second-state":
                hooks.before_setxdata = before_state
            elif point == "after-change":
                hooks.after_change = lambda _ctl: boom()
            else:
                # before_end_undo에서 던지면 종료 실패로 unverified다. 마지막 SetXData가
                # 실제로 끝난 즉시 던져 내부 예외 경로에서 EndUndoMark를 실행하게 한다.
                original = run.ctl.set_xdata
                def after_write(*args, **kwargs):
                    value = original(*args, **kwargs)
                    states.append(1)
                    if len(states) == state_count and not fired:
                        boom()
                    return value
                patch.setattr(run.ctl, "set_xdata", after_write)
            result = run.apply(request, label, hooks)
        assert fired, "실패 주입 지점을 지나지 않음"
        assert result.status is Status.RECOVERED, result.summary
        if point in ("before-second-state", "after-last-state"):
            assert result.meta.states_rewritten >= (state_count - 1 if point == "after-last-state" else 1)
        _, after = run.snap(label + ":restored")
        run.prove(label + ":all-handles-tags-records", b5.compare_unchanged(before, after))
        assert b5.fingerprint(before.entitydb[marker]) == b5.fingerprint(after.entitydb[marker])
        assert b5.records(before) == b5.records(after) and op not in b5.records(after)
        assert service.GUARD.reason is None
        run.close()


def test_b5_dangsan_acceptance(prepared, cad, tmp_path, monkeypatch):
    """계획 CAD 1~8: 직렬 성공·재전송·충돌·복구·독립 A/B·원본 및 사용자 보호."""
    path, manifest = prepared
    ctl, _ = cad
    user_before = _user_state(ctl)
    run = Run(ctl, tmp_path, path, manifest)
    run.evidence.data["user_before"] = user_before
    run.evidence.write()
    try:
        _success(run)
        _conflicts(run)
        _recoveries(run, monkeypatch)
        run.evidence.data["status"] = "checks_passed"
    except BaseException as error:
        run.safe = False
        run.evidence.data.update(status="failed-no-further-save", error=repr(error))
        run.evidence.write()
        raise
    finally:
        from _cadutil import close_work_docs
        try:
            close_work_docs(ctl, tmp_path)
            active = next((name for name, value in user_before.items() if value[1]), None)
            if active:
                assert ctl.switch_drawing(active)
            run.evidence.data["user_after"] = _user_state(ctl)
            run.evidence.data["originals_unchanged"] = all(b5.file_identity(v["path"]) == v for v in manifest["inputs"].values())
            assert run.evidence.data["user_after"] == user_before
            assert run.evidence.data["originals_unchanged"]
            if run.evidence.data["status"] == "checks_passed":
                run.evidence.data["status"] = "passed"
        except BaseException as error:
            run.evidence.data.update(status="failed-cleanup", cleanup_error=repr(error))
            raise
        finally:
            run.evidence.write()
    assert run.evidence.data["status"] == "passed"
