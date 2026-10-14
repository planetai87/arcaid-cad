"""E6 Undo 복구: 묶음 뒤 UNDO 한 번으로 삽입 전체가 사라지는지, 중간 실패·입력 대기 뒤 복귀를 확인할 수 있는지

각 시나리오는 새 사본에서: 표시 선(그 전 작업) → 묶음 안에서 생성 DXF Import → (실패 상황) → UNDO 1
판정: UNDO 뒤 핸들 집합이 묶음 직전과 같고, 표시 선은 남아 있는가
"""
from _harness import WORK, Spike

GEN = WORK / "gen.dxf"


def handles(ctl):
    ms = ctl._call(getattr, ctl.doc, "ModelSpace")
    return {str(ctl._safe_attr(ctl._call_attr(ms, "Item", i), "Handle")) for i in range(ctl._call(getattr, ms, "Count"))}


def do_import(s):
    s.ctl._call_attr(s.ctl.doc, "Import", str(GEN), s.ctl._pt((0, 0, 0)), 1.0)


def scenario(s, label, body):
    ctl = s.ctl
    s.copy_and_open(WORK / "synthetic_base.dwg", f"e6_{label}.dwg")
    marker = ctl.draw_line((-5000, -5000), (-4000, -5000), layer="TXT")
    marker_h = str(ctl._safe_attr(marker, "Handle"))
    base = handles(ctl)
    info = {}
    try:
        body(s, info)
    except Exception as e:  # noqa: BLE001
        info["raised"] = repr(e)
    applied = handles(ctl)
    info["added_by_apply"] = len(applied - base)
    s.guard()
    info["undo"] = ctl._send_and_wait("UNDO 1\n")
    after = handles(ctl)
    info["restored"] = after == base
    info["marker_kept"] = marker_h in after
    info["left_over"] = len(after - base)
    info["lost_existing"] = len(base - after)
    s.record(label, info)


def a_com_mark(s, info):
    ctl = s.ctl
    ctl._call_attr(ctl.doc, "StartUndoMark")
    do_import(s)
    ctl._call_attr(ctl.doc, "EndUndoMark")


def b_cmd_mark(s, info):
    # 명령행 UNDO 묶음 안에서 COM Import
    info["begin"] = s.cmd("_.UNDO\n_BE\n")
    do_import(s)
    info["end"] = s.cmd("_.UNDO\n_E\n")


def b2_cmd_only(s, info):
    # 묶음·삽입·분해를 한 명령 문자열로(BricsCAD 방식)
    info["cmd"] = s.cmd(f"_.UNDO\n_BE\n_.-INSERT\n{GEN}\n0,0\n1\n1\n0\n_.EXPLODE\n_L\n\n_.UNDO\n_E\n", timeout=30)


def c_fail_midway(s, info):
    ctl = s.ctl
    ctl._call_attr(ctl.doc, "StartUndoMark")
    try:
        do_import(s)
        ctl.draw_line((30000, 0), (31000, 0), layer="TXT")
        raise RuntimeError("일부러 낸 실패")
    finally:
        ctl._call_attr(ctl.doc, "EndUndoMark")


def d_pending_cancel(s, info):
    ctl = s.ctl
    ctl._call_attr(ctl.doc, "StartUndoMark")
    try:
        do_import(s)
        info["pending"] = s.cmd("_.LINE\n0,0\n")
        info["cancel"] = ctl.cancel_command()
    finally:
        ctl._call_attr(ctl.doc, "EndUndoMark")


if __name__ == "__main__":
    with Spike("E6") as s:
        for label, body in (("A_com_mark", a_com_mark), ("B_cmd_mark_com_import", b_cmd_mark),
                            ("B2_cmd_only", b2_cmd_only), ("C_fail_midway", c_fail_midway),
                            ("D_pending_cancel", d_pending_cancel)):
            scenario(s, label, body)
