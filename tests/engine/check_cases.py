"""표준 검사 시험 도면: template.dxf 사본에 ezdxf로 그리고 {이름표: 핸들}을 돌려준다

이름표는 골든 파일(tests/engine/golden/check_*.json)이 핸들 대신 쓰는 객체 이름이다.
"""
from pathlib import Path
from typing import Dict, Tuple

import ezdxf
from ezdxf.enums import TextEntityAlignment

from src.engine.standards import DEFAULT_PROFILE_DIR

TEMPLATE = DEFAULT_PROFILE_DIR / "template.dxf"
ANNOTATIVE_XDATA = [(1000, "AnnotativeData"), (1002, "{"), (1070, 1), (1070, 1), (1002, "}")]


def _new(insunits: int = 4):
    doc = ezdxf.readfile(TEMPLATE)
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for e in list(msp):
        msp.delete_entity(e)
    return doc, msp


def _dim(msp, x: float, dimstyle: str, layer: str, override=None):
    d = msp.add_linear_dim(base=(x + 500, -1350), p1=(x, 0), p2=(x + 1000, 0), dimstyle=dimstyle,
                           override=override or {}, dxfattribs={"layer": layer})
    d.render()
    return d.dimension


def _text(msp, text: str, x: float, height: float, layer: str, style: str = "돋움"):
    t = msp.add_text(text, height=height, dxfattribs={"layer": layer, "style": style})
    t.set_placement((x, 3000), align=TextEntityAlignment.MIDDLE_CENTER)
    return t


def clean(path: Path, k: int = 100, insunits: int = 4) -> Tuple[Path, Dict[str, str]]:
    """표준대로 그린 도면(축척 1/k)"""
    s = k / 100
    doc, msp = _new(insunits)
    doc.header["$LTSCALE"] = float(k)
    lab = {}
    lab["cen_x1"] = msp.add_line((0, -450 * s), (0, 6450), dxfattribs={"layer": "CEN"}).dxf.handle
    lab["cen_y1"] = msp.add_line((-450 * s, 0), (6450, 0), dxfattribs={"layer": "CEN"}).dxf.handle
    bubble = msp.add_blockref("축열버블-상", (0, 7750 * s), dxfattribs={"layer": "DIM", "xscale": s, "yscale": s})
    bubble.add_auto_attribs({"축열": "X1"})
    lab["bubble"] = bubble.dxf.handle
    lab["wall"] = msp.add_lwpolyline([(0, 0), (6000, 0), (6000, 6000)], dxfattribs={"layer": "WAL"}).dxf.handle
    lab["lim_phantom"] = msp.add_line((0, -5000), (9000, -5000),
                                      dxfattribs={"layer": "LIM", "linetype": "PHANTOM"}).dxf.handle
    lab["room"] = _text(msp, "객실 101", 3000, 135 * s, "ROM").dxf.handle
    lab["title"] = _text(msp, "기준층 평면도", 3000, 255 * s, "TIT1").dxf.handle
    lab["lev"] = msp.add_blockref("LEV", (8000, 0), dxfattribs={"layer": "LEV", "xscale": 0.5 * s,
                                                                 "yscale": 0.5 * s}).dxf.handle
    lab["sheet"] = msp.add_blockref("HEOarchitects_sheet", (-5000, -5000),
                                    dxfattribs={"layer": "TIT", "xscale": s, "yscale": s}).dxf.handle
    lab["dim330"] = _dim(msp, 0, "DIM330", "DIM", {"dimscale": k / 2}).dxf.handle
    lab["dim270"] = _dim(msp, 3000, "DIM270", "DIM1", {"dimscale": k / 2}).dxf.handle
    doc.saveas(str(path))
    return path, lab


def entities(path: Path) -> Tuple[Path, Dict[str, str]]:
    """레이어·색·선종류·선굵기 위반"""
    doc, msp = _new()
    lab = {}

    def line(name, y, **attribs):
        lab[name] = msp.add_line((0, y), (1000, y), dxfattribs=attribs).dxf.handle

    line("nonstd", 0, layer="A-WALL")
    line("nonstd_red", 100, layer="A-WALL", color=1)
    line("layer0", 200, layer="0")
    line("defpoints", 300, layer="Defpoints")
    line("red", 400, layer="WAL", color=1)
    line("byblock", 500, layer="WAL", color=0)
    lab["truecolor"] = msp.add_line((0, 600), (1000, 600), dxfattribs={"layer": "WAL"}).dxf.handle
    msp.entitydb[lab["truecolor"]].rgb = (255, 0, 0)
    line("dashed", 700, layer="WAL", linetype="DASHED")
    line("lim_phantom", 800, layer="LIM", linetype="PHANTOM")
    line("lim_dashed", 900, layer="LIM", linetype="DASHED")
    line("lineweight", 1000, layer="WAL", lineweight=50)
    line("pat_lower", 1100, layer="pat")
    line("ok", 1200, layer="WAL")
    doc.saveas(str(path))
    return path, lab


def text(path: Path) -> Tuple[Path, Dict[str, str]]:
    """문자 스타일·높이 위반과 검사하지 못하는 문자"""
    doc, msp = _new()
    lab = {}
    lab["style"] = _text(msp, "a", 0, 135, "ROM", style="Standard").dxf.handle
    lab["rom200"] = _text(msp, "b", 1000, 200, "ROM").dxf.handle
    lab["rem200"] = _text(msp, "c", 2000, 200, "REM").dxf.handle
    lab["con_text"] = _text(msp, "d", 3000, 135, "CON").dxf.handle
    lab["rom105"] = _text(msp, "e", 4000, 105, "ROM").dxf.handle
    lab["sheet_number"] = _text(msp, "A-101", 5000, 400, "TIT1").dxf.handle
    lab["mtext_h"] = msp.add_mtext("{\\H2x;큰}글", dxfattribs={"layer": "ROM", "char_height": 135, "style": "돋움",
                                                              "insert": (6000, 3000)}).dxf.handle
    lab["mtext_ok"] = msp.add_mtext("UP", dxfattribs={"layer": "TXT", "char_height": 150, "style": "돋움",
                                                       "insert": (7000, 3000)}).dxf.handle
    doc.saveas(str(path))
    return path, lab


def dims_blocks(path: Path) -> Tuple[Path, Dict[str, str]]:
    """치수 스타일·레이어·축척·재정의, 주석 객체, 블록 레이어·배율·안 쓰는 블록"""
    doc, msp = _new()
    if not doc.appids.has_entry("AcadAnnotative"):
        doc.appids.new("AcadAnnotative")
    lab = {}
    lab["std_style"] = _dim(msp, 0, "Standard", "DIM").dxf.handle
    lab["dim330_on_dim1"] = _dim(msp, 2000, "DIM330", "DIM1").dxf.handle
    lab["dim330_on_txt"] = _dim(msp, 4000, "DIM330", "TXT").dxf.handle
    lab["dimscale100"] = _dim(msp, 6000, "DIM330", "DIM", {"dimscale": 100}).dxf.handle
    lab["dimtxt5"] = _dim(msp, 8000, "DIM330", "DIM", {"dimtxt": 5}).dxf.handle
    annotative = _dim(msp, 10000, "DIM330", "DIM", {"dimscale": 100})
    annotative.set_xdata("AcadAnnotative", ANNOTATIVE_XDATA)
    lab["annotative"] = annotative.dxf.handle
    lab["dim_ok"] = _dim(msp, 12000, "DIM330", "DIM").dxf.handle
    lab["bubble_layer0"] = msp.add_blockref("축열버블-상", (0, 7750), dxfattribs={"layer": "0"}).dxf.handle
    lab["bubble_scale2"] = msp.add_blockref("축열버블-상", (6000, 7750),
                                            dxfattribs={"layer": "DIM", "xscale": 2, "yscale": 2}).dxf.handle
    lab["bubble_mirror"] = msp.add_blockref("축열버블-좌", (-1750, 0),
                                            dxfattribs={"layer": "DIM", "xscale": -1}).dxf.handle
    lab["unused"] = msp.add_blockref("실명", (3000, 3000), dxfattribs={"layer": "ROM"}).dxf.handle
    mine = doc.blocks.new("MY_FURNITURE")
    mine.add_line((0, 0), (500, 0))
    lab["own_block"] = msp.add_blockref("MY_FURNITURE", (0, -3000), dxfattribs={"layer": "FUR"}).dxf.handle
    doc.saveas(str(path))
    return path, lab


def definitions(path: Path) -> Tuple[Path, Dict[str, str]]:
    """같은 이름, 다른 정의(객체 없음). 표준 레이어 하나가 없는 것은 위반이 아니다"""
    doc, _ = _new()
    doc.dimstyles.get("DIM330").dxf.dimtxt = 2.5
    doc.layers.get("CEN").dxf.color = 3
    for attdef in doc.blocks.get("축열버블-상").query("ATTDEF"):
        attdef.dxf.height = 300
    doc.layers.remove("LGT")
    doc.saveas(str(path))
    return path, {}


CASES = {"entities": entities, "text": text, "dims_blocks": dims_blocks, "definitions": definitions}
