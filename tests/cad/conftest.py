import os
import shutil
import sys
from pathlib import Path

import pytest

# 실행 중인 CAD가 필요한 연동 테스트: Windows에서 ARCAID_CAD_TESTS=1일 때만 모은다
if sys.platform != "win32" or os.environ.get("ARCAID_CAD_TESTS") != "1":
    collect_ignore_glob = ["test_*.py"]

ROOT = Path(__file__).resolve().parents[2]

# 축선·버블·실명 좌표(1/100, 회사 표준 annotation.md 예시와 같은 배치)
GRID_X = {"X1": 0, "X2": 6000, "X3": 12000}
GRID_Y = {"Y1": 0, "Y2": 6000}
ROOMS = {"객실 101": (3000, 3000), "객실 102": (9000, 3000)}


@pytest.fixture
def cad(tmp_path):
    """실행 중인 CAD에 붙는다. 끝나면 임시 폴더에서 연 문서를 닫고 사용자 활성 도면으로 돌아간다"""
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools" / "template"))
    from _cadutil import close_work_docs, connect, restore_user

    ctl, user = connect()
    before = {d["full_name"] or d["name"]: d["saved"] for d in ctl.list_drawings()}
    try:
        yield ctl, user
    finally:
        try:
            ctl.cancel_command()
        finally:
            close_work_docs(ctl, tmp_path)
            restore_user(ctl, user)
    after = {d["full_name"] or d["name"]: d["saved"] for d in ctl.list_drawings()}
    assert after == before, "시험이 사용자 도면 목록·저장 상태를 바꿨음"


@pytest.fixture
def synthetic_dwg(cad, tmp_path):
    """회사 템플릿 사본에 축선 X1~X3·Y1~Y2, 축열 버블, 실명 2개를 그려 저장한 시험 도면(닫힌 상태로 돌려준다)"""
    from _cadutil import ensure_active, lisp
    from src.engine.standards import load_standard

    ctl, _ = cad
    std = load_standard()
    path = tmp_path / "plan.dwg"
    shutil.copyfile(std.path(std.profile.template.dwg), path)
    assert ctl.open_drawing(str(path))
    doc = ctl.doc
    for x in GRID_X.values():
        assert ctl.draw_line((x, -450), (x, 6450), layer="CEN")
    for y in GRID_Y.values():
        assert ctl.draw_line((-450, y), (12450, y), layer="CEN")
    ensure_active(ctl, doc)
    ctl._call_attr(doc, "SetVariable", "CLAYER", "DIM")
    for cmd in ("_.ATTDIA\n0\n", "_.ATTREQ\n1\n"):
        ctl._send_and_wait(cmd)
    for name, x in GRID_X.items():
        ctl._send_and_wait(f"_.-INSERT\n축열버블-상\n{x},7750\n1\n1\n0\n{name}\n")
    for name, y in GRID_Y.items():
        ctl._send_and_wait(f"_.-INSERT\n축열버블-좌\n-1750,{y}\n1\n1\n0\n{name}\n")
    ctl._call_attr(doc, "SetVariable", "CLAYER", "0")
    for text, (x, y) in ROOMS.items():
        lisp(ctl, f'(entmake \'((0 . "TEXT") (8 . "ROM") (7 . "돋움") (10 {x} {y} 0.0) (11 {x} {y} 0.0) '
                  f'(40 . 135) (1 . "{text}") (72 . 1) (73 . 2)))')
    ctl._call_attr(doc, "Save")
    ctl._call_attr(doc, "Close", False)
    return path
