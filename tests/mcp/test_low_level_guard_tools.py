"""저수준 9개 툴의 실제 MCP 응답과 단일 COM 작업 계약(CAD 불필요)."""
import json
import os
import subprocess
import sys

from test_get_standard import ROOT


def test_nine_tools_return_structured_results_in_one_com_call():
    code = '''
import asyncio, json, sys
sys.path[:0] = ['.', 'tests/mcp']
from src import server
from src.models import ToolResult
from test_low_level_guard import ctl, TAG
import jsonschema
async def running(ctx):
    return True
server._ensure_running = running
calls = []
async def com(fn, *args):
    calls.append(fn.__name__)
    return fn(*args)
server.com = com
tools = {
    'erase_entity': {}, 'move_entity': dict(displacement=[10,20]),
    'rotate_entity': dict(base_point=[0,0], angle_degrees=90),
    'scale_entity': dict(base_point=[0,0], scale_factor=2),
    'copy_entity': dict(displacement=[10,20]),
    'mirror_entity': dict(point1=[0,0], point2=[0,1]), 'offset_entity': dict(distance=10),
    'array_linear_entity': dict(count=4, displacement=[10,20]),
    'array_polar_entity': dict(count=4, center=[0,0])}
for mode in ('human', 'tag', 'read_error', 'sticky', 'partial'):
    for index, (name, args) in enumerate(tools.items()):
        c = ctl.__wrapped__()
        server.cad_service.controller = c
        obj = c.doc.entities['A']
        if mode in ('tag', 'sticky'):
            obj.xdata['ARCAID'] = TAG
        obj.read_error = mode == 'read_error'
        obj.sticky = mode == 'sticky'
        c.doc.move_error = mode == 'partial'
        calls.clear()
        r = asyncio.run(server.mcp.call_tool(name, dict(handle='A', **args)))
        payload = r[1]
        jsonschema.validate(payload, ToolResult.model_json_schema())
        assert json.loads(r[0][0].text) == payload
        assert calls == [name], (name, calls)
        create = index >= 4
        failed = ((not create and mode in ('tag', 'read_error', 'sticky'))
                  or (create and mode in ('read_error', 'sticky'))
                  or (mode == 'partial' and name in ('move_entity', 'rotate_entity', 'copy_entity',
                                                    'array_linear_entity', 'array_polar_entity')))
        assert payload['success'] is not failed, (mode, name, payload)
        if create:
            expected = [h for h in c.doc.entities if h != 'A']
            assert payload['entity_handles'] == expected, (mode, name, payload)
            if mode in ('read_error', 'sticky'):
                assert '같은 oid 중복' in payload['message']
            if failed and expected:
                assert '다시 시도하지 말고 확인' in payload['message']
print(json.dumps(dict(checked=len(tools)*5)))
'''
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"checked": 45}
