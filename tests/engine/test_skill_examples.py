"""배포 스킬의 실제 JSON 예제가 명세→DXF→판독·검사를 끝까지 통과하는지 확인한다."""
import json
from pathlib import Path

import ezdxf
import pytest

from src.engine.check import check_standard
from src.engine.read import read_drawing
from src.engine.spec import parse_spec, validate_spec
from src.engine.standards import load_standard
from src.engine.write import build_dxf

EXAMPLE = (Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard"
           / "references" / "examples" / "synthetic-plan.json")


@pytest.mark.parametrize("scale", [100, 200])
def test_packaged_example_roundtrip_and_scaling(tmp_path, scale):
    # 예제의 role·attribs·그리드·대칭·섬이 실제 계약과 달라지면 실패한다.
    std = load_standard()
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["scale"] = scale
    spec = parse_spec(data)
    assert validate_spec(spec, std) == []
    path = tmp_path / "example.dxf"
    result = build_dxf(spec, std, path)
    assert result.status.value == "success", result.summary
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    rooms = read_drawing(path, std).rooms
    assert sorted(r.text for r in rooms) == ["객실 101", "객실 102", "객실 103", "객실 104"]
    assert sorted(tuple(r.position) for r in rooms) == [(1500, 2000), (1500, 8000), (4500, 2000), (4500, 8000)]
    assert all(r.height == 135 * scale / 100 for r in rooms)
    labels = [e for e in msp.query("TEXT") if e.dxf.layer == "ROM"]
    assert all(e.dxf.halign == 1 and e.dxf.valign == 2 for e in labels)
    bubbles = [e for e in msp.query("INSERT") if e.dxf.name == "축열버블-상"]
    assert [e.get_attrib_text("축열") for e in bubbles] == ["X1", "X2", "X3"]
    assert all(e.dxf.xscale == scale / 100 for e in bubbles)
    assert all(len(e.paths) == 2 for e in msp.query("HATCH"))
    dims = list(msp.query("DIMENSION"))
    assert len(dims) == 3
    assert all(e.override().get("dimscale") == scale / 2 for e in dims)
    check = check_standard(path, std, scale)
    assert check.violations == []
    assert check.unchecked == []
