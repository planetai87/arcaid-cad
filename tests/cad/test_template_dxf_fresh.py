"""템플릿 DWG를 지금 CAD로 다시 내보내도 표준과 일치하는지(커밋된 template.dxf가 낡지 않았는지)

실행 중인 CAD가 필요하다(ARCAID_CAD_TESTS=1). 임시 폴더의 사본에서만 작업하고 사용자 도면으로 돌아간다.
"""
import hashlib
import shutil
import sys
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools" / "template"))

from _cadutil import close_work_docs, connect, restore_user  # noqa: E402
from src.engine.definitions import compare_definitions  # noqa: E402
from src.engine.standards import load_standard  # noqa: E402


def test_fresh_export_matches_standard(tmp_path):
    std = load_standard()
    dwg = std.path(std.profile.template.dwg)
    assert hashlib.sha256(dwg.read_bytes()).hexdigest() == std.profile.template.dwg_sha256
    src, out = tmp_path / "template_fresh.dwg", tmp_path / "template_fresh.dxf"
    shutil.copyfile(dwg, src)
    ctl, user = connect()
    try:
        assert ctl.open_drawing(str(src))
        ctl._call_attr(ctl.doc, "SaveAs", str(out), 61)
        ctl._call_attr(ctl.doc, "Close", False)
    finally:
        close_work_docs(ctl, tmp_path)
        restore_user(ctl, user)
    violations = compare_definitions(std, ezdxf.readfile(str(out)))
    assert violations == [], [v.message for v in violations]
