"""T10 로컬 준비 도구: 실제 DWG 없이 변환·독립 비교의 실패를 검출한다."""
import math

import ezdxf
import pytest

from tools.acceptance import t10_dangsan as t10


def test_ellipse_approximation_has_proven_error_bound():
    doc = ezdxf.new()
    ellipse = doc.modelspace().add_ellipse((15, -4), major_axis=(1200, 300), ratio=0.21,
                                          start_param=0.2, end_param=5.9)
    points, proof = t10.ellipse_polyline(ellipse, tolerance=0.01)
    assert proof["max_error_bound_mm"] <= 0.01
    assert points[0] == pytest.approx(tuple(next(ellipse.vertices([0.2])))[:2])
    # 생성기와 다른 조밀한 표본으로 곡선→선분 거리를 확인한다.
    for p in ellipse.vertices([0.2 + 5.7 * i / 2000 for i in range(2001)]):
        assert min(t10.segment_distance(p, a, b) for a, b in zip(points, points[1:])) <= 0.01


@pytest.mark.parametrize("sx,sy,mirror,rotation", [(1, 1, None, 0), (-1, 1, "x", 0),
                                                  (1, -1, "y", 0), (-1, -1, None, 180)])
def test_signed_block_pose_uses_supported_group_transform(sx, sy, mirror, rotation):
    d = ezdxf.new(); d.blocks.new("detail")
    e = d.modelspace().add_blockref("detail", (100, 200), dxfattribs={"xscale": sx, "yscale": sy})
    pose = t10.block_place(e, (0.03, -0.32))
    assert pose.get("mirror") == mirror
    assert pose.get("rotation", 0) == rotation
    assert pose["at"] == pytest.approx((100.03, 199.68))


def test_room_count_excludes_area_ps_and_other_names():
    d = ezdxf.new(); m = d.modelspace()
    for text in ("객실(A)", "객실(A)", "객실(B)", "PS", "21.60㎡", "객실(C)"):
        m.add_text(text, dxfattribs={"layer": "ROM"})
    m.add_text("객실(A)", dxfattribs={"layer": "TXT"})
    assert t10.room_counts(d) == {"A": 2, "B": 1}


def test_geometry_comparison_detects_hatch_boundary_change_and_missing_duplicate():
    a = ezdxf.new(); b = ezdxf.new()
    for d in (a, b):
        d.modelspace().add_line((0, 0), (1, 2))
        d.modelspace().add_line((0, 0), (1, 2))
        h = d.modelspace().add_hatch()
        h.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert t10.compare_documents(a, b)["equal"]
    b.modelspace().query("HATCH")[0].paths[0].vertices[1] = (11, 0, 0)
    assert not t10.compare_documents(a, b)["equal"]
    b.modelspace().delete_entity(b.modelspace().query("LINE")[0])
    assert t10.compare_documents(a, b)["missing"] > 0


def test_axis_alignment_checks_every_axis_not_just_origin():
    source = {"X": {"X1": 1.03, "X2": 3601.03}, "Y": {"Y1": 2.68, "Y2": 6002.68}}
    ref = {"X": {"X1": 1, "X2": 3601}, "Y": {"Y1": 3, "Y2": 6003}}
    delta, rows = t10.align_axes(source, ref)
    assert delta == pytest.approx((0.03, -0.32))
    assert max(x["residual_mm"] for x in rows) < 1e-8
    ref["X"]["X2"] += 0.02
    with pytest.raises(ValueError, match="축"):
        t10.align_axes(source, ref)


def test_selection_uses_full_extents_and_explicit_layers():
    d = ezdxf.new(); m = d.modelspace()
    wanted = m.add_line((10, 10), (20, 20), dxfattribs={"layer": "WA1"})
    m.add_lwpolyline([(-200, -200), (200, -200), (200, 200), (-200, 200)], close=True,
                     dxfattribs={"layer": "WA1"})
    m.add_line((10, 10), (20, 20), dxfattribs={"layer": "대지안의 공지"})
    assert [e.dxf.handle for e in t10.select_core(d, bounds=(0, 0, 100, 100))] == [wanted.dxf.handle]


def test_entity_conversion_preserves_mirrored_arc_and_rejects_unsupported():
    from ezdxf.math import Matrix44
    from src.engine.standards import load_standard
    from src.engine.write import build_dxf
    d = ezdxf.new()
    e = d.modelspace().add_arc((10, 20), 50, 20, 115)
    e.transform(Matrix44.scale(-1, 1, 1))
    item = t10.entity_item(e, "DOR", [])
    assert item["center"] == pytest.approx((-10, 20))
    assert item["start_angle"] == pytest.approx(65)
    assert item["end_angle"] == pytest.approx(160)
    with pytest.raises(ValueError, match="지원"):
        t10.entity_item(d.modelspace().add_point((0, 0)), "DOR", [])


def test_core_block_internal_text_gets_supported_text_layer():
    d = ezdxf.new()
    e = d.modelspace().add_text("ELEVATOR", dxfattribs={"insert": (30000, 22000)})
    item = t10.entity_item(e, "CIR", [])
    assert item["layer"] == "TXT" and item["role"] == "general"
    assert item["at"] == [30000, 22000] and item["content"] == "ELEVATOR"


def test_reference_expectation_detects_perturbed_and_missing_geometry():
    d = ezdxf.new(); m = d.modelspace()
    e = m.add_line((10, 20), (30, 40))
    expectation = {"x": t10.expected_record(e, (0.03, -0.32))}
    out = ezdxf.new(); actual = out.modelspace().add_line((10.03, 19.68), (30.03, 39.68))
    assert t10.verify_geometry(expectation, {"x": actual})["passed"]
    actual.dxf.end = (30.03, 39.70)
    assert not t10.verify_geometry(expectation, {"x": actual})["passed"]
    assert not t10.verify_geometry(expectation, {})["passed"]


def test_hatch_geometry_ignores_vertex_start_and_winding_but_checks_island():
    a = ezdxf.new(); b = ezdxf.new()
    h1 = a.modelspace().add_hatch(); h2 = b.modelspace().add_hatch()
    h1.paths.add_polyline_path([(0, 0), (20, 0), (20, 20), (0, 20)])
    h1.paths.add_polyline_path([(5, 5), (5, 8), (8, 8), (8, 5)], flags=16)
    h2.paths.add_polyline_path([(20, 20), (20, 0), (0, 0), (0, 20)])
    h2.paths.add_polyline_path([(8, 5), (8, 8), (5, 8), (5, 5)], flags=16)
    assert t10.geometry_error(t10.expected_record(h1), h2) == 0
    h2.paths[1].vertices[0] = (9, 5, 0)
    assert t10.geometry_error(t10.expected_record(h1), h2) >= 1


def test_reference_annotation_manifest_preserves_dimension_and_bubble_meaning():
    d = ezdxf.new(); m = d.modelspace()
    m.add_linear_dim(base=(13259, 36044), p1=(9659, 34244), p2=(13259, 34244),
                     dxfattribs={"layer": "DIM"})
    d.blocks.new("축열버블-상")
    b = m.add_blockref("축열버블-상", (9659, 36844), dxfattribs={"layer": "DIM"})
    b.add_attrib("축열", "X1")
    expected = t10.reference_annotations(d, (0.03, -0.32), {"X": {"X1": 9659, "X2": 13259, "X3": 16859}, "Y": {}})
    assert expected["x-chain.1"]["p2"] == pytest.approx([13259.03, 34243.68])
    assert expected["X1"]["attribs"] == [["축열", "X1"]]


def test_repeat_compression_preserves_gaps_and_mirror_modes():
    places = [{"at": [x, 5], "vars": {"room": {"start": n}}} for n, x in enumerate((10, 7210, 14410, 30000))]
    places.append({"at": [37200, 5], "mirror": "x", "vars": {"room": {"start": 4}}})
    packed = t10.compress_places(places)
    assert packed[0]["repeat"] == {"count": 3, "step": [7200, 0]}
    assert len(packed) == 3
    assert packed[-1]["mirror"] == "x"


def test_reference_room_chunks_fail_on_missing_bath_and_classify_b_type():
    d = ezdxf.new(); m = d.modelspace()
    for name in t10.DETAIL_LAYERS:
        d.blocks.new(name).add_line((0, 0), (1, 1))
        m.add_blockref(name, (100, 200), dxfattribs={"layer": t10.DETAIL_LAYERS[name]})
    m.add_text("객실(B)", dxfattribs={"layer": "ROM"})
    m.add_text("19.80㎡", dxfattribs={"layer": "ROM"})
    chunks = t10.room_chunks(d)
    assert chunks[0]["type"] == "B" and len(chunks[0]["entities"]) == 6
    m.delete_entity(m.query('INSERT[name=="HEO_BATH"]')[0])
    with pytest.raises(ValueError, match="상세"):
        t10.room_chunks(d)


def test_prepare_uses_real_profile_and_frozen_reference_expectation(tmp_path):
    import json
    from src.engine.standards import load_standard
    from src.engine.write import build_dxf
    from src.engine.state import arcaid_tags
    source, ref = ezdxf.new(), ezdxf.new()
    for i in range(16):
        x = 9659 + 3600 * i if i < 15 else 63359
        source.modelspace().add_line((x + 0.03, 19643.68), (x + 0.03, 33343.68), dxfattribs={"layer": "A-CEN"})
        ref.modelspace().add_line((x, 18744), (x, 34244), dxfattribs={"layer": "CEN"})
    for y in (19644, 25644, 27344, 33344):
        source.modelspace().add_line((9659.03, y - 0.32), (63359.03, y - 0.32), dxfattribs={"layer": "A-CEN"})
        ref.modelspace().add_line((8759, y), (64259, y), dxfattribs={"layer": "CEN"})
    source.modelspace().add_line((30000, 20000), (31000, 21000), dxfattribs={"layer": "WA1"})
    for name in t10.DETAIL_LAYERS:
        b = ref.blocks.new(name)
        b.add_line((1, 2), (3, 4))
        if name == "HEO_BATH":
            b.add_ellipse((8, 6), major_axis=(10, 0), ratio=0.4)
    for n in range(27):
        for name, layer in t10.DETAIL_LAYERS.items():
            offset = (100 if n % 2 == 0 else -100) if name in ("HEO_DOOR", "HEO_WIN") else 0
            ref.modelspace().add_blockref(name, (10000 + n * 500 + offset, 28000),
                                          dxfattribs={"layer": layer, "xscale": -1 if n % 2 else 1})
        ref.modelspace().add_text("객실(A)" if n < 25 else "객실(B)",
                                  dxfattribs={"layer": "ROM", "insert": (10100 + n * 500, 32000)})
        ref.modelspace().add_text("21.60㎡", dxfattribs={"layer": "ROM", "insert": (10100 + n * 500, 31700)})
    source.saveas(tmp_path / "source.dxf"); ref.saveas(tmp_path / "ref.dxf")
    out = tmp_path / "prepared"
    manifest = t10.prepare(tmp_path / "source.dxf", tmp_path / "ref.dxf", out, load_standard())
    assert manifest["baseline_ready"], manifest["baseline_check"]
    assert manifest["specs"]["t10-rooms"]["objects"] == 135
    assert manifest["specs"]["t10-rooms"]["depth"] <= 4
    spec = json.loads((out / "t10-rooms.json").read_text(encoding="utf-8"))
    assert any(place.get("repeat", {}).get("count", 1) > 1
               for group in spec["items"] for place in group.get("place", []))
    built = build_dxf(spec, load_standard(), tmp_path / "rooms.dxf")
    assert built.status.value == "success", built.summary
    actual = ezdxf.readfile(tmp_path / "rooms.dxf")
    verified = t10.verify_geometry(manifest["room_expectation"],
                                   {arcaid_tags(e)["path"]: e for e in actual.modelspace()})
    assert verified["passed"], verified
