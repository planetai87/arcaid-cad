"""B2 보호된 변경 시험. Claude가 새 C:\\arcaid\\work 사본에서만 실행한다."""
import copy

import ezdxf
import pytest

from src import apply_service as service
from src.apply_service import Hooks, apply_changes, draw_batch, fix_standard
from src.engine import changes
from src.engine.results import Status
from src.engine.state import arcaid_tags, state_hash
from src.snapshot_service import snapshot_active


BUILD = dict(schema_version=1, standard="heo-2026@2026.1", op="b2-seed", units="mm", scale=100,
             items=[dict(kind="line", id="line", layer="WAL", start=[0, -20000], end=[1000, -20000]),
                    dict(kind="polyline", id="poly", layer="WAL", points=[[2000, -20000], [3000, -20000], [3000, -19000]]),
                    dict(kind="text", id="text", layer="ROM", role="general", content="기준 문자", at=[4000, -20000], align="MC"),
                    dict(kind="block", id="block", name="축열버블-상", at=[5000, -20000], attribs={"축열": "X8"}),
                    dict(kind="line", id="delete", layer="WAL", start=[0, -22000], end=[1000, -22000])])


def snap(ctl, path):
    result = ctl.export_dxf(str(path))
    assert result["ok"], result
    return ezdxf.readfile(result["path"])


def obj(ctl, handle):
    return ctl._call_attr(ctl.doc, "HandleToObject", handle)


def boom(*_):
    raise RuntimeError("B2 일부러 낸 실패")


def base_request(ctl, work, std, items, op="b2-change"):
    meta, _ = snapshot_active(ctl, work)
    return dict(schema_version=1, standard=std.ref, op=op,
                target=dict(snapshot_id=meta.snapshot_id, drawing_id=meta.drawing_id), changes=items)


def change(handle, action="move", id="c1", **kw):
    return dict(id=id, select={"handles": [handle]}, action=action, **({"delta": [600, 300]} if action == "move" else {}), **kw)


@pytest.fixture
def prepared(opened, tmp_path):
    ctl, work, std = opened
    built = draw_batch(ctl, work, std, BUILD)
    assert built.status is Status.SUCCESS, built.summary
    doc = snap(ctl, tmp_path / "seed")
    handles = {arcaid_tags(e)["path"]: e.dxf.handle for e in doc.modelspace()
               if (arcaid_tags(e) or {}).get("op") == BUILD["op"]}
    ms = ctl._call(getattr, ctl.doc, "ModelSpace")
    # 사람 객체와 이전 작업 표시선은 COM으로만 만든다. 이 표시선이 실패 뒤에도 남아야 한다.
    for name, x in (("human", 8000), ("marker", 10000)):
        entity = ctl._call_attr(ms, "AddLine", ctl._pt((x, -20000, 0)), ctl._pt((x + 1000, -20000, 0)))
        handles[name] = str(ctl._call(getattr, entity, "Handle"))
    return ctl, work, std, handles


def test_success_all_changes_records_and_rerun(prepared, tmp_path):
    ctl, work, std, h = prepared
    items = [change(h["line"]), change(h["delete"], "delete", "delete"),
             change(h["poly"], "set", "props", props=dict(layer="CON", color={"rgb": [17, 83, 201]}, linetype="HIDDEN", lineweight=50)),
             change(h["text"], "set", "text", props=dict(text="길이가 다른 새 문자", color=1)),
             change(h["block"], "set", "attrib", props=dict(attribs={"축열": "Y99"})),
             change(h["human"], "set", "aci", props=dict(color=2))]
    spec = base_request(ctl, work, std, items)
    before = snap(ctl, tmp_path / "before")
    plan = changes.resolve(changes.parse_changes(spec), before, std, spec["target"]["snapshot_id"])
    result = apply_changes(ctl, work, std, spec)
    assert result.status is Status.SUCCESS, result.summary
    post = snap(ctl, tmp_path / "post")
    assert changes.verify_applied(plan, before, post) == []
    assert post.entitydb[h["poly"]].dxf.true_color == 0x1153C9
    assert post.entitydb[h["text"]].dxf.text == "길이가 다른 새 문자"
    assert post.entitydb[h["block"]].attribs[0].dxf.text == "Y99"
    assert post.entitydb.get(h["delete"]) is None
    assert result.new_violations and not result.unchecked
    for key in ("line", "poly", "text", "block"):
        e = post.entitydb[h[key]]
        assert arcaid_tags(e)["state"] == state_hash(e)
    record = changes.read_records(post)[spec["op"]]
    assert record.targets[h["line"]].after_hash == changes.state_digest(changes.change_state(post.entitydb[h["line"]]))
    assert apply_changes(ctl, work, std, spec).meta.decision == "skip"
    altered = copy.deepcopy(spec)
    altered["changes"][0]["delta"] = [700, 300]
    assert apply_changes(ctl, work, std, altered).status is Status.CONFLICT


def test_manual_before_and_after_base(prepared, tmp_path):
    ctl, work, std, h = prepared
    # 기준에 포함된 수동 수정은 overwrite로 허용하고, 기준 뒤 수정은 허용하지 않는다.
    ctl.set_props(h["line"], color=1)
    spec = base_request(ctl, work, std, [change(h["line"])])
    assert apply_changes(ctl, work, std, spec).status is Status.CONFLICT
    result = apply_changes(ctl, work, std, spec, overwrite_manual=True)
    assert result.status is Status.SUCCESS, result.summary
    spec = base_request(ctl, work, std, [change(h["line"])], op="b2-after-base")
    ctl.set_props(h["line"], color=2)
    assert apply_changes(ctl, work, std, spec, overwrite_manual=True).status is Status.CONFLICT


def test_non_target_edit_after_judgment_is_preserved(prepared, tmp_path):
    ctl, work, std, h = prepared
    spec = base_request(ctl, work, std, [change(h["line"])])
    before = snap(ctl, tmp_path / "before")
    result = apply_changes(ctl, work, std, spec,
                           hooks=Hooks(before_change=lambda c: c.set_props(h["human"], color=1)))
    assert result.status is Status.UNVERIFIED and service.GUARD.reason
    after = snap(ctl, tmp_path / "after")
    assert changes.same_state(changes.change_state(before.entitydb[h["line"]]), changes.change_state(after.entitydb[h["line"]]))
    assert after.entitydb[h["human"]].dxf.color == 1
    assert changes.fingerprint(before.entitydb[h["marker"]]) == changes.fingerprint(after.entitydb[h["marker"]])


@pytest.mark.parametrize("side", ["source", "destination"])
@pytest.mark.parametrize("flag", ["Lock", "Freeze", "LayerOn"])
def test_resolve_refuses_layer_flags(prepared, side, flag):
    ctl, work, std, h = prepared
    name = "WAL" if side == "source" else "CON"
    layers = ctl._call(getattr, ctl.doc, "Layers")
    layer = ctl._call_attr(layers, "Item", name)
    try:
        ctl._call(setattr, layer, flag, flag != "LayerOn")
        spec = base_request(ctl, work, std, [change(h["line"], "set", props={"layer": "CON"})])
        result = apply_changes(ctl, work, std, spec)
        assert result.status is Status.FAILED and any(v.code == "CHG_LAYER" for v in result.violations)
    finally:
        ctl._call(setattr, layer, flag, flag == "LayerOn")


def test_recheck_refuses_layer_locked_after_base(prepared):
    ctl, work, std, h = prepared
    spec = base_request(ctl, work, std, [change(h["line"])])
    layers = ctl._call(getattr, ctl.doc, "Layers")
    layer = ctl._call_attr(layers, "Item", "WAL")
    try:
        result = apply_changes(ctl, work, std, spec,
                               hooks=Hooks(before_apply=lambda c: c._call(setattr, layer, "Lock", True)))
        assert result.status is Status.CONFLICT and "현재 사본" in result.summary
    finally:
        ctl._call(setattr, layer, "Lock", False)


def test_constant_attribute_and_group_are_rejected(prepared, tmp_path):
    import pythoncom
    import win32com.client

    ctl, work, std, h = prepared
    d = ezdxf.new("R2013")
    b = d.blocks.new("B2_CONSTANT")
    b.add_attdef("CONST", (0, 0), text="고정값", height=135, dxfattribs={"flags": 2})
    d.modelspace().add_blockref(b.name, (12000, -20000)).add_auto_attribs({"CONST": "고정값"})
    path = tmp_path / "constant.dxf"
    d.saveas(path)
    ctl.import_dxf(path)
    doc = snap(ctl, tmp_path / "constant_before")
    insert = next(e for e in doc.modelspace() if e.dxftype() == "INSERT" and e.dxf.name == b.name)
    spec = base_request(ctl, work, std, [change(insert.dxf.handle, "set", props={"attribs": {"CONST": "새 값"}})])
    result = apply_changes(ctl, work, std, spec)
    assert result.status is Status.FAILED and any(v.code == "CHG_ATTRIB" for v in result.violations)
    groups = ctl._call(getattr, ctl.doc, "Groups")
    group = ctl._call_attr(groups, "Add", "B2_GROUP")
    members = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [obj(ctl, h["line"])])
    ctl._call_attr(group, "AppendItems", members)
    spec = base_request(ctl, work, std, [change(h["line"])])
    result = apply_changes(ctl, work, std, spec)
    assert result.status is Status.FAILED and any(v.code == "CHG_RELATION" for v in result.violations)


@pytest.mark.parametrize("point", ["after_nth_change", "before_setxdata"])
def test_failure_recovers_deleted_handles_tags_and_marker(prepared, tmp_path, point):
    ctl, work, std, h = prepared
    spec = base_request(ctl, work, std, [change(h["delete"], "delete", "delete"), change(h["block"], id="move")])
    before = snap(ctl, tmp_path / "before")
    plan = changes.resolve(changes.parse_changes(spec), before, std, spec["target"]["snapshot_id"])

    def after_nth(c, n):
        if n == 2:
            boom(c)

    result = apply_changes(ctl, work, std, spec,
                           hooks=Hooks(**{point: after_nth if point == "after_nth_change" else boom}))
    assert result.status is Status.RECOVERED, result.summary
    after = snap(ctl, tmp_path / "after")
    assert changes.verify_restored(plan, before, after) == []
    assert spec["op"] not in changes.read_records(after)
    assert changes.fingerprint(before.entitydb[h["marker"]]) == changes.fingerprint(after.entitydb[h["marker"]])


def test_before_change_skips_undo(prepared, monkeypatch):
    ctl, work, std, h = prepared
    spec = base_request(ctl, work, std, [change(h["line"])])
    calls = []
    monkeypatch.setattr(ctl, "undo_once", lambda: calls.append(1))
    assert apply_changes(ctl, work, std, spec, hooks=Hooks(before_change=boom)).status is Status.FAILED
    assert calls == []


def test_close_failure_never_sends_undo(prepared, monkeypatch):
    ctl, work, std, h = prepared
    spec = base_request(ctl, work, std, [change(h["line"])])
    calls = []
    monkeypatch.setattr(ctl, "undo_once", lambda: calls.append(1))
    try:
        result = apply_changes(ctl, work, std, spec, hooks=Hooks(before_end_undo=boom))
        assert result.status is Status.UNVERIFIED and service.GUARD.reason and calls == []
    finally:
        ctl.end_undo()  # 시험이 남긴 열린 묶음만 닫는다. 제품 경로는 추가 호출하지 않는다.


def test_inverse_failure_guards(prepared, monkeypatch):
    ctl, work, std, h = prepared
    spec = base_request(ctl, work, std, [change(h["line"])])
    monkeypatch.setattr(ctl, "undo_once", lambda: {"ok": True})
    result = apply_changes(ctl, work, std, spec, hooks=Hooks(after_nth_change=boom, before_inverse=boom))
    assert result.status is Status.UNVERIFIED and service.GUARD.reason


@pytest.mark.parametrize("point", ["record_write", "record_readback"])
def test_record_failure_guards_without_undo_then_resume(prepared, tmp_path, point):
    ctl, work, std, h = prepared
    spec = base_request(ctl, work, std, [change(h["line"])])
    result = apply_changes(ctl, work, std, spec, hooks=Hooks(**{point: boom}))
    assert result.status is Status.UNVERIFIED and service.GUARD.reason
    after = snap(ctl, tmp_path / "after")
    assert list(after.entitydb[h["line"]].dxf.start) == [600, -19700, 0]
    assert apply_changes(ctl, work, std, spec).status is Status.FAILED
    # 기록 실패 뒤 같은 요청을 재적용하지 않는다. 확인한 사람이 새 기준으로 resume한다.
    next_spec = base_request(ctl, work, std, [change(h["human"])], op="b2-resume")
    result = apply_changes(ctl, work, std, next_spec, resume=True)
    assert result.status is Status.SUCCESS and service.GUARD.reason is None, result.summary


@pytest.mark.parametrize("operation", ["draw", "fix"])
def test_0a_post_snapshot_failure_guards(prepared, monkeypatch, operation):
    ctl, work, std, h = prepared
    original = service.snapshot_active
    count = 0

    def fail_post(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("B2 사후 스냅샷 실패 주입")
        return original(*args)

    monkeypatch.setattr(service, "snapshot_active", fail_post)
    if operation == "draw":
        spec = copy.deepcopy(BUILD)
        spec["op"] = "b2-guard-regression"
        result = draw_batch(ctl, work, std, spec)
    else:
        ctl.set_props(h["human"], color=1)
        result = fix_standard(ctl, work, std, k=100)
    assert result.status is Status.UNVERIFIED and service.GUARD.reason
