"""외부 반환 필터(src/egress.py, 구조 결정 D8) 테스트

기본 정책(config/egress.yaml)은 A안(제한 없음, run_lisp 결과의 경로만 가림)이다.
B안처럼 막는 정책을 줘도 결과가 출력 스키마를 그대로 통과하는지(키를 지우지 않고 값만 바꾸는지) 본다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import jsonschema
import pytest
from ezdxf.enums import TextEntityAlignment

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import egress  # noqa: E402
from engine.read import ReadDrawingResult, read_drawing  # noqa: E402
from engine.standards import load_standard  # noqa: E402
from models import EntityPropertiesResult, ListDrawingsResult, LispResult  # noqa: E402

from test_get_standard import ROOT  # noqa: E402

DENY_ALL = {"policy": {"path": "deny", "text": "deny", "block_attribute": "deny", "sheet": "deny",
                       "screenshot": "deny", "lisp_value": "deny", "unclassified": "deny"}}


@pytest.fixture(scope="module")
def default_policy():
    return egress.EgressPolicy.load(ROOT / "config" / "egress.yaml")


@pytest.fixture(scope="module")
def deny_policy(default_policy):
    return default_policy.with_overrides(DENY_ALL["policy"])


@pytest.fixture
def read_payload(tmp_path):
    std = load_standard()
    doc = ezdxf.readfile(std.path(std.profile.template.dxf))
    msp = doc.modelspace()
    msp.add_text("객실 비밀 101", height=135, dxfattribs={"layer": "ROM", "style": "돋움"}).set_placement(
        (3000, 3000), align=TextEntityAlignment.MIDDLE_CENTER)
    doc.blocks.new("XREF_SITE", dxfattribs={"flags": 4, "xref_path": r"D:\건축주\현장\site.dwg"})
    path = tmp_path / "비밀 프로젝트.dxf"
    doc.saveas(path)
    result = read_drawing(path, std, full_out=tmp_path / "read.json")
    result.warnings.append(f"사본 위치 {path} 를 확인")
    return json.loads(result.model_dump_json()), path


def test_default_policy_is_plan_a(default_policy):
    assert default_policy.decide("path") == "allow" and default_policy.decide("text") == "allow"
    assert default_policy.decide("screenshot") == "allow"
    assert default_policy.decide("lisp_value") == "scrub_paths"
    assert default_policy.unclassified == "allow"


def test_plan_a_passes_results_through(default_policy, read_payload):
    payload, _ = read_payload
    filtered, changed = egress.filter_payload("read_drawing", payload, default_policy)
    assert filtered == payload and changed is False


def test_plan_a_scrubs_paths_only_in_lisp_values(default_policy):
    payload = LispResult(success=True, message="ok", value='("D:\\\\현장\\\\비밀 도면.dwg" 42)').model_dump()
    filtered, changed = egress.filter_payload("run_lisp", payload, default_policy)
    assert changed and "현장" not in filtered["value"] and "[경로]" in filtered["value"] and "42" in filtered["value"]
    other, changed = egress.filter_payload("get_standard", {"message": "D:\\a\\b.dwg"}, default_policy)
    assert not changed and other == {"message": "D:\\a\\b.dwg"}


def test_deny_redacts_and_keeps_schema(deny_policy, read_payload):
    payload, path = read_payload
    filtered, changed = egress.filter_payload("read_drawing", payload, deny_policy)
    assert changed
    text = json.dumps(filtered, ensure_ascii=False)
    for secret in ("객실 비밀 101", "건축주", "비밀 프로젝트", str(path.parent)):
        assert secret not in text, secret
    assert filtered["meta"]["dxf_path"] == egress.REDACTED and filtered["full_path"] == egress.REDACTED
    assert filtered["rooms"][0]["text"] == egress.REDACTED and filtered["xrefs"][0]["path"] == egress.REDACTED
    assert filtered["status"] == payload["status"] and filtered["grid"] == payload["grid"]
    jsonschema.validate(filtered, ReadDrawingResult.model_json_schema())


@pytest.mark.parametrize("model, payload, tool", [
    (ListDrawingsResult, {"success": True, "message": "열린 도면 1개: D:\\현장\\a.dwg",
                          "drawings": [{"index": 0, "name": "a.dwg", "full_name": "D:\\현장\\a.dwg",
                                        "is_active": True, "saved": True}]}, "list_drawings"),
    (EntityPropertiesResult, {"success": True, "message": "ok", "handle": "1A", "entity_type": "text",
                              "layer": "ROM", "properties": {"text": "건축주 이름", "height": 135}},
     "get_entity_properties"),
    (LispResult, {"success": True, "message": "ok", "value": '"건축주"', "cmdactive": 0}, "run_lisp"),
])
def test_deny_keeps_other_models_valid(deny_policy, model, payload, tool):
    filtered, changed = egress.filter_payload(tool, json.loads(model(**payload).model_dump_json()), deny_policy)
    assert changed and "현장" not in json.dumps(filtered, ensure_ascii=False)
    assert "건축주" not in json.dumps(filtered, ensure_ascii=False)
    jsonschema.validate(filtered, model.model_json_schema())


def test_deny_scrubs_paths_present_only_in_sentences(deny_policy):
    payload = {"summary": "축척 1/100, 사본 /tmp/현장 비밀/plan.dxf",
               "warnings": [r"읽기 실패 C:\건축주\도면.dxf", "실패 '/home/건축주/도면.dxf'"]}
    result, changed = egress.filter_payload("check_standard", payload, deny_policy)
    assert changed and "1/100" in result["summary"]
    assert "건축주" not in str(result) and "현장 비밀" not in str(result)


def test_manifest_identity_is_always_returned(deny_policy):
    payload = {"manifest": [{"oid": "wall-1", "path": "items[0]/wall", "kind": "line", "layer": "WAL"}]}
    result, changed = egress.filter_payload("build", payload, deny_policy)
    assert result == payload and not changed


def test_apply_changes_deny_keeps_apply_result_valid(deny_policy):
    from src.engine.apply import ApplyMeta, ApplyResult
    from src.engine.results import Applied, Scope, Status

    payload = ApplyResult(status=Status.SUCCESS, summary="변경 완료", scope=Scope(target="활성 도면"),
                          meta=ApplyMeta(op="b3-change", drawing=r"D:\비밀\도면.dwg", decision="apply",
                                         result_path=r"D:\비밀\변경.json"),
                          applied=[Applied(action="move", count=1, handles=["1A"])]).model_dump(mode="json")
    result, changed = egress.filter_payload("apply_changes", payload, deny_policy)
    assert changed and "비밀" not in json.dumps(result, ensure_ascii=False)
    assert result["status"] == "success" and result["meta"]["decision"] == "apply"
    assert result["applied"][0]["handles"] == ["1A"]
    jsonschema.validate(result, ApplyResult.model_json_schema())


def _server(code, env):
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8", **env},
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_server_filters_tool_results_and_keeps_full_copy(tmp_path):
    policy = tmp_path / "deny.yaml"
    base = egress.EgressPolicy.load(ROOT / "config" / "egress.yaml")
    policy.write_text(json.dumps({"policy": DENY_ALL["policy"], "fields": base.fields, "always": sorted(base.always)},
                                 ensure_ascii=False), encoding="utf-8")
    work = tmp_path / "work"
    dxf = ROOT / "standards" / "heo-2026" / "template.dxf"
    code = ("import asyncio, json, sys; sys.path.insert(0, 'src'); import server; "
            f"server.config['work'] = {{'directory': {str(work)!r}}}; "
            f"r = asyncio.run(server.mcp.call_tool('read_drawing', {{'file_path': {str(dxf)!r}}})); "
            "shot = asyncio.run(server.mcp.call_tool('screenshot', {})); "
            "shot = shot[0] if isinstance(shot, tuple) else shot; "
            "print(json.dumps({'read': r[1], 'text': r[0][0].text, 'shot': [c.type for c in shot]}, ensure_ascii=False))")
    r = _server(code, {"ARCAID_EGRESS_FILE": str(policy)})
    assert r["read"]["full_path"] == egress.REDACTED and r["read"]["meta"]["source"] == egress.REDACTED
    assert "template.dxf" not in r["text"]                       # JSON 문자열 쪽도 같은 결과
    assert r["shot"] == ["text"]                                 # 화면 캡처는 막힘
    saved = list((work / "egress").rglob("*read_drawing.json"))
    assert len(saved) == 1 and "template.dxf" in saved[0].read_text(encoding="utf-8")
