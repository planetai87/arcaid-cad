"""B2 보호된 변경 시험. Claude가 새 C:\\arcaid\\work 사본에서만 실행한다."""
import copy
import json
import math
from pathlib import Path

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


@pytest.fixture
def curves_prepared(request, tmp_path):
    """원·호 사전 관문은 새 합성 사본에서만 준비하고 이전 작업 표시선을 별도 묶음으로 둔다."""
    assert tmp_path.resolve().is_relative_to(Path("C:/arcaid/work").resolve()), "새 C:/arcaid/work basetemp 필요"
    ctl, work, std = request.getfixturevalue("opened")
    seed = dict(schema_version=1, standard=std.ref, op="curve-seed", units="mm", scale=100, items=[])
    for n, action in enumerate(("move", "delete", "props")):
        for kind, y in (("circle", -30000), ("arc", -35000)):
            item = dict(kind=kind, id=f"{kind}-{action}", layer="WAL",
                        center=[3000 * n + .000123, y - .000456], radius=1250.000123)
            if kind == "arc":
                item.update(start_angle=359.999999, end_angle=123.456789)
            seed["items"].append(item)
    built = draw_batch(ctl, work, std, seed)
    assert built.status is Status.SUCCESS, built.summary
    doc = snap(ctl, tmp_path / "curve_seed")
    handles = {}
    for e in doc.modelspace():
        tags = arcaid_tags(e) or {}
        if tags.get("op") != seed["op"]:
            continue
        handles[tags["path"]] = e.dxf.handle
        # 생성 명세의 2D 좌표에 Z·두께를 보완해 COM Move가 보존하는지도 확인한다.
        entity = obj(ctl, e.dxf.handle)
        ctl._call(setattr, entity, "Center", ctl._pt((*tuple(e.dxf.center)[:2], 7.000789)))
        ctl._call(setattr, entity, "Thickness", 2.000123)
        ctl.set_xdata(e.dxf.handle, [t.value for t in e.get_xdata("ARCAID")] + ["custom=원호 보존"])
    assert len(handles) == 6
    ctl.start_undo()
    try:
        ms = ctl._call(getattr, ctl.doc, "ModelSpace")
        marker = ctl._call_attr(ms, "AddLine", ctl._pt((10000, -30000, 0)), ctl._pt((11000, -30000, 0)))
        handles["marker"] = str(ctl._call(getattr, marker, "Handle"))
    finally:
        assert ctl.end_undo()
    return ctl, work, std, handles


CURVE_DELTA = (600.000123, -300.000456)


def curve_changes(handles):
    items = []
    # 삭제를 먼저 지나고 이동·속성을 쓴 뒤 실패를 넣는다.
    for kind in ("circle", "arc"):
        items.append(change(handles[f"{kind}-delete"], "delete", f"{kind}-delete"))
        item = change(handles[f"{kind}-move"], id=f"{kind}-move")
        item["delta"] = list(CURVE_DELTA)
        items.append(item)
        items.append(change(handles[f"{kind}-props"], "set", f"{kind}-props",
                            props=dict(layer="CON", color={"rgb": [17, 83, 201]}, linetype="HIDDEN", lineweight=50)))
    return items


def assert_curve_result(before, after, handles, evidence_path):
    """엔진의 after 계산과 별개로 중심·반지름·끝점·네 표시 속성의 실제 Export 값을 확인한다."""
    errors = {}
    for kind in ("circle", "arc"):
        assert after.entitydb.get(handles[f"{kind}-delete"]) is None
        for action in ("move", "props"):
            key = f"{kind}-{action}"
            old, now = before.entitydb[handles[key]], after.entitydb[handles[key]]
            delta = (*CURVE_DELTA, 0) if action == "move" else (0, 0, 0)
            wanted = [v + d for v, d in zip(old.dxf.center, delta)]
            errors[key] = {"center_mm": math.dist(wanted, now.dxf.center),
                           "radius_mm": abs(old.dxf.radius - now.dxf.radius),
                           "thickness_mm": abs(old.dxf.thickness - now.dxf.thickness)}
            assert tuple(now.dxf.extrusion) == (0, 0, 1)
            if kind == "arc":
                for point in ("start_point", "end_point"):
                    wanted = [v + d for v, d in zip(getattr(old, point), delta)]
                    errors[key][point + "_mm"] = math.dist(wanted, getattr(now, point))
            assert arcaid_tags(now)["state"] == state_hash(now)
            assert arcaid_tags(now)["custom"] == "원호 보존"
            if action == "props":
                assert (now.dxf.layer, now.dxf.true_color, now.dxf.linetype.upper(), now.dxf.lineweight) == (
                    "CON", 0x1153C9, "HIDDEN", 50)
    evidence_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    assert all(error <= .01 for values in errors.values() for error in values.values()), errors
    assert changes.fingerprint(before.entitydb[handles["marker"]]) == changes.fingerprint(after.entitydb[handles["marker"]])


def test_curve_gate_success_then_undo_restores_deleted_handles_and_tags(curves_prepared, tmp_path):
    """사전 관문: 성공한 변경을 UNDO 1 한 번으로 되돌린다. 재전송은 이 사본에서 하지 않는다."""
    ctl, work, std, h = curves_prepared
    spec = base_request(ctl, work, std, curve_changes(h), op="curve-undo")
    before = snap(ctl, tmp_path / "curve_before")
    plan = changes.resolve(changes.parse_changes(spec), before, std, spec["target"]["snapshot_id"])
    result = apply_changes(ctl, work, std, spec)
    assert result.status is Status.SUCCESS, result.summary
    post = snap(ctl, tmp_path / "curve_applied")
    assert changes.verify_applied(plan, before, post) == []
    assert_curve_result(before, post, h, tmp_path / "curve-move-errors.json")
    assert ctl.undo_once()["ok"]
    restored = snap(ctl, tmp_path / "curve_undone")
    assert changes.verify_restored(plan, before, restored) == []
    for name, handle in h.items():
        assert restored.entitydb[handle].dxf.handle == before.entitydb[handle].dxf.handle
        if name != "marker":
            assert list(restored.entitydb[handle].get_xdata("ARCAID")) == list(before.entitydb[handle].get_xdata("ARCAID"))


@pytest.mark.parametrize("point", ["after_nth_change", "before_setxdata"])
def test_curve_gate_failure_recovers_deleted_handles_tags_and_marker(curves_prepared, tmp_path, point):
    ctl, work, std, h = curves_prepared
    spec = base_request(ctl, work, std, curve_changes(h), op="curve-recovery")
    before = snap(ctl, tmp_path / "curve_failure_before")
    plan = changes.resolve(changes.parse_changes(spec), before, std, spec["target"]["snapshot_id"])

    def after_nth(c, n):
        if n == len(plan.targets):
            boom(c)

    result = apply_changes(ctl, work, std, spec,
                           hooks=Hooks(**{point: after_nth if point == "after_nth_change" else boom}))
    assert result.status is Status.RECOVERED, result.summary
    restored = snap(ctl, tmp_path / "curve_recovered")
    assert changes.verify_restored(plan, before, restored) == []
    assert changes.read_records(restored) == changes.read_records(before)
    for kind in ("circle", "arc"):
        handle = h[f"{kind}-delete"]
        assert restored.entitydb[handle].dxf.handle == handle
        assert list(restored.entitydb[handle].get_xdata("ARCAID")) == list(before.entitydb[handle].get_xdata("ARCAID"))
    assert changes.fingerprint(before.entitydb[h["marker"]]) == changes.fingerprint(restored.entitydb[h["marker"]])


def test_curve_success_rerun_skips_without_undo(curves_prepared, tmp_path):
    """성공한 원래 명세를 그대로 재전송한다. UNDO 관문과 별도 사본이다."""
    ctl, work, std, h = curves_prepared
    spec = base_request(ctl, work, std, curve_changes(h), op="curve-rerun")
    before = snap(ctl, tmp_path / "curve_rerun_before")
    result = apply_changes(ctl, work, std, spec)
    assert result.status is Status.SUCCESS, result.summary
    post = snap(ctl, tmp_path / "curve_rerun_applied")
    assert_curve_result(before, post, h, tmp_path / "curve-rerun-errors.json")
    rerun = apply_changes(ctl, work, std, spec)
    assert rerun.status is Status.SUCCESS and rerun.meta.decision == "skip", rerun.summary
    after = snap(ctl, tmp_path / "curve_rerun_skipped")
    assert {handle: changes.fingerprint(e) for handle, e in changes.drawing_entities(after).items()} == {
        handle: changes.fingerprint(e) for handle, e in changes.drawing_entities(post).items()}
    assert changes.read_records(after) == changes.read_records(post)
