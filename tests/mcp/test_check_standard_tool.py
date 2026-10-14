"""check_standard 툴: DXF 경로를 CAD 연결 없이 검사한다. 요약은 코드별 상한, 전체는 작업 폴더 파일"""
import json
from pathlib import Path

import ezdxf

from test_get_standard import ROOT, call_tool

TEMPLATE = ROOT / "standards" / "heo-2026" / "template.dxf"


def _drawing(path: Path, red_lines: int) -> Path:
    doc = ezdxf.readfile(TEMPLATE)
    msp = doc.modelspace()
    for i in range(red_lines):
        msp.add_line((0, i * 100), (1000, i * 100), dxfattribs={"layer": "WAL", "color": 1})
    msp.add_line((0, -100), (1000, -100), dxfattribs={"layer": "A-WALL"})
    doc.saveas(str(path))
    return path


def test_checks_dxf_with_assumed_scale_and_truncates(tmp_path):
    work = tmp_path / "work"
    dxf = _drawing(tmp_path / "plan.dxf", red_lines=25)
    r = call_tool("check_standard", {"file_path": str(dxf)}, work=work)
    assert r["status"] == "success", r["summary"]
    assert r["violation_counts"] == {"COLOR_NOT_BYLAYER": 25, "LAYER_NOT_STANDARD": 1}
    assert len(r["violations"]) == 21 and r["truncated"] is True
    assert r["assumptions"] == [{"name": "scale", "value": 100, "reason": "요청에 축척이 없어 A3 1/100로 가정"}]
    (fix,) = r["fixes"]
    assert fix["property"] == "color" and fix["count"] == 25 and len(fix["handles"]) == 20
    full = Path(r["full_path"])
    assert full.parent == work / "snapshots"
    assert len(json.loads(full.read_text(encoding="utf-8"))["violations"]) == 26


def test_scale_is_passed_to_engine(tmp_path):
    dxf = _drawing(tmp_path / "plan.dxf", red_lines=0)
    r = call_tool("check_standard", {"file_path": str(dxf), "scale": 200}, work=tmp_path / "work")
    assert r["meta"]["k"] == 200 and r["meta"]["k_assumed"] is False and r["assumptions"] == []
    assert r["violation_counts"] == {"LAYER_NOT_STANDARD": 1, "LTSCALE": 1}
