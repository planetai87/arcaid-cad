"""read_drawing 툴: DXF 경로를 CAD 연결 없이 바로 읽는다. 작업 폴더가 아직 없어도 만들어 쓴다"""
from pathlib import Path

from test_get_standard import ROOT, call_tool


def test_reads_dxf_directly_into_fresh_work_dir(tmp_path):
    work = tmp_path / "work"
    dxf = ROOT / "standards" / "heo-2026" / "template.dxf"
    r = call_tool("read_drawing", {"file_path": str(dxf)}, work=work)
    assert r["status"] == "success", r["summary"]
    assert r["meta"]["dxfversion"] == "AC1027" and r["meta"]["units"] == "mm"
    assert Path(r["full_path"]).parent == work / "snapshots" and Path(r["full_path"]).exists()
