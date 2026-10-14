import sys

# MCP 서버는 pywin32가 필요해 Windows에서만 불러올 수 있다
if sys.platform != "win32":
    collect_ignore_glob = ["test_*.py"]
