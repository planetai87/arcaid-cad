"""DXF 파일 모드의 CLI·MCP 결과 동일성(Windows, CAD 불필요)"""
import json
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/engine"))
from check_cases import clean  # noqa: E402
from src import egress  # noqa: E402
from test_get_standard import call_tool  # noqa: E402

CASES = ("violations", "clean", "한글 공백", "missing")
CALLS = (("read", None), ("check", 100), ("check", None))


@pytest.fixture(scope="module")
def comparisons(tmp_path_factory):
    folder = tmp_path_factory.mktemp("cli-mcp")
    paths = {name: folder / f"{name}.dxf" for name in CASES}
    for name in CASES[:-1]:
        clean(paths[name])
    doc = ezdxf.readfile(paths["violations"])
    for n in range(25):
        doc.modelspace().add_line((0, n * 100), (1000, n * 100), dxfattribs={"layer": "WAL", "color": 1})
    doc.modelspace().query("TEXT[layer=='ROM']").first.dxf.text = "극비 객실명"
    doc.saveas(paths["violations"])
    base = egress.EgressPolicy.load(ROOT / "config/egress.yaml")
    deny = folder / "deny.yaml"
    deny.write_text(json.dumps({"policy": {key: "deny" for key in base.policy}, "fields": base.fields,
                                "always": sorted(base.always)}, ensure_ascii=False), encoding="utf-8")
    policies = {"allow": ROOT / "config/egress.yaml", "deny": deny}
    requests, keys = [], []
    for case in CASES:
        for policy in policies:
            for command, scale in CALLS:
                arguments = {"file_path": str(paths[case])}
                if scale is not None:
                    arguments["scale"] = scale
                requests.append({"tool": "read_drawing" if command == "read" else "check_standard",
                                 "arguments": arguments, "policy": str(policies[policy])})
                keys.append((case, policy, command, scale))
    code = """
import asyncio, json, os, sys
sys.path.insert(0, 'src')
import server
server.config['work'] = {'directory': sys.argv[1]}
async def collect():
    results = []
    for request in json.load(sys.stdin):
        os.environ['ARCAID_EGRESS_FILE'] = request['policy']
        result = await server.mcp.call_tool(request['tool'], request['arguments'])
        results.append(result[1])
    return results
print(json.dumps(asyncio.run(collect()), ensure_ascii=False))
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARCAID_")}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.run([sys.executable, "-B", "-c", code, str(folder / "work")], cwd=ROOT, env=env,
                           input=json.dumps(requests, ensure_ascii=False), capture_output=True,
                           text=True, encoding="utf-8", timeout=120)
    assert child.returncode == 0, child.stderr
    results = dict(zip(keys, json.loads(child.stdout)))
    violation = results[("violations", "allow", "check", 100)]
    assert violation["violation_counts"]["COLOR_NOT_BYLAYER"] == 25 and violation["truncated"]
    return paths, policies, results


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("policy", ("allow", "deny"))
@pytest.mark.parametrize("command,scale", CALLS)
def test_cli_matches_filtered_call_tool(comparisons, case, policy, command, scale):
    paths, policies, expected = comparisons
    args = [command, str(paths[case]), "--policy", str(policies[policy])]
    if scale is not None:
        args += ["--scale", str(scale)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARCAID_")}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.run([sys.executable, "-B", "-m", "src.cli", *args], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert child.returncode == (1 if case == "missing" else 0), child.stderr
    actual = json.loads(child.stdout)
    mcp = expected[(case, policy, command, scale)]
    assert {k: v for k, v in actual.items() if k != "full_path"} == {k: v for k, v in mcp.items() if k != "full_path"}
    if policy == "deny":
        assert str(paths[case]) not in child.stdout and "극비 객실명" not in child.stdout


@pytest.mark.parametrize("command,tool", [("read", "read_drawing"), ("check", "check_standard")])
@pytest.mark.parametrize("broken", [False, True])
def test_invalid_standard_matches_mcp(tmp_path, monkeypatch, command, tool, broken):
    standard = tmp_path / "잘못된 표준"
    if broken:
        standard.mkdir()
        (standard / "profile.yaml").write_text("schema_version: invalid", encoding="utf-8")
    monkeypatch.setenv("ARCAID_STANDARD_DIR", str(standard))
    monkeypatch.setenv("ARCAID_EGRESS_FILE", str(ROOT / "config/egress.yaml"))
    source = str(ROOT / "standards/heo-2026/template.dxf")
    expected = call_tool(tool, {"file_path": source}, work=tmp_path / "work")
    child = subprocess.run([sys.executable, "-B", "-m", "src.cli", command, source], cwd=ROOT,
                           capture_output=True, timeout=120)
    actual = json.loads(child.stdout.decode("utf-8"))
    assert child.returncode == 1 and actual == expected
