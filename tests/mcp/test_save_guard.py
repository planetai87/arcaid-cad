"""save_drawing은 DXF로 저장하지 않는다(열린 문서가 DXF로 바뀌는 위험, D6). CAD 연결 없이도 거부해야 한다"""
from test_get_standard import call_tool


def test_save_drawing_refuses_dxf_without_touching_cad():
    r = call_tool("save_drawing", {"file_path": r"C:\arcaid\work\x.DXF"})
    assert r["success"] is False
    assert "export_snapshot" in r["message"]
