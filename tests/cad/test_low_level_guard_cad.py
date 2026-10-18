"""B3 GstarCAD 실증. Claude만 새 C:\\arcaid\\work basetemp에서 실행한다.

SetXData([1001], ["ARCAID"]) 삭제가 검증되지 않으면 B3를 중단하고 대안을 검토한다.
LISP로 태그를 지우는 우회는 이 시험과 제품 코드에 넣지 않는다.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.apply_service import draw_batch
from src.engine import changes
from src.engine.results import Status
from src.engine.state import arcaid_tags
from test_apply_changes_cad import BUILD, obj, snap


SEED = copy.deepcopy(BUILD)
SEED["op"] = "b3-seed"
SEED["items"] = [item for item in SEED["items"] if item["id"] in ("line", "text", "block")]
EDITS = [("erase_entity", []), ("move_entity", [[600, 300]]),
         ("rotate_entity", [[0, -20000], 30]), ("scale_entity", [[0, -20000], 2])]
CREATES = [("copy_entity", [[2000, 1000]]), ("mirror_entity", [[0, 0], [0, 1]]),
           ("offset_entity", [300]), ("array_linear_entity", [3, [2000, 1000]]),
           ("array_polar_entity", [3, [0, -20000]])]
ATTRIBUTE_ERROR = "속성은 블록 참조 핸들로 편집한다(속성 단독 편집은 지원하지 않음)"


@pytest.fixture
def guarded(request, tmp_path):
    # CAD fixture를 요청하기 전에 작업 위치를 검증해 사용자 도면을 시험에 쓰지 않는다.
    assert tmp_path.resolve().is_relative_to(Path(r"C:\arcaid\work").resolve()), "새 C:\\arcaid\\work basetemp 필요"
    ctl, work, std = request.getfixturevalue("opened")
    result = draw_batch(ctl, work, std, SEED)
    assert result.status is Status.SUCCESS, result.summary
    before = snap(ctl, tmp_path / "seed")
    handles = {arcaid_tags(e)["path"]: e.dxf.handle for e in before.modelspace()
               if (arcaid_tags(e) or {}).get("op") == SEED["op"]}
    apps = ctl._call(getattr, ctl.doc, "RegisteredApplications")
    ctl._call_attr(apps, "Add", "TESTAPP")
    # INSERT와 종속 ATTRIB 모두 원시 태그를 붙여 복제본에서 각각 제거되는지 확인한다.
    attr = ctl._call_attr(obj(ctl, handles["block"]), "GetAttributes")[0]
    attr_handle = str(ctl._call(getattr, attr, "Handle"))
    ctl.set_xdata(attr_handle, ["op=b3-attribute", "oid=b3-attribute:1"])
    handles["attr"] = attr_handle
    for handle in handles.values():
        ctl.set_xdata(handle, ["보존할 다른 앱"], app="TESTAPP")
    ms = ctl._call(getattr, ctl.doc, "ModelSpace")
    human = ctl._call_attr(ms, "AddLine", ctl._pt((8000, -20000)), ctl._pt((9000, -20000)))
    handles["human"] = str(ctl._call(getattr, human, "Handle"))
    return ctl, work, std, handles


def family(doc, handle):
    entity = doc.entitydb[handle]
    return [entity, *(entity.attribs if entity.dxftype() == "INSERT" else [])]


def raw_present(ctl, handle, app="ARCAID"):
    types, values = ctl._call_attr(obj(ctl, handle), "GetXData", app)
    return bool(types or values)


@pytest.mark.parametrize("method,args", EDITS)
@pytest.mark.parametrize("kind", ["line", "text", "block", "attr"])
def test_engine_edits_refused_and_human_edits_applied(guarded, tmp_path, method, args, kind):
    ctl, work, std, handles = guarded
    before = snap(ctl, tmp_path / "edit_before")
    result = getattr(ctl, method)(handles[kind], *args)
    assert not result.ok and not result.new_handles
    if kind == "attr":
        assert result.error == ATTRIBUTE_ERROR
    else:
        assert "엔진 객체" in result.error
    after = snap(ctl, tmp_path / "edit_refused")
    assert changes.fingerprint(before.entitydb[handles[kind]]) == changes.fingerprint(after.entitydb[handles[kind]])
    assert getattr(ctl, method)(handles["human"], *args).ok
    after_human = snap(ctl, tmp_path / "edit_human")
    if method == "erase_entity":
        assert after_human.entitydb.get(handles["human"]) is None
    else:
        assert not changes.same_state(changes.change_state(before.entitydb[handles["human"]]),
                                      changes.change_state(after_human.entitydb[handles["human"]]))


@pytest.mark.parametrize("method,args", CREATES)
def test_direct_attribute_copy_is_refused(guarded, tmp_path, method, args):
    ctl, work, std, handles = guarded
    before = snap(ctl, tmp_path / "attribute_before")
    result = getattr(ctl, method)(handles["attr"], *args)
    assert not result.ok and not result.new_handles and result.error == ATTRIBUTE_ERROR
    after = snap(ctl, tmp_path / "attribute_after")
    for entity in family(before, handles["block"]):
        assert changes.fingerprint(entity) == changes.fingerprint(after.entitydb[entity.dxf.handle])


@pytest.mark.parametrize("method,args", CREATES)
@pytest.mark.parametrize("kind", ["line", "text", "block"])
def test_copy_detags_com_and_export_preserves_other_app(guarded, tmp_path, method, args, kind):
    if method == "offset_entity" and kind != "line":
        pytest.skip("Offset 실증은 선만 지원")
    ctl, work, std, handles = guarded
    before = snap(ctl, tmp_path / "copy_before")
    originals = {e.dxf.handle: changes.fingerprint(e) for e in family(before, handles[kind])}
    result = getattr(ctl, method)(handles[kind], *args)
    assert result.ok, f"{result.error}; 생성={result.new_handles}; 태그 잔존={result.tag_left}"
    assert result.new_handles and not result.tag_left
    after = snap(ctl, tmp_path / "copy_after")
    for handle, fingerprint in originals.items():
        assert changes.fingerprint(after.entitydb[handle]) == fingerprint
        assert raw_present(ctl, handle) and raw_present(ctl, handle, "TESTAPP")
    for handle in result.new_handles:
        for entity in family(after, handle):
            assert not raw_present(ctl, entity.dxf.handle), "SetXData([1001 ARCAID]) 삭제 실증 실패: B3 중단"
            assert not entity.has_xdata("ARCAID"), "Export DXF에 ARCAID 잔존: B3 중단"
            assert raw_present(ctl, entity.dxf.handle, "TESTAPP") and entity.has_xdata("TESTAPP")
            assert list(entity.get_xdata("TESTAPP")) == list(before.entitydb[handles[kind]].get_xdata("TESTAPP"))
    again = draw_batch(ctl, work, std, SEED)
    assert again.status is Status.SUCCESS and again.meta.decision == "skip", again.summary


def test_block_definition_handles_refused(guarded, tmp_path):
    ctl, work, std, handles = guarded
    before = snap(ctl, tmp_path / "definition_before")
    definition = before.blocks[before.entitydb[handles["block"]].dxf.name]
    internal = next(e for e in definition if e.dxftype() in ("LINE", "TEXT", "ATTDEF", "LWPOLYLINE", "CIRCLE"))
    for method, args in EDITS + CREATES:
        result = getattr(ctl, method)(internal.dxf.handle, *args)
        assert not result.ok and not result.new_handles and "공간" in result.error
    after = snap(ctl, tmp_path / "definition_after")
    assert changes.fingerprint(before.entitydb[internal.dxf.handle]) == changes.fingerprint(after.entitydb[internal.dxf.handle])


def test_mcp_snapshot_apply_and_skip_subprocess(guarded, tmp_path):
    ctl, work, std, handles = guarded
    source = ctl.doc_state(ctl.doc)["full_name"]
    before = snap(ctl, tmp_path / "mcp_before")
    code = '''
import asyncio, json, os
from src import server
server.config['work'] = dict(directory=WORK)
async def run():
    ctl = server.cad_service.controller
    assert await server.com(ctl.start_cad)
    state = await server.com(ctl.doc_state, ctl.doc)
    assert os.path.normcase(state['full_name']) == os.path.normcase(SOURCE), '시험 도면이 활성 도면이어야 함'
    base = (await server.mcp.call_tool('export_snapshot', {}))[1]
    assert base['status'] == 'success', base
    spec = dict(schema_version=1, standard=STANDARD, op='b3-mcp-change',
                target=dict(snapshot_id=base['snapshot']['snapshot_id']),
                changes=[dict(id='move', select=dict(handles=[HANDLE]), action='move', delta=[600,300])])
    first = (await server.mcp.call_tool('apply_changes', dict(spec=spec, scale=100)))[1]
    second = (await server.mcp.call_tool('apply_changes', dict(spec=spec, scale=100)))[1]
    assert first['status'] == second['status'] == 'success', (first, second)
    assert second['meta']['decision'] == 'skip', second
    print(json.dumps([first['status'], second['meta']['decision']]))
asyncio.run(run())
'''
    # 토큰 치환은 Python 리터럴에만 하며 셸을 통하지 않는다.
    for key, value in (("WORK", str(work)), ("SOURCE", source), ("STANDARD", std.ref), ("HANDLE", handles["line"])):
        code = code.replace(key, repr(value))
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True,
                            text=True, encoding="utf-8", timeout=240)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == ["success", "skip"]
    after = snap(ctl, tmp_path / "mcp_after")
    delta = after.entitydb[handles["line"]].dxf.start - before.entitydb[handles["line"]].dxf.start
    assert tuple(delta) == (600, 300, 0)
