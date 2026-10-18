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
    assert params["apply_changes"] == ["overwrite_manual", "resume_after_unverified", "scale", "spec"]
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


def test_apply_changes_rejects_before_connecting(tmp_path):
    cases = [("{not json", "CHG_SCHEMA"), ({}, "CHG_SCHEMA"),
             (change_spec(standard="other@1"), "CHG_STANDARD")]
    code = '''
import asyncio, json, sys
sys.path.insert(0, 'src')
import server
async def forbidden(*args):
    raise AssertionError('CAD 연결 전에 거절해야 함')
server._ensure_running = server.com = forbidden
for spec, code in CASES:
    r = asyncio.run(server.mcp.call_tool('apply_changes', dict(spec=spec)))[1]
    assert r['status'] == 'failed' and r['violations'][0]['code'] == code
print('확인')
'''.replace("CASES", repr(cases))
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr


def change_spec(**kw):
    return dict(schema_version=1, standard=kw.get("standard", "heo-2026@2026.1"), op="b3-change",
                target={"snapshot_id": "base"},
                changes=[dict(id="move", select={"handles": ["1A"]}, action="move", delta=[10, 20])])


def test_apply_changes_forwards_options(tmp_path):
    # 실제 MCP 파싱을 거쳐 서비스에 넘기는 위치 인자까지 확인한다(CAD를 연결하지 않음).
    code = '''
import asyncio, json, sys
sys.path.insert(0, 'src')
import server
from engine.apply import ApplyResult, ApplyMeta
from engine.results import Scope, Status
async def running(ctx):
    return True
server._ensure_running = running
seen = []
async def com(fn, *args):
    assert fn is server.apply_service.apply_changes
    seen.append([args[3], args[4], args[5], args[6], args[7]])
    return ApplyResult(status=Status.SUCCESS, summary='확인', scope=Scope(target='시험'), meta=ApplyMeta(op='b3'))
server.com = com
spec = SPEC
for options in ({}, dict(overwrite_manual=True, resume_after_unverified=True, scale=200)):
    r = asyncio.run(server.mcp.call_tool('apply_changes', dict(spec=spec, **options)))
    assert r[1]['status'] == 'success'
print(json.dumps(seen))
'''.replace("SPEC", repr(change_spec()))
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert seen == [[change_spec(), False, None, False, None], [change_spec(), True, None, True, 200]]
