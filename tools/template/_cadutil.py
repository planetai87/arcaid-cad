"""템플릿 도구가 함께 쓰는 CAD 보조 함수(COM, Windows 전용)"""
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cad_controller import CADController  # noqa: E402,F401  (도구가 여기서 가져다 쓴다)


def ensure_active(ctl, doc, timeout=10.0):
    """SendCommand·LISP는 활성 문서로 가므로, 보내기 전에 대상 문서가 활성인지 확인한다

    문서를 닫거나 연 직후에는 활성 문서가 바로 바뀌지 않아 명령이 사라진 적이 있다(결과 파일 없음).
    """
    name = ctl._safe_attr(doc, "Name")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = ctl._safe_attr(ctl._safe_attr(ctl.app, "ActiveDocument"), "Name")
        if active == name and ctl._read_cmdactive() == 0:
            return
        try:
            ctl._call_attr(doc, "Activate")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise RuntimeError(f"대상 문서가 활성화되지 않음: {name}")


def lisp(ctl, expr):
    """활성 문서를 확인한 뒤 LISP 식을 실행하고 결과 문자열을 돌려준다"""
    ensure_active(ctl, ctl.doc)
    r = ctl.run_lisp(expr)
    if not r["ok"]:
        raise RuntimeError(f"LISP 실패: {r['error']} :: {expr[:100]}")
    return r["value"]


def close_work_docs(ctl, work: Path):
    """작업 폴더에서 연 문서가 남아 있으면 저장하지 않고 닫는다(앞선 실행이 실패했을 때 대비)"""
    prefix = os.path.normcase(str(work.resolve())) + os.sep
    docs = ctl._call(getattr, ctl.app, "Documents")
    for i in range(ctl._call(getattr, docs, "Count") - 1, -1, -1):
        doc = ctl._call_attr(docs, "Item", i)
        full = os.path.normcase(str(ctl._safe_attr(doc, "FullName") or ""))
        if full.startswith(prefix):
            ctl._call_attr(doc, "Close", False)
            print("작업 사본 닫음:", full)


def connect():
    """실행 중인 CAD에 붙고 (컨트롤러, 사용자 활성 도면 정보)를 돌려준다"""
    ctl = CADController()
    if not ctl.start_cad():
        raise SystemExit("CAD에 연결하지 못함")
    user = next((d for d in ctl.list_drawings() if d["is_active"]), None)
    return ctl, user


def restore_user(ctl, user):
    if user:
        ctl.switch_drawing(user["full_name"] or user["name"])
        print("사용자 도면 재활성화:", user["name"])
