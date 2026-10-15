"""CAD 없이 실행하는 CLI: 엔진 동일성·저장 경계·정책·종료 코드"""
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/engine"))
from check_cases import clean, entities  # noqa: E402
from src import egress  # noqa: E402
from src.engine.apply import CheckFixResult  # noqa: E402
from src.engine.check import check_standard  # noqa: E402
from src.engine.read import read_drawing  # noqa: E402
from src.engine.standards import load_standard  # noqa: E402
from src.engine.write import build_dxf  # noqa: E402

SPEC = ROOT / "tests/engine/specs/dangsan_typical.json"


def run_cli(*args, cwd, env=None, json_output=True):
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("ARCAID_")}
    child_env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    child_env.update(env or {})
    result = subprocess.run([sys.executable, "-B", "-m", "src.cli", *map(str, args)], cwd=cwd,
                            env=child_env, capture_output=True, timeout=120)
    if not json_output:
        return result
    output = result.stdout.decode("utf-8")
    payload = json.loads(output)
    assert isinstance(payload, dict), output
    return result.returncode, payload, output


@pytest.fixture(scope="module")
def std():
    return load_standard()


def without_full(payload):
    return {k: v for k, v in payload.items() if k != "full_path"}


def test_read_matches_engine_and_writes_only_requested_full(tmp_path, std):
    path, _ = clean(tmp_path / "한글 도면.dxf")
    before = set(tmp_path.rglob("*"))
    code, result, _ = run_cli("read", path, cwd=tmp_path)
    assert code == 0
    assert without_full(result) == without_full(json.loads(read_drawing(path, std).model_dump_json()))
    assert set(tmp_path.rglob("*")) == before
    full = tmp_path / "전체/객체.json"
    code, result, _ = run_cli("read", path, "--full", full, cwd=tmp_path)
    assert code == 0 and result["full_path"] == str(full)
    expected = tmp_path / "expected.json"
    read_drawing(path, std, full_out=expected)
    assert json.loads(full.read_text(encoding="utf-8")) == json.loads(expected.read_text(encoding="utf-8"))
    assert set(tmp_path.rglob("*")) - before == {full.parent, full, expected}


def test_check_matches_engine_and_limits_each_code(tmp_path, std):
    path, _ = entities(tmp_path / "위반.dxf")
    before = set(tmp_path.iterdir())
    code, result, _ = run_cli("check", path, "--scale", 100, cwd=tmp_path)
    assert code == 0 and result["fix"] is None
    expected = CheckFixResult(**check_standard(path, std, k=100, limit=20).model_dump())
    assert without_full(result) == without_full(json.loads(expected.model_dump_json()))
    assert set(tmp_path.iterdir()) == before
    code, result, _ = run_cli("check", path, "--limit", 1, cwd=tmp_path)
    assert code == 0 and result["truncated"]
    assert max(Counter(v["code"] for v in result["violations"]).values()) == 1
    assert result["violation_counts"] == expected.violation_counts
    assert all(len(f["handles"]) <= 1 for f in result["fixes"])
    assert result["assumptions"] == [{"name": "scale", "value": 100, "reason": "요청에 축척이 없어 A3 1/100로 가정"}]


def test_check_strict_and_full(tmp_path, std):
    path, _ = entities(tmp_path / "위반.dxf")
    full = tmp_path / "검사.json"
    code, result, _ = run_cli("check", path, "--strict", "--limit", 1, "--full", full, cwd=tmp_path)
    assert code == 3 and result["status"] == "success"
    saved = json.loads(full.read_text(encoding="utf-8"))
    assert saved == json.loads(check_standard(path, std).model_dump_json())
    assert "fix" not in saved and saved["full_path"] is None and not saved["truncated"]
    good, _ = clean(tmp_path / "깨끗함.dxf")
    code, result, _ = run_cli("check", good, "--strict", cwd=tmp_path)
    assert code == 0 and not result["violation_counts"] and not result["unchecked"]


def test_strict_rejects_unchecked_without_violations(tmp_path):
    path, _ = clean(tmp_path / "검사불가.dxf")
    doc = ezdxf.readfile(path)
    doc.modelspace().add_mtext("{\\H2x;검사불가}",
                             dxfattribs={"layer": "ROM", "style": "돋움", "char_height": 135})
    doc.saveas(path)
    code, result, _ = run_cli("check", path, "--strict", cwd=tmp_path)
    assert code == 3 and not result["violation_counts"] and result["unchecked"]


@pytest.mark.parametrize("option,value", [("--scale", "0"), ("--scale", "10001"), ("--scale", "a"),
                                          ("--limit", "0"), ("--limit", "-1")])
def test_bad_numeric_arguments_use_argparse(tmp_path, option, value):
    result = run_cli("check", "unused.dxf", option, value, cwd=tmp_path, json_output=False)
    assert result.returncode == 2 and not result.stdout and result.stderr
    assert list(tmp_path.iterdir()) == []


def test_build_matches_engine_limits_stdout_and_passes_strict(tmp_path, std):
    out, full = tmp_path / "출력.dxf", tmp_path / "전체/작성.json"
    code, result, _ = run_cli("build", SPEC, "-o", out, "--full", full, cwd=tmp_path)
    assert code == 0 and len(result["manifest"]) == 20 and result["truncated"]
    saved = json.loads(full.read_text(encoding="utf-8"))
    assert len(saved["manifest"]) == 107 and not saved["truncated"]
    expected = build_dxf(SPEC.read_text(encoding="utf-8"), std, out)
    assert saved == json.loads(expected.model_dump_json())
    assert result["manifest"] == saved["manifest"][:20]
    assert {k: v for k, v in result.items() if k not in {"manifest", "truncated"}} == {
        k: v for k, v in saved.items() if k not in {"manifest", "truncated"}}
    assert run_cli("check", out, "--strict", cwd=tmp_path)[0] == 0


def test_build_without_full_creates_only_dxf(tmp_path):
    out = tmp_path / "out.dxf"
    assert run_cli("build", SPEC, "-o", out, cwd=tmp_path)[0] == 0
    assert set(tmp_path.iterdir()) == {out}


def test_invalid_spec_is_engine_failure_and_does_not_write_dxf(tmp_path):
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    spec["items"][2]["items"][0]["layer"] = "A-WALL"
    source, out = tmp_path / "bad.json", tmp_path / "out.dxf"
    source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    code, result, _ = run_cli("build", source, "-o", out, cwd=tmp_path)
    assert code == 1 and result["status"] == "failed"
    assert [v["code"] for v in result["violations"]] == ["SPEC_LAYER"]
    assert not out.exists()


@pytest.mark.parametrize("command", ["read", "check"])
def test_dwg_requires_mcp_and_missing_dxf_matches_engine(tmp_path, std, command):
    code, result, _ = run_cli(command, "도면.DWG", cwd=tmp_path)
    assert code == 1 and result["status"] == "failed" and "export_snapshot" in result["summary"]
    code, result, _ = run_cli(command, "없는 도면.dxf", cwd=tmp_path)
    engine = read_drawing if command == "read" else check_standard
    expected = json.loads(engine(Path("없는 도면.dxf"), std).model_dump_json())
    if command == "check":
        expected["fix"] = None
    assert code == 1 and result == expected and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["read", "check", "build"])
def test_conflicting_paths_are_rejected_before_writing(tmp_path, command):
    path = tmp_path / ("source.json" if command == "build" else "source.dxf")
    path.write_bytes(b"original")
    args = (command, path, "-o", path) if command == "build" else (command, path, "--full", path)
    code, result, _ = run_cli(*args, cwd=tmp_path)
    assert code == 1 and result["status"] == "failed" and path.read_bytes() == b"original"
    if command == "build":
        code, result, _ = run_cli("build", SPEC, "-o", path, "--full", path, cwd=tmp_path)
        assert code == 1 and path.read_bytes() == b"original"


def test_hardlink_full_path_is_same_file(tmp_path):
    path, _ = clean(tmp_path / "source.dxf")
    alias = tmp_path / "alias.json"
    os.link(path, alias)
    before = path.read_bytes()
    assert run_cli("read", path, "--full", alias, cwd=tmp_path)[0] == 1
    assert path.read_bytes() == before


def test_nonexistent_normalized_output_paths_conflict(tmp_path):
    out = tmp_path / "out.dxf"
    code, result, _ = run_cli("build", SPEC, "-o", "out.dxf", "--full", out, cwd=tmp_path)
    assert code == 1 and result["status"] == "failed" and not out.exists()


@pytest.mark.parametrize("command", ["read", "check", "build"])
def test_full_save_failure_takes_priority_over_strict(tmp_path, command):
    path, _ = entities(tmp_path / "source.dxf")
    blocker = tmp_path / "file"
    blocker.write_text("막힘", encoding="utf-8")
    args = ("build", SPEC, "-o", tmp_path / "out.dxf") if command == "build" else (command, path)
    if command == "check":
        args += ("--strict",)
    code, result, _ = run_cli(*args, "--full", blocker / "full.json", cwd=tmp_path)
    assert code == 1 and result["warnings"]


@pytest.mark.parametrize("content", [None, "policy: [", "policy: {path: invalid}"])
def test_bad_policy_is_fixed_json_and_prevents_all_writes(tmp_path, content):
    policy = tmp_path / "secret-policy.yaml"
    if content is not None:
        policy.write_text(content, encoding="utf-8")
    before = set(tmp_path.rglob("*"))
    failures = []
    for args in [("read", "secret.dxf"), ("check", "secret.dxf", "--strict"),
                 ("build", SPEC, "-o", "secret.dxf")]:
        code, result, output = run_cli(*args, "--policy", policy, "--full", "full.json", cwd=tmp_path)
        assert code == 1 and result["status"] == "failed" and result["scope"]["target"] == ""
        assert result["summary"] == "외부 반환 정책을 읽지 못했습니다"
        assert "secret" not in output and str(tmp_path) not in output
        failures.append(result)
    assert failures[0] == failures[1] == failures[2]
    assert set(tmp_path.rglob("*")) == before


def deny_policy(path):
    base = egress.EgressPolicy.load(ROOT / "config/egress.yaml")
    path.write_text(json.dumps({"policy": {k: "deny" for k in base.policy}, "fields": base.fields,
                                "always": sorted(base.always)}, ensure_ascii=False), encoding="utf-8")


def test_deny_filters_entire_stdout_without_saving_original(tmp_path):
    path, _ = clean(tmp_path / "비밀 프로젝트.dxf")
    doc = ezdxf.readfile(path)
    doc.modelspace().query("TEXT[layer=='ROM']").first.dxf.text = "실명 극비 101"
    doc.blocks.new("XREF_SITE", dxfattribs={"flags": 4, "xref_path": "/home/secret/site.dwg"})
    doc.saveas(path)
    policy = tmp_path / "deny.yaml"
    deny_policy(policy)
    before = set(tmp_path.rglob("*"))
    for command in ("read", "check"):
        code, _, output = run_cli(command, path, "--policy", policy, cwd=tmp_path)
        assert code == 0
        for secret in (str(tmp_path), "비밀 프로젝트", "실명 극비", "/home/secret", "C:\\secret"):
            assert secret not in output
    for missing in ("/home/secret/missing.dxf", r"C:\secret\missing.dxf"):
        code, _, output = run_cli("read", missing, "--policy", policy, cwd=tmp_path)
        assert code == 1 and "secret" not in output
    assert set(tmp_path.rglob("*")) == before


def test_deny_build_preserves_manifest_identity_and_writes_only_dxf(tmp_path):
    policy, out = tmp_path / "deny.yaml", tmp_path / "비밀 출력.dxf"
    deny_policy(policy)
    code, result, output = run_cli("build", SPEC, "-o", out, "--policy", policy, cwd=tmp_path)
    assert code == 0 and len(result["manifest"]) == 20
    assert all(m["oid"] != egress.REDACTED and m["path"] != egress.REDACTED and m["kind"] != egress.REDACTED
               for m in result["manifest"])
    assert str(tmp_path) not in output and "비밀 출력" not in output
    assert set(tmp_path.iterdir()) == {policy, out}


def test_stdout_is_utf8_even_with_cp949_environment(tmp_path):
    path, _ = clean(tmp_path / "한글 공백 도면.dxf")
    code, result, output = run_cli("read", path, cwd=tmp_path, env={"PYTHONIOENCODING": "cp949"})
    assert code == 0 and "객실 101" in output and result["meta"]["source"] == str(path)


def test_explicit_standard_and_environment_standard(tmp_path):
    standard = ROOT / "standards/heo-2026"
    assert run_cli("check", standard / "template.dxf", "--standard", standard, cwd=tmp_path)[0] == 0
    assert run_cli("check", standard / "template.dxf", cwd=tmp_path,
                   env={"ARCAID_STANDARD_DIR": str(standard)})[0] == 0
    code, result, _ = run_cli("read", "unused.dxf", "--standard", "missing", cwd=tmp_path)
    assert code == 1 and result["status"] == "failed"


def test_build_refuses_input_full_collision(tmp_path):
    source = tmp_path / "source.json"
    source.write_bytes(SPEC.read_bytes())
    before = source.read_bytes()
    out = tmp_path / "out.dxf"
    assert run_cli("build", source, "-o", out, "--full", source, cwd=tmp_path)[0] == 1
    assert source.read_bytes() == before and not out.exists()


def test_build_write_failure_and_missing_spec_are_json_failures(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("막힘", encoding="utf-8")
    for source, out in [(SPEC, blocker / "out.dxf"), ("missing.json", tmp_path / "out.dxf")]:
        code, result, _ = run_cli("build", source, "-o", out, cwd=tmp_path)
        assert code == 1 and result["status"] == "failed" and not out.exists()


def test_malformed_spec_and_corrupt_dxf_are_json_failures(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("{", encoding="utf-8")
    code, result, _ = run_cli("build", source, "-o", "out.dxf", cwd=tmp_path)
    assert code == 1 and result["violations"][0]["code"] == "SPEC_SCHEMA"
    for command in ("read", "check"):
        code, result, _ = run_cli(command, source, cwd=tmp_path)
        assert code == 1 and result["status"] == "failed"


def test_deny_full_is_explicit_unfiltered_copy(tmp_path):
    path, _ = clean(tmp_path / "비밀 프로젝트.dxf")
    policy, full = tmp_path / "deny.yaml", tmp_path / "full.json"
    deny_policy(policy)
    code, _, output = run_cli("read", path, "--policy", policy, "--full", full, cwd=tmp_path)
    assert code == 0 and "객실 101" not in output and str(path) not in output
    assert "객실 101" in full.read_text(encoding="utf-8")
    assert set(tmp_path.iterdir()) == {path, policy, full}


def test_cli_policy_argument_overrides_environment(tmp_path):
    path, _ = clean(tmp_path / "비밀 프로젝트.dxf")
    deny = tmp_path / "deny.yaml"
    deny_policy(deny)
    code, _, output = run_cli("read", path, cwd=tmp_path, env={"ARCAID_EGRESS_FILE": str(deny)})
    assert code == 0 and "객실 101" not in output
    code, _, output = run_cli("read", path, "--policy", ROOT / "config/egress.yaml", cwd=tmp_path,
                              env={"ARCAID_EGRESS_FILE": str(deny)})
    assert code == 0 and "객실 101" in output
