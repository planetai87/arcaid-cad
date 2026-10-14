"""CAD 연결 확인(읽기 전용): 실행 중인 CAD에 붙어 열린 도면 목록을 읽는다"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cad_controller import CADController  # noqa: E402


def test_attach_and_list_drawings():
    ctl = CADController()
    assert ctl.executor.submit(ctl.start_cad)
    drawings = ctl.executor.submit(ctl.list_drawings)
    assert len(drawings) >= 1
    assert sum(1 for d in drawings if d["is_active"]) == 1
