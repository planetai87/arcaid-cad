import importlib.util
import sys

# MCP 서버는 pywin32와 mcp가 필요해 Windows에서, 두 패키지가 있을 때만 불러올 수 있다
_needed = ("win32com", "mcp")
if sys.platform != "win32" or any(importlib.util.find_spec(m) is None for m in _needed):
    collect_ignore_glob = ["test_*.py"]
