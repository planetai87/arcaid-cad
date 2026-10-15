"""엔진이 CAD(COM)에 의존하지 않는지 확인한다(구조 결정 D1)"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("mcp", "win32com", "pythoncom", "pywintypes", "win32api", "win32gui")
CODE = f"""
import importlib, pkgutil, sys
import src.engine as engine
import src.cli, src.egress
for m in pkgutil.walk_packages(engine.__path__, "src.engine."):
    importlib.import_module(m.name)
bad = [n for n in sys.modules if n.split('.')[0] in {FORBIDDEN!r}]
print(",".join(bad))
sys.exit(1 if bad else 0)
"""


def test_engine_does_not_import_com():
    r = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, f"엔진이 COM 모듈을 불러옴: {r.stdout} {r.stderr}"
