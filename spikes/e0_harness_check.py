"""하네스 시험: guard가 사용자 도면을 거부하는지, 입력 대기 명령을 남긴 채 끝나도 정리되는지"""
from _harness import Spike, TEMPLATE

with Spike("E0_harness") as s:
    s.record("user_docs", [(d["name"], d["full_name"], d["saved"], d["is_active"]) for d in s.user_docs])
    # 1) 사용자 활성 도면에 대해 guard가 거부해야 한다
    try:
        s.guard(s.ctl.doc)
        s.record("guard_user_doc", "거부 안 함(실패)")
    except RuntimeError as e:
        s.record("guard_user_doc", f"거부함: {e}")
    # 2) 시험 도면을 열고 입력 대기 명령을 남긴다
    s.copy_and_open(TEMPLATE, "e0_probe.dwg")
    s.record("acadver", s.ctl._call_attr(s.ctl.doc, "GetVariable", "ACADVER"))
    r = s.cmd("_.LINE\n")
    s.record("line_pending", r)
