"""E9 보조: 빈 PDF 원인 찾기 — 출력 범위 방식(범위 전체·화면·창)별 COM 출력"""
import pythoncom
import win32com.client

from _harness import WORK, Spike

PC3, CTB = "DWG To PDF.pc3", "HEO(2020)_A3.ctb"


def pt2(x, y):
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y])


with Spike("E9b") as s:
    ctl = s.ctl
    doc = s.open(WORK / "synthetic_base.dwg")
    s.set_app_var("BACKGROUNDPLOT", 0)
    s.cmd("_.ZOOM\n_W\n-3000,-4000\n15000,9000\n")
    for label, ptype, fit in (("window_type_first", 41, False), ("display_1to100", 0, False)):
        layout = ctl._call(getattr, doc, "ActiveLayout")
        ctl._call(setattr, layout, "ConfigName", PC3)
        ctl._call_attr(layout, "RefreshPlotDeviceInfo")
        ctl._call(setattr, layout, "CanonicalMediaName", "A3")
        ctl._call(setattr, layout, "StyleSheet", CTB)
        ctl._call(setattr, layout, "PlotWithPlotStyles", True)
        ctl._call(setattr, layout, "PaperUnits", 1)
        if ptype == 41:  # 창: 유형을 먼저 정하고 범위를 준다
            ctl._call(setattr, layout, "PlotType", 4)
            ctl._call_attr(layout, "SetWindowToPlot", pt2(-3000.0, -4000.0), pt2(15000.0, 9000.0))
        else:
            ctl._call(setattr, layout, "PlotType", ptype)
        if fit:
            ctl._call(setattr, layout, "UseStandardScale", True)
            ctl._call(setattr, layout, "StandardScale", 0)  # acScaleToFit
        else:
            ctl._call(setattr, layout, "UseStandardScale", False)
            ctl._call_attr(layout, "SetCustomScale", 1.0, 100.0)
        ctl._call(setattr, layout, "CenterPlot", True)
        out = WORK / f"e9b_{label}.pdf"
        if out.exists():
            out.unlink()
        ok = ctl._call_attr(ctl._call(getattr, doc, "Plot"), "PlotToFile", str(out), PC3)
        s.record(label, {"ok": ok, "bytes": out.stat().st_size if out.exists() else 0,
                         "plottype_read": ctl._safe_attr(layout, "PlotType")})
