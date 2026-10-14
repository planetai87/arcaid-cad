"""get_standard MCP 툴: CAD 없이 표준 프로파일을 조회한다(Windows 전용, CAD 불필요)"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def call_tool(name, arguments, work=None):
    # server.py는 불러올 때 표준 입출력을 다시 설정하므로 pytest 밖의 하위 프로세스에서 부른다
    # work: 작업 폴더(config work.directory)를 바꿔 부른다
    setup = f"server.config['work'] = {{'directory': {str(work)!r}}}; " if work is not None else ""
    code = ("import asyncio, json, sys; sys.path.insert(0, 'src'); import server; " + setup +
            f"r = asyncio.run(server.mcp.call_tool({name!r}, {arguments!r})); "
            "r = r[1] if isinstance(r, tuple) else r; "
            "print(json.dumps(r if isinstance(r, dict) else [c.model_dump() for c in r], ensure_ascii=False))")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_layer_lookup_returns_purpose_and_object_linetypes():
    r = call_tool("get_standard", {"section": "layers", "name": "LIM"})
    assert r["success"] is True and r["standard"] == "heo-2026@2026.1"
    (layer,) = r["data"]["layers"]
    assert "대지경계선" in layer["purpose"] and "PHANTOM" in layer["object_linetypes"]


def test_text_heights_scaled_for_1_200():
    r = call_tool("get_standard", {"section": "text", "scale": 200})
    general = next(h for h in r["data"]["heights"] if h["id"] == "general")
    assert general["scaled"] == 270


def test_unknown_name_is_a_failure_not_an_exception():
    r = call_tool("get_standard", {"section": "blocks", "name": "없는블록"})
    assert r["success"] is False and "없는블록" in r["message"]
