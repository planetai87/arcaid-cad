"""T0 사전 검증 실험 공통 도구 (제품 코드 아님)

안전 규칙:
- 실행 중인 CAD에 붙되, 실험은 WORK(기본 C:\\arcaid\\work\\spike) 아래 새 시험 도면에서만 한다.
- 시작할 때 사용자가 연 도면 목록과 활성 도면, 저장 상태를 기록한다.
- 수정 작업 전에 대상 문서 경로가 WORK 아래인지 확인한다(guard). 아니면 중단한다.
- 끝날 때(실패해도) 진행 중 명령을 취소하고, 연 시험 도면을 저장하지 않고 닫고,
  바꾼 앱 시스템 변수를 되돌리고, 사용자의 활성 도면을 다시 활성화한다.

COM은 이 스크립트의 메인 스레드에서 직접 부른다(CADController.start_cad가 메인 스레드에서 COM을 초기화).
제품 코드처럼 COMExecutor에 본문을 넘기면 '바쁨' 오류 때 본문 전체가 다시 실행될 수 있어 쓰지 않는다.
"""
import json
import logging
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cad_controller import CADController  # noqa: E402

WORK = Path(os.environ.get("ARCAID_SPIKE_DIR", r"C:\arcaid\work\spike"))
RESULTS = WORK / "results"
TEMPLATE = WORK / "template.dwg"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def under_work(path) -> bool:
    return bool(path) and _norm(path).startswith(_norm(WORK) + os.sep)


class Spike:
    """with Spike("E2") as s: ... 형태로 쓴다"""

    def __init__(self, name: str):
        self.name = name
        self.ctl = CADController()
        self.data = {"name": name, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}
        self.opened = []          # 이 실험이 연 문서 경로(정규화)
        self.app_vars = {}        # 바꾼 앱 시스템 변수의 원래 값
        self.user_docs = []
        self.user_active = None

    # ---------- 시작·마무리 ----------

    def __enter__(self):
        if not self.ctl.start_cad():
            raise RuntimeError("CAD에 연결하지 못함")
        self.user_docs = self.ctl.list_drawings()
        self.user_active = next((d for d in self.user_docs if d["is_active"]), None)
        self.data["user_docs_before"] = self.user_docs
        print(f"[{self.name}] CAD 연결. 사용자 도면 {len(self.user_docs)}개, 활성: "
              f"{self.user_active and self.user_active['name']}")
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.data["error"] = "".join(traceback.format_exception(exc_type, exc, tb))
            print(f"[{self.name}] 오류: {exc}")
        cleanup = {}
        try:
            cleanup["cancel"] = self.ctl.cancel_command()
        except Exception as e:  # noqa: BLE001
            cleanup["cancel"] = f"실패: {e}"
        try:
            self._restore_app_vars()
            cleanup["app_vars_restored"] = self.app_vars
        except Exception as e:  # noqa: BLE001
            cleanup["app_vars_restored"] = f"실패: {e}"
        cleanup["closed"] = self._close_opened()
        cleanup["reactivated"] = self._reactivate_user()
        after = self.ctl.list_drawings()
        self.data["user_docs_after"] = after
        before_state = {d["full_name"] or d["name"]: d["saved"] for d in self.user_docs}
        after_state = {d["full_name"] or d["name"]: d["saved"] for d in after}
        cleanup["user_docs_unchanged"] = before_state == after_state
        cleanup["active_after"] = next((d["name"] for d in after if d["is_active"]), None)
        cleanup["cmdactive_after"] = self.ctl._read_cmdactive(retry=False)
        self.data["cleanup"] = cleanup
        self.data["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"{self.name}.json"
        out.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"[{self.name}] 마무리: 사용자 도면 그대로={cleanup['user_docs_unchanged']}, "
              f"활성={cleanup['active_after']}, CMDACTIVE={cleanup['cmdactive_after']} → {out}")
        return False

    def _close_opened(self):
        closed = []
        docs = self.ctl._call(getattr, self.ctl.app, "Documents")
        for i in range(self.ctl._call(getattr, docs, "Count") - 1, -1, -1):
            doc = self.ctl._call_attr(docs, "Item", i)
            full = str(self.ctl._safe_attr(doc, "FullName") or "")
            name = str(self.ctl._safe_attr(doc, "Name") or "")
            if (full and under_work(full)) or (_norm(full) in self.opened if full else False):
                try:
                    self.ctl._call_attr(doc, "Close", False)
                    closed.append(name)
                except Exception as e:  # noqa: BLE001
                    closed.append(f"{name}: 닫기 실패 {e}")
        return closed

    def _reactivate_user(self):
        if not self.user_active:
            return None
        target = self.user_active["full_name"] or self.user_active["name"]
        try:
            return self.ctl.switch_drawing(target)
        except Exception as e:  # noqa: BLE001
            return f"실패: {e}"

    # ---------- 안전장치 ----------

    def guard(self, doc=None):
        """수정 대상 문서가 WORK 아래 시험 도면인지 확인한다"""
        doc = doc if doc is not None else self.ctl.doc
        full = str(self.ctl._safe_attr(doc, "FullName") or "")
        if not under_work(full):
            raise RuntimeError(f"시험 도면이 아니라 수정하지 않음: {full or self.ctl._safe_attr(doc, 'Name')}")
        return doc

    def open(self, path):
        """WORK 아래 파일만 열고 작업 대상으로 지정한다"""
        path = Path(path)
        if not under_work(path):
            raise RuntimeError(f"WORK 밖 파일은 열지 않음: {path}")
        if not self.ctl.open_drawing(str(path)):
            raise RuntimeError(f"열기 실패: {path}")
        self.opened.append(_norm(path))
        self.guard()
        return self.ctl.doc

    def copy_and_open(self, src, dst_name):
        dst = WORK / dst_name
        shutil.copyfile(src, dst)
        return self.open(dst)

    def set_app_var(self, name, value):
        """앱 수준 시스템 변수(FILEDIA 등)를 바꾸고 원래 값을 기록한다(시험 도면에서만)"""
        self.guard()
        if name not in self.app_vars:
            self.app_vars[name] = self.ctl._call_attr(self.ctl.doc, "GetVariable", name)
        self.ctl._call_attr(self.ctl.doc, "SetVariable", name, value)

    def _restore_app_vars(self):
        if not self.app_vars:
            return
        doc = self.ctl._call(getattr, self.ctl.app, "ActiveDocument")
        for name, value in self.app_vars.items():
            self.ctl._call_attr(doc, "SetVariable", name, value)

    # ---------- 명령·LISP ----------

    def cmd(self, command: str, timeout: float = None):
        self.guard()
        if not command.endswith(("\n", "\r", " ")):
            command += "\n"
        return self.ctl._send_and_wait(command, timeout)

    def lisp(self, expression: str, timeout: float = None):
        self.guard()
        r = self.ctl.run_lisp(expression, timeout)
        if not r["ok"]:
            raise RuntimeError(f"LISP 실패: {r['error']} :: {expression[:120]}")
        return r["value"]

    # ---------- 기록 ----------

    def record(self, key, value):
        self.data["steps"][key] = value
        print(f"[{self.name}] {key}: {json.dumps(value, ensure_ascii=False, default=str)[:400]}")

    def timed(self, label, fn, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            ms = round((time.perf_counter() - t0) * 1000)
            self.data.setdefault("timings_ms", {})[label] = ms
