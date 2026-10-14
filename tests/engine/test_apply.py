"""보호된 적용 판정(src/engine/apply.py)과 기준 상태(src/engine/state.py) 테스트: 스냅샷을 ezdxf로 흉내 낸다"""
import json
from pathlib import Path

import ezdxf
import pytest

from check_cases import CASES
from src.engine.apply import (changed_since, judge_rerun, plan_fixes, restore_status, stale_states,
                              used_definition_conflicts, verify_inserted)
from src.engine.check import check_standard
from src.engine.spec import parse_spec, spec_hash
from src.engine.standards import load_standard
from src.engine.state import APP, arcaid_tags, state_hash
from src.engine.write import build_dxf

HERE = Path(__file__).parent
SPEC = json.loads((HERE / "specs" / "dangsan_typical.json").read_text(encoding="utf-8"))
OP = "dangsan-typical"
HASH = spec_hash(parse_spec(SPEC))


@pytest.fixture(scope="module")
def std():
    return load_standard()


@pytest.fixture(scope="module")
def gen(tmp_path_factory, std):
    out = tmp_path_factory.mktemp("apply") / "gen.dxf"
    result = build_dxf(SPEC, std, out)
    return out, {m.oid for m in result.manifest}


def fresh(gen):
    return ezdxf.readfile(gen[0])


def by_oid(doc, oid):
    return next(e for e in doc.modelspace() if (arcaid_tags(e) or {}).get("oid") == oid)


def retag(e, **changes):
    tags = arcaid_tags(e)
    tags.update(changes)
    e.set_xdata(APP, [(1000, f"{k}={v}") for k, v in tags.items() if v is not None])


ROOM = f"{OP}:room[0]"


# ---------- 기준 상태 ----------

def test_state_hash_tracks_meaningful_changes(gen):
    doc = fresh(gen)
    text, bubble, wall = by_oid(doc, f"{ROOM}/name"), by_oid(doc, f"{OP}:bubble[0]/b"), by_oid(doc, f"{ROOM}/wall")
    before = {k: state_hash(e) for k, e in (("text", text), ("bubble", bubble), ("wall", wall))}
    bubble.dxf.insert = (bubble.dxf.insert.x + 0.001, bubble.dxf.insert.y)
    assert state_hash(bubble) == before["bubble"]                      # 0.01mm 아래는 같다
    bubble.dxf.insert = (bubble.dxf.insert.x + 0.02, bubble.dxf.insert.y)
    assert state_hash(bubble) != before["bubble"]
    text.dxf.text = "객실 999"
    assert state_hash(text) != before["text"]
    wall.dxf.layer = "CON"
    assert state_hash(wall) != before["wall"]
    attrib = next(iter(by_oid(doc, f"{OP}:bubble[1]/b").attribs))
    old = state_hash(by_oid(doc, f"{OP}:bubble[1]/b"))
    attrib.dxf.text = "X99"
    assert state_hash(by_oid(doc, f"{OP}:bubble[1]/b")) != old


# ---------- 재실행 판정 ----------

def test_rerun_apply_when_nothing_tagged(std, gen):
    empty = ezdxf.readfile(std.path(std.profile.template.dxf))
    assert judge_rerun(empty, OP, HASH, gen[1]).action == "apply"


def test_rerun_skip_when_complete(gen):
    d = judge_rerun(fresh(gen), OP, HASH, gen[1])
    assert (d.action, d.reason) == ("skip", "이미 완료")


def _conflict(doc, gen, word):
    d = judge_rerun(doc, OP, HASH, gen[1])
    assert d.action == "conflict" and word in d.reason, d
    return d


def test_rerun_conflicts(gen):
    doc = fresh(gen)
    retag(by_oid(doc, f"{ROOM}/wall"), **{"in": "0000000000000000"})
    _conflict(doc, gen, "다른 입력")

    doc = fresh(gen)
    doc.modelspace().delete_entity(by_oid(doc, f"{ROOM}/wall"))
    d = _conflict(doc, gen, "일부만")
    assert any(f"{ROOM}/wall" in x for x in d.details)

    doc = fresh(gen)
    doc.modelspace().add_entity(by_oid(doc, f"{ROOM}/wall").copy())
    _conflict(doc, gen, "복사")

    doc = fresh(gen)
    retag(by_oid(doc, f"{ROOM}/wall"), state=None)
    _conflict(doc, gen, "기준 상태 없음")

    doc = fresh(gen)
    by_oid(doc, f"{ROOM}/name").dxf.text = "손으로 바꿈"
    d = _conflict(doc, gen, "손으로")
    assert d.details == [f"{ROOM}/name"]


# ---------- 적용 직전 확인 ----------

def test_definition_conflicts_only_for_used_names(std, gen, tmp_path):
    target = ezdxf.readfile(std.path(std.profile.template.dxf))
    target.layers.get("LGT").dxf.color = 1          # 명세가 쓰지 않는 레이어
    assert used_definition_conflicts(std, target, fresh(gen)) == []
    target.layers.get("CEN").dxf.color = 3          # 명세가 쓰는 레이어
    target.dimstyles.get("DIM330").dxf.dimtxt = 5
    assert sorted(v.code for v in used_definition_conflicts(std, target, fresh(gen))) == ["DIMSTYLE_VAR", "LAYER_COLOR"]


def test_verify_inserted(gen):
    generated = fresh(gen)
    same = list(fresh(gen).modelspace())
    assert verify_inserted(generated, same) == []
    cad = fresh(gen)
    line = by_oid(cad, f"{ROOM}/door-leaf")
    line.dxf.end = (line.dxf.end.x + 0.02, line.dxf.end.y)
    by_oid(cad, f"{ROOM}/wall").dxf.layer = "CON"
    cad.modelspace().delete_entity(by_oid(cad, f"{ROOM}/name"))
    cad.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "WAL"})
    problems = verify_inserted(generated, list(cad.modelspace()))
    text = " / ".join(problems)
    assert f"{ROOM}/door-leaf" in text and f"{ROOM}/wall" in text and f"{ROOM}/name" in text and "태그 없는" in text


def test_stale_states_lists_only_changed(gen):
    doc = fresh(gen)
    assert stale_states(doc.modelspace()) == {}
    wall = by_oid(doc, f"{ROOM}/wall")
    wall.dxf.const_width = 0  # 비교 필드가 아니면 그대로
    assert stale_states(doc.modelspace()) == {}
    retag(wall, state="0000000000000000")
    assert stale_states(doc.modelspace()) == {wall.dxf.handle: state_hash(wall)}


def test_restore_status(gen):
    base = fresh(gen)
    base_handles = {e.dxf.handle for e in base.modelspace()}
    now = fresh(gen)
    assert restore_status(base_handles, now, OP) == (set(), set(), set())
    now.modelspace().delete_entity(by_oid(now, f"{ROOM}/wall"))
    tagged = now.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "WAL"})
    tagged.set_xdata(APP, [(1000, f"op={OP}"), (1000, "oid=x")])
    untagged = now.modelspace().add_line((0, 1), (1, 1), dxfattribs={"layer": "WAL"})
    missing, left_tagged, left_untagged = restore_status(base_handles, now, OP)
    assert missing == {by_oid(base, f"{ROOM}/wall").dxf.handle}
    assert left_tagged == {tagged.dxf.handle} and left_untagged == {untagged.dxf.handle}


# ---------- 고칠 대상 ----------

def test_plan_fixes_filters_properties_and_missing_layers(std, tmp_path):
    path, labels = CASES["entities"](tmp_path / "entities.dxf")
    full = check_standard(path, std, k=100)
    doc = ezdxf.readfile(path)
    targets, unchecked = plan_fixes(full, doc)
    got = sorted((t.property, t.value, t.handle) for t in targets)
    assert got == sorted([("color", 256, labels[n]) for n in ("byblock", "nonstd_red", "red", "truecolor")]
                         + [("linetype", "BYLAYER", labels[n]) for n in ("dashed", "lim_dashed")]
                         + [("lineweight", -1, labels["lineweight"])])
    assert unchecked == [] and not any(t.manual for t in targets)
    red = next(t for t in targets if t.handle == labels["red"])
    assert red.before == {"layer": "WAL", "color": 1, "true_color": None, "linetype": "BYLAYER", "lineweight": -1}

    path, labels = CASES["dims_blocks"](tmp_path / "dims.dxf")
    full = check_standard(path, std, k=100)
    doc = ezdxf.readfile(path)
    doc.layers.remove("DIM")
    targets, unchecked = plan_fixes(full, doc)
    assert targets == []      # DIM 레이어가 없는 도면 + dimscale·dimstyle 등은 고치지 않음
    assert [(u.item, u.count) for u in unchecked] == [("대상 도면에 없는 레이어로 옮기기", 2)]


def test_plan_fixes_marks_manual_engine_objects(std, gen, tmp_path):
    doc = fresh(gen)
    by_oid(doc, f"{ROOM}/name").dxf.color = 1
    path = tmp_path / "manual.dxf"
    doc.saveas(path)
    targets, _ = plan_fixes(check_standard(path, std, k=100), ezdxf.readfile(path))
    (t,) = targets
    assert (t.property, t.oid, t.manual) == ("color", f"{ROOM}/name", True)


def test_changed_since(gen):
    base, now = fresh(gen), fresh(gen)
    h = by_oid(base, f"{ROOM}/wall").dxf.handle
    other = by_oid(base, f"{ROOM}/bath").dxf.handle
    assert changed_since(base, now, [h, other]) == []
    now.entitydb[h].dxf.color = 1
    now.modelspace().delete_entity(now.entitydb[other])
    assert sorted(changed_since(base, now, [h, other])) == sorted([h, other])
