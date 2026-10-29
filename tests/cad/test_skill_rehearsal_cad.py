"""T9 합성 리허설: 문서 예제·보호된 수정·실패 복구. 사용자 도면은 fixture가 보존한다."""
import copy
import json
from pathlib import Path

import ezdxf
import pytest

from src import apply_service, tools_service
from src.engine.check import check_standard
from src.engine.read import read_drawing
from src.engine.results import Status
from src.engine.standards import load_standard
from src.engine.state import arcaid_tags
from src.snapshot_service import snapshot_active

EXAMPLE = (Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard"
           / "references" / "examples" / "synthetic-plan.json")
CHANGE_EXAMPLE = EXAMPLE.with_name("synthetic-change.json")


@pytest.fixture
def fresh(cad, tmp_path):
    ctl, _ = cad
    std = load_standard()
    apply_service.GUARD.reason = None
    assert tools_service.new_from_template(ctl, std, str(tmp_path / "t9-synthetic.dwg")).status is Status.SUCCESS
    try:
        yield ctl, tmp_path / "work", std
    finally:
        apply_service.GUARD.reason = None


def snap(ctl, work):
    meta, _ = snapshot_active(ctl, work)
    return meta, ezdxf.readfile(meta.dxf_path)


def handles(doc):
    return {e.dxf.handle for e in doc.modelspace()}


def fail_after_change(_):
    raise RuntimeError("T9 시험용 변경 직후 실패")


def test_documented_example_replay_conflict_and_recovery(fresh):
    ctl, work, std = fresh
    before, doc = snap(ctl, work)
    assert len(doc.modelspace()) == 0
    assert read_drawing(Path(before.dxf_path), std).rooms == []
    spec = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    spec["target"] = {"drawing_id": before.drawing_id, "snapshot_id": before.snapshot_id}
    result = apply_service.draw_batch(ctl, work, std, spec)
    assert result.status is Status.SUCCESS and result.applied[0].count == 27, result.summary
    after, doc = snap(ctl, work)
    expected_handles = handles(doc)
    assert len(expected_handles) == 27
    read = read_drawing(Path(after.dxf_path), std)
    assert len(read.rooms) == 4 and len(read.grid) == 7
    checked = check_standard(Path(after.dxf_path), std, 100)
    assert checked.violations == [] and checked.unchecked == []

    again = apply_service.draw_batch(ctl, work, std, spec)
    assert again.status is Status.SUCCESS and again.meta.decision == "skip"
    assert handles(snap(ctl, work)[1]) == expected_handles
    changed = copy.deepcopy(spec)
    changed["items"][2]["items"][1]["content"] = "다른 객실 {no}"
    assert apply_service.draw_batch(ctl, work, std, changed).status is Status.CONFLICT
    assert handles(snap(ctl, work)[1]) == expected_handles

    failure = copy.deepcopy(spec)
    failure["op"] = "t9-failure-injection"
    recovered = apply_service.draw_batch(ctl, work, std, failure,
                                        hooks=apply_service.Hooks(after_change=fail_after_change))
    assert recovered.status is Status.RECOVERED, recovered.summary
    assert handles(snap(ctl, work)[1]) == expected_handles
    assert apply_service.GUARD.reason is None


def test_protected_fix_rollback_then_success(fresh):
    ctl, work, std = fresh
    # 고장 주입 전용: 태그 없는 새 시험 객체에 위반을 넣는다. 실제 작도 절차가 아니다.
    entity = ctl.draw_line((0, -2000), (1000, -2000), layer="WAL", color=1)
    handle = ctl._safe_attr(entity, "Handle")
    ctl.set_props(handle, linetype="HIDDEN", lineweight=50)
    before, doc = snap(ctl, work)
    checked = check_standard(Path(before.dxf_path), std, 100)
    assert {v.code for v in checked.violations} == {"COLOR_NOT_BYLAYER", "LINETYPE_NOT_BYLAYER", "LINEWEIGHT_NOT_BYLAYER"}
    recovered = apply_service.fix_standard(ctl, work, std, 100, base_snapshot_id=before.snapshot_id,
                                           hooks=apply_service.Hooks(after_change=fail_after_change))
    assert recovered.status is Status.RECOVERED, recovered.summary
    now = snap(ctl, work)[1]
    assert handles(now) == handles(doc)
    assert now.entitydb[handle].dxf.color == 1
    assert now.entitydb[handle].dxf.lineweight == 50
    fixed = apply_service.fix_standard(ctl, work, std, 100)
    assert fixed.status is Status.SUCCESS, fixed.summary
    final, _ = snap(ctl, work)
    checked = check_standard(Path(final.dxf_path), std, 100)
    assert checked.violations == [] and checked.unchecked == []


def test_documented_change_example_replay_check_and_save(fresh):
    ctl, work, std = fresh
    initial, _ = snap(ctl, work)
    spec = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    spec["target"] = {"drawing_id": initial.drawing_id, "snapshot_id": initial.snapshot_id}
    drawn = apply_service.draw_batch(ctl, work, std, spec)
    assert drawn.status is Status.SUCCESS and drawn.unchecked == [], drawn.summary

    # 그린 뒤 얻은 실제 기준을 한 번만 채운다. 재전송에도 이 명세를 그대로 쓴다.
    base, base_doc = snap(ctl, work)
    data = json.loads(CHANGE_EXAMPLE.read_text(encoding="utf-8"))
    assert data["target"]["snapshot_id"] == "<export_snapshot의 snapshot_id>"
    data["target"]["snapshot_id"] = base.snapshot_id
    result = apply_service.apply_changes(ctl, work, std, data, k=spec["scale"])
    assert result.status is Status.SUCCESS and result.meta.decision == "apply", result.summary
    assert {a.action: a.count for a in result.applied} == {"move": 1, "set": 2, "delete": 1}
    assert result.new_violations == {} and result.unchecked == []
    assert result.ids.request_id == data["op"]
    again = apply_service.apply_changes(ctl, work, std, data, k=spec["scale"])
    assert again.status is Status.SUCCESS and again.meta.decision == "skip", again.summary
    assert again.unchecked == []

    final, doc = snap(ctl, work)
    read = read_drawing(Path(final.dxf_path), std)
    assert {r.text: tuple(r.position) for r in read.rooms} == {
        "객실 101": (2100, 2000), "회의실 102": (4500, 2000), "객실 103": (1500, 8000)}
    bubbles = [e for e in doc.modelspace().query("INSERT") if e.dxf.name == "축열버블-상"]
    assert [e.get_attrib_text("축열") for e in bubbles] == ["X1", "X2", "X3-변경"]
    deleted = next(e.dxf.handle for e in base_doc.modelspace()
                   if (arcaid_tags(e) or {}).get("oid") == "skill-synthetic-plan:room[3]/label")
    assert handles(doc) == handles(base_doc) - {deleted}
    for kind, count in (("LWPOLYLINE", 6), ("HATCH", 4)):   # 합쳐진 벽 고리 6(바깥 2·섬 4), 바닥 해치 4
        assert len(doc.modelspace().query(kind)) == count
    checked = check_standard(Path(final.dxf_path), std, spec["scale"])
    assert checked.status is Status.SUCCESS
    assert checked.violations == [] and checked.unchecked == []
    saved = work.parent / "synthetic-changed.dwg"
    assert ctl.save_drawing(str(saved))
    assert saved.is_file()
