"""출력 범위 계산(src/engine/sheet.py) 테스트: 도곽 블록 참조 → 출력 창·축척 k"""
import ezdxf
import pytest

from src.engine.sheet import SHEET_BLOCK, find_sheets
from src.engine.standards import load_standard


@pytest.fixture(scope="module")
def std():
    return load_standard()


def doc_with(std, *refs):
    doc = ezdxf.readfile(std.path(std.profile.template.dxf))
    msp = doc.modelspace()
    for e in list(msp):
        msp.delete_entity(e)
    handles = []
    for insert, attribs in refs:
        handles.append(msp.add_blockref(SHEET_BLOCK, insert, dxfattribs={"layer": "TIT", **attribs}).dxf.handle)
    return doc, handles


def test_sheet_window_and_scale(std):
    doc, (h1, h2) = doc_with(std, ((1000, 2000), {}), ((100000, 0), {"xscale": 2, "yscale": 2}))
    sheets, problems = find_sheets(doc, std)
    assert problems == []
    got = {s.handle: (s.k, s.window) for s in sheets}
    assert got[h1] == (100, [[1000.0, 2000.0], [43000.0, 31700.0]])
    assert got[h2] == (200, [[100000.0, 0.0], [184000.0, 59400.0]])


def test_rotated_or_uneven_sheets_are_reported(std):
    doc, (rot, uneven, odd) = doc_with(std, ((0, 0), {"rotation": 90}), ((0, 0), {"xscale": 1, "yscale": 2}),
                                       ((0, 0), {"xscale": 1.234, "yscale": 1.234}))
    sheets, problems = find_sheets(doc, std)
    assert sheets == []
    text = " / ".join(problems)
    assert rot in text and "회전" in text and uneven in text and "배율" in text and odd in text and "축척" in text


def test_no_sheet(std):
    doc, _ = doc_with(std)
    assert find_sheets(doc, std) == ([], [])
