"""E9 출력: synthetic_base.dwg를 회사 CTB(HEO(2020)_A3.ctb)·DWG To PDF.pc3·A3·1/100으로 PDF 출력

결과: WORK/e9_auto.pdf. 사용자가 같은 도면을 손으로 출력한 WORK/manual.pdf와 비교한다(e9_compare.py).
"""
from _harness import WORK, Spike

PC3 = "DWG To PDF.pc3"
CTB = "HEO(2020)_A3.ctb"
OUT = WORK / "e9_auto.pdf"

with Spike("E9") as s:
    ctl = s.ctl
    doc = s.open(WORK / "synthetic_base.dwg")
    s.set_app_var("BACKGROUNDPLOT", 0)
    layout = ctl._call(getattr, doc, "ActiveLayout")
    ctl._call(setattr, layout, "ConfigName", PC3)
    ctl._call_attr(layout, "RefreshPlotDeviceInfo")
    styles = list(ctl._call_attr(layout, "GetPlotStyleTableNames") or [])
    s.record("ctb_available", CTB in styles)
    ctl._call(setattr, layout, "StyleSheet", CTB)
    ctl._call(setattr, layout, "PlotWithPlotStyles", True)
    medias = list(ctl._call_attr(layout, "GetCanonicalMediaNames") or [])
    a3 = [m for m in medias if "A3" in m.upper()]
    s.record("a3_media", a3)
    media = next((m for m in a3 if "FULL_BLEED" in m.upper() and "297" in m), a3[0] if a3 else None)
    ctl._call(setattr, layout, "CanonicalMediaName", media)
    ctl._call(setattr, layout, "PaperUnits", 1)          # 밀리미터
    # GstarCAD 2021 COM에서 창(PlotType 4)+SetWindowToPlot은 빈 PDF가 나온다(e9b_plot_variants.py).
    # 그래서 화면을 출력 범위로 줌한 뒤 화면(PlotType 0)으로 출력한다
    s.cmd("_.ZOOM\n_W\n-3000,-4000\n15000,9000\n")
    ctl._call(setattr, layout, "PlotType", 0)            # 화면
    ctl._call(setattr, layout, "UseStandardScale", False)
    ctl._call_attr(layout, "SetCustomScale", 1.0, 100.0)  # 종이 1mm = 도면 100mm (A3 1/100)
    ctl._call(setattr, layout, "CenterPlot", True)
    s.record("layout", {k: ctl._safe_attr(layout, k) for k in
                        ("ConfigName", "StyleSheet", "CanonicalMediaName", "PlotType", "PlotRotation",
                         "PlotWithPlotStyles", "PlotWithLineweights", "CenterPlot")})
    if OUT.exists():
        OUT.unlink()
    plot = ctl._call(getattr, doc, "Plot")
    ok = ctl._call_attr(plot, "PlotToFile", str(OUT), PC3)
    s.record("plot", {"returned": ok, "exists": OUT.exists(), "bytes": OUT.stat().st_size if OUT.exists() else 0})
