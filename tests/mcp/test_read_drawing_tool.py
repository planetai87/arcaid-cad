"""read_drawing 툴: DXF 경로를 CAD 연결 없이 바로 읽는다. 작업 폴더가 아직 없어도 만들어 쓴다"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_reads_dxf_directly_into_fresh_work_dir(tmp_path):
    work = tmp_path / "work"
    dxf = ROOT / "standards" / "heo-2026" / "template.dxf"
    code = ("import asyncio, json, sys; sys.path.insert(0, 'src'); import server; "
            f"server.config['work'] = {{'directory': {str(work)!r}}}; "
            f"r = asyncio.run(server.mcp.call_tool('read_drawing', {{'file_path': {str(dxf)!r}}})); "
            "r = r[1] if isinstance(r, tuple) else r; print(json.dumps(r, ensure_ascii=False))")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["status"] == "success", r["summary"]
    assert r["meta"]["dxfversion"] == "AC1027" and r["meta"]["units"] == "mm"
    assert Path(r["full_path"]).parent == work / "snapshots" and Path(r["full_path"]).exists()
