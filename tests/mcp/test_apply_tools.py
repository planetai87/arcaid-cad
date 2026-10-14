"""draw_batch·check_standard(fix) 툴: CAD 없이 거부되는 경우와 툴 스키마(시험 훅이 노출되지 않음)"""
import json
import os
import subprocess
import sys

from test_get_standard import ROOT, call_tool


def tool_params():
    code = ("import asyncio, json, sys; sys.path.insert(0, 'src'); import server; "
            "print(json.dumps({t.name: sorted(t.inputSchema.get('properties', {})) "
            "for t in asyncio.run(server.mcp.list_tools())}))")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_schemas_expose_no_test_hooks():
    params = tool_params()
    assert params["draw_batch"] == ["resume_after_unverified", "spec"]
    assert params["check_standard"] == ["file_path", "fix", "overwrite_manual", "resume_after_unverified", "scale",
                                        "snapshot_id"]


def test_draw_batch_rejects_bad_spec_without_cad(tmp_path):
    spec = {"schema_version": 1, "standard": "heo-2026@2026.1", "op": "bad", "units": "mm",
            "items": [{"kind": "line", "layer": "A-WALL", "start": [0, 0], "end": [1, 0]}]}
    r = call_tool("draw_batch", {"spec": spec}, work=tmp_path / "work")
    assert r["status"] == "failed" and [v["code"] for v in r["violations"]] == ["SPEC_LAYER"]
    r = call_tool("draw_batch", {"spec": "{not json"}, work=tmp_path / "work")
    assert r["status"] == "failed" and r["violations"][0]["code"] == "SPEC_SCHEMA"


def test_fix_refuses_file_path(tmp_path):
    r = call_tool("check_standard", {"file_path": str(tmp_path / "x.dxf"), "fix": True}, work=tmp_path / "work")
    assert r["status"] == "failed" and "활성 도면" in r["summary"]
