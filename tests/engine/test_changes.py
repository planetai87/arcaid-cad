"""B1 변경 명세·판정 시험: CAD 없이 합성 문서와 B0의 실제 XRECORD를 쓴다."""
import copy
import hashlib
import json
import math
from pathlib import Path

import ezdxf
import pytest

from src.engine.standards import load_standard
from src.engine.state import APP, arcaid_tags, state_hash
from src.engine import changes


@pytest.fixture(scope="module")
def chg():
    return changes


@pytest.fixture(scope="module")
def std():
    return load_standard()


@pytest.fixture
def doc(std):
    d = ezdxf.readfile(std.path(std.profile.template.dxf))
    d.modelspace().delete_all_entities()
    return d


def request(handles=("A",), **change):
    return {"schema_version": 1, "standard": "heo-2026@2026.1", "op": "shift-1",
            "target": {"snapshot_id": "snap-1"},
            "changes": [{"id": "c1", "select": {"handles": list(handles)},
                         "action": "move", "delta": [10, 20], **change}]}


def parse_error(chg, data):
    with pytest.raises(chg.ChangeError) as caught:
        chg.parse_changes(data)
    assert {v.code for v in caught.value.violations} == {"CHG_SCHEMA"}
    return caught.value


def resolve(chg, doc, std, entities, **change):
    return chg.resolve(chg.parse_changes(request([e.dxf.handle for e in entities], **change)), doc, std, "snap-1")


def rejected(chg, doc, std, spec, code):
    with pytest.raises(chg.ChangeError) as caught:
        chg.resolve(chg.parse_changes(spec), doc, std, "snap-1")
    assert code in {v.code for v in caught.value.violations}
    return caught.value.violations


def tag(e, op="draw-1", path="room/7/wall", **extra):
    if APP not in e.doc.appids:
        e.doc.appids.new(APP)
    tags = {"op": op, "oid": f"{op}:{path}", "path": path, "in": "0123456789abcdef", "ver": "1",
            "state": state_hash(e), **extra}
    e.set_xdata(APP, [(1000, f"{k}={v}") for k, v in tags.items()])


def line(doc):
    return doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={"layer": "WAL"})


def test_fingerprint_covers_raw_geometry_xdata_and_children(doc):
    circle = doc.modelspace().add_circle((0, 0), 3)
    before = changes.fingerprint(circle)
    circle.dxf.radius += 0.000001
    assert changes.fingerprint(circle) != before
    before = changes.fingerprint(circle)
    tag(circle)
    assert changes.fingerprint(circle) != before
    insert, attr, _ = block(doc)
    before = changes.fingerprint(insert)
    attr.dxf.text = "새 속성"
    assert changes.fingerprint(insert) != before
    hatch = doc.modelspace().add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (1, 0), (1, 1)])
    before = changes.fingerprint(hatch)
    hatch.paths[0].vertices[1] = (2, 0, 0)
    assert changes.fingerprint(hatch) != before


def test_verify_applied_checks_after_tags_and_non_targets(doc, std):
    target = line(doc)
    tag(target, custom="보존")
    other = doc.modelspace().add_circle((0, 0), 5)
    plan = resolve(changes, doc, std, [target])
    now = copy.deepcopy(doc)
    moved = now.entitydb[target.dxf.handle]
    moved.translate(10, 20, 0)
    tag(moved, custom="보존")
    assert changes.verify_applied(plan, doc, now) == []
    now.entitydb[other.dxf.handle].dxf.radius = 6
    assert any(other.dxf.handle in p for p in changes.verify_applied(plan, doc, now))
    now.entitydb[other.dxf.handle].dxf.radius = 5
    tag(moved, custom="손상")
    assert changes.verify_applied(plan, doc, now)


def test_verify_restored_checks_state_and_attribute_handles(doc, std):
    insert, attr, _ = block(doc)
    tag(insert)
    plan = resolve(changes, doc, std, [insert])
    now = copy.deepcopy(doc)
    assert changes.verify_restored(plan, doc, now) == []
    tag(now.entitydb[insert.dxf.handle], state="옛 태그 아님")
    assert changes.verify_restored(plan, doc, now)
    now = copy.deepcopy(doc)
    now.entitydb[insert.dxf.handle].attribs[0].dxf.handle = "FFFFF"
    assert changes.verify_restored(plan, doc, now)


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("target_insert", [False, True])
def test_verify_ignores_reassigned_seqend_handles(doc, std, restored, target_insert):
    insert, _, definition = block(doc)
    other = doc.modelspace().add_blockref(definition.name, (30, 40))
    other.add_attrib("번호", "X2", (31, 42))
    polyline = doc.modelspace().add_polyline2d([(0, 0), (10, 0)])
    target = insert if target_insert else line(doc)
    plan = resolve(changes, doc, std, [target])
    now = copy.deepcopy(doc)
    if not restored:
        now.entitydb[target.dxf.handle].translate(10, 20, 0)
    # B2 CAD 실측처럼 대상·대상 밖 INSERT와 POLYLINE의 SEQEND만 새 핸들로 바꾼다.
    for parent in (insert, other, polyline):
        seqend = now.entitydb[parent.dxf.handle].seqend
        assert now.entitydb.reset_handle(seqend, now.entitydb.next_handle())
    verify = changes.verify_restored if restored else changes.verify_applied
    assert verify(plan, doc, now) == []


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("child_kind", ["target_attrib", "other_attrib", "vertex"])
def test_verify_rejects_reassigned_attrib_and_vertex_handles(doc, std, restored, child_kind):
    insert, attr, _ = block(doc)
    polyline = doc.modelspace().add_polyline2d([(0, 0), (10, 0)])
    target = insert if child_kind == "target_attrib" else line(doc)
    plan = resolve(changes, doc, std, [target])
    now = copy.deepcopy(doc)
    if not restored:
        now.entitydb[target.dxf.handle].translate(10, 20, 0)
    child = now.entitydb[polyline.vertices[0].dxf.handle if child_kind == "vertex" else attr.dxf.handle]
    old_handle = child.dxf.handle
    new_handle = now.entitydb.next_handle()
    assert now.entitydb.reset_handle(child, new_handle)
    verify = changes.verify_restored if restored else changes.verify_applied
    problems = verify(plan, doc, now)
    assert f"핸들 집합 불일치: 없음 {old_handle}" in problems
    assert f"핸들 집합 불일치: 추가 {new_handle}" in problems
    if child_kind == "target_attrib":
        assert f"{insert.dxf.handle}: 종속 핸들 연결 불일치" in problems
    else:
        parent = polyline if child_kind == "vertex" else insert
        assert f"{parent.dxf.handle}: 대상이 아닌 객체 변경" in problems


def test_verify_preserves_attribute_tags_and_parent_handle_binding(doc, std):
    insert, attr, _ = block(doc)
    tag(attr)
    plan = resolve(changes, doc, std, [insert])
    now = copy.deepcopy(doc)
    tag(now.entitydb[attr.dxf.handle], state="수정된 속성 태그")
    assert changes.verify_restored(plan, doc, now)
    now.entitydb[insert.dxf.handle].translate(10, 20, 0)
    assert changes.verify_applied(plan, doc, now)


def test_verify_detects_non_target_paperspace_change(doc, std):
    target = line(doc)
    other = doc.layouts.new("검증 레이아웃").add_circle((0, 0), 3)
    plan = resolve(changes, doc, std, [target])
    now = copy.deepcopy(doc)
    now.entitydb[target.dxf.handle].translate(10, 20, 0)
    assert changes.verify_applied(plan, doc, now) == []
    now.entitydb[other.dxf.handle].dxf.radius = 4
    assert changes.verify_applied(plan, doc, now)


def test_verify_deletion_requires_absence_and_exact_handle_set(doc, std):
    insert, attr, _ = block(doc)
    data = request([insert.dxf.handle], action="delete")
    del data["changes"][0]["delta"]
    plan = changes.resolve(changes.parse_changes(data), doc, std, "snap-1")
    now = copy.deepcopy(doc)
    assert changes.verify_applied(plan, doc, now)
    now.modelspace().delete_entity(now.entitydb[insert.dxf.handle])
    assert changes.verify_applied(plan, doc, now) == []
    now.modelspace().add_line((1, 2), (3, 4))
    assert changes.verify_applied(plan, doc, now)


def block(doc):
    b = doc.blocks.new("B1_TEST")
    b.add_attdef("번호", (1, 2), dxfattribs={"height": 2})
    e = doc.modelspace().add_blockref(b.name, (10, 20), dxfattribs={"layer": "WAL"})
    a = e.add_attrib("번호", "X1", (11, 22), dxfattribs={"height": 2, "halign": 1, "valign": 2,
                                                           "align_point": (13, 24)})
    return e, a, b


@pytest.mark.parametrize("field", ["schema_version", "standard", "op", "target", "changes"])
def test_schema_requires_top_level_fields(chg, field):
    data = request()
    del data[field]
    parse_error(chg, data)


@pytest.mark.parametrize("selector", [{}, {"handles": []}, {"oids": []}, {"op": "draw-1"},
                                      {"path_prefix": "room/"}, {"handles": ["A"], "oids": ["draw-1:x"]},
                                      {"op": "draw-1", "path_prefix": "", "handles": ["A"]},
                                      {"oids": ["no-colon"]}, {"handles": ["not-a-handle"]}])
def test_schema_accepts_exactly_one_complete_selector(chg, selector):
    parse_error(chg, request(select=selector))


@pytest.mark.parametrize("edit", [{"delta": [float("nan"), 0]}, {"delta": [0, float("inf")]},
                                   {"delta": [float("-inf"), 0]}, {"delta": [1, 2, 3]},
                                   {"props": {"text": "x"}}, {"action": "delete"},
                                   {"action": "rotate", "delta": None}])
def test_schema_rejects_invalid_move_and_action_fields(chg, edit):
    parse_error(chg, request(**edit))


@pytest.mark.parametrize("props", [{}, {"unknown": 1}, {"color": 0}, {"color": 257}, {"color": True},
                                    {"color": {"rgb": [1, 2, 256]}}, {"color": {"rgb": [1, 2]}},
                                    {"color": {"rgb": [1, 2, 3], "alpha": 1}}, {"lineweight": 17},
                                    {"layer": ""}, {"attribs": {}}, {"text": None}])
def test_schema_rejects_invalid_properties(chg, props):
    data = request(action="set", props=props)
    del data["changes"][0]["delta"]
    parse_error(chg, data)


def test_schema_target_id_unknown_fields_and_empty_changes(chg):
    for key, value in [("target", {}), ("target", {"snapshot_id": ""}), ("op", "a:b"),
                       ("op", "a" * 65), ("changes", []), ("unknown", 1), ("schema_version", 2)]:
        data = request()
        data[key] = value
        parse_error(chg, data)
    data = request()
    del data["changes"][0]["id"]
    parse_error(chg, data)
    parse_error(chg, "{broken")


def test_hash_uses_canonical_json_with_defaults(chg):
    spec = chg.parse_changes(request())
    canonical = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert chg.change_hash(spec) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    assert chg.change_hash(chg.parse_changes(json.dumps(request(), sort_keys=True))) == chg.change_hash(spec)
    assert chg.change_hash(chg.parse_changes(request(delta=[11, 20]))) != chg.change_hash(spec)
    assert chg.parse_changes(spec) is spec


def test_resolve_all_selectors_and_captures_raw_before_after(chg, doc, std):
    a, b, c = line(doc), line(doc), line(doc)
    tag(b)
    tag(c, path="room/8/wall")
    for selector, want in [({"handles": [a.dxf.handle.lower()]}, [a]),
                           ({"oids": ["draw-1:room/7/wall"]}, [b]),
                           ({"op": "draw-1", "path_prefix": "room/"}, [b, c])]:
        spec = chg.parse_changes(request(select=selector))
        plan = chg.resolve(spec, doc, std, "snap-1")
        assert [t.handle for t in plan.targets] == [e.dxf.handle for e in want]
        assert all(t.change_id == "c1" and t.action == "move" for t in plan.targets)
        assert plan.targets[0].before["start"] == [0, 0, 0]
        assert plan.targets[0].after["start"] == [10, 20, 0]
    assert chg.resolve(chg.parse_changes(request(select={"oids": ["draw-1:room/7/wall"]})),
                       doc, std, "snap-1").targets[0].oid == "draw-1:room/7/wall"


def test_resolve_rejects_standard_snapshot_missing_and_non_modelspace(chg, doc, std):
    e = line(doc)
    for key, value, code in [("standard", "other@1", "CHG_STANDARD"),
                             ("target", {"snapshot_id": "old"}, "CHG_TARGET")]:
        data = request([e.dxf.handle])
        data[key] = value
        rejected(chg, doc, std, data, code)
    outside = doc.blocks.new("OUTSIDE").add_line((0, 0), (1, 1))
    paper = doc.layouts.new("B1_종이").add_line((0, 0), (1, 1))
    for selector in [{"handles": ["FFFFFF"]}, {"handles": [outside.dxf.handle]},
                     {"handles": [paper.dxf.handle]}, {"oids": ["missing:x"]},
                     {"op": "missing", "path_prefix": ""}]:
        rejected(chg, doc, std, request(select=selector), "CHG_SELECT")


def test_resolve_rejects_duplicate_targets_and_change_ids(chg, doc, std):
    e, other = line(doc), line(doc)
    tag(e)
    rejected(chg, doc, std, request([e.dxf.handle, e.dxf.handle]), "CHG_DUPLICATE")
    data = request([e.dxf.handle])
    data["changes"].append({"id": "c2", "select": {"oids": ["draw-1:room/7/wall"]}, "action": "delete"})
    rejected(chg, doc, std, data, "CHG_DUPLICATE")
    data["changes"][1] = {"id": "c1", "select": {"handles": [other.dxf.handle]}, "action": "delete"}
    rejected(chg, doc, std, data, "CHG_DUPLICATE")


@pytest.mark.parametrize("kind", ["ellipse", "hatch", "extrusion", "text_on_line", "attribs_on_line"])
def test_resolve_rejects_entire_request_on_unsupported_kind(chg, doc, std, kind):
    good = line(doc)
    bad = {"ellipse": lambda: doc.modelspace().add_ellipse((0, 0), major_axis=(2, 0)),
           "hatch": lambda: doc.modelspace().add_hatch(),
           "extrusion": lambda: doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"extrusion": (0, 0, -1)})}
    data = request([good.dxf.handle, bad[kind]().dxf.handle]) if kind in bad else request([good.dxf.handle])
    if kind.endswith("on_line"):
        data["changes"][0].pop("delta")
        data["changes"][0].update(action="set", props={"text": "x"} if kind == "text_on_line" else {"attribs": {"x": "y"}})
    rejected(chg, doc, std, data, "CHG_KIND")


def test_limit_counts_expanded_objects(chg, doc, std):
    for i in range(1000):
        tag(line(doc), path=f"room/{i}")
    data = request(select={"op": "draw-1", "path_prefix": "room/"})
    assert len(chg.resolve(chg.parse_changes(data), doc, std, "snap-1").targets) == 1000
    tag(line(doc), path="room/1000")
    rejected(chg, doc, std, data, "CHG_LIMIT")


@pytest.mark.parametrize("relation", ["group", "hatch", "reactor"])
def test_relations_reject_group_hatch_boundary_and_dimension_reactor(chg, doc, std, relation):
    e = line(doc)
    if relation == "group":
        doc.groups.new("B1").set_data([e])
        e.set_reactors([])  # 역검사가 반응자 누락도 막아야 한다.
    elif relation == "hatch":
        h = doc.modelspace().add_hatch()
        h.paths.add_polyline_path([(0, 0), (1, 0), (1, 1)], is_closed=True).source_boundary_objects = [e.dxf.handle]
    else:
        dim = doc.modelspace().add_linear_dim(base=(0, 2), p1=(0, 0), p2=(1, 0)).dimension
        e.set_reactors([dim.dxf.handle])
    assert e.dxf.handle in chg.relations(doc)
    rejected(chg, doc, std, request([e.dxf.handle]), "CHG_RELATION")


def test_extension_dictionary_alone_is_allowed(chg, doc, std):
    e = line(doc)
    e.new_extension_dict().add_xrecord("metadata")
    assert e.dxf.handle not in chg.relations(doc)
    assert len(resolve(chg, doc, std, [e]).targets) == 1


@pytest.mark.parametrize("source", [True, False])
@pytest.mark.parametrize("condition", ["lock", "freeze", "off", "missing"])
def test_original_and_destination_layers_must_be_writable(chg, doc, std, source, condition):
    e = line(doc)
    layer_name = "WAL" if source else "CON"
    if condition == "missing":
        doc.layers.remove(layer_name)
    else:
        getattr(doc.layers.get(layer_name), condition)()
    data = request([e.dxf.handle], action="set", props={"layer": "CON"})
    data["changes"][0].pop("delta")
    rejected(chg, doc, std, data, "CHG_LAYER")


def test_missing_linetype_and_current_layer_recheck(chg, doc, std):
    e = line(doc)
    data = request([e.dxf.handle], action="set", props={"linetype": "missing"})
    data["changes"][0].pop("delta")
    rejected(chg, doc, std, data, "CHG_LINETYPE")
    plan = resolve(chg, doc, std, [e])
    assert chg.recheck(plan, doc) == []
    doc.layers.get("WAL").lock()
    violations = chg.recheck(plan, doc)
    assert [v.code for v in violations] == ["CHG_LAYER"]
    assert violations[0].handle == e.dxf.handle


@pytest.mark.parametrize("condition", ["missing", "constant_attrib", "constant_attdef"])
def test_attribs_reject_missing_tags_and_both_constant_forms(chg, doc, std, condition):
    e, a, b = block(doc)
    props = {"attribs": {"missing" if condition == "missing" else "번호": "X2"}}
    if condition == "constant_attrib":
        a.dxf.flags = 2
    elif condition == "constant_attdef":
        next(iter(b.query("ATTDEF"))).dxf.flags = 2
    data = request([e.dxf.handle], action="set", props=props)
    data["changes"][0].pop("delta")
    rejected(chg, doc, std, data, "CHG_ATTRIB")


@pytest.mark.parametrize("halign,valign,keys", [(0, 0, {"insert"}), (1, 2, {"align_point"}),
                                               (3, 0, {"insert", "align_point"}), (5, 0, {"insert", "align_point"})])
def test_text_points_follow_alignment_and_content_edits(chg, doc, std, halign, valign, keys):
    e = doc.modelspace().add_text("객실", dxfattribs={"layer": "ROM", "insert": (1, 2), "align_point": (3, 4),
                                                    "height": 2, "halign": halign, "valign": valign})
    state = chg.change_state(e)
    assert {k for k in ("insert", "align_point") if k in state} == keys
    plan = resolve(chg, doc, std, [e])
    for k in keys:
        assert plan.targets[0].after[k] == ([11, 22, 0] if k == "insert" else [13, 24, 0])
    change = chg.parse_changes({**request(), "changes": [{"id": "c1", "select": {"handles": [e.dxf.handle]},
                                                         "action": "set", "props": {"text": "객실(A)"}}]}).changes[0]
    after = chg.apply_to_state(state, change)
    assert after["text"] == "객실(A)" and all(after[k] == state[k] for k in keys)
    assert state["text"] == "객실"
    if keys == {"align_point"}:
        e.dxf.insert = (999, 999)
        assert chg.same_state(state, chg.change_state(e))


def test_insert_move_and_attrib_values_preserve_alignment(chg, doc, std):
    e, a, b = block(doc)
    before = chg.change_state(e)
    target = resolve(chg, doc, std, [e]).targets[0]
    assert target.after["insert"] == [20, 40, 0]
    assert target.after["attribs"][0]["align_point"] == [23, 44, 0]
    assert "insert" not in before["attribs"][0]
    data = request([e.dxf.handle], action="set", props={"attribs": {"번호": "X2"}})
    data["changes"][0].pop("delta")
    target = chg.resolve(chg.parse_changes(data), doc, std, "snap-1").targets[0]
    assert target.after["attribs"][0]["text"] == "X2"
    assert target.after["attribs"][0]["align_point"] == [13, 24, 0]
    a.dxf.insert = (999, 999)
    assert chg.same_state(before, chg.change_state(e))


def test_polyline_move_preserves_bulges_widths_elevation_and_precision(chg, doc, std):
    e = doc.modelspace().add_lwpolyline([(0.000123, 2, 3, 4, .5), (10, 20, 1, 2, -.25)], format="xyseb",
                                      close=True, dxfattribs={"layer": "WAL", "elevation": 7, "const_width": 6})
    target = resolve(chg, doc, std, [e]).targets[0]
    assert target.before["points"][0] == [0.000123, 2, 3, 4, .5]
    assert target.after["points"][0] == [10.000123, 22, 3, 4, .5]
    assert target.after["closed"] and target.after["elevation"] == 7 and target.after["const_width"] == 6


@pytest.mark.parametrize("shift,want", [((.009, 0), True), ((.011, 0), False), ((.01, 0), True),
                                       ((.008, .008), False), ((.006, .008), True)])
def test_state_points_use_inclusive_euclidean_distance(chg, doc, shift, want):
    e = line(doc)
    a = chg.change_state(e)
    e.dxf.start = (shift[0], shift[1])
    b = chg.change_state(e)
    assert chg.same_state(a, b) is want
    assert ("start" in chg.diff_fields(a, b, .01)) is (not want)


def test_state_angles_scales_discrete_values_and_tags(chg, doc):
    e, _, _ = block(doc)
    tag(e)
    e.dxf.rotation = 359.999
    a = chg.change_state(e)
    e.dxf.rotation = 0
    assert chg.same_state(a, chg.change_state(e))
    e.dxf.xscale = 1 + 1e-8
    assert not chg.same_state(a, chg.change_state(e))
    e.dxf.xscale = 1 + .5e-9
    assert chg.same_state(a, chg.change_state(e))
    assert "state" not in a["tags"]
    e.set_xdata(APP, [(1000, f"{k}={'changed' if k == 'in' else v}") for k, v in arcaid_tags(e).items()])
    assert chg.diff_fields(a, chg.change_state(e), .01) == ["tags"]
    assert chg.state_digest(a) != chg.state_digest(chg.change_state(e))


def test_set_properties_color_and_delete_are_pure(chg, doc, std):
    e = line(doc)
    e.dxf.true_color = 0x010203
    for color, want in [(7, (7, None)), ({"rgb": [12, 34, 56]}, (None, 0x0C2238))]:
        data = request([e.dxf.handle], action="set", props={"color": color, "layer": "CON", "linetype": "BYLAYER",
                                                           "lineweight": 25})
        data["changes"][0].pop("delta")
        t = chg.resolve(chg.parse_changes(data), doc, std, "snap-1").targets[0]
        assert (t.after["color"], t.after["true_color"]) == want
        assert (t.after["layer"], t.after["linetype"], t.after["lineweight"]) == ("con", "bylayer", 25)
    assert e.dxf.true_color == 0x010203
    data = request([e.dxf.handle], action="delete")
    data["changes"][0].pop("delta")
    assert chg.resolve(chg.parse_changes(data), doc, std, "snap-1").targets[0].after is None


@pytest.mark.parametrize("true_color", [1135561, 0])
def test_true_color_state_ignores_derived_aci(chg, doc, true_color):
    a, b = line(doc), line(doc)
    a.dxf.color, b.dxf.color = 152, 256
    a.dxf.true_color = b.dxf.true_color = true_color
    first, second = chg.change_state(a), chg.change_state(b)
    assert first["color"] is None and second["color"] is None
    assert chg.same_state(first, second)
    assert chg.state_digest(first) == chg.state_digest(second)


def test_rgb_set_after_matches_cad_export_with_derived_aci(chg, doc, std):
    e = line(doc)
    data = request([e.dxf.handle], action="set", props={"color": {"rgb": [17, 83, 201]}})
    data["changes"][0].pop("delta")
    target = chg.resolve(chg.parse_changes(data), doc, std, "snap-1").targets[0]
    # B0 E11에서 확인한 GstarCAD Export 사본의 색상 조합이다.
    e.dxf.color, e.dxf.true_color = 152, 1135561
    assert chg.same_state(target.after, chg.change_state(e))
    e.dxf.true_color = 1135562
    assert not chg.same_state(target.after, chg.change_state(e))


@pytest.mark.parametrize("color", [7, 152, 256])
def test_aci_set_after_requires_true_color_removal(chg, doc, std, color):
    e = line(doc)
    e.dxf.true_color = 1135561
    data = request([e.dxf.handle], action="set", props={"color": color})
    data["changes"][0].pop("delta")
    target = chg.resolve(chg.parse_changes(data), doc, std, "snap-1").targets[0]
    assert target.after["color"] == color and target.after["true_color"] is None
    e.dxf.color = color
    assert not chg.same_state(target.after, chg.change_state(e))
    e.dxf.discard("true_color")
    assert chg.same_state(target.after, chg.change_state(e))


def record(chg, plan, **overrides):
    return chg.ChangeRecord.model_validate({"op": plan.spec.op, "in": chg.change_hash(plan.spec), "result": "applied",
        "targets": {t.handle: {"action": t.action, "after_hash": chg.state_digest(t.after) if t.after else None}
                    for t in plan.targets}, **overrides})


def test_judgement_without_record_and_read_since_conflict(chg, doc, std):
    e = line(doc)
    plan = resolve(chg, doc, std, [e])
    assert chg.judge_changes(plan, doc, {}).action == "apply"
    e.dxf.start = (1, 0)
    assert chg.judge_changes(plan, doc, {}, overwrite_manual=True).action == "conflict"
    doc.modelspace().delete_entity(e)
    assert chg.judge_changes(plan, doc, {}).action == "conflict"


def test_judgement_matching_record_requires_after_and_digest(chg, doc, std):
    e = line(doc)
    plan = resolve(chg, doc, std, [e])
    rec = record(chg, plan)
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"
    e.dxf.start, e.dxf.end = (10, 20), (110, 20)
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "skip"
    rec.targets[e.dxf.handle].after_hash = "0000000000000000"
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"
    rec.targets[e.dxf.handle].after_hash = None
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"


def test_judgement_delete_skip_needs_record_and_absence(chg, doc, std):
    e = line(doc)
    data = request([e.dxf.handle], action="delete")
    data["changes"][0].pop("delta")
    plan = chg.resolve(chg.parse_changes(data), doc, std, "snap-1")
    rec = record(chg, plan)
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"
    doc.modelspace().delete_entity(e)
    assert chg.judge_changes(plan, doc, {}).action == "conflict"
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "skip"


@pytest.mark.parametrize("edit", ["input", "op", "targets", "action", "damaged", "version"])
def test_judgement_rejects_invalid_or_mismatched_record(chg, doc, std, edit):
    e = line(doc)
    plan = resolve(chg, doc, std, [e])
    rec = record(chg, plan)
    if edit == "input":
        rec.input_hash = "0000000000000000"
    elif edit == "op":
        rec.op = "other"
    elif edit == "targets":
        rec.targets = {}
    elif edit == "action":
        rec.targets[e.dxf.handle].action = "delete"
    else:
        try:
            chg.parse_record(["arcaid-change-record/2" if edit == "version" else "arcaid-change-record/1", "broken"])
        except chg.RecordError as error:
            rec = error
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"


def test_manual_overwrite_only_allows_changes_already_in_base(chg, doc, std):
    e = line(doc)
    tag(e)
    e.dxf.end = (101, 0)
    plan = resolve(chg, doc, std, [e])
    decision = chg.judge_changes(plan, doc, {})
    assert decision.action == "conflict" and decision.manual == [e.dxf.handle]
    decision = chg.judge_changes(plan, doc, {}, overwrite_manual=True)
    assert decision.action == "apply" and decision.manual == [e.dxf.handle]
    e.dxf.end = (102, 0)
    assert chg.judge_changes(plan, doc, {}, overwrite_manual=True).action == "conflict"


def test_conflict_details_are_bounded(chg, doc, std):
    entities = [line(doc) for _ in range(25)]
    plan = resolve(chg, doc, std, entities)
    for e in entities:
        e.dxf.end = (200, 0)
    decision = chg.judge_changes(plan, doc, {})
    assert len(decision.details) == 21 and decision.details[-1] == "외 5개"


def test_record_roundtrip_with_korean_chunk_boundary_and_5000_handles(chg):
    rec = chg.ChangeRecord.model_validate({"op": "new-op", "in": "0123456789abcdef", "result": "applied",
        "targets": {f"{n + 0x10000:X}": {"action": "move", "after_hash": None} for n in range(5000)}})
    strings = chg.record_strings(rec)
    assert strings[0] == "arcaid-change-record/1"
    assert all(len(s) == 250 for s in strings[1:-1]) and len(strings[-1]) <= 250
    assert chg.parse_record(strings) == rec
    # 기록 변환은 유니코드 문자열도 보존한다(요청 op의 ASCII 제한과는 별개다).
    korean = rec.model_copy(update={"op": "한글" * 260})
    strings = chg.record_strings(korean)
    assert any("한" in s for s in strings[1:])
    assert chg.parse_record(strings) == korean


@pytest.mark.parametrize("strings", [[], ["other", "{}"], ["arcaid-change-record/2", "{}"],
                                      ["arcaid-change-record/1", "{"], ["arcaid-change-record/1", "[]"]])
def test_record_rejects_damaged_version_or_json(chg, strings):
    with pytest.raises(chg.RecordError):
        chg.parse_record(strings)


def test_read_records_from_actual_gstarcad_fixture(chg):
    path = Path(__file__).parent / "fixtures" / "b0_record.dxf"
    records = chg.read_records(ezdxf.readfile(path))
    rec = records["new-op"]
    assert isinstance(rec, chg.ChangeRecord)
    assert rec.op == "new-op" and rec.input_hash == "0123456789abcdef" and len(rec.targets) == 5000
    assert rec.targets["10000"].action == "move"


def test_read_records_keeps_errors_per_op(chg, doc):
    assert chg.read_records(doc) == {}
    root = doc.rootdict.add_new_dict("ARCAID")
    x = doc.objects.add_xrecord(owner=root.dxf.handle)
    root.add("bad", x)
    x.extend([(1, "arcaid-change-record/2"), (1, "{}")])
    assert isinstance(chg.read_records(doc)["bad"], chg.RecordError)


def test_skip_cannot_hide_changed_extrusion(chg, doc, std):
    e = line(doc)
    plan = resolve(chg, doc, std, [e])
    rec = record(chg, plan)
    e.dxf.start, e.dxf.end = (10, 20), (110, 20)
    e.dxf.extrusion = (0, 0, -1)
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"


@pytest.mark.parametrize("condition", ["source", "destination", "linetype", "relation", "missing_tag", "constant", "attdef"])
def test_recheck_uses_current_definitions_and_relations(chg, doc, std, condition):
    e, a, b = block(doc)
    doc.linetypes.new("B1_LINE", dxfattribs={"pattern": [0]})
    data = request([e.dxf.handle], action="set", props={"attribs": {"번호": "X2"}, "layer": "CON", "linetype": "B1_LINE"})
    data["changes"][0].pop("delta")
    plan = chg.resolve(chg.parse_changes(data), doc, std, "snap-1")
    if condition in ("source", "destination"):
        doc.layers.get("WAL" if condition == "source" else "CON").lock()
        code = "CHG_LAYER"
    elif condition == "linetype":
        doc.linetypes.remove("B1_LINE")
        code = "CHG_LINETYPE"
    elif condition == "relation":
        e.set_reactors(["FFFFFF"])
        code = "CHG_RELATION"
    else:
        code = "CHG_ATTRIB"
        if condition == "missing_tag":
            e.delete_attrib("번호")
        elif condition == "constant":
            a.dxf.flags = 2
        else:
            next(iter(b.query("ATTDEF"))).dxf.flags = 2
    assert code in {v.code for v in chg.recheck(plan, doc)}
    assert chg.judge_changes(plan, doc, {}).action == "conflict"


def test_skip_digest_uses_actual_verified_state_inside_tolerance(chg, doc, std):
    e = line(doc)
    plan = resolve(chg, doc, std, [e])
    rec = record(chg, plan)
    e.dxf.start, e.dxf.end = (10.005, 20), (110, 20)
    assert chg.same_state(plan.targets[0].after, chg.change_state(e))
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"
    rec.targets[e.dxf.handle].after_hash = chg.state_digest(chg.change_state(e))
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "skip"
    e.dxf.start = (10.02, 20)
    rec.targets[e.dxf.handle].after_hash = chg.state_digest(chg.change_state(e))
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"


def test_mixed_partial_apply_is_never_complete(chg, doc, std):
    a, b = line(doc), line(doc)
    t = doc.modelspace().add_text("객실", dxfattribs={"layer": "ROM", "height": 2})
    data = request([a.dxf.handle])
    data["changes"].extend([
        {"id": "c2", "select": {"handles": [b.dxf.handle]}, "action": "delete"},
        {"id": "c3", "select": {"handles": [t.dxf.handle]}, "action": "set", "props": {"text": "객실(A)"}}])
    plan = chg.resolve(chg.parse_changes(data), doc, std, "snap-1")
    rec = record(chg, plan)
    a.dxf.start, a.dxf.end = (10, 20), (110, 20)
    assert chg.judge_changes(plan, doc, {}).action == "conflict"
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"
    doc.modelspace().delete_entity(b)
    t.dxf.text = "객실(A)"
    assert chg.judge_changes(plan, doc, {}).action == "conflict"
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "skip"


def test_engine_after_skip_and_manual_state_rewrite(chg, doc, std):
    e = line(doc)
    tag(e)
    plan = resolve(chg, doc, std, [e])
    e.dxf.start, e.dxf.end = (10, 20), (110, 20)
    rec = record(chg, plan)
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"
    tag(e)
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "skip"
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).manual == []


def test_state_all_geometry_and_display_fields_are_checked(chg, doc):
    e = doc.modelspace().add_lwpolyline([(0, 0, 1, 2, .5), (1, 0, 2, 3, 0)], format="xyseb")
    a = chg.change_state(e)
    for key, value in [("closed", True), ("const_width", 1), ("elevation", 1), ("color", 2),
                       ("true_color", 0x123456), ("lineweight", 25)]:
        b = copy.deepcopy(a)
        b[key] = value
        assert not chg.same_state(a, b), key
    for index in (2, 3, 4):
        b = copy.deepcopy(a)
        b["points"][0][index] += .02
        assert chg.diff_fields(a, b) == ["points"]
    assert chg.same_state(None, None) and not chg.same_state(None, a)
    assert chg.state_digest(a) == chg.state_digest(dict(reversed(list(a.items()))))


def test_current_missing_target_and_blockspace_change_rejected(chg, doc, std):
    e = line(doc)
    plan = resolve(chg, doc, std, [e])
    doc.modelspace().move_to_layout(e, doc.blocks.new("MOVED"))
    assert chg.recheck(plan, doc)[0].code == "CHG_SELECT"
    assert chg.judge_changes(plan, doc, {}).action == "conflict"


@pytest.mark.parametrize("layout", ["paper", "block"])
def test_delete_record_requires_absence_from_entire_drawing(chg, doc, std, layout):
    e = line(doc)
    data = request([e.dxf.handle], action="delete")
    data["changes"][0].pop("delta")
    plan = chg.resolve(chg.parse_changes(data), doc, std, "snap-1")
    rec = record(chg, plan)
    destination = doc.layouts.new("B1_MOVED") if layout == "paper" else doc.blocks.new("B1_MOVED")
    doc.modelspace().move_to_layout(e, destination)
    assert e.is_alive and doc.entitydb.get(plan.targets[0].handle) is e
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"


@pytest.mark.parametrize("field,value", [("row_count", 2), ("column_count", 3), ("row_spacing", 10), ("column_spacing", 20)])
def test_insert_grid_fields_cannot_change_since_read_or_application(chg, doc, std, field, value):
    e, _, _ = block(doc)
    plan = resolve(chg, doc, std, [e])
    rec = record(chg, plan)
    setattr(e.dxf, field, value)
    assert chg.judge_changes(plan, doc, {}).action == "conflict"
    e.dxf.insert = (20, 40)
    a = e.attribs[0]
    a.dxf.align_point = (23, 44)
    assert chg.judge_changes(plan, doc, {"shift-1": rec}).action == "conflict"


@pytest.mark.parametrize("selector", ["handles", "oids", "prefix"])
@pytest.mark.parametrize("copy_space", ["model", "paper", "block"])
def test_copied_engine_oid_is_ambiguous_for_every_selector(chg, doc, std, selector, copy_space):
    a, b = line(doc), line(doc)
    tag(a)
    tag(b)
    if copy_space != "model":
        destination = doc.layouts.new("COPIED") if copy_space == "paper" else doc.blocks.new("COPIED")
        doc.modelspace().move_to_layout(b, destination)
    select = {"handles": {"handles": [a.dxf.handle]}, "oids": {"oids": ["draw-1:room/7/wall"]},
              "prefix": {"op": "draw-1", "path_prefix": "room/"}}[selector]
    rejected(chg, doc, std, request(select=select), "CHG_DUPLICATE")


@pytest.mark.parametrize("copy_space", ["model", "paper", "block"])
def test_copied_oid_after_base_conflicts_on_apply_and_skip(chg, doc, std, copy_space):
    e = line(doc)
    tag(e)
    plan = resolve(chg, doc, std, [e])
    copy_e = e.copy()
    doc.modelspace().add_entity(copy_e)
    if copy_space != "model":
        destination = doc.layouts.new("COPIED") if copy_space == "paper" else doc.blocks.new("COPIED")
        doc.modelspace().move_to_layout(copy_e, destination)
    assert "CHG_DUPLICATE" in {v.code for v in chg.recheck(plan, doc)}
    assert chg.judge_changes(plan, doc, {}).action == "conflict"
    e.dxf.start, e.dxf.end = (10, 20), (110, 20)
    tag(e)
    assert chg.judge_changes(plan, doc, {"shift-1": record(chg, plan)}).action == "conflict"


def test_multiline_attributes_are_outside_text_scope(chg, doc, std):
    doc.dxfversion = "AC1032"
    e, a, _ = block(doc)
    mtext = doc.modelspace().add_mtext("내용\\P둘째 줄", dxfattribs={"width": 10, "char_height": 2})
    a.set_mtext(mtext)
    assert a.has_embedded_mtext_entity
    rejected(chg, doc, std, request([e.dxf.handle]), "CHG_KIND")


def test_multiline_attribute_added_since_base_is_rejected(chg, doc, std):
    e, a, _ = block(doc)
    plan = resolve(chg, doc, std, [e])
    a.set_mtext(doc.modelspace().add_mtext("내용", dxfattribs={"width": 10}))
    assert "CHG_KIND" in {v.code for v in chg.recheck(plan, doc)}


@pytest.mark.parametrize("flags", [1, 2, 4, 8])
def test_attribute_flags_are_part_of_read_since_state(chg, doc, std, flags):
    e, a, _ = block(doc)
    plan = resolve(chg, doc, std, [e])
    a.dxf.flags = flags
    assert chg.diff_fields(plan.targets[0].before, chg.change_state(e)) == ["attribs"]
    assert chg.judge_changes(plan, doc, {}).action == "conflict"


def test_record_python_field_name_serializes_to_wire_alias(chg):
    rec = chg.ChangeRecord(op="test", input_hash="0123456789abcdef", result="applied", targets={})
    payload = json.loads("".join(chg.record_strings(rec)[1:]))
    assert payload["in"] == "0123456789abcdef" and "input_hash" not in payload


@pytest.mark.parametrize("kind", ["text", "insert", "polyline", "CIRCLE", "ARC"])
def test_explicit_cad_numeric_defaults_keep_record_digest(chg, doc, kind):
    if kind == "text":
        e = doc.modelspace().add_text("문자", dxfattribs={"height": 2})
        defaults = {"rotation": 0, "width": 1, "oblique": 0}
    elif kind == "insert":
        e, _, _ = block(doc)
        defaults = {"xscale": 1, "yscale": 1, "zscale": 1, "rotation": 0, "row_spacing": 0, "column_spacing": 0}
    elif kind == "polyline":
        e = doc.modelspace().add_lwpolyline([(0, 0), (1, 0)])
        defaults = {"const_width": 0, "elevation": 0}
    else:
        e = curve(doc, kind)
        e.dxf.discard("thickness")
        defaults = {"thickness": 0}
    before = chg.change_state(e)
    for field, value in defaults.items():
        setattr(e.dxf, field, value)
    after = chg.change_state(e)
    assert chg.same_state(before, after)
    assert chg.state_digest(before) == chg.state_digest(after)


def curve(doc, kind):
    center = (1000.000123, -2000.000456, 7.000789)
    attrs = {"layer": "WAL", "thickness": 2.000123}
    if kind == "CIRCLE":
        return doc.modelspace().add_circle(center, 1250.000123, dxfattribs=attrs)
    return doc.modelspace().add_arc(center, 1250.000123, 359.999999, 123.456789, dxfattribs=attrs)


@pytest.mark.parametrize("kind", ["CIRCLE", "ARC"])
def test_curve_move_keeps_raw_3d_geometry_and_judges_after(chg, doc, std, kind):
    e = curve(doc, kind)
    tag(e)
    original = chg.fingerprint(e)
    plan = resolve(chg, doc, std, [e])
    t = plan.targets[0]
    assert t.before["center"] == [1000.000123, -2000.000456, 7.000789]
    assert t.after["center"] == [1010.000123, -1980.000456, 7.000789]
    assert t.before["radius"] == t.after["radius"] == 1250.000123
    assert t.before["thickness"] == t.after["thickness"] == 2.000123
    assert t.after["extrusion"] == [0, 0, 1]
    if kind == "ARC":
        assert t.before["start_angle"] == t.after["start_angle"] == 359.999999
        assert t.before["end_angle"] == t.after["end_angle"] == 123.456789
    assert chg.fingerprint(e) == original
    assert chg.judge_changes(plan, doc, {}).action == "apply"
    e.translate(10, 20, 0)
    tag(e)
    assert chg.same_state(t.after, chg.change_state(e))
    assert chg.judge_changes(plan, doc, {}).action == "conflict"
    rec = record(chg, plan)
    assert chg.judge_changes(plan, doc, {plan.spec.op: rec}).action == "skip"
    e.dxf.center += (0, 0, .011)
    assert chg.judge_changes(plan, doc, {plan.spec.op: rec}).action == "conflict"


@pytest.mark.parametrize("kind", ["CIRCLE", "ARC"])
@pytest.mark.parametrize("field", ["center_x", "center_y", "center_z", "radius", "thickness"])
@pytest.mark.parametrize("distance,want", [(.009, True), (.01, True), (.011, False)])
def test_curve_lengths_use_mm_tolerance(chg, doc, kind, field, distance, want):
    e = curve(doc, kind)
    a = chg.change_state(e)
    if field.startswith("center_"):
        point = list(e.dxf.center)
        point["xyz".index(field[-1])] += distance
        e.dxf.center = point
    else:
        setattr(e.dxf, field, getattr(e.dxf, field) + distance)
    assert chg.same_state(a, chg.change_state(e)) is want


@pytest.mark.parametrize("kind", ["CIRCLE", "ARC"])
def test_curve_center_uses_3d_distance(chg, doc, kind):
    e = curve(doc, kind)
    before = chg.change_state(e)
    e.dxf.center += (.006, .006, .006)
    assert not chg.same_state(before, chg.change_state(e))
    assert "center" in chg.diff_fields(before, chg.change_state(e))


@pytest.mark.parametrize("kind", ["CIRCLE", "ARC"])
@pytest.mark.parametrize("field", ["center", "radius", "thickness"])
@pytest.mark.parametrize("distance,want", [(.009, "apply"), (.011, "conflict")])
def test_curve_judgement_uses_raw_length_tolerance(chg, doc, std, kind, field, distance, want):
    e = curve(doc, kind)
    plan = resolve(chg, doc, std, [e])
    if field == "center":
        e.dxf.center += (0, 0, distance)
    else:
        setattr(e.dxf, field, getattr(e.dxf, field) + distance)
    assert chg.judge_changes(plan, doc, {}).action == want


@pytest.mark.parametrize("field", ["start_angle", "end_angle"])
@pytest.mark.parametrize("radius", [1, 100000])
@pytest.mark.parametrize("distance,want", [(.009, True), (.01, True), (.011, False)])
def test_arc_angles_compare_wcs_endpoints_in_mm(chg, doc, field, radius, distance, want):
    e = doc.modelspace().add_arc((0, 0, 7), radius, 0, 180)
    before = chg.change_state(e)
    # 현 길이로 각도를 정한다. 작은 반지름의 큰 각도와 큰 반지름의 작은 각도를 함께 검증한다.
    setattr(e.dxf, field, getattr(e.dxf, field) + math.degrees(2 * math.asin(distance / (2 * radius))))
    after = chg.change_state(e)
    assert chg.same_state(before, after) is want
    assert chg.diff_fields(before, after) == ([] if want else [field])


@pytest.mark.parametrize("angles", [(0, 360), (359.999999, -.000001), (-90, 270)])
def test_arc_angles_wrap_at_zero(chg, doc, angles):
    e = doc.modelspace().add_arc((0, 0, 7), 100000, angles[0], 90)
    before = chg.change_state(e)
    e.dxf.start_angle = angles[1]
    assert chg.same_state(before, chg.change_state(e))


def test_arc_endpoint_includes_center_and_radius_changes(chg, doc):
    e = doc.modelspace().add_arc((0, 0, 7), 1000, 0, 90)
    before = chg.change_state(e)
    e.dxf.center = (0, .006, 7)
    e.dxf.start_angle = math.degrees(.006 / 1000)
    assert "start_angle" in chg.diff_fields(before, chg.change_state(e))
    e.dxf.center = (.006, 0, 7)
    e.dxf.start_angle = 0
    e.dxf.radius += .006
    assert "start_angle" in chg.diff_fields(before, chg.change_state(e))


@pytest.mark.parametrize("kind", ["CIRCLE", "ARC"])
@pytest.mark.parametrize("color", [2, {"rgb": [17, 83, 201]}])
def test_curves_set_common_display_properties_only_and_delete(chg, doc, std, kind, color):
    e = curve(doc, kind)
    data = request([e.dxf.handle], action="set", props=dict(layer="CON", color=color, linetype="BYLAYER", lineweight=50))
    data["changes"][0].pop("delta")
    t = chg.resolve(chg.parse_changes(data), doc, std, "snap-1").targets[0]
    assert (t.after["layer"], t.after["linetype"], t.after["lineweight"]) == ("con", "bylayer", 50)
    assert (t.after["color"], t.after["true_color"]) == ((2, None) if color == 2 else (None, 0x1153C9))
    for field in ("center", "radius", "thickness", "extrusion", "start_angle", "end_angle"):
        if field in t.before:
            assert t.after[field] == t.before[field]
    data["changes"][0].update(action="delete")
    data["changes"][0].pop("props")
    plan = chg.resolve(chg.parse_changes(data), doc, std, "snap-1")
    assert plan.targets[0].after is None
    rec = record(chg, plan)
    doc.modelspace().delete_entity(e)
    assert chg.judge_changes(plan, doc, {}).action == "conflict"
    assert chg.judge_changes(plan, doc, {plan.spec.op: rec}).action == "skip"


@pytest.mark.parametrize("kind", ["CIRCLE", "ARC"])
@pytest.mark.parametrize("props", [{"text": "문자"}, {"attribs": {"태그": "값"}}])
def test_curve_text_and_attribs_reject_entire_request(chg, doc, std, kind, props):
    good, e = line(doc), curve(doc, kind)
    data = request([good.dxf.handle, e.dxf.handle], action="set", props=props)
    data["changes"][0].pop("delta")
    violations = rejected(chg, doc, std, data, "CHG_KIND")
    assert any(v.handle == e.dxf.handle for v in violations)


@pytest.mark.parametrize("kind", ["CIRCLE", "ARC"])
@pytest.mark.parametrize("extrusion", [(0, 0, -1), (0, 1, 1)])
@pytest.mark.parametrize("action", ["move", "delete", "set"])
def test_curve_negative_or_tilted_extrusion_is_rejected(chg, doc, std, kind, extrusion, action):
    e = curve(doc, kind)
    data = request([e.dxf.handle], action=action)
    if action != "move":
        data["changes"][0].pop("delta")
    if action == "set":
        data["changes"][0]["props"] = {"layer": "CON"}
    plan = chg.resolve(chg.parse_changes(data), doc, std, "snap-1")
    e.dxf.extrusion = extrusion
    rejected(chg, doc, std, data, "CHG_KIND")
    assert any(v.code == "CHG_KIND" for v in chg.recheck(plan, doc))
    assert chg.judge_changes(plan, doc, {}, overwrite_manual=True).action == "conflict"
