"""E2 내보내기 + E3 핸들 일치: 열린 도면을 문서 이름을 바꾸지 않고 DXF 사본으로 만드는 방법 비교

후보: ① COM Export ② -WBLOCK(도면 전체) 또는 COM Wblock → 열어서 SaveAs(R2013 DXF) ③ DXFOUT
기준: 원본 문서 이름·경로·미저장 상태와 활성 문서 유지, 출력 버전, 데이터 보존(라이브와 비교), 핸들 일치, 시간, 실패 때 상태
"""
import os
import time

from _harness import WORK, Spike
from _inspect import diff, dxf_summary, live_summary


def doc_state(ctl, doc):
    active = ctl._call(getattr, ctl.app, "ActiveDocument")
    return {"name": ctl._safe_attr(doc, "Name"), "full": ctl._safe_attr(doc, "FullName"),
            "saved": ctl._safe_attr(doc, "Saved"), "active": ctl._safe_attr(active, "Name")}


def remove(path):
    for p in (path, str(path) + ".dxf"):
        if os.path.exists(p):
            os.remove(p)


def evaluate(s, label, out_path, live, before, t0, extra=None):
    ctl = s.ctl
    res = {"ms": round((time.perf_counter() - t0) * 1000), "before": before,
           "after": doc_state(ctl, s.src_doc), "out": str(out_path), "exists": os.path.exists(out_path)}
    res["state_kept"] = (res["after"]["name"], res["after"]["full"], res["after"]["saved"], res["after"]["active"]) == \
                        (before["name"], before["full"], before["saved"], before["active"])
    if res["exists"]:
        try:
            summ = dxf_summary(out_path)
            res["dxfversion"] = summ["dxfversion"]
            res["insunits"] = summ["insunits"]
            res["diff"] = diff(live, summ)
            res["has_unsaved_marker"] = "저장 안 한 표시" in [r.get("text") for r in summ["entities"].values()]
        except Exception as e:  # noqa: BLE001
            res["read_error"] = str(e)
    if extra:
        res.update(extra)
    s.record(label, res)
    return res


def back_to_src(s):
    s.ctl.switch_drawing(s.src_full)
    s.ctl.doc = s.src_doc


with Spike("E2") as s:
    ctl = s.ctl
    s.src_doc = s.copy_and_open(WORK / "synthetic_base.dwg", "e2_src.dwg")
    s.src_full = ctl._safe_attr(s.src_doc, "FullName")
    # 저장하지 않은 수정을 만든다
    ctl.draw_text((500, -2000), "저장 안 한 표시", height=135, layer="TXT")
    live = live_summary(ctl, s.src_doc)
    s.record("live_count", len(live["entities"]))
    s.set_app_var("FILEDIA", 0)

    # ① COM Export(파일 이름에 확장자 없이 / 있게)
    for label, name in (("export_noext", "e2_export_a"), ("export_ext", "e2_export_b.dxf")):
        out = WORK / name
        remove(out)
        before = doc_state(ctl, s.src_doc)
        t0 = time.perf_counter()
        err = None
        try:
            ss = ctl._call_attr(ctl._call(getattr, s.src_doc, "SelectionSets"), "Add", f"ARCAID_{label}")
            ctl._call_attr(s.src_doc, "Export", str(out), "DXF", ss)
            ctl._call_attr(ss, "Delete")
        except Exception as e:  # noqa: BLE001
            err = str(e)
        real = out if str(out).lower().endswith(".dxf") else WORK / (name + ".dxf")
        evaluate(s, label, real, live, before, t0, {"error": err})

    # ②-1 -WBLOCK 도면 전체(*) → 열어서 SaveAs 61 → 닫기
    wb = WORK / "e2_wblock_all.dwg"
    remove(wb)
    before = doc_state(ctl, s.src_doc)
    t0 = time.perf_counter()
    r = s.cmd(f"_.-WBLOCK\n{wb}\n*\n", timeout=30)
    extra = {"cmd": r, "dwg_written": wb.exists()}
    out = WORK / "e2_wblock_all.dxf"
    remove(out)
    if wb.exists():
        try:
            d = s.open(wb)
            ctl._call_attr(d, "SaveAs", str(out), 61)
            ctl._call_attr(d, "Close", False)
        except Exception as e:  # noqa: BLE001
            extra["convert_error"] = str(e)
        back_to_src(s)
    evaluate(s, "wblock_all", out, live, before, t0, extra)

    # ②-2 COM Wblock(모형 공간 전체 선택 세트)
    wb2 = WORK / "e2_wblock_ss.dwg"
    remove(wb2)
    before = doc_state(ctl, s.src_doc)
    t0 = time.perf_counter()
    extra = {}
    try:
        ss = ctl._call_attr(ctl._call(getattr, s.src_doc, "SelectionSets"), "Add", "ARCAID_WB")
        ctl._call_attr(ss, "Select", 5)  # acSelectionSetAll
        extra["selected"] = ctl._safe_attr(ss, "Count")
        ctl._call_attr(s.src_doc, "Wblock", str(wb2), ss)
        ctl._call_attr(ss, "Delete")
    except Exception as e:  # noqa: BLE001
        extra["error"] = str(e)
    out = WORK / "e2_wblock_ss.dxf"
    remove(out)
    if wb2.exists():
        try:
            d = s.open(wb2)
            ctl._call_attr(d, "SaveAs", str(out), 61)
            ctl._call_attr(d, "Close", False)
        except Exception as e:  # noqa: BLE001
            extra["convert_error"] = str(e)
        back_to_src(s)
    evaluate(s, "wblock_ss", out, live, before, t0, extra)

    # ③ DXFOUT: 버전 옵션으로 2013 지정, 안 되면 기본값으로
    for label, seq in (("dxfout_v2013", "_V\n2013\n16\n"), ("dxfout_default", "16\n")):
        out = WORK / f"e2_{label}.dxf"
        remove(out)
        before = doc_state(ctl, s.src_doc)
        t0 = time.perf_counter()
        r = s.cmd(f"_.DXFOUT\n{out}\n{seq}", timeout=30)
        extra = {"cmd": r}
        if r.get("cmdactive", 0) != 0 or r.get("pending"):
            extra["cancel"] = ctl.cancel_command()
        evaluate(s, label, out, live, before, t0, extra)

    # 실패 때 상태: 없는 드라이브로 Export, DXFOUT
    before = doc_state(ctl, s.src_doc)
    t0 = time.perf_counter()
    err = None
    try:
        ss = ctl._call_attr(ctl._call(getattr, s.src_doc, "SelectionSets"), "Add", "ARCAID_FAIL")
        ctl._call_attr(s.src_doc, "Export", r"Q:\no_such_dir\fail", "DXF", ss)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    evaluate(s, "export_fail", r"Q:\no_such_dir\fail.dxf", live, before, t0, {"error": err})
    before = doc_state(ctl, s.src_doc)
    t0 = time.perf_counter()
    r = s.cmd("_.DXFOUT\nQ:\\no_such_dir\\fail.dxf\n_V\n2013\n16\n", timeout=15)
    extra = {"cmd": r}
    if r.get("cmdactive", 0) != 0 or r.get("pending"):
        extra["cancel"] = ctl.cancel_command()
    evaluate(s, "dxfout_fail", r"Q:\no_such_dir\fail.dxf", live, before, t0, extra)
