"""E6 보조: 겹친 Undo 묶음이 없는 중간 실패(C2), 그리고 UNDO가 복귀하지 못했을 때 태그(op)로 지우는 대안"""
import time

from _harness import WORK, Spike
from e6_undo import GEN, do_import, handles

with Spike("E6b") as s:
    ctl = s.ctl
    # C2: COM 묶음 안에서 Import 뒤 바로 실패(다른 묶음·명령 없음)
    s.copy_and_open(WORK / "synthetic_base.dwg", "e6b_c2.dwg")
    base = handles(ctl)
    ctl._call_attr(ctl.doc, "StartUndoMark")
    try:
        do_import(s)
        raise RuntimeError("일부러 낸 실패")
    except RuntimeError:
        pass
    finally:
        ctl._call_attr(ctl.doc, "EndUndoMark")
    s.guard()
    ctl._send_and_wait("UNDO 1\n")
    s.record("C2_no_nested_mark", {"restored": handles(ctl) == base})

    # D 상황 재현 후 대안: op 태그로 찾아 지우고 남은 것이 없는지 확인
    s.copy_and_open(WORK / "synthetic_base.dwg", "e6b_fallback.dwg")
    base = handles(ctl)
    ctl._call_attr(ctl.doc, "StartUndoMark")
    do_import(s)
    s.cmd("_.LINE\n0,0\n")
    ctl.cancel_command()
    ctl._call_attr(ctl.doc, "EndUndoMark")
    ctl._send_and_wait("UNDO 1\n")
    left = handles(ctl) - base
    t0 = time.perf_counter()
    n = s.lisp('(progn (setq ss (ssget "_X" \'((-3 ("ARCAID")))) n 0 i 0) '
               '(if ss (repeat (sslength ss) (setq e (ssname ss i) i (1+ i)) '
               '(if (member \'(1000 . "op=e4") (cdr (cadr (assoc -3 (entget e \'("ARCAID")))))) '
               '(progn (entdel e) (setq n (1+ n)))))) n)')
    ms = round((time.perf_counter() - t0) * 1000)
    s.record("fallback_tag_delete", {"left_after_undo": len(left), "deleted": n, "ms": ms,
                                     "restored": handles(ctl) == base})
