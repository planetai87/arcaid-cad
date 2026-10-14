"""보호된 적용 경로 CAD 연동 테스트(GstarCAD 실행 중, ARCAID_CAD_TESTS=1)

T6 완료 기준: 재실행(이미 완료·다른 입력·일부만), 일부러 낸 실패의 복귀 확인, 손으로 고친 엔진 객체 충돌,
보호된 경로로 바꾼 것은 잡히지 않음, 적용 직전 확인(단위·같은 이름 정의·읽은 뒤 바뀜), fix.
"""
import copy

import ezdxf
import pytest

from src import apply_service
from src.apply_service import Hooks, draw_batch, fix_standard
from src.engine.apply import judge_rerun
from src.engine.results import Status
from src.engine.spec import parse_spec, spec_hash
from src.engine.standards import load_standard
from src.engine.state import arcaid_tags

OP = "t6-cad"
SPEC = {
    "schema_version": 1, "standard": "heo-2026@2026.1", "op": OP, "units": "mm", "scale": 100,
    "grid": {"X": {"X1": 0, "X2": 6000, "X3": 12000}, "Y": {"Y1": 0, "Y2": 6000}},
    "items": [
        {"kind": "group", "id": "room", "path": "t6/room/{no}", "items": [
            {"kind": "polyline", "id": "wall", "layer": "WAL", "points": [[0, 0], [3000, 0], [3000, 4000], [0, 4000]],
             "closed": True},
            {"kind": "text", "id": "name", "layer": "ROM", "role": "general", "content": "객실 {no}", "at": [1500, 2000],
             "align": "MC"},
            {"kind": "hatch", "id": "floor", "layer": "HAT", "pattern": "ANSI31", "pattern_scale": 50,
             "boundary": [[200, 200], [1200, 200], [1200, 1200], [200, 1200]],
             "holes": [[[500, 500], [900, 500], [900, 900], [500, 900]]]}],
         "place": [{"at": ["X1", "Y1-10000"], "repeat": {"count": 2, "step": [3000, 0]}, "vars": {"no": {"start": 101}}}]},
        {"kind": "block", "id": "arrow", "name": "계단화살표", "at": [7000, -9000]},
        {"kind": "block", "id": "bubble", "name": "축열버블-상", "at": ["X3", "Y1-12000"], "attribs": {"축열": "X9"}},
        {"kind": "dim", "id": "d", "style": "DIM330", "p1": ["X1", "Y1-10000"], "p2": ["X1+6000", "Y1-10000"],
         "direction": "x", "line_at": "Y1-11000"},
    ],
}
COUNT = 9


@pytest.fixture(autouse=True)
def reset_guard():
    apply_service.GUARD.reason = None
    yield
    apply_service.GUARD.reason = None


@pytest.fixture
def opened(cad, synthetic_dwg, tmp_path):
    ctl, _ = cad
    assert ctl.open_drawing(str(synthetic_dwg))
    return ctl, tmp_path / "work", load_standard()


def snap(ctl, tmp_path, name):
    r = ctl.export_dxf(str(tmp_path / name))
    assert r["ok"], r
    return ezdxf.readfile(r["path"])


def handles(doc):
    return {e.dxf.handle for e in doc.modelspace()}


def handle_of(doc, oid):
    return next(e.dxf.handle for e in doc.modelspace() if (arcaid_tags(e) or {}).get("oid") == oid)


def com(ctl, handle):
    return ctl._call_attr(ctl.doc, "HandleToObject", handle)


def test_insert_rerun_and_conflicts(opened, tmp_path):
    ctl, work, std = opened
    r = draw_batch(ctl, work, std, SPEC)
    assert r.status is Status.SUCCESS, r.summary
    assert r.applied[0].count == COUNT and r.new_violations == {}
    doc = snap(ctl, tmp_path, "after1")
    expected = {f"{OP}:{x}" for x in ("room[0]/wall", "room[0]/name", "room[0]/floor", "room[1]/wall",
                                       "room[1]/name", "room[1]/floor", "arrow", "bubble", "d")}
    assert judge_rerun(doc, OP, spec_hash(parse_spec(SPEC)), expected).action == "skip"
    after1 = handles(doc)

    again = draw_batch(ctl, work, std, SPEC)
    assert again.status is Status.SUCCESS and again.meta.decision == "skip" and "이미 완료" in again.summary
    assert handles(snap(ctl, tmp_path, "after2")) == after1

    ctl.draw_line((0, -20000), (1000, -20000), layer="WAL")   # 다른 작업 뒤 저장해도 그대로
    ctl._call_attr(ctl.doc, "Save")
    assert draw_batch(ctl, work, std, SPEC).meta.decision == "skip"
    before = handles(snap(ctl, tmp_path, "after3"))

    changed = copy.deepcopy(SPEC)
    changed["items"][0]["items"][1]["content"] = "방 {no}"
    r = draw_batch(ctl, work, std, changed)
    assert r.status is Status.CONFLICT and "다른 입력" in r.summary
    assert handles(snap(ctl, tmp_path, "after4")) == before

    ctl.erase_entity(handle_of(snap(ctl, tmp_path, "s5"), f"{OP}:room[1]/floor"))
    r = draw_batch(ctl, work, std, SPEC)
    assert r.status is Status.CONFLICT and "일부만" in r.summary


def test_manual_edits_conflict_and_protected_fix_is_accepted(opened, tmp_path):
    ctl, work, std = opened
    assert draw_batch(ctl, work, std, SPEC).status is Status.SUCCESS
    doc = snap(ctl, tmp_path, "s0")
    wall = handle_of(doc, f"{OP}:room[0]/wall")
    ctl.set_props(wall, color=1)                       # 손으로 색을 바꿈(표준 위반이기도 함)
    r = draw_batch(ctl, work, std, SPEC)
    assert r.status is Status.CONFLICT and "손으로" in r.summary
    r = fix_standard(ctl, work, std, 100)
    assert r.status is Status.CONFLICT and "overwrite_manual" in r.summary
    r = fix_standard(ctl, work, std, 100, overwrite_manual=True)
    assert r.status is Status.SUCCESS and r.meta.states_rewritten == 0, r.summary   # 원래 상태로 돌아감
    assert draw_batch(ctl, work, std, SPEC).meta.decision == "skip"

    # 내용과 색을 함께 손으로 바꾼 뒤 색만 보호된 경로로 고치면, 바뀐 내용이 새 기준 상태가 된다
    name = handle_of(snap(ctl, tmp_path, "s_name"), f"{OP}:room[0]/name")
    ctl._call(setattr, com(ctl, name), "TextString", "객실 101A")
    ctl.set_props(name, color=1)
    assert draw_batch(ctl, work, std, SPEC).status is Status.CONFLICT
    r = fix_standard(ctl, work, std, 100, overwrite_manual=True)
    assert r.status is Status.SUCCESS and r.meta.states_rewritten == 1, r.summary
    assert draw_batch(ctl, work, std, SPEC).meta.decision == "skip"   # 보호된 경로로 바꾼 것은 잡히지 않음

    for oid, edit in ((f"{OP}:room[1]/name", lambda o: setattr(o, "TextString", "손으로 바꾼 실명")),
                      (f"{OP}:bubble", lambda o: setattr(o.GetAttributes()[0], "TextString", "X8")),
                      (f"{OP}:room[1]/wall", lambda o: setattr(o, "Layer", "CON"))):
        h = handle_of(snap(ctl, tmp_path, "s_edit"), oid)
        ctl._call(edit, com(ctl, h))
        r = draw_batch(ctl, work, std, SPEC)
        assert r.status is Status.CONFLICT and "손으로" in r.summary and any(oid in v.message for v in r.violations)


def test_failure_inside_group_is_rolled_back(opened, tmp_path):
    ctl, work, std = opened
    before = handles(snap(ctl, tmp_path, "s0"))

    def boom(_):
        raise RuntimeError("일부러 낸 실패")

    r = draw_batch(ctl, work, std, SPEC, hooks=Hooks(after_change=boom))
    assert r.status is Status.RECOVERED and "일부러 낸 실패" in r.summary
    assert handles(snap(ctl, tmp_path, "s1")) == before
    assert apply_service.GUARD.reason is None


def test_nested_undo_group_failure_is_reported_honestly(opened, tmp_path):
    ctl, work, std = opened
    before = handles(snap(ctl, tmp_path, "s0"))

    def nested(c):
        c.draw_line((0, -30000), (500, -30000), layer="WAL")   # 자체 묶음을 여는 함수(E6 C 상황)
        raise RuntimeError("겹친 묶음 뒤 실패")

    r = draw_batch(ctl, work, std, SPEC, hooks=Hooks(after_change=nested))
    now = handles(snap(ctl, tmp_path, "s1"))
    if r.status is Status.RECOVERED:
        assert now == before
    else:
        assert r.status is Status.UNVERIFIED and now != before
        blocked = draw_batch(ctl, work, std, SPEC)
        assert blocked.status is Status.FAILED and "멈춤" in blocked.summary
        resumed = draw_batch(ctl, work, std, SPEC, resume=True)
        assert resumed.status is not Status.FAILED or "멈춤" not in resumed.summary
    print(f"\n겹친 묶음 실패 결과: {r.status.value} / {r.summary}")


def test_precheck_refuses_units_and_definition_mismatch(opened, tmp_path):
    ctl, work, std = opened
    before = handles(snap(ctl, tmp_path, "s0"))
    ctl._call_attr(ctl.doc, "SetVariable", "INSUNITS", 6)
    r = draw_batch(ctl, work, std, SPEC)
    assert r.status is Status.FAILED and "mm" in r.summary
    ctl._call_attr(ctl.doc, "SetVariable", "INSUNITS", 4)
    assert ctl.send_command("_.-LAYER\n_C\n1\nWAL\n\n")["ok"]   # 대상 도면의 WAL 색을 표준(3)과 다르게
    r = draw_batch(ctl, work, std, SPEC)
    assert r.status is Status.CONFLICT and [v.code for v in r.violations] == ["LAYER_COLOR"]
    assert handles(snap(ctl, tmp_path, "s1")) == before


def test_fix_properties_precheck_and_rollback(opened, tmp_path):
    ctl, work, std = opened
    red = ctl._safe_attr(ctl.draw_line((0, -20000), (1000, -20000), layer="WAL", color=1), "Handle")
    hidden = ctl._safe_attr(ctl.draw_line((0, -20500), (1000, -20500), layer="WAL"), "Handle")
    ctl.set_props(hidden, linetype="HIDDEN", lineweight=50)
    arrow = ctl._safe_attr(ctl.insert_block("계단화살표", [3000, -20000]), "Handle")
    ctl.set_props(arrow, layer="TXT")

    def recolor(c):
        c.set_props(red, color=3)

    r = fix_standard(ctl, work, std, 100, hooks=Hooks(before_apply=recolor))
    assert r.status is Status.CONFLICT and "읽은 뒤 바뀐" in r.summary
    ctl.set_props(red, color=1)

    def boom(_):
        raise RuntimeError("일부러 낸 실패")

    r = fix_standard(ctl, work, std, 100, hooks=Hooks(after_change=boom))
    assert r.status is Status.RECOVERED, r.summary
    doc = snap(ctl, tmp_path, "s_back")
    assert doc.entitydb[red].dxf.color == 1 and doc.entitydb[arrow].dxf.layer == "TXT"

    r = fix_standard(ctl, work, std, 100)
    assert r.status is Status.SUCCESS, r.summary
    doc = snap(ctl, tmp_path, "s_fixed")
    assert doc.entitydb[red].dxf.get("color", 256) == 256
    assert doc.entitydb[hidden].dxf.get("linetype", "BYLAYER").casefold() == "bylayer"
    assert doc.entitydb[hidden].dxf.get("lineweight", -1) == -1
    assert doc.entitydb[arrow].dxf.layer == "DIM"
