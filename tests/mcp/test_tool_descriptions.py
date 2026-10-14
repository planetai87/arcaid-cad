"""툴 설명문: 보호되지 않는 저수준 툴에는 그 사실이 적혀 있고, 보호된 툴에는 없다(T7)"""
import json
import os
import subprocess
import sys

from test_get_standard import ROOT

UNPROTECTED = {
    "draw_line", "draw_circle", "draw_arc", "draw_ellipse", "draw_polyline", "draw_rectangle", "draw_text",
    "draw_hatch", "add_dimension", "erase_entity", "move_entity", "rotate_entity", "scale_entity", "copy_entity",
    "mirror_entity", "offset_entity", "array_linear_entity", "array_polar_entity", "undo", "redo",
    "set_current_layer", "create_block", "insert_block", "send_command", "run_lisp",
}
MARK = "보호되지 않는 경로"


def descriptions():
    code = ("import asyncio, json, sys; sys.path.insert(0, 'src'); import server; "
            "print(json.dumps({t.name: t.description for t in asyncio.run(server.mcp.list_tools())}, ensure_ascii=False))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_unprotected_tools_are_marked():
    d = descriptions()
    assert UNPROTECTED <= set(d)
    assert sorted(n for n in d if MARK in d[n]) == sorted(UNPROTECTED)
    assert "문자 높이를 재정의" in d["add_dimension"]
