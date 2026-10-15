"""MCP·CLI 공용 출력: 검사 결과 모델과 상한, 원본 보존"""
import json
from pathlib import Path

import pytest
import yaml

from check_cases import entities
from src.engine.apply import CheckFixResult
from src.engine.check import check_standard
from src.engine.ops import check_file, limit_build
from src.engine.results import Scope, Status
from src.engine.standards import load_standard
from src.engine.write import BuildResult, ManifestItem
from src import egress

ROOT = Path(__file__).resolve().parents[2]


def test_check_file_matches_engine_and_wraps_fix(tmp_path):
    path, _ = entities(tmp_path / "위반 도면.dxf")
    std = load_standard()
    result = check_file(path, std, k=100)
    assert isinstance(result, CheckFixResult) and result.fix is None
    assert result.model_dump(exclude={"fix"}) == check_standard(path, std, k=100, limit=20).model_dump()
    full = tmp_path / "전체.json"
    limited = check_file(path, std, full_out=full, limit=1)
    assert limited.truncated and limited.assumptions
    assert "fix" not in json.loads(full.read_text(encoding="utf-8"))


@pytest.mark.parametrize("count, kept, truncated", [(0, 0, False), (20, 20, False), (21, 20, True), (107, 20, True)])
@pytest.mark.parametrize("status", [Status.SUCCESS, Status.FAILED])
def test_limit_build_keeps_original(count, kept, truncated, status):
    original = BuildResult(status=status, summary="작성 결과", scope=Scope(target="도면"),
                           manifest=[ManifestItem(oid=str(i), path=f"items[{i}]", kind="line", layer="WAL")
                                     for i in range(count)])
    before = original.model_dump()
    result = limit_build(original)
    assert result is not original and len(result.manifest) == kept and result.truncated is truncated
    assert result.status is status and original.model_dump() == before
    if result.manifest:
        result.manifest[0].layer = "CEN"
        assert original.manifest[0].layer == "WAL"


def test_limit_build_custom_limit():
    original = BuildResult(status=Status.SUCCESS, summary="작성 결과", scope=Scope(target="도면"),
                           manifest=[ManifestItem(oid="벽", path="items[0]", kind="line", layer="WAL")])
    assert limit_build(original, limit=0).manifest == []
    assert limit_build(original, limit=0).truncated


def _policy(path, decision):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"policy": {"path": decision}}), encoding="utf-8")


def test_policy_precedence_and_relative_paths(tmp_path, monkeypatch):
    repo, cwd = tmp_path / "repo", tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("ARCAID_EGRESS_FILE", raising=False)
    _policy(repo / "config/egress.yaml", "deny")
    _policy(repo / "configured.yaml", "allow")
    _policy(repo / "environment.yaml", "deny")
    _policy(cwd / "explicit.yaml", "allow")
    assert egress.load_policy(None, {}, repo).decide("path") == "deny"
    config = {"egress": {"file": "configured.yaml"}}
    assert egress.load_policy(None, config, repo).decide("path") == "allow"
    monkeypatch.setenv("ARCAID_EGRESS_FILE", "environment.yaml")
    assert egress.load_policy(None, config, repo).decide("path") == "deny"
    assert egress.load_policy("explicit.yaml", config, repo).decide("path") == "allow"
    assert egress.load_policy(str(cwd / "explicit.yaml"), config, repo).decide("path") == "allow"


@pytest.mark.parametrize("content", [None, "policy: [", "policy: {path: invalid}", "policy: []", "fields: {path: 1}",
                                      "[]", "false", "always: bad"])
def test_policy_load_errors_are_not_replaced(tmp_path, monkeypatch, content):
    monkeypatch.delenv("ARCAID_EGRESS_FILE", raising=False)
    path = tmp_path / "policy.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises((OSError, ValueError, TypeError, yaml.YAMLError)):
        egress.load_policy(str(path), {}, ROOT)


def test_deny_scrubs_posix_paths_but_keeps_scale_and_manifest():
    policy = egress.EgressPolicy.load(ROOT / "config/egress.yaml").with_overrides(
        {"path": "deny", "unclassified": "deny"})
    payload = {"summary": "축척 1/100, 사본 /tmp/비밀 프로젝트/plan.dxf 확인",
               "warnings": ["열기 실패 '/home/건축주/도면.dxf'"],
               "manifest": [{"oid": "wall-1", "path": "items[0]/wall", "kind": "line", "layer": "WAL"}]}
    result, changed = egress.filter_payload("build", payload, policy)
    assert changed and "1/100" in result["summary"]
    assert "비밀" not in json.dumps(result, ensure_ascii=False) and "건축주" not in str(result)
    assert result["manifest"] == payload["manifest"]


def test_path_scrub_keeps_urls_and_lone_slashes():
    assert egress.scrub_text("축선 / 버블, 1/100", [], True) == "축선 / 버블, 1/100"
    assert egress.scrub_text("문서 https://example.com/a 참고", [], True) == "문서 https://example.com/a 참고"
    assert "건축주" not in egress.scrub_text(r"열기 D:\건축주\a.dxf, /home/건축주/b.dxf", [], True)
