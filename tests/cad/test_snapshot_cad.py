"""스냅샷·도면 읽기 CAD 연동 테스트(GstarCAD 실행 중, ARCAID_CAD_TESTS=1)"""
import time
from pathlib import Path

import pytest

from conftest import GRID_X, GRID_Y, ROOMS
from src.engine.read import read_drawing
from src.engine.snapshot import SnapshotError
from src.engine.standards import load_standard
from src.snapshot_service import snapshot_active, snapshot_file


def _grid(result):
    return {a.name: a.position for a in result.grid}


def test_open_document_snapshot_keeps_state_and_reads(cad, synthetic_dwg, tmp_path):
    ctl, _ = cad
    assert ctl.open_drawing(str(synthetic_dwg))
    doc = ctl.doc
    ctl.draw_text((500, -1500), "저장 안 한 표시", height=135, layer="TXT")
    before = ctl.doc_state(doc)
    assert before["saved"] is False
    meta, warnings = snapshot_active(ctl, tmp_path / "work")
    assert ctl.doc_state(doc) == before
    assert warnings == []
    assert meta.method == "open_document" and meta.dxfversion in ("AC1027", "AC1032")
    r = read_drawing(Path(meta.dxf_path), load_standard(), meta)
    assert _grid(r) == {**GRID_X, **GRID_Y}
    assert {x.text: tuple(x.position) for x in r.rooms} == {k: tuple(map(float, v)) for k, v in ROOMS.items()}
    full = tmp_path / "full.json"
    read_drawing(Path(meta.dxf_path), load_standard(), meta, full_out=full)
    assert "저장 안 한 표시" in full.read_text(encoding="utf-8")


def test_refuses_while_command_is_waiting(cad, synthetic_dwg, tmp_path):
    ctl, _ = cad
    assert ctl.open_drawing(str(synthetic_dwg))
    ctl._send_and_wait("_.LINE\n0,0\n")
    with pytest.raises(SnapshotError, match="명령"):
        snapshot_active(ctl, tmp_path / "work")
    ctl.cancel_command()


def test_closed_file_export_cache_and_refresh(cad, synthetic_dwg, tmp_path):
    ctl, user = cad
    work = tmp_path / "work"
    active_before = next(d["name"] for d in ctl.list_drawings() if d["is_active"])
    t0 = time.perf_counter()
    meta, _ = snapshot_file(ctl, work, str(synthetic_dwg))
    first = time.perf_counter() - t0
    print(f"\n닫힌 파일 내보내기: {first:.2f}초")
    assert meta.method == "closed_file" and meta.cached is False
    assert next(d["name"] for d in ctl.list_drawings() if d["is_active"]) == active_before
    assert all(Path(d["full_name"]).resolve() != synthetic_dwg.resolve() for d in ctl.list_drawings() if d["full_name"])
    assert _grid(read_drawing(Path(meta.dxf_path), load_standard(), meta)) == {**GRID_X, **GRID_Y}

    t0 = time.perf_counter()
    again, _ = snapshot_file(ctl, work, str(synthetic_dwg))
    assert again.cached is True and again.snapshot_id == meta.snapshot_id
    assert time.perf_counter() - t0 < 1.0

    # 파일을 고쳐 저장하면 새로 내보낸다
    assert ctl.open_drawing(str(synthetic_dwg))
    ctl.draw_line((0, -3000), (1000, -3000), layer="TXT")
    ctl._call_attr(ctl.doc, "Save")
    ctl._call_attr(ctl.doc, "Close", False)
    if user:
        ctl.switch_drawing(user["full_name"] or user["name"])
    fresh, _ = snapshot_file(ctl, work, str(synthetic_dwg))
    assert fresh.cached is False and fresh.snapshot_id != meta.snapshot_id
