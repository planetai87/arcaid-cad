"""CAD 없이 보호된 변경 경로를 시험한다. COM 경계만 메모리 DXF로 대체한다."""
import copy
import json
from pathlib import Path

import ezdxf
import pytest

from src import apply_service as service
from src.engine.changes import read_records
from src.engine.results import Status
from src.engine.standards import load_standard
from src.engine.state import arcaid_tags, state_hash
from src.snapshot_service import snapshot_active


class MemoryCAD:
    def __init__(self, doc):
        self.doc = doc
        self.undo_calls = 0
        self.open_group = False
        self.source = "C:/arcaid/work/b2.dwg"

    def is_running(self):
        return True

    def command_busy_reason(self):
        return None

    def doc_state(self, doc=None):
        return dict(name="b2.dwg", full_name=self.source, saved=False)

    def ensure_active(self, doc=None):
        assert doc is self.doc or doc is None

    def export_dxf(self, path, raise_on_error=False):
        path = Path(str(path) + ".dxf")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.saveas(path)
        return dict(ok=True, path=str(path))

    def start_undo(self):
        assert not self.open_group
        self.saved = copy.deepcopy(self.doc)
        self.open_group = True

    def end_undo(self):
        self.open_group = False
        return True

    def undo_once(self):
        assert not self.open_group
        self.undo_calls += 1
        self.doc = copy.deepcopy(self.saved)
        return {"ok": True}

    def move_handles(self, handles, delta):
        for h in handles:
            self.doc.entitydb[h].translate(*delta, 0)

    def delete_handles(self, handles, raise_on_error=False):
        for h in handles:
            self.doc.modelspace().delete_entity(self.doc.entitydb[h])
        return len(handles), []

    def set_change_props(self, handle, **props):
        e = self.doc.entitydb[handle]
        for key, value in props.items():
            if key == "color" and isinstance(value, dict):
                r, g, b = value["rgb"]
                e.dxf.true_color = (r << 16) | (g << 8) | b
            elif key == "attribs":
                for a in e.attribs:
                    if a.dxf.tag in value:
                        a.dxf.text = value[a.dxf.tag]
            else:
                setattr(e.dxf, key, value)
                if key == "color":
                    e.dxf.discard("true_color")

    def set_xdata(self, handle, strings):
        self.doc.entitydb[handle].set_xdata("ARCAID", [(1000, s) for s in strings])

    def set_props(self, handle, **props):
        self.set_change_props(handle, **props)

    def import_dxf(self, path):
        for entity in ezdxf.readfile(path).modelspace():
            self.doc.modelspace().add_entity(entity.copy())

    def create_change_record(self, op, root_exists):
        assert not self.open_group
        root = self.doc.rootdict.get("ARCAID") if root_exists else self.doc.rootdict.add_new_dict("ARCAID")
        if op in root:
            raise RuntimeError("같은 op 기록이 있음")
        return root.add_xrecord(op)

    def write_change_record(self, record, strings):
        assert not self.open_group
        record.reset([(1, s) for s in strings])


@pytest.fixture(autouse=True)
def guard():
    service.GUARD.reason = None
    yield
    service.GUARD.reason = None


@pytest.fixture
def prepared(tmp_path):
    std = load_standard()
    d = ezdxf.readfile(std.path(std.profile.template.dxf))
    d.modelspace().delete_all_entities()
    e = d.modelspace().add_line((0, 0), (100, 0), dxfattribs={"layer": "WAL"})
    d.appids.new("ARCAID") if "ARCAID" not in d.appids else None
    e.set_xdata("ARCAID", [(1000, x) for x in ["op=draw", "oid=draw:line", "path=line", "in=123",
                                             "ver=1", "custom=옛 태그", f"state={state_hash(e)}"]])
    other = d.modelspace().add_circle((200, 0), 10)
    ctl = MemoryCAD(d)
    meta, _ = snapshot_active(ctl, tmp_path)
    spec = dict(schema_version=1, standard=std.ref, op="change-1", target={"snapshot_id": meta.snapshot_id},
                changes=[dict(id="move", select={"handles": [e.dxf.handle]}, action="move", delta=[10, 20])])
    return ctl, tmp_path, std, spec, e.dxf.handle, other.dxf.handle


def run(p, **kwargs):
    return service.apply_changes(*p[:4], **kwargs)


def boom(*_, **__):
    raise RuntimeError("일부러 낸 실패")


def test_draw_wall_direction_warnings_survive_apply_and_replay(prepared):
    ctl, work, std = prepared[:3]
    part = [[0, 0], [6000, 0], [6000, 200], [0, 200]]
    wall = dict(kind="wall", layer="CON", parts=[part])
    spec = dict(schema_version=1, standard=std.ref, op="draw-walls", scale=100, items=[
        wall, dict(kind="group", items=[wall], place=[dict(rotation=23.76)])])
    for decision in ("apply", "skip"):
        result = service.draw_batch(ctl, work, std, spec)
        assert result.status is Status.SUCCESS, result.summary
        assert result.meta.decision == decision
        assert len(result.warnings) == 1
        assert "방향이 다른 벽 접합부는 합치지 않음" in result.warnings[0]


def test_bad_schema_never_uses_cad(tmp_path):
    r = service.apply_changes(object(), tmp_path, load_standard(), "{bad")
    assert r.status is Status.FAILED and r.violations[0].code == "CHG_SCHEMA"


@pytest.mark.parametrize("source", ["closed", "other", "drawing_id"])
def test_source_mismatch_refuses_without_writing(prepared, source):
    ctl, work, std, spec, h, _ = prepared
    path = work / "snapshots" / (spec["target"]["snapshot_id"] + ".json")
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update({"method": "closed_file"} if source == "closed" else
                {"source_path": "C:/other.dwg"} if source == "other" else {"drawing_id": "other"})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert run(prepared).status is Status.CONFLICT
    assert ctl.undo_calls == 0 and tuple(ctl.doc.entitydb[h].dxf.start) == (0, 0, 0)


def test_success_writes_state_and_record_then_skips(prepared):
    ctl, _, _, spec, h, _ = prepared
    r = run(prepared)
    assert r.status is Status.SUCCESS, r.summary
    assert tuple(ctl.doc.entitydb[h].dxf.start) == (10, 20, 0)
    assert arcaid_tags(ctl.doc.entitydb[h])["state"] == state_hash(ctl.doc.entitydb[h])
    assert arcaid_tags(ctl.doc.entitydb[h])["custom"] == "옛 태그"
    assert read_records(ctl.doc)[spec["op"]].result == "applied"
    assert run(prepared).meta.decision == "skip"
    spec["changes"][0]["delta"] = [11, 20]
    assert run(prepared).status is Status.CONFLICT


@pytest.mark.parametrize("point", ["after_nth_change", "before_setxdata"])
def test_failure_recovers_without_record(prepared, point):
    ctl, _, _, _, h, _ = prepared
    r = run(prepared, hooks=service.Hooks(**{point: boom}))
    assert r.status is Status.RECOVERED, r.summary
    assert ctl.undo_calls == 1 and tuple(ctl.doc.entitydb[h].dxf.start) == (0, 0, 0)
    assert read_records(ctl.doc) == {} and service.GUARD.reason is None


def test_no_write_failure_skips_undo(prepared):
    r = run(prepared, hooks=service.Hooks(before_change=boom))
    assert r.status is Status.FAILED and prepared[0].undo_calls == 0


def test_close_failure_never_undo_or_inverse(prepared):
    r = run(prepared, hooks=service.Hooks(before_end_undo=boom, before_inverse=boom))
    assert r.status is Status.UNVERIFIED and prepared[0].undo_calls == 0
    assert service.GUARD.reason


@pytest.mark.parametrize("point", ["record_write", "record_readback"])
def test_record_failure_keeps_verified_changes_and_guards(prepared, point):
    ctl, _, _, _, h, _ = prepared
    r = run(prepared, hooks=service.Hooks(**{point: boom}))
    assert r.status is Status.UNVERIFIED and service.GUARD.reason
    assert tuple(ctl.doc.entitydb[h].dxf.start) == (10, 20, 0) and ctl.undo_calls == 0
    assert run(prepared).status is Status.FAILED
    run(prepared, resume=True)
    assert service.GUARD.reason is None


@pytest.mark.parametrize("export_fails", [False, True])
def test_record_export_follows_readback_hook_and_counts_time(prepared, monkeypatch, export_fails):
    ctl, _, _, spec, _, _ = prepared
    original = ctl.export_dxf
    elapsed, hook_called = 0.0, False

    def before_export(c):
        nonlocal hook_called
        assert not c.open_group
        assert read_records(c.doc)[spec["op"]].result == "applied"
        hook_called = True

    def export(path, raise_on_error=False):
        nonlocal elapsed
        if Path(path).name == "record":
            assert hook_called and not ctl.open_group
            elapsed += 0.125
            if export_fails:
                raise RuntimeError("기록 사본 Export 실패")
        return original(path, raise_on_error=raise_on_error)

    monkeypatch.setattr(ctl, "export_dxf", export)
    monkeypatch.setattr(service.time, "perf_counter", lambda: elapsed)
    r = run(prepared, hooks=service.Hooks(record_readback=before_export))

    assert r.status is (Status.UNVERIFIED if export_fails else Status.SUCCESS), r.summary
    assert r.meta.timings_ms["record"] == 125
    saved = json.loads(Path(r.meta.result_path).read_text(encoding="utf-8"))
    assert saved["meta"]["timings_ms"]["record"] == 125
    assert ctl.undo_calls == 0
    if export_fails:
        assert "재읽기 실패(기록이 남았을 수 있음)" in r.summary and service.GUARD.reason
    else:
        exported = ezdxf.readfile(Path(r.meta.result_path).with_name("record.dxf"))
        assert read_records(exported) == read_records(ctl.doc)


@pytest.mark.parametrize("problem,reason", [
    ("root", "ARCAID 루트가 사전이 아님"),
    ("record", "적용 기록 머리말·버전이 다름"),
    ("missing", "쓴 적용 기록과 재읽은 값이 다름"),
    ("other_op", "쓴 적용 기록과 재읽은 값이 다름"),
    ("different", "쓴 적용 기록과 재읽은 값이 다름"),
])
def test_record_readback_rejects_bad_export_without_undo(prepared, monkeypatch, problem, reason):
    ctl, _, _, spec, h, _ = prepared
    original = ctl.export_dxf

    def export(path, raise_on_error=False):
        result = original(path, raise_on_error=raise_on_error)
        if Path(path).name == "record":
            # 원본 기록은 그대로 두고 Export 사본만 바꿔 재읽기 경로를 확인한다.
            doc = ezdxf.readfile(result["path"])
            root = doc.rootdict["ARCAID"]
            entry = root[spec["op"]]
            if problem == "root":
                del doc.rootdict["ARCAID"]
                doc.rootdict.add_xrecord("ARCAID")
            elif problem == "record":
                entry.reset([(1, "손상된 기록")])
            elif problem == "missing":
                del root[spec["op"]]
            else:
                strings = [t.value for t in entry.tags if t.code == 1]
                data = json.loads("".join(strings[1:]))
                if problem == "other_op":
                    del root[spec["op"]]
                    data["op"] = "other-op"
                    entry = root.add_xrecord("other-op")
                else:
                    data["targets"][h]["after_hash"] = "0" * 16
                entry.reset([(1, "arcaid-change-record/1"), (1, json.dumps(data))])
            doc.saveas(result["path"])
        return result

    monkeypatch.setattr(ctl, "export_dxf", export)
    r = run(prepared)

    assert Path(r.meta.result_path).with_name("record.dxf").is_file()
    assert r.status is Status.UNVERIFIED and service.GUARD.reason
    assert "재읽기 실패(기록이 남았을 수 있음)" in r.summary
    assert r.summary.endswith(reason)
    assert read_records(ctl.doc)[spec["op"]].result == "applied"
    assert tuple(ctl.doc.entitydb[h].dxf.start) == (10, 20, 0) and ctl.undo_calls == 0
    assert run(prepared).status is Status.FAILED


def test_post_snapshot_exception_sets_guard(prepared, monkeypatch):
    original = service.snapshot_active
    count = 0

    def snap(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("사후 읽기 실패")
        return original(*args)

    monkeypatch.setattr(service, "snapshot_active", snap)
    r = run(prepared)
    assert r.status is Status.UNVERIFIED and service.GUARD.reason


def test_concurrent_non_target_edit_is_detected_and_preserved(prepared):
    ctl, _, _, _, h, other = prepared
    r = run(prepared, hooks=service.Hooks(before_change=lambda c: setattr(c.doc.entitydb[other].dxf, "radius", 11)))
    assert r.status is Status.UNVERIFIED and service.GUARD.reason
    assert "적용 뒤 확인 불일치 1건" in r.summary
    assert "넣은 결과가 생성 DXF와 다름" not in r.summary
    assert ctl.doc.entitydb[other].dxf.radius == 11
    assert tuple(ctl.doc.entitydb[h].dxf.start) == (0, 0, 0)


def test_result_guards_before_save_failure(tmp_path, monkeypatch):
    from src.engine.apply import ApplyMeta
    from src.engine.results import Scope, ResultIds
    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(RuntimeError):
        service._result(Status.UNVERIFIED, "불명확", Scope(target="x"), ResultIds(), ApplyMeta(op="x"), tmp_path)
    assert service.GUARD.reason


@pytest.mark.parametrize("operation", ["draw", "fix"])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_0a_post_snapshot_exceptions_set_guard(prepared, monkeypatch, operation, error_type):
    ctl, work, std, _, h, _ = prepared
    original = service.snapshot_active
    count = 0

    def snap(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise error_type("사후 스냅샷 예외")
        return original(*args)

    monkeypatch.setattr(service, "snapshot_active", snap)
    if operation == "draw":
        spec = dict(schema_version=1, standard=std.ref, op="draw-2", units="mm", scale=100,
                    items=[dict(kind="line", id="line", layer="WAL", start=[0, 1], end=[100, 1])])
        r = service.draw_batch(ctl, work, std, spec)
    else:
        ctl.set_change_props(h, color=1)
        r = service.fix_standard(ctl, work, std, k=100, overwrite_manual=True)
    assert r.status is Status.UNVERIFIED and service.GUARD.reason


@pytest.mark.parametrize("fail_inverse", [False, True])
def test_inverse_only_after_confirmed_move(prepared, monkeypatch, fail_inverse):
    ctl, _, _, _, h, _ = prepared
    monkeypatch.setattr(ctl, "undo_once", lambda: {"ok": True})  # UNDO가 값만 덜 돌린 CAD를 모사
    hooks = service.Hooks(after_nth_change=boom, before_inverse=boom if fail_inverse else None)
    r = run(prepared, hooks=hooks)
    if fail_inverse:
        assert r.status is Status.UNVERIFIED and service.GUARD.reason
    else:
        assert r.status is Status.RECOVERED and tuple(ctl.doc.entitydb[h].dxf.start) == (0, 0, 0)


def test_partial_write_that_raises_is_rolled_back(prepared, monkeypatch):
    ctl, _, _, _, h, _ = prepared
    original = ctl.move_handles

    def partial(*args):
        original(*args)
        raise RuntimeError("쓴 뒤 예외")

    monkeypatch.setattr(ctl, "move_handles", partial)
    r = run(prepared)
    assert r.status is Status.RECOVERED and ctl.undo_calls == 1
    assert tuple(ctl.doc.entitydb[h].dxf.start) == (0, 0, 0)


def test_timeout_guards_even_after_verified_restore(prepared):
    def timeout(*_):
        raise TimeoutError("COM 시간 초과")

    r = run(prepared, hooks=service.Hooks(after_nth_change=timeout))
    assert r.status is Status.UNVERIFIED and service.GUARD.reason


def test_root_record_error_is_conflict(prepared):
    ctl, _, _, _, _, _ = prepared
    ctl.doc.rootdict.add_xrecord("ARCAID")
    r = run(prepared)
    assert r.status is Status.CONFLICT and ctl.undo_calls == 0


def test_pre_snapshot_must_still_have_original_source(prepared):
    ctl, _, _, _, h, _ = prepared
    r = run(prepared, hooks=service.Hooks(before_apply=lambda c: setattr(c, "source", "C:/other.dwg")))
    assert r.status is Status.CONFLICT and ctl.undo_calls == 0
    assert tuple(ctl.doc.entitydb[h].dxf.start) == (0, 0, 0)


def test_new_violations_are_target_differences(prepared):
    ctl, work, std, spec, h, _ = prepared
    spec["changes"][0] = dict(id="set", select={"handles": [h]}, action="set", props={"color": 1})
    r = run(prepared)
    assert r.status is Status.SUCCESS and sum(r.new_violations.values()) == 1
    base, _ = snapshot_active(ctl, work)
    spec.update(op="manual-move", target={"snapshot_id": base.snapshot_id},
                changes=[dict(id="move", select={"handles": [h]}, action="move", delta=[10, 20])])
    r = run(prepared)
    assert r.status is Status.SUCCESS and r.new_violations == {}


@pytest.mark.parametrize("point", ["before", "after", "both"])
@pytest.mark.parametrize("incomplete", ["failed", "truncated"])
def test_incomplete_standard_check_reports_unchecked(prepared, monkeypatch, point, incomplete):
    from src.engine.check import CheckResult
    from src.engine.results import Scope

    original = service.check_standard
    prepared[3]["changes"][0] = dict(id="set", select={"handles": [prepared[4]]}, action="set", props={"color": 1})
    n = 0

    def check(*args, **kw):
        nonlocal n
        n += 1
        if point == "both" or n == (1 if point == "before" else 2):
            if incomplete == "failed":
                return CheckResult(status=Status.FAILED, summary="검사 실패", scope=Scope(target="시험"))
            return original(*args, **kw).model_copy(update={"truncated": True})
        return original(*args, **kw)

    monkeypatch.setattr(service, "check_standard", check)
    r = run(prepared)
    assert r.status is Status.SUCCESS and not r.is_complete()
    (u,) = r.unchecked
    assert u.item == "대상 새 위반 차분" and u.count is None
    expected = {} if incomplete == "failed" and point in ("after", "both") else {"COLOR_NOT_BYLAYER": 1}
    assert r.new_violations == expected
    assert f"새 위반 {sum(expected.values())}건, 미검사 1종" in r.summary


@pytest.mark.parametrize("target_text", [False, True])
def test_unchecked_text_outside_targets_is_omitted(prepared, target_text):
    ctl, work, std, spec, _, _ = prepared
    for _ in range(2):
        ctl.doc.modelspace().add_text("대상 밖", dxfattribs={"layer": "0"})
    if target_text:
        text = ctl.doc.modelspace().add_text("대상", dxfattribs={"layer": "ROM", "height": 135,
                                                                "style": std.text.style.name})
        base, _ = snapshot_active(ctl, work)
        spec["target"] = {"snapshot_id": base.snapshot_id}
        spec["changes"][0]["select"] = {"handles": [text.dxf.handle]}
    r = run(prepared)
    assert r.status is Status.SUCCESS and r.unchecked == [] and r.is_complete()
    assert r.new_violations == {} and "새 위반 0건" in r.summary


@pytest.mark.parametrize("action", ["move", "delete", "set"])
def test_target_unchecked_text_is_counted_once_and_keeps_new_violations(prepared, action):
    ctl, work, _, spec, _, _ = prepared
    texts = [ctl.doc.modelspace().add_text("문자", dxfattribs={"layer": "0"}) for _ in range(2)]
    base, _ = snapshot_active(ctl, work)
    change = dict(id="text", select={"handles": [texts[0].dxf.handle]}, action=action)
    if action == "move":
        change["delta"] = [10, 20]
    elif action == "set":
        change["props"] = {"color": 1}
    spec.update(target={"snapshot_id": base.snapshot_id}, changes=[change])
    r = run(prepared)
    assert r.status is Status.SUCCESS and not r.is_complete(), r.summary
    assert [u.model_dump() for u in r.unchecked] == [dict(item="높이 규칙이 없는 레이어의 문자",
                                                         reason="text.yaml에 그 레이어의 문자 높이가 없음", count=1)]
    assert r.new_violations == ({"COLOR_NOT_BYLAYER": 1} if action == "set" else {})
    assert f"새 위반 {int(action == 'set')}건, 미검사 1종" in r.summary


def test_unchecked_before_after_counts_union_of_target_handles(prepared):
    ctl, work, std, spec, _, _ = prepared
    ms = ctl.doc.modelspace()
    first = ms.add_text("규칙 없음", dxfattribs={"layer": "0"})
    second = ms.add_text("규칙 있음", dxfattribs={"layer": "ROM", "height": 135, "style": std.text.style.name})
    base, _ = snapshot_active(ctl, work)
    spec.update(target={"snapshot_id": base.snapshot_id}, changes=[
        dict(id="to-standard", select={"handles": [first.dxf.handle]}, action="set", props={"layer": "ROM"}),
        dict(id="from-standard", select={"handles": [second.dxf.handle]}, action="set", props={"layer": "0"})])
    r = run(prepared)
    assert r.status is Status.SUCCESS, r.summary
    (u,) = r.unchecked
    assert u.item == "높이 규칙이 없는 레이어의 문자" and u.count == 2
    assert r.new_violations == {"LAYER_NOT_DRAWABLE": 1, "TEXT_HEIGHT": 1}


def test_global_unchecked_is_retained_once(prepared, monkeypatch):
    from src.engine.results import Unchecked
    original = service.check_standard

    def check(*args, **kwargs):
        result = original(*args, **kwargs)
        key = ("전역", "도면 전체의 검사 전제")
        kwargs["_unchecked_handles"][key] = None
        result.unchecked.append(Unchecked(item=key[0], reason=key[1], count=2))
        return result

    monkeypatch.setattr(service, "check_standard", check)
    r = run(prepared)
    assert r.status is Status.SUCCESS and not r.is_complete()
    assert [(u.item, u.reason, u.count) for u in r.unchecked] == [("전역", "도면 전체의 검사 전제", 2)]


def test_insert_target_includes_attribute_check_results(prepared, monkeypatch):
    """검사기 경계에 속성 결과를 주입해 종속 핸들의 검사 범위를 확인한다."""
    from src.engine.results import Unchecked, Violation
    ctl, work, _, spec, _, outside = prepared
    block = ctl.doc.blocks.new("SCOPE_ATTRIBUTE")
    block.add_attdef("ID", (0, 0), text="옛 값", height=135)
    entity = ctl.doc.modelspace().add_blockref(block.name, (0, 0))
    entity.add_auto_attribs({"ID": "옛 값"})
    attribute = entity.attribs[0].dxf.handle
    base, _ = snapshot_active(ctl, work)
    spec.update(target={"snapshot_id": base.snapshot_id}, changes=[
        dict(id="attrib", select={"handles": [entity.dxf.handle]}, action="set", props={"attribs": {"ID": "새 값"}})])
    original = service.check_standard

    def check(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        key = ("속성 검사", "속성 규칙 없음")
        kwargs["_unchecked_handles"][key] = {attribute, outside}
        result.unchecked.append(Unchecked(item=key[0], reason=key[1], count=2))
        doc = ezdxf.readfile(path)
        if doc.entitydb[attribute].dxf.text == "새 값":
            result.violations.append(Violation(code="TEXT_HEIGHT", message="속성 문자 높이", handle=attribute))
        return result

    monkeypatch.setattr(service, "check_standard", check)
    r = run(prepared)
    assert r.status is Status.SUCCESS and not r.is_complete(), r.summary
    assert [(u.item, u.count) for u in r.unchecked] == [("속성 검사", 1)]
    assert r.new_violations == {"TEXT_HEIGHT": 1}


def test_undo_failure_stays_guarded_after_inverse(prepared, monkeypatch):
    monkeypatch.setattr(prepared[0], "undo_once", lambda: {"ok": False, "error": "원격 호출 실패"})
    r = run(prepared, hooks=service.Hooks(after_nth_change=boom))
    assert r.status is Status.UNVERIFIED and service.GUARD.reason


def test_export_preserves_timeout_type_even_without_message(prepared, monkeypatch):
    ctl = prepared[0]
    original = ctl.export_dxf

    def export(path, raise_on_error=False):
        if str(path).endswith("s1"):
            if raise_on_error:
                raise TimeoutError()
            return {"ok": False, "error": ""}
        return original(path)

    monkeypatch.setattr(ctl, "export_dxf", export)
    r = run(prepared)
    assert r.status is Status.UNVERIFIED and service.GUARD.reason


def test_requested_scale_controls_target_violation_difference(prepared):
    ctl, work, std, spec, _, _ = prepared
    e = ctl.doc.modelspace().add_text("표제", dxfattribs={"layer": "TIT1", "height": 330, "style": std.text.style.name})
    base, _ = snapshot_active(ctl, work)
    spec.update(target={"snapshot_id": base.snapshot_id}, changes=[dict(id="layer", select={"handles": [e.dxf.handle]},
                                                                      action="set", props={"layer": "TXT"})])
    r = run(prepared, k=200)
    assert r.status is Status.SUCCESS and r.new_violations == {"TEXT_HEIGHT": 1}
    assert r.meta.k == 200 and r.assumptions == []


def test_default_scale_is_disclosed(prepared):
    r = run(prepared)
    assert r.status is Status.SUCCESS and any(a.name == "scale" and a.value == r.meta.k for a in r.assumptions)


def test_record_creation_failure_never_rolls_back_verified_changes(prepared, monkeypatch):
    monkeypatch.setattr(prepared[0], "create_change_record", boom)
    r = run(prepared)
    assert r.status is Status.UNVERIFIED and service.GUARD.reason and "생성 실패" in r.summary
    assert prepared[0].undo_calls == 0


def test_result_write_failure_after_changes_is_guarded(prepared, monkeypatch):
    original = Path.write_text

    def write(path, *args, **kw):
        if path.name == "result.json":
            raise OSError("결과 저장 실패")
        return original(path, *args, **kw)

    monkeypatch.setattr(Path, "write_text", write)
    r = run(prepared)
    assert r.status is Status.UNVERIFIED and service.GUARD.reason and "결과 저장 실패" in r.summary
    assert prepared[0].undo_calls == 0


def test_inverse_refuses_target_whose_preserved_tags_changed(prepared, monkeypatch):
    ctl, _, _, _, h, _ = prepared
    monkeypatch.setattr(ctl, "undo_once", lambda: {"ok": True})

    def alter_tag(c, n):
        strings = [t.value for t in c.doc.entitydb[h].get_xdata("ARCAID")]
        c.set_xdata(h, [s if not s.startswith("custom=") else "custom=사람 수정" for s in strings])
        boom()

    r = run(prepared, hooks=service.Hooks(after_nth_change=alter_tag))
    assert r.status is Status.UNVERIFIED and service.GUARD.reason
    assert arcaid_tags(ctl.doc.entitydb[h])["custom"] == "사람 수정"
    assert tuple(ctl.doc.entitydb[h].dxf.start) == (10, 20, 0)


@pytest.fixture
def retained_insert_tags(prepared, monkeypatch):
    """속성값은 되돌리고 INSERT의 XDATA는 남기는 GstarCAD UNDO를 모사한다."""
    ctl, work, std, spec, _, other = prepared
    block = ctl.doc.blocks.new("B2_ATTRIBUTE")
    block.add_attdef("ID", (0, 0), text="옛 값", height=135)
    entity = ctl.doc.modelspace().add_blockref(block.name, (0, 0))
    entity.add_auto_attribs({"ID": "옛 값"})
    strings = ["op=draw", "custom=보존할 값", "oid=draw:block", "path=block", "ver=1", "in=123",
               f"state={state_hash(entity)}", "custom=마지막 값", "별도 문자열"]
    entity.set_xdata("ARCAID", [(1000, s) for s in strings])
    base, _ = snapshot_active(ctl, work)
    h = entity.dxf.handle
    spec.update(target={"snapshot_id": base.snapshot_id},
                changes=[dict(id="attrib", select={"handles": [h]}, action="set", props={"attribs": {"ID": "새 값"}})])
    original_undo, original_start, original_set = ctl.undo_once, ctl.start_undo, ctl.set_xdata
    groups, writes = [], []

    def undo():
        current = ctl.doc.entitydb.get(h)
        retained = list(current.get_xdata("ARCAID")) if current is not None else None
        result = original_undo()
        if retained is not None:
            ctl.doc.entitydb[h].set_xdata("ARCAID", retained)
        return result

    def start():
        groups.append(1)
        original_start()

    def set_xdata(handle, values):
        assert ctl.open_group
        writes.append((handle, list(values)))
        original_set(handle, values)
        if len(writes) == 1:
            boom()  # state 쓰기 직후 실패해도 UNDO는 속성값만 되돌린다.

    monkeypatch.setattr(ctl, "undo_once", undo)
    monkeypatch.setattr(ctl, "start_undo", start)
    monkeypatch.setattr(ctl, "set_xdata", set_xdata)
    return (ctl, work, std, spec, h, other), strings, groups, writes


def test_unclosed_group_never_restores_insert_tags(retained_insert_tags):
    p, strings, groups, writes = retained_insert_tags
    ctl, _, _, _, h, _ = p

    def fail_close(c):
        c.doc.entitydb[h].attribs[0].dxf.text = "옛 값"
        boom()

    result = run(p, hooks=service.Hooks(before_end_undo=fail_close))

    assert result.status is Status.UNVERIFIED and service.GUARD.reason
    assert ctl.undo_calls == 0 and len(groups) == len(writes) == 1
    assert [t.value for t in ctl.doc.entitydb[h].get_xdata("ARCAID")] != strings


@pytest.mark.parametrize("scenario", ["normal", "pre_tags", "non_target"])
def test_undo_retained_insert_tags_recover(retained_insert_tags, monkeypatch, scenario):
    p, strings, groups, writes = retained_insert_tags
    ctl, work, _, _, h, other = p
    hooks = service.Hooks()
    if scenario == "pre_tags":
        # 요청 기준 뒤 달라진 태그도 적용 직전 사본의 원문으로 되돌린다. state를 재계산하지 않는다.
        strings = ["state=적용 직전 저장값" if s.startswith("state=") else s for s in strings] + ["pre=추가 태그"]
        hooks.before_apply = lambda c: c.doc.entitydb[h].set_xdata("ARCAID", [(1000, s) for s in strings])
    elif scenario == "non_target":
        original_undo = ctl.undo_once

        def undo():
            result = original_undo()
            ctl.doc.entitydb[other].dxf.radius = 11
            return result

        monkeypatch.setattr(ctl, "undo_once", undo)
    result = run(p, hooks=hooks, overwrite_manual=scenario == "pre_tags")

    assert result.status is (Status.UNVERIFIED if scenario == "non_target" else Status.RECOVERED), result.summary
    assert ctl.doc.entitydb[h].attribs[0].dxf.text == "옛 값"
    assert [t.value for t in ctl.doc.entitydb[h].get_xdata("ARCAID")] == strings
    assert len(groups) == 2 and writes[-1] == (h, strings)
    assert "UNDO가 INSERT의 ARCAID 태그를 되돌리지 않아 태그만 원래 값으로 복원함(1개)" in result.warnings
    assert list(work.glob("apply/*/*/rollback_tags.dxf"))
    assert read_records(ctl.doc) == {}
    assert bool(service.GUARD.reason) == (scenario == "non_target")
    assert ctl.doc.entitydb[other].dxf.radius == (11 if scenario == "non_target" else 10)


@pytest.mark.parametrize("mismatch", ["value", "custom", "delete", "missing", "state_unchanged", "state_reordered"])
def test_tag_restore_refuses_ineligible_insert(retained_insert_tags, monkeypatch, mismatch):
    p, strings, groups, writes = retained_insert_tags
    ctl, _, _, spec, h, other = p
    original_undo = ctl.undo_once

    def undo():
        result = original_undo()
        entity = ctl.doc.entitydb[h]
        if mismatch == "value":
            entity.attribs[0].dxf.text = "사람이 바꾼 값"
        elif mismatch == "custom":
            tags = [s if not s.startswith("custom=") else "custom=사람 수정"
                    for s in [t.value for t in entity.get_xdata("ARCAID")]]
            entity.set_xdata("ARCAID", [(1000, s) for s in tags])
        elif mismatch == "delete":
            entity.set_xdata("ARCAID", [(1000, s) for s in strings if not s.startswith("state=")]
                             + [(1000, "state=남은 값")])
        elif mismatch == "missing":
            ctl.doc.modelspace().delete_entity(entity)
        elif mismatch == "state_reordered":
            entity.set_xdata("ARCAID", [(1000, s) for s in strings if not s.startswith("state=")]
                             + [(1000, next(s for s in strings if s.startswith("state=")))])
        else:
            entity.set_xdata("ARCAID", [(1000, s) for s in strings])
            ctl.doc.entitydb[other].dxf.radius = 11
        return result

    if mismatch == "delete":
        spec["changes"][0] = dict(id="delete", select={"handles": [h]}, action="delete")
    monkeypatch.setattr(ctl, "undo_once", undo)
    result = run(p, hooks=service.Hooks(after_nth_change=boom) if mismatch == "delete" else None)

    assert result.status is Status.UNVERIFIED and service.GUARD.reason
    assert len(groups) == 1 and len(writes) == (0 if mismatch == "delete" else 1)
    assert not any("태그만 원래 값으로 복원함" in w for w in result.warnings)
    if mismatch == "value":
        assert ctl.doc.entitydb[h].attribs[0].dxf.text == "사람이 바꾼 값"
    elif mismatch == "custom":
        assert arcaid_tags(ctl.doc.entitydb[h])["custom"] == "사람 수정"


def test_tag_restore_after_inverse_recovers(retained_insert_tags, monkeypatch):
    p, strings, groups, writes = retained_insert_tags
    ctl, _, _, _, h, _ = p
    original_set = ctl.set_xdata

    def undo():
        ctl.undo_calls += 1
        return {"ok": True}  # UNDO가 값을 덜 돌리면 기존 역변경을 먼저 시도한다.

    def set_xdata(handle, values):
        if len(writes) == 1:
            writes.append((handle, list(values)))  # 역변경 때도 state가 남는 경우를 모사한다.
        else:
            original_set(handle, values)

    monkeypatch.setattr(ctl, "undo_once", undo)
    monkeypatch.setattr(ctl, "set_xdata", set_xdata)
    result = run(p)

    assert result.status is Status.RECOVERED, result.summary
    assert ctl.doc.entitydb[h].attribs[0].dxf.text == "옛 값"
    assert [t.value for t in ctl.doc.entitydb[h].get_xdata("ARCAID")] == strings
    assert len(groups) == len(writes) == 3 and service.GUARD.reason is None
    assert any("태그만 원래 값으로 복원함(1개)" in w for w in result.warnings)


@pytest.mark.parametrize("failure", ["close", "write", "verify"])
def test_tag_restore_failure_guards(retained_insert_tags, monkeypatch, failure):
    p, _, groups, writes = retained_insert_tags
    ctl, work, _, _, _, other = p
    original_end, original_set = ctl.end_undo, ctl.set_xdata

    def end():
        closed = original_end()
        return False if failure == "close" and len(groups) == 2 else closed

    def set_xdata(handle, values):
        if len(writes) == 1:
            if failure == "write":
                boom()
            if failure == "verify":
                return  # 성공 반환만으로 복귀를 인정하면 안 된다.
        original_set(handle, values)

    monkeypatch.setattr(ctl, "end_undo", end)
    monkeypatch.setattr(ctl, "set_xdata", set_xdata)
    result = run(p)

    assert result.status is Status.UNVERIFIED and service.GUARD.reason
    assert len(groups) == 2 and not ctl.open_group
    assert ctl.doc.entitydb[other].dxf.radius == 10
    assert bool(list(work.glob("apply/*/*/rollback_tags.dxf"))) == (failure == "verify")
