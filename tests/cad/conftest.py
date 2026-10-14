import os
import sys

# 실행 중인 CAD가 필요한 연동 테스트: Windows에서 ARCAID_CAD_TESTS=1일 때만 모은다
if sys.platform != "win32" or os.environ.get("ARCAID_CAD_TESTS") != "1":
    collect_ignore_glob = ["test_*.py"]
