"""draw_batch·check_standard(fix) 툴: CAD 없이 거부되는 경우와 툴 스키마(시험 훅이 노출되지 않음)"""
import json
import os
import subprocess
import sys

import pytest

from test_get_standard import ROOT, call_tool
from test_egress import _server


def tool_params():
    code = ("import asyncio, json, sys; sys.path.insert(0, 'src'); import server; "
            "print(json.dumps({t.name: t.inputSchema "
            "for t in asyncio.run(server.mcp.list_tools())}))")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_schemas_expose_no_test_hooks():
    schemas = tool_params()
    params = {name: sorted(schema.get("properties", {})) for name, schema in schemas.items()}
    assert params["draw_batch"] == ["resume_after_unverified", "spec", "spec_path"]
    assert params["apply_changes"] == ["overwrite_manual", "resume_after_unverified", "scale", "spec"]
    assert params["check_standard"] == ["file_path", "fix", "overwrite_manual", "resume_after_unverified", "scale",
                                        "snapshot_id"]
    schema = schemas["draw_batch"]
    assert schema.get("required", []) == []
    props = schema["properties"]
    assert {branch["type"] for branch in props["spec"]["anyOf"]} == {"object", "string", "null"}
    assert {branch["type"] for branch in props["spec_path"]["anyOf"]} == {"string", "null"}
    assert props["resume_after_unverified"]["type"] == "boolean"
    assert props["resume_after_unverified"]["default"] is False
    assert props["spec"]["default"] is None and props["spec_path"]["default"] is None


DRAW_SPEC = dict(schema_version=1, standard="heo-2026@2026.1", op="file-input", units="mm", scale=100,
                 items=[dict(kind="line", id="wall", layer="WAL", start=[-0.0, 0.0], end=[1000, 0])])

DRAW_SETUP = '''
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, 'src')
import server
from engine.apply import ApplyMeta, ApplyResult
from engine.results import Scope, Status
from engine.spec import parse_spec, spec_hash
async def forbidden(*args):
    raise AssertionError('CAD 연결 전에 거절해야 함')
server._ensure_running = server.com = forbidden
'''


def test_draw_batch_rejects_inputs_before_connecting(tmp_path):
    folder = tmp_path / "폴더 명세.json"
    folder.mkdir()
    bad_utf8 = tmp_path / "잘못된 문자.json"
    bad_utf8.write_bytes(b"\xff")
    bad_json = tmp_path / "깨진 명세.json"
    bad_json.write_text("{not json", encoding="utf-8")
    cases = [({}, "SPEC_INPUT"), ({"spec": None, "spec_path": None}, "SPEC_INPUT"),
             ({"spec": {}, "spec_path": ""}, "SPEC_INPUT"),
             ({"spec": "", "spec_path": str(bad_json)}, "SPEC_INPUT"),
             ({"spec": DRAW_SPEC, "spec_path": str(bad_json)}, "SPEC_INPUT"),
             ({"spec": {}}, "SPEC_SCHEMA"), ({"spec": ""}, "SPEC_SCHEMA"),
             ({"spec_path": ""}, "SPEC_PATH"), ({"spec_path": "relative.json"}, "SPEC_PATH"),
             ({"spec_path": r"C:relative.json"}, "SPEC_PATH"),
             ({"spec_path": r"\root-relative.json"}, "SPEC_PATH"),
             ({"spec_path": r"\\host\share\명세.json"}, "SPEC_PATH"),
             ({"spec_path": "//host/share/명세.json"}, "SPEC_PATH"),
             ({"spec_path": r"\\?\C:\명세.json"}, "SPEC_PATH"),
             ({"spec_path": r"\\.\C:\명세.json"}, "SPEC_PATH"),
             ({"spec_path": r"C:\NUL.json"}, "SPEC_PATH"),
             ({"spec_path": str(tmp_path / "x.txt")}, "SPEC_PATH"),
             ({"spec_path": str(folder)}, "SPEC_FILE"),
             ({"spec_path": str(tmp_path / "없는 명세.json")}, "SPEC_READ"),
             ({"spec_path": str(bad_utf8)}, "SPEC_ENCODING"),
             ({"spec_path": str(bad_json)}, "SPEC_SCHEMA")]
    code = DRAW_SETUP + f'''
for args, expected in {cases!r}:
    result = asyncio.run(server.mcp.call_tool('draw_batch', args))[1]
    assert result['status'] == 'failed', (args, result)
    assert result['violations'][0]['code'] == expected, (args, result)
print(json.dumps(True))
'''
    assert _server(code, {})


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
@pytest.mark.parametrize("suffix", [".json", ".JSON"])
def test_draw_batch_accepts_file_and_preserves_text(tmp_path, encoding, suffix):
    path = tmp_path / f"한글 공백 명세{suffix}"
    raw = json.dumps(DRAW_SPEC, ensure_ascii=False, indent=2)
    path.write_bytes(raw.encode(encoding))
    code = DRAW_SETUP + f'''
raw = {raw!r}
seen = []
original_refusal = server.apply_service.spec_refusal
def refusal(std, spec):
    assert type(spec) is str and spec == raw
    seen.append(spec)
    return original_refusal(std, spec)
server.apply_service.spec_refusal = refusal
async def running(ctx):
    return True
server._ensure_running = running
async def com(fn, *args):
    assert fn is server.apply_service.draw_batch
    assert args[3] is seen[-1] and args[4:] == (None, True)
    # 읽은 뒤 파일이 바뀌어도 이미 읽은 문자열 하나를 적용한다.
    Path({str(path)!r}).write_text('{{broken', encoding='utf-8')
    h = spec_hash(parse_spec(args[3]))
    assert h == spec_hash(parse_spec({DRAW_SPEC!r}))
    assert h != spec_hash(parse_spec(raw.replace('-0.0', '0.0')))
    return ApplyResult(status=Status.SUCCESS, summary='확인', scope=Scope(target='활성 도면'),
                       meta=ApplyMeta(op='file-input', spec_hash=h))
server.com = com
result = asyncio.run(server.mcp.call_tool('draw_batch',
                     dict(spec=None, spec_path={str(path)!r}, resume_after_unverified=True)))[1]
assert len(seen) == 1
print(json.dumps(result))
'''
    assert _server(code, {})["status"] == "success"


@pytest.mark.parametrize("extra", [0, 1])
def test_draw_batch_file_size_boundary(tmp_path, extra):
    path = tmp_path / "크기 경계.json"
    raw = json.dumps(DRAW_SPEC).encode("utf-8-sig")
    limit = 5 * 1024 * 1024
    path.write_bytes(raw + b" " * (limit + extra - len(raw)))
    code = DRAW_SETUP + f'''
assert server.apply_service.SPEC_MAX_BYTES == {limit}
async def running(ctx):
    return True
async def com(fn, *args):
    return ApplyResult(status=Status.SUCCESS, summary='확인', scope=Scope(target='활성 도면'), meta=ApplyMeta(op='file-input'))
if not {extra}:
    server._ensure_running, server.com = running, com
print(json.dumps(asyncio.run(server.mcp.call_tool('draw_batch', dict(spec_path={str(path)!r})))[1]))
'''
    result = _server(code, {})
    if extra:
        assert result["status"] == "failed" and result["violations"][0]["code"] == "SPEC_SIZE"
    else:
        assert result["status"] == "success"


def test_draw_batch_file_reads_once_with_limit_and_handles_oserror(tmp_path):
    path = tmp_path / "읽기 명세.json"
    path.write_text(json.dumps(DRAW_SPEC), encoding="utf-8")
    code = DRAW_SETUP + f'''
from unittest.mock import patch, MagicMock
original_open = Path.open
with original_open(Path({str(path)!r}), 'rb') as source:
    reader = MagicMock(wraps=source)
    reader.__enter__.return_value = reader
    with patch.object(Path, 'open', return_value=reader) as opened:
        result = server.apply_service.load_batch_spec(server._standard(), spec_path={str(path)!r})
        assert isinstance(result, str)
        opened.assert_called_once_with('rb')
        reader.read.assert_called_once_with(server.apply_service.SPEC_MAX_BYTES + 1)
    reader.read.side_effect = OSError('일부러 낸 읽기 실패')
    # Path.open 전체를 바꾸면 egress 정책 읽기도 막히므로 읽기 함수를 직접 부른다
    with patch.object(Path, 'open', return_value=reader):
        result = server.apply_service.load_batch_spec(server._standard(), spec_path={str(path)!r})
    assert isinstance(result, ApplyResult)
    assert result.status.value == 'failed' and result.violations[0].code == 'SPEC_READ'
print(json.dumps(True))
'''
    assert _server(code, {})


def test_draw_batch_file_failure_path_is_redacted(tmp_path):
    import egress
    import jsonschema
    from engine.apply import ApplyResult

    path = tmp_path / "비밀 현장" / "없는 명세.json"
    policy = tmp_path / "deny.yaml"
    base = egress.EgressPolicy.load(ROOT / "config" / "egress.yaml")
    policy.write_text(json.dumps({"policy": {**base.policy, "path": "deny"}, "fields": base.fields,
                                  "always": sorted(base.always)}, ensure_ascii=False), encoding="utf-8")
    work = tmp_path / "work"
    code = DRAW_SETUP + f'''
server.config['work'] = dict(directory={str(work)!r})
r = asyncio.run(server.mcp.call_tool('draw_batch', dict(spec_path={str(path)!r})))
print(json.dumps(dict(result=r[1], text=r[0][0].text), ensure_ascii=False))
'''
    result = _server(code, {"ARCAID_EGRESS_FILE": str(policy)})
    assert result["result"]["status"] == "failed"
    assert result["result"]["violations"][0]["code"] == "SPEC_READ"
    for secret in ("비밀 현장", "없는 명세", str(tmp_path)):
        assert secret not in json.dumps(result, ensure_ascii=False)
    assert "[경로]" in result["result"]["summary"]
    jsonschema.validate(result["result"], ApplyResult.model_json_schema())
    saved = list((work / "egress").rglob("*draw_batch.json"))
    assert len(saved) == 1 and "비밀 현장" in saved[0].read_text(encoding="utf-8")


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
