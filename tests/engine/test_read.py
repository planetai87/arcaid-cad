"""도면 읽기(src/engine/read.py) 테스트: template.dxf 사본에 ezdxf로 그린 시험 도면"""
import json
from pathlib import Path

import ezdxf
import pytest
from ezdxf.enums import TextEntityAlignment

from src.engine.read import read_drawing
from src.engine.results import Status
from src.engine.standards import DEFAULT_PROFILE_DIR, load_standard

TEMPLATE = DEFAULT_PROFILE_DIR / "template.dxf"


@pytest.fixture(scope="module")
def std():
    return load_standard()


def make_plan(path: Path, insunits: int = 4) -> Path:
    doc = ezdxf.readfile(TEMPLATE)
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for e in list(msp):
        msp.delete_entity(e)
    cen = {"layer": "CEN"}
    msp.add_line((0, -450), (0, 6450), dxfattribs=cen)
    msp.add_line((6000, -450), (6000, 3000), dxfattribs=cen)           # X2는 두 조각
    msp.add_line((6000, 3000), (6000, 6450), dxfattribs=cen)
    msp.add_lwpolyline([(12000, -450), (12000, 6450)], dxfattribs=cen)  # 두 점 폴리선
    msp.add_line((15000, -450), (15000, 6450), dxfattribs=cen)         # 버블 없는 축
    msp.add_line((-450, 0), (12450, 0), dxfattribs=cen)
    msp.add_line((-450, 6000), (12450, 6000), dxfattribs=cen)
    msp.add_line((-450, -450), (500, 500), dxfattribs=cen)             # 사선 축선
    for i, x in enumerate((0, 6000, 12000), 1):
        msp.add_blockref("축열버블-상", (x, 7750), dxfattribs={"layer": "DIM"}).add_auto_attribs({"축열": f"X{i}"})
    for i, y in enumerate((0, 6000), 1):
        msp.add_blockref("축열버블-좌", (-1750, y), dxfattribs={"layer": "DIM"}).add_auto_attribs({"축열": f"Y{i}"})
    msp.add_blockref("축열버블-상", (30000, 7750), dxfattribs={"layer": "DIM"}).add_auto_attribs({"축열": "X9"})  # 축 없음
    t = msp.add_text("객실 101", height=135, dxfattribs={"layer": "ROM", "style": "돋움"})
    t.set_placement((3000, 3000), align=TextEntityAlignment.MIDDLE_CENTER)
    msp.add_mtext("객실 102", dxfattribs={"layer": "ROM", "char_height": 135, "insert": (9000, 3000)})
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "A-CEN"})        # 비표준 레이어
    msp.add_blockref("계단화살표", (20000, 0), dxfattribs={"layer": "DIM", "xscale": 1000, "yscale": 1000})
    doc.paperspace().add_line((0, 0), (10, 10))
    doc.blocks.new("XREF_SITE", dxfattribs={"flags": 4, "xref_path": "..\\site.dwg"})
    doc.saveas(str(path))
    return path


def test_grid_axes_merged_and_named(tmp_path, std):
    r = read_drawing(make_plan(tmp_path / "plan.dxf"), std)
    assert r.status is Status.SUCCESS
    xs = [(a.name, a.position) for a in r.grid if a.direction == "X"]
    ys = [(a.name, a.position) for a in r.grid if a.direction == "Y"]
    assert xs == [("X1", 0), ("X2", 6000), ("X3", 12000), (None, 15000)]
    assert ys == [("Y1", 0), ("Y2", 6000)]
    x2 = next(a for a in r.grid if a.name == "X2")
    assert (x2.start, x2.end) == (-450, 6450) and len(x2.handles) == 2


def test_unchecked_reports_unnamed_axis_stray_bubble_and_skewed_line(tmp_path, std):
    r = read_drawing(make_plan(tmp_path / "plan.dxf"), std)
    items = {u.item: u.count for u in r.unchecked}
    assert items["이름 없는 축"] == 1
    assert items["축에 맞지 않는 버블"] == 1
    assert items["사선 축선"] == 1


def test_rooms_from_text_and_mtext(tmp_path, std):
    r = read_drawing(make_plan(tmp_path / "plan.dxf"), std)
    rooms = {x.text: x.position[:2] for x in r.rooms}
    assert rooms == {"객실 101": [3000, 3000], "객실 102": [9000, 3000]}


def test_layers_blocks_xrefs_and_scope(tmp_path, std):
    r = read_drawing(make_plan(tmp_path / "plan.dxf"), std)
    assert r.nonstandard_layers == ["A-CEN"]
    assert {b.name: b.count for b in r.blocks}["축열버블-상"] == 4
    assert [(x.name, x.path) for x in r.xrefs] == [("XREF_SITE", "..\\site.dwg")]
    scope = {o.item: o.reason for o in r.out_of_scope}
    assert "레이아웃" in scope and "1" in scope["레이아웃"]
    assert {"Xref 내부 객체", "블록 정의 내부", "벽체 인식", "창호·문 종류", "비표준 레이어의 의미 해석"} <= set(scope)
    assert any("단위 섞임" in w for w in r.warnings)
    assert r.meta.paperspace_count == 1


@pytest.mark.parametrize("insunits,warned", [(4, False), (6, True), (0, True)])
def test_unit_warning(tmp_path, std, insunits, warned):
    r = read_drawing(make_plan(tmp_path / "plan.dxf", insunits), std)
    assert any("단위" in w and "섞임" not in w for w in r.warnings) is warned
    assert r.meta.insunits == insunits


def test_full_extract_written_to_file(tmp_path, std):
    full = tmp_path / "plan.read.json"
    r = read_drawing(make_plan(tmp_path / "plan.dxf"), std, full_out=full)
    data = json.loads(full.read_text(encoding="utf-8"))
    assert r.full_path == str(full)
    assert len(data["entities"]) == r.meta.modelspace_count
    bubble = next(e for e in data["entities"] if e.get("block") == "축열버블-좌")
    assert bubble["attribs"] == {"축열": "Y1"} and bubble["layer"] == "DIM"


def test_full_extract_creates_missing_folder(tmp_path, std):
    full = tmp_path / "work" / "snapshots" / "plan.read.json"
    r = read_drawing(make_plan(tmp_path / "plan.dxf"), std, full_out=full)
    assert full.exists() and r.full_path == str(full)


def test_full_extract_write_failure_is_warning(tmp_path, std):
    blocker = tmp_path / "blocker"
    blocker.write_text("파일이라 폴더를 만들 수 없음", encoding="utf-8")
    r = read_drawing(make_plan(tmp_path / "plan.dxf"), std, full_out=blocker / "plan.read.json")
    assert r.status is Status.SUCCESS and r.full_path is None
    assert any("전체 목록" in w for w in r.warnings)


def test_unreadable_file_fails(tmp_path, std):
    bad = tmp_path / "bad.dxf"
    bad.write_text("not a dxf", encoding="utf-8")
    r = read_drawing(bad, std)
    assert r.status is Status.FAILED and not r.is_complete()
