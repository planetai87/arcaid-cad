"""T7 새 툴: CAD에 붙기 전에 거부되는 경우(Windows, CAD 불필요)"""
from test_get_standard import call_tool


def test_new_from_template_refuses_bad_paths(tmp_path):
    r = call_tool("new_from_template", {"file_path": str(tmp_path / "x.dxf")})
    assert r["status"] == "failed" and ".dwg" in r["summary"]
    existing = tmp_path / "있음.dwg"
    existing.write_bytes(b"x")
    r = call_tool("new_from_template", {"file_path": str(existing)})
    assert r["status"] == "failed" and "덮어쓰지 않음" in r["summary"]
    r = call_tool("new_from_template", {"file_path": "상대경로.dwg"})
    assert r["status"] == "failed" and "절대 경로" in r["summary"]


def test_ensure_standard_layers_refuses_unknown_names():
    r = call_tool("ensure_standard_layers", {"names": ["CEN", "A-WALL", "0"]})
    assert r["status"] == "failed" and "A-WALL" in r["summary"] and "0" in r["summary"]


def test_plot_refuses_bad_ranges(tmp_path):
    r = call_tool("plot", {"sheet_handle": "1A", "window": [[0, 0], [10, 10]]})
    assert r["status"] == "failed" and "하나만" in r["summary"]
    r = call_tool("plot", {"window": [[10, 10], [0, 0]]})
    assert r["status"] == "failed" and "window" in r["summary"]
    r = call_tool("plot", {"window": [[0, 0], [10, 10]], "out_path": str(tmp_path / "a.png")})
    assert r["status"] == "failed" and ".pdf" in r["summary"]
