"""B5 독립 판정의 반례와 CAD 없는 준비의 결정성."""
import copy
import json

import ezdxf
from ezdxf.lldxf.types import DXFTag
from ezdxf.math import Vec3
import pytest

from src.engine.state import state_hash
from tools.acceptance import b5_dangsan as b5


def clone(doc):
    import io
    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    return ezdxf.read(stream)


def tag(e, op="seed", path="line"):
    e.set_xdata("ARCAID", [(1000, f"{k}={v}") for k, v in
                          dict(op=op, path=path, oid=f"{op}:{path}", ver="1", **{"in": "seed"},
                               state=state_hash(e)).items()])


def spec(e, action="move", **kwargs):
    return dict(schema_version=1, standard="heo-2026@2026.1", op="b5-test",
                target=dict(snapshot_id="test"), changes=[dict(id="c1", select=dict(handles=[e.dxf.handle]),
                                                             action=action, **kwargs)])


@pytest.fixture
def drawing():
    doc = ezdxf.new("R2013")
    doc.appids.new("ARCAID")
    a = doc.modelspace().add_line((1, 2, 3), (4, 5, 6))
    tag(a)
    doc.modelspace().add_leader([(0, 0, 0), (2, 3, 4)])
    return doc, a


def test_outside_change_and_unsupported_leader_are_not_ignored(drawing):
    before, a = drawing
    request = spec(a, delta=[600, 0])
    after = b5.expected_document(before, request)
    assert b5.compare_transition(before, after, request, records="ignore")["equal"]
    after.modelspace().query("LEADER").first.vertices[1] = Vec3(2, 3, 4.02)
    proof = b5.compare_transition(before, after, request, records="ignore")
    assert not proof["equal"] and any("대상 밖" in p for p in proof["problems"])


def test_expected_copy_preserves_explicit_zero_dxf_attributes(drawing):
    before, a = drawing
    untouched = before.modelspace().add_lwpolyline([(0, 0), (10, 10)], dxfattribs=dict(const_width=0.0))
    request = spec(a, delta=[600, 0])
    after = copy.deepcopy(before)
    after.entitydb[a.dxf.handle].translate(600, 0, 0)
    tag(after.entitydb[a.dxf.handle])
    proof = b5.compare_transition(before, after, request, records="ignore")
    assert proof["equal"], proof
    expected = b5.expected_document(before, request)
    assert b5.fingerprint(expected.entitydb[untouched.dxf.handle]) == b5.fingerprint(untouched)


def test_target_default_extrusion_presence_is_normalized_but_value_is_exact():
    before = ezdxf.new()
    e = before.modelspace().add_lwpolyline([(0, 0), (10, 10)])
    request = spec(e, delta=[0, 0])
    assert b5.compare_transition(before, before, request, records="ignore")["equal"]
    after = copy.deepcopy(before)
    after.entitydb[e.dxf.handle].dxf.extrusion = (0, 0, -1)
    assert not b5.compare_transition(before, after, request, records="ignore")["equal"]


@pytest.mark.parametrize("app", ["ARCAID", "OTHER"])
def test_complete_xdata_including_target_is_protected(drawing, app):
    before, a = drawing
    request = spec(a, delta=[600, 0])
    after = b5.expected_document(before, request)
    after.appids.new(app) if app not in after.appids else None
    target = after.entitydb[a.dxf.handle]
    target.set_xdata(app, list(target.get_xdata(app)) + [(1000, "unknown=changed")]
                     if target.has_xdata(app) else [(1000, "unknown=changed")])
    assert not b5.compare_transition(before, after, request, records="ignore")["equal"]


def test_state_duplicates_and_stale_state_are_not_hidden(drawing):
    before, a = drawing
    request = spec(a, delta=[600, 0])
    after = b5.expected_document(before, request)
    target = after.entitydb[a.dxf.handle]
    old = list(target.get_xdata("ARCAID"))
    target.set_xdata("ARCAID", old + [(1000, "state=" + state_hash(target))])
    assert not b5.compare_transition(before, after, request, records="ignore")["equal"]
    target.set_xdata("ARCAID", [(t.code, "state=0000000000000000" if t.code == 1000
                                and str(t.value).startswith("state=") else t.value) for t in old])
    assert not b5.compare_transition(before, after, request, records="ignore")["equal"]


@pytest.mark.parametrize("dz, equal", [(.009, True), (.011, False)])
def test_target_uses_3d_distance(drawing, dz, equal):
    before, a = drawing
    request = spec(a, delta=[600, 0])
    after = b5.expected_document(before, request)
    target = after.entitydb[a.dxf.handle]
    target.dxf.start += Vec3(0, 0, dz)
    tag(target)
    assert b5.compare_transition(before, after, request, records="ignore")["equal"] is equal


@pytest.mark.parametrize("radius, angle, equal", [(1, .1, True), (100000, .00001, False),
                                                 (100000, 360, True), (100000, -.000001, True)])
def test_arc_uses_wcs_endpoints(radius, angle, equal):
    before = ezdxf.new("R2013")
    a = before.modelspace().add_arc((0, 0, 7), radius, 0, 90)
    request = spec(a, delta=[600, 0])
    after = b5.expected_document(before, request)
    after.entitydb[a.dxf.handle].dxf.start_angle = angle
    assert b5.compare_transition(before, after, request, records="ignore")["equal"] is equal


@pytest.mark.parametrize("extrusion", [(0, 0, -1), (0, 1, 1)])
def test_oracle_refuses_nonstandard_extrusion(extrusion):
    doc = ezdxf.new()
    a = doc.modelspace().add_arc((0, 0), 10, 0, 90, dxfattribs=dict(extrusion=extrusion))
    with pytest.raises(ValueError, match="extrusion"):
        b5.expected_document(doc, spec(a, delta=[1, 0]))


def record(doc, op, value):
    root = doc.rootdict.get_required_dict("ARCAID")
    if op in root:
        root[op].reset([(1, "arcaid-change-record/1"), (1, json.dumps(value))])
    else:
        root.add_xrecord(op).reset([(1, "arcaid-change-record/1"), (1, json.dumps(value))])


def test_records_are_compared_and_new_record_is_required(drawing):
    before, a = drawing
    record(before, "old", {"opaque": "그대로"})
    after = clone(before)
    assert b5.compare_unchanged(before, after)["equal"]
    record(after, "old", {"opaque": "changed"})
    assert not b5.compare_unchanged(before, after)["equal"]
    request = spec(a, delta=[1, 2])
    after = b5.expected_document(before, request)
    assert not b5.compare_transition(before, after, request)["equal"]
    b5_record = b5.expected_record(request, before, after)
    record(after, request["op"], b5_record)
    assert b5.compare_transition(before, after, request)["equal"]
    b5_record["targets"][a.dxf.handle]["after_hash"] = "0" * 16
    record(after, request["op"], b5_record)
    assert not b5.compare_transition(before, after, request)["equal"]


def test_reopen_tolerates_measured_block_float_normalization():
    before = ezdxf.new("R2013")
    before.blocks.new("part")
    block = before.blocks.new("container")
    insert = block.add_blockref("part", (0, 0, 0.0919544446546352))
    ellipse = block.add_ellipse((0, 0), (2, 0), ratio=.5,
                               dxfattribs=dict(extrusion=(0, 0, 0.9999999999999998)))
    after = copy.deepcopy(before)
    after.entitydb[insert.dxf.handle].dxf.insert = (0, 0, 0.0919544446546356)
    after.entitydb[ellipse.dxf.handle].dxf.extrusion = (0, 0, 1.0)
    proof = b5.compare_unchanged(before, after, float_rel_tol=1e-12)
    assert proof["equal"] and proof["problems"] == []
    assert proof["float_tolerated"]["handles"] == 2
    assert proof["float_tolerated"]["max_abs_diff"] == pytest.approx(4e-16, rel=.02, abs=0)


@pytest.mark.parametrize("delta, equal", [(1e-16, True), (1e-6, False)])
def test_reopen_float_tolerance_rejects_larger_changes(delta, equal):
    before = ezdxf.new("R2013")
    e = before.modelspace().add_circle((0, 0), .1)
    after = copy.deepcopy(before)
    after.entitydb[e.dxf.handle].dxf.radius += delta
    proof = b5.compare_unchanged(before, after, float_rel_tol=1e-12)
    assert proof["equal"] is equal
    assert proof["problem_count"] == (0 if equal else 1)
    assert proof["float_tolerated"]["handles"] == (1 if equal else 0)
    assert proof["float_tolerated"]["max_abs_diff"] == (
        pytest.approx(1e-16, rel=.1, abs=0) if equal else 0.0)


@pytest.mark.parametrize("kwargs", [{}, {"float_rel_tol": None}])
def test_unchanged_default_rejects_even_small_float_changes(kwargs):
    before = ezdxf.new("R2013")
    e = before.modelspace().add_circle((0, 0), .1)
    after = copy.deepcopy(before)
    after.entitydb[e.dxf.handle].dxf.radius += 1e-16
    proof = b5.compare_unchanged(before, after, **kwargs)
    assert not proof["equal"] and proof["problem_count"] == 1


def test_reopen_does_not_count_exact_fingerprints_as_tolerated(drawing):
    before, _ = drawing
    proof = b5.compare_unchanged(before, copy.deepcopy(before), float_rel_tol=1e-12)
    assert proof["equal"] and proof["problems"] == []
    assert proof["float_tolerated"] == dict(handles=0, max_abs_diff=0.0)


@pytest.mark.parametrize("attr, old, new", [("text", "원문", "수정"), ("color", 1, 2)])
def test_reopen_keeps_string_and_integer_attributes_exact(attr, old, new):
    before = ezdxf.new("R2013")
    e = before.modelspace().add_text("원문")
    setattr(e.dxf, attr, old)
    after = copy.deepcopy(before)
    setattr(after.entitydb[e.dxf.handle].dxf, attr, new)
    proof = b5.compare_unchanged(before, after, float_rel_tol=1e-12)
    assert not proof["equal"] and proof["problem_count"] == 1
    assert proof["float_tolerated"]["handles"] == 0


@pytest.mark.parametrize("old, new", [
    ([(1000, "원문")], [(1000, "수정")]),
    ([(1070, 1)], [(1070, 2)]),
    ([(1004, b"\x01\x02")], [(1004, b"\x01\x03")]),
    ([(1040, 1)], [(1040, 1.0)]),
    ([(1040, 1.0)], [(1040, 1)]),
    ([(1000, "원문")], [(1000, "원문"), (1000, "추가")]),
    ([(1000, "원문")], [(1003, "원문")]),
    ([(1000, "가"), (1000, "나")], [(1000, "나"), (1000, "가")]),
])
def test_reopen_keeps_xdata_values_types_and_tag_structure_exact(old, new):
    before = ezdxf.new("R2013")
    before.appids.new("OTHER")
    e = before.modelspace().add_point((0, 0))
    e.set_xdata("OTHER", [(1000, "기준")])
    # 정수·실수 혼합도 검사하도록 ezdxf의 태그 값 형변환을 우회한다.
    e.xdata.get("OTHER")[1:] = [DXFTag(c, v) for c, v in old]
    after = copy.deepcopy(before)
    after.entitydb[e.dxf.handle].xdata.get("OTHER")[1:] = [DXFTag(c, v) for c, v in new]
    proof = b5.compare_unchanged(before, after, float_rel_tol=1e-12)
    assert not proof["equal"] and proof["problem_count"] == 1
    assert proof["float_tolerated"] == dict(handles=0, max_abs_diff=0.0)


@pytest.mark.parametrize("code, old, new", [(40, .1, .1 + 1e-16), (1, "원문", "수정")])
def test_reopen_keeps_application_records_exact(code, old, new):
    before = ezdxf.new("R2013")
    before.rootdict.get_required_dict("ARCAID").add_xrecord("old").reset([(code, old)])
    after = copy.deepcopy(before)
    after.rootdict["ARCAID"]["old"].reset([(code, new)])
    proof = b5.compare_unchanged(before, after, float_rel_tol=1e-12)
    assert not proof["equal"] and proof["problems"] == ["적용 기록 변경"]
    assert proof["float_tolerated"] == dict(handles=0, max_abs_diff=0.0)


@pytest.mark.parametrize("kind", ["LINE", "LWPOLYLINE", "CIRCLE", "ARC", "TEXT", "INSERT"])
def test_independent_record_serialization_matches_public_contract(kind):
    # 제품 함수는 이 시험의 대조값에만 사용한다. B5 판정기에서는 호출하지 않는다.
    from src.engine import changes
    doc = ezdxf.new("R2013")
    doc.appids.new("ARCAID")
    ms = doc.modelspace()
    if kind == "LINE":
        e = ms.add_line((0, 0, 7), (3, 4, 7))
    elif kind == "LWPOLYLINE":
        e = ms.add_lwpolyline([(0, 0), (3, 4)], dxfattribs=dict(elevation=7))
    elif kind == "CIRCLE":
        e = ms.add_circle((0, 0, 7), 10)
    elif kind == "ARC":
        e = ms.add_arc((0, 0, 7), 10, 359.9, 90)
    elif kind == "TEXT":
        e = ms.add_text("한글", dxfattribs=dict(insert=(1, 2, 7), halign=1, valign=2, align_point=(5, 6, 7)))
    else:
        block = doc.blocks.new("bubble")
        block.add_attdef("축열", (0, 0), text="X1")
        e = ms.add_blockref("bubble", (0, 0, 7))
        e.add_auto_attribs({"축열": "X1"})
    tag(e)
    request = spec(e, delta=[600, 0])
    after = b5.expected_document(doc, request)
    expected = dict(op=request["op"], **{"in": changes.change_hash(changes.parse_changes(request))},
                    result="applied", targets={e.dxf.handle: dict(action="move", after_hash=changes.state_digest(changes.change_state(after.entitydb[e.dxf.handle])))})
    assert b5.expected_record(request, doc, after) == expected


def test_ab_correspondence_is_semantic_and_detects_tags(drawing):
    before, a = drawing
    after = clone(before)
    after.entitydb.reset_handle(after.entitydb[a.dxf.handle], "FFFF")
    assert b5.compare_copies(before, after)["equal"]
    after.entitydb["FFFF"].set_xdata("ARCAID", [(1000, "op=wrong")])
    assert not b5.compare_copies(before, after)["equal"]


@pytest.fixture
def inputs(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4
    doc.appids.new("ARCAID")
    for layer in ("WAL", "FUR", "WIN", "ROM", "DIM"):
        doc.layers.new(layer)
    for room in range(2):
        x = room * 5000
        for n in range(637):
            group, layer = ("shape3[0]", "FUR") if n < 43 else (("shape5[0]", "WIN") if n < 59 else ("shape2[0]", "WAL"))
            e = doc.modelspace().add_line((x + n % 10, 0), (x + 100 + n % 10, 100), dxfattribs=dict(layer=layer))
            tag(e, "t10-rooms", f"room/{room}/{group}/e{n}")
        e = doc.modelspace().add_text("객실(A)", dxfattribs=dict(insert=(x + 50, 50), layer="ROM"))
        tag(e, "t10-annotations", f"room-text-{room}")
    block = doc.blocks.new("bubble")
    block.add_attdef("축열", (0, 0), text="X1")
    e = doc.modelspace().add_blockref("bubble", (0, 500), dxfattribs=dict(layer="DIM"))
    e.add_auto_attribs({"축열": "X1"})
    tag(e, "t10-annotations", "X1")
    doc.saveas(folder / "engine-A-snapshot.dxf")
    dwg = folder / "engine.dwg"
    dwg.write_bytes(b"synthetic-dwg-provenance")
    b5.write_json(folder / "engine-A-snapshot.json", dict(dxf_sha256=b5.file_identity(folder / "engine-A-snapshot.dxf")["sha256"],
                                                       source_path=str(dwg), method="open_document"))
    for name in ("t10-rooms", "t10-annotations"):
        b5.write_json(folder / f"{name}.json", {"op": name, "items": []})
    human = ezdxf.new("R2013")
    human.header["$INSUNITS"] = 6
    human.layers.new("off").off()
    human.modelspace().add_line((0, 0), (1, 0), dxfattribs=dict(layer="off"))
    human.modelspace().add_line((0, 0), (100, 0))
    human.modelspace().add_lwpolyline([(0, 0), (10, 20), (20, 0)])
    human.modelspace().add_text("원문", dxfattribs=dict(insert=(0, 0)))
    human.blocks.new("hidden").add_line((0, 0), (1, 0))
    human.saveas(folder / "human-source-snapshot.dxf")
    return folder, dwg


def test_prepare_is_deterministic_and_only_changes_human_units(inputs, tmp_path):
    folder, dwg = inputs
    outputs = [tmp_path / "prepared-1", tmp_path / "prepared-2"]
    manifests = [b5.prepare(folder, p, engine_dwg=dwg) for p in outputs]
    assert manifests[0] == manifests[1]
    assert {p.name: p.read_bytes() for p in outputs[0].iterdir()} == {p.name: p.read_bytes() for p in outputs[1].iterdir()}
    requests = manifests[0]["requests"]
    assert [r["count"] for r in requests] == [637, 61, 3]
    assert manifests[0]["selection"]["move_room"] != manifests[0]["selection"]["edit_room"]
    old = ezdxf.readfile(folder / "human-source-snapshot.dxf")
    new = ezdxf.readfile(outputs[0] / "human-base.dxf")
    assert new.header["$INSUNITS"] == 4
    assert b5.compare_unchanged(old, new)["equal"]
    for p in outputs:
        b5.load_prepared(p)
    with pytest.raises(FileExistsError):
        b5.prepare(folder, outputs[0], engine_dwg=dwg)


def test_prepare_rejects_stale_state_and_changed_artifacts(inputs, tmp_path):
    folder, dwg = inputs
    out = tmp_path / "prepared"
    b5.prepare(folder, out, engine_dwg=dwg)
    (out / "b5-human-edit.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="해시"):
        b5.load_prepared(out)
    doc = ezdxf.readfile(folder / "engine-A-snapshot.dxf")
    doc.modelspace().query("LINE").first.dxf.start += Vec3(20, 0, 0)
    doc.saveas(folder / "engine-A-snapshot.dxf")
    meta = json.loads((folder / "engine-A-snapshot.json").read_text(encoding="utf-8"))
    meta["dxf_sha256"] = b5.file_identity(folder / "engine-A-snapshot.dxf")["sha256"]
    b5.write_json(folder / "engine-A-snapshot.json", meta)
    with pytest.raises(ValueError, match="state"):
        b5.prepare(folder, tmp_path / "bad", engine_dwg=dwg)


@pytest.mark.parametrize("field, value", [("violation_counts", {"LAYER": 1}), ("unchecked", [{"item": "scope"}]), ("truncated", True)])
def test_success_status_does_not_bypass_standard_gate(field, value):
    result = dict(status="success", violation_counts={}, unchecked=[], truncated=False)
    result[field] = value
    with pytest.raises(AssertionError):
        b5.verify_standard_result("engine", result)
    assert b5.verify_standard_result("human", result)[field] == value


@pytest.mark.parametrize("kind, targets, count", [
    ("engine", [("TEXT", "0")], 0),
    ("human", [("LWPOLYLINE", "0"), ("LINE", "0"), ("TEXT", "비표준")], 1),
    ("human", [("TEXT", "비표준"), ("TEXT", "다른비표준"), ("TEXT", "ROM")], 2),
    ("human", [("TEXT", "rom"), ("TEXT", "TIT1"), ("LINE", "비표준")], 0),
])
def test_apply_unchecked_expectation_uses_manifest_texts_and_standard(kind, targets, count):
    from src.engine.standards import load_standard
    description = dict(targets=[dict(handle=str(2170 + i), kind=t, layer=layer)
                                for i, (t, layer) in enumerate(targets)])
    expected = [dict(item="높이 규칙이 없는 레이어의 문자",
                     reason="text.yaml에 그 레이어의 문자 높이가 없음", count=count)] if count else []
    assert b5.expected_apply_unchecked(kind, description, load_standard()) == expected


@pytest.mark.parametrize("new_violations, counts", [({}, []), ({}, [51, 51]), ({}, [1, 1]),
                                                    ({}, [2]), ({"TEXT_HEIGHT": 1}, [1])])
def test_apply_gate_rejects_missing_duplicate_or_wrong_scope(new_violations, counts):
    from src.engine.standards import load_standard
    description = dict(targets=[dict(handle="2170", kind="TEXT", layer="비표준")])
    result = dict(new_violations=new_violations, unchecked=[dict(item="높이 규칙이 없는 레이어의 문자",
                  reason="text.yaml에 그 레이어의 문자 높이가 없음", count=n) for n in counts])
    with pytest.raises(AssertionError):
        b5.verify_apply_result("human", description, load_standard(), result)


def test_apply_gate_preserves_expected_human_unchecked_in_evidence():
    from src.engine.standards import load_standard
    description = dict(targets=[dict(handle="2170", kind="TEXT", layer="비표준")])
    result = dict(new_violations={}, unchecked=[dict(item="높이 규칙이 없는 레이어의 문자",
                  reason="text.yaml에 그 레이어의 문자 높이가 없음", count=1)])
    assert b5.verify_apply_result("human", description, load_standard(), result) == result
