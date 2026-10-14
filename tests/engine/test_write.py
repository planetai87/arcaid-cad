"""DXF 작성(src/engine/write.py) 테스트: 명세 → DXF → 다시 읽기 왕복, 결정성(골든), 정의 최소, 자체 검사

골든(tests/engine/golden/build_dangsan.json)은 ARCAID_UPDATE_GOLDEN=1일 때만 다시 쓴다.
다시 만들면 객실 위치·번호·대칭 좌표를 손으로 계산한 값과 대조한 뒤 커밋한다.
"""
import copy
import json
import os
from pathlib import Path

import ezdxf
import pytest

from src.engine import ENGINE_VERSION
from src.engine.check import check_standard
from src.engine.read import read_drawing
from src.engine.results import Status
from src.engine.spec import parse_spec, spec_hash
from src.engine.state import state_hash
from src.engine.standards import load_standard
from src.engine.write import build_dxf

HERE = Path(__file__).parent
SPEC = json.loads((HERE / "specs" / "dangsan_typical.json").read_text(encoding="utf-8"))
GOLDEN = HERE / "golden" / "build_dangsan.json"


@pytest.fixture(scope="module")
def std():
    return load_standard()


@pytest.fixture(scope="module")
def built(tmp_path_factory, std):
    out = tmp_path_factory.mktemp("build") / "dangsan.dxf"
    result = build_dxf(SPEC, std, out)
    assert result.status is Status.SUCCESS, [v.message for v in result.violations]
    return result, out, ezdxf.readfile(out)


def tags(e):
    return dict(v.split("=", 1) for _, v in e.get_xdata("ARCAID"))


def _r(v):
    return [round(float(v[0]), 3), round(float(v[1]), 3)]


def dump(doc):
    """객체·좌표·태그의 정준 덤프(핸들 제외)"""
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        rec = {"tags": tags(e), "type": t, "layer": e.dxf.layer, "linetype": e.dxf.get("linetype", "BYLAYER"),
               "color": e.dxf.get("color", 256)}
        if t == "LINE":
            rec.update(start=_r(e.dxf.start), end=_r(e.dxf.end))
        elif t == "LWPOLYLINE":
            rec.update(points=[[round(x, 3), round(y, 3), round(b, 6)] for x, y, b in e.get_points("xyb")],
                       closed=e.closed)
        elif t in ("CIRCLE", "ARC"):
            rec.update(center=_r(e.dxf.center), radius=round(e.dxf.radius, 3))
            if t == "ARC":
                rec.update(angles=[round(e.dxf.start_angle, 3), round(e.dxf.end_angle, 3)])
        elif t == "TEXT":
            rec.update(text=e.dxf.text, height=round(e.dxf.height, 3), rotation=round(e.dxf.rotation, 3),
                       style=e.dxf.style, halign=e.dxf.halign, valign=e.dxf.valign, insert=_r(e.dxf.insert),
                       align_point=_r(e.dxf.align_point) if e.dxf.hasattr("align_point") else None)
        elif t == "INSERT":
            rec.update(name=e.dxf.name, insert=_r(e.dxf.insert), xscale=e.dxf.xscale, yscale=e.dxf.yscale,
                       rotation=round(e.dxf.rotation, 3), attribs={a.dxf.tag: a.dxf.text for a in e.attribs})
        elif t == "HATCH":
            rec.update(pattern=e.dxf.pattern_name, solid=e.dxf.solid_fill, style=e.dxf.hatch_style,
                       scale=e.dxf.get("pattern_scale"), angle=e.dxf.get("pattern_angle"),
                       paths=[[p.path_type_flags, [_r(v) for v in p.vertices]] for p in e.paths])
        elif t == "DIMENSION":
            o = e.override()
            rec.update(dimstyle=e.dxf.dimstyle, p1=_r(e.dxf.defpoint2), p2=_r(e.dxf.defpoint3),
                       base=_r(e.dxf.defpoint), angle=round(e.dxf.angle, 3), dimscale=o.get("dimscale"),
                       measurement=round(e.get_measurement(), 3))
        out.append(rec)
    return sorted(out, key=lambda r: r["tags"]["oid"])


def _area(points):
    pts = [(p[0], p[1]) for p in points]
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])) / 2


def test_build_result_and_manifest(built):
    result, out, doc = built
    assert result.meta.object_count == len(result.manifest) == len(doc.modelspace()) == 107
    assert result.meta.by_kind == {"arc": 10, "block": 7, "dim": 7, "hatch": 1, "line": 41, "polyline": 21, "text": 20}
    assert result.ids.request_id == "dangsan-typical" and result.meta.spec_hash == spec_hash(parse_spec(SPEC))
    assert result.is_complete() and result.meta.out_path == str(out)


def test_roundtrip_header_tags_and_bylayer(built, std):
    result, _, doc = built
    assert doc.dxfversion == "AC1027"
    assert doc.header["$INSUNITS"] == 4 and doc.header["$LTSCALE"] == 100
    h = spec_hash(parse_spec(SPEC))
    oids = set()
    for e in doc.modelspace():
        t = tags(e)
        assert set(t) == {"op", "oid", "path", "ver", "in", "state"}
        assert (t["op"], t["ver"], t["in"]) == ("dangsan-typical", ENGINE_VERSION, h)
        assert e.dxf.get("color", 256) == 256 and not e.dxf.hasattr("true_color")
        oids.add(t["oid"])
    assert oids == {m.oid for m in result.manifest}
    (hatch,) = doc.modelspace().query("HATCH")
    assert [p.path_type_flags & 17 for p in hatch.paths] == [1, 16] and hatch.dxf.hatch_style == 0
    outer, hole = ([(v[0], v[1]) for v in p.vertices] for p in hatch.paths)
    assert _area(outer) > 0 > _area(hole)
    assert {d.override().get("dimscale") for d in doc.modelspace().query("DIMENSION")} == {50.0}


def test_only_needed_definitions(built, std):
    _, _, doc = built
    template = ezdxf.readfile(std.path(std.profile.template.dxf))
    used = {"CEN", "WAL", "WAL1", "DOR", "WIN", "ROM", "CON", "HAT", "DIM", "0", "Defpoints"}
    inner = {e.dxf.layer for e in template.blocks.get("축열버블-상")}
    assert {x.dxf.name.casefold() for x in doc.layers} == {x.casefold() for x in used | inner}
    assert sorted(b.name for b in doc.blocks if not b.name.startswith("*")) == ["dots", "축열버블-상"]
    assert doc.styles.get("돋움").get_extended_font_data()[0] == "Dotum"


def test_generated_dxf_passes_standard_check_and_reads(built, std):
    _, out, _ = built
    check = check_standard(out, std, k=100)
    assert check.violations == [] and check.is_complete()
    read = read_drawing(out, std)
    assert {a.name: a.position for a in read.grid if a.direction == "X"} == \
        {f"X{n}": 3300.0 * (n - 1) for n in range(1, 8)}
    names = sorted(r.text for r in read.rooms if r.text.startswith("객실"))
    assert names == [f"객실 {n}" for n in range(301, 311)]


def test_same_spec_same_dxf_and_golden(built, std, tmp_path):
    _, _, doc = built
    again = build_dxf(json.loads(json.dumps(SPEC, sort_keys=True)), std, tmp_path / "again.dxf")
    first = dump(doc)
    assert dump(ezdxf.readfile(tmp_path / "again.dxf")) == first
    actual = json.loads(json.dumps(first, ensure_ascii=False))
    if os.environ.get("ARCAID_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    assert actual == json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert again.meta.spec_hash == spec_hash(parse_spec(SPEC))


def test_scale_200(std, tmp_path):
    spec = copy.deepcopy(SPEC)
    spec["scale"] = 200
    out = tmp_path / "k200.dxf"
    assert build_dxf(spec, std, out).status is Status.SUCCESS
    check = check_standard(out, std, k=200)
    assert check.violations == [] and check.is_complete()
    doc = ezdxf.readfile(out)
    msp = doc.modelspace()
    assert doc.header["$LTSCALE"] == 200
    assert {t.dxf.height for t in msp.query("TEXT")} == {270.0, 210.0}
    assert {b.dxf.xscale for b in msp.query("INSERT")} == {2.0}
    assert {d.override().get("dimscale") for d in msp.query("DIMENSION")} == {100.0}


def test_rejected_spec_writes_nothing(std, tmp_path):
    spec = copy.deepcopy(SPEC)
    spec["items"][2]["items"][0]["layer"] = "A-WALL"
    out = tmp_path / "bad.dxf"
    r = build_dxf(spec, std, out)
    assert r.status is Status.FAILED and [v.code for v in r.violations] == ["SPEC_LAYER"] and not out.exists()
    spec = copy.deepcopy(SPEC)
    spec["items"].append({"kind": "mtext", "layer": "TXT"})
    r = build_dxf(spec, std, out)
    assert r.status is Status.FAILED and r.violations[0].code == "SPEC_SCHEMA" and not out.exists()


def test_state_tag_matches_entity(built):
    _, _, doc = built
    for e in doc.modelspace():
        assert tags(e)["state"] == state_hash(e)


def test_layer_definition_dxf(std, tmp_path):
    from src.engine.definitions import compare_definitions
    from src.engine.write import build_layer_dxf
    out = build_layer_dxf(std, ["cen", "LIM", "WAL"], tmp_path / "layers.dxf")
    doc = ezdxf.readfile(out)
    assert len(doc.modelspace()) == 0
    assert {x.dxf.name for x in doc.layers} == {"0", "Defpoints", "CEN", "LIM", "WAL"}
    assert {"CEN", "HID"} <= {x.dxf.name for x in doc.linetypes}
    assert [v for v in compare_definitions(std, doc, layers=["CEN", "LIM", "WAL"], dimstyles=[], blocks=[],
                                           text_style=False) if not v.code.endswith("_MISSING")] == []
