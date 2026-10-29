"""배포 스킬의 실제 JSON 예제가 명세→DXF→판독·검사를 끝까지 통과하는지 확인한다."""
import json
from pathlib import Path

import ezdxf
import pytest

from src.engine import changes
from src.engine.check import check_standard
from src.engine.read import read_drawing
from src.engine.spec import parse_spec, validate_spec
from src.engine.standards import load_standard
from src.engine.state import arcaid_tags
from src.engine.write import build_dxf

EXAMPLE = (Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard"
           / "references" / "examples" / "synthetic-plan.json")
CHANGE_EXAMPLE = EXAMPLE.with_name("synthetic-change.json")


@pytest.mark.parametrize("scale", [100, 200])
def test_packaged_example_roundtrip_and_scaling(tmp_path, scale):
    # 예제의 role·attribs·그리드·대칭·섬이 실제 계약과 달라지면 실패한다.
    std = load_standard()
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["scale"] = scale
    spec = parse_spec(data)
    assert validate_spec(spec, std) == []
    path = tmp_path / "example.dxf"
    result = build_dxf(spec, std, path)
    assert result.status.value == "success", result.summary
    assert result.meta.object_count == 27
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    rooms = read_drawing(path, std).rooms
    assert sorted(r.text for r in rooms) == ["객실 101", "객실 102", "객실 103", "객실 104"]
    assert sorted(tuple(r.position) for r in rooms) == [(1500, 2000), (1500, 8000), (4500, 2000), (4500, 8000)]
    assert all(r.height == 135 * scale / 100 for r in rooms)
    labels = [e for e in msp.query("TEXT") if e.dxf.layer == "ROM"]
    assert all(e.dxf.halign == 1 and e.dxf.valign == 2 for e in labels)
    bubbles = [e for e in msp.query("INSERT") if e.dxf.name == "축열버블-상"]
    assert [e.get_attrib_text("축열") for e in bubbles] == ["X1", "X2", "X3"]
    assert all(e.dxf.xscale == scale / 100 for e in bubbles)
    assert all(len(e.paths) == 2 for e in msp.query("HATCH"))
    dims = list(msp.query("DIMENSION"))
    assert len(dims) == 3
    assert all(e.override().get("dimscale") == scale / 2 for e in dims)
    check = check_standard(path, std, scale)
    assert check.violations == []
    assert check.unchecked == []


def test_packaged_change_example_resolves_only_intended_objects(tmp_path):
    # 잘못된 oid·내용·속성·삭제 대상과 벽·해치까지 넓어진 선택을 잡는다.
    std = load_standard()
    path = tmp_path / "example.dxf"
    result = build_dxf(parse_spec(json.loads(EXAMPLE.read_text(encoding="utf-8"))), std, path)
    assert result.status.value == "success", result.summary
    doc = ezdxf.readfile(path)
    before = {e.dxf.handle: changes.change_state(e) for e in doc.modelspace()}
    fingerprints = {e.dxf.handle: changes.fingerprint(e) for e in doc.modelspace()}
    data = json.loads(CHANGE_EXAMPLE.read_text(encoding="utf-8"))
    assert data["target"]["snapshot_id"] == "<export_snapshot의 snapshot_id>"
    data["target"]["snapshot_id"] = "example-base"
    plan = changes.resolve(changes.parse_changes(data), doc, std, "example-base")

    targets = {t.oid: t for t in plan.targets}
    prefix = "skill-synthetic-plan:"
    assert len(plan.targets) == 4
    assert {oid: t.action for oid, t in targets.items()} == {
        prefix + "room[0]/label": "move",
        prefix + "room[1]/label": "set",
        prefix + "bubble[2]/b": "set",
        prefix + "room[3]/label": "delete",
    }
    moved = targets[prefix + "room[0]/label"]
    assert moved.before["text"] == "객실 101"
    assert moved.before["align_point"] == [1500, 2000, 0]
    assert next(c.delta for c in plan.spec.changes if c.id == moved.change_id) == (600, 0)
    assert moved.after == {**moved.before, "align_point": [2100, 2000, 0]}
    renamed = targets[prefix + "room[1]/label"]
    assert renamed.before["text"] == "객실 102"
    assert renamed.after == {**renamed.before, "text": "회의실 102"}
    bubble = targets[prefix + "bubble[2]/b"]
    assert [(a["tag"], a["text"]) for a in bubble.before["attribs"]] == [("축열", "X3")]
    assert bubble.after == {**bubble.before, "attribs": [{**bubble.before["attribs"][0], "text": "X3-변경"}]}
    deleted = targets[prefix + "room[3]/label"]
    assert deleted.before["text"] == "객실 104"
    assert deleted.after is None

    # resolve는 쓰지 않는다. 확정된 after로 본 결과에서도 대상 밖 객체는 그대로여야 한다.
    after = {**before, **{t.handle: t.after for t in plan.targets}}
    after = {h: state for h, state in after.items() if state is not None}
    assert len(after) == len(before) - 1
    assert deleted.handle not in after
    assert {s["text"] for s in after.values() if s["type"] == "TEXT"} == {
        "객실 101", "회의실 102", "객실 103"}
    untouched = set(before) - {t.handle for t in plan.targets}
    assert all(after[h] == before[h] for h in untouched)
    for kind, count in (("wall", 6), ("floor", 4)):
        preserved = [e for e in doc.modelspace() if
                     (":wall/WAL1/" in (arcaid_tags(e) or {}).get("oid", "") if kind == "wall" else
                      (arcaid_tags(e) or {}).get("oid", "").endswith("/floor"))]
        assert len(preserved) == count
        assert all(e.dxf.handle in untouched for e in preserved)
        if kind == "wall":
            assert all(e.dxftype() == "LWPOLYLINE" and e.closed for e in preserved)
            assert sum(arcaid_tags(e)["oid"].endswith("/outer") for e in preserved) == 2
    assert {e.dxf.handle: changes.fingerprint(e) for e in doc.modelspace()} == fingerprints
