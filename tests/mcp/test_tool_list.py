"""MCP 툴 목록 고정 테스트: 의도하지 않은 추가·삭제를 막는다(Windows 전용, CAD 불필요)"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TOOLS = sorted([
    "draw_line", "draw_circle", "draw_arc", "draw_ellipse", "draw_polyline", "draw_rectangle",
    "draw_text", "draw_hatch", "add_dimension",
    "list_layers", "list_entities", "get_entity_properties", "screenshot",
    "erase_entity", "move_entity", "rotate_entity", "scale_entity", "copy_entity", "mirror_entity",
    "offset_entity", "array_linear_entity", "array_polar_entity", "undo", "redo",
    "create_layer", "set_current_layer",
    "new_drawing", "open_drawing", "close_drawing", "list_drawings", "switch_drawing", "save_drawing",
    "send_command", "cancel_command", "run_lisp",
    "create_block", "insert_block", "list_blocks",
    "get_standard", "export_snapshot", "read_drawing",
])


def registered_tools():
    # server.py는 불러올 때 표준 입출력을 다시 설정하므로 pytest 밖의 하위 프로세스에서 읽는다
    code = ("import asyncio, json, sys; sys.path.insert(0, 'src'); import server; "
            "print(json.dumps(sorted(t.name for t in asyncio.run(server.mcp.list_tools()))))")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_tool_list_is_fixed():
    assert len(EXPECTED_TOOLS) == 41
    assert registered_tools() == EXPECTED_TOOLS
