"""T7 사전 확인(제품 코드 아님): 새 툴이 기대는 GstarCAD 동작

P1 레이어·선종류 정의만 담은 DXF(객체 없음)를 Import하면 레이어가 생기는가(안 되면 점을 담아 넣고 지우는 방법)
P2 LISP sssetfirst로 고른 객체가 COM PickfirstSelectionSet·ActiveSelectionSet에 보이는가
P3 출력: COM ZoomWindow, PlotToFile 뒤 레이아웃 값 되돌리기, 문서 Saved, PDF 용지 크기
P4 Documents.Add → 시험 폴더에 SaveAs(시험용 빈 도면)
"""
import re
import time

import ezdxf
from ezdxf.addons import Importer

from _harness import WORK, Spike
from engine.standards import load_standard

OUT = WORK / "t7"
LAYERS = ["CEN", "LIM", "WAL"]
LAYOUT_PROPS = ("ConfigName", "StyleSheet", "CanonicalMediaName", "PlotType", "UseStandardScale", "StandardScale",
                "CenterPlot", "PlotWithPlotStyles", "PaperUnits", "PlotRotation")


def layer_dxf(std, path, with_points=False):
    src = ezdxf.readfile(str(std.path(std.profile.template.dxf)))
    doc = ezdxf.new("R2013", setup=False)
    imp = Importer(src, doc)
    imp.import_table("layers", LAYERS)
    imp.finalize()
    doc.header["$INSUNITS"] = 4
    if with_points:
        for i, name in enumerate(LAYERS):
            doc.modelspace().add_point((i * 10, -99999), dxfattribs={"layer": name})
    doc.saveas(str(path))
    return path


def layers(ctl, doc):
    out = {}
    coll = ctl._call(getattr, doc, "Layers")
    for i in range(ctl._call(getattr, coll, "Count")):
        lay = ctl._call_attr(coll, "Item", i)
        out[str(ctl._safe_attr(lay, "Name"))] = (ctl._read_color(lay), str(ctl._safe_attr(lay, "Linetype")),
                                                 bool(ctl._safe_attr(lay, "Plottable")))
    return out


def new_blank(s, name):
    ctl = s.ctl
    docs = ctl._call(getattr, ctl.app, "Documents")
    doc = ctl._call_attr(docs, "Add")
    path = WORK / name
    if path.exists():
        path.unlink()
    ctl._call_attr(doc, "SaveAs", str(path))
    ctl.doc = doc
    s.guard(doc)
    return doc


def media_box(pdf):
    data = pdf.read_bytes()
    m = re.search(rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]", data)
    if not m:
        return None
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return round((x1 - x0) * 25.4 / 72, 1), round((y1 - y0) * 25.4 / 72, 1)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    std = load_standard()
    only_tables = layer_dxf(std, OUT / "layers_only.dxf")
    with_points = layer_dxf(std, OUT / "layers_points.dxf", with_points=True)

    with Spike("T7_probe") as s:
        ctl = s.ctl

        # P4 + P1
        doc = new_blank(s, "t7_blank1.dwg")
        before = layers(ctl, doc)
        s.record("P4_blank", {"saved_path": str(ctl._safe_attr(doc, "FullName")), "layers": sorted(before)})
        ctl._call_attr(doc, "Import", str(only_tables), ctl._pt((0, 0, 0)), 1.0)
        after = layers(ctl, doc)
        s.record("P1_tables_only", {name: after.get(name) for name in LAYERS})
        if not all(name in after for name in LAYERS):
            doc = new_blank(s, "t7_blank2.dwg")
            ms = ctl._call(getattr, doc, "ModelSpace")
            n0 = ctl._call(getattr, ms, "Count")
            ctl._call_attr(doc, "StartUndoMark")
            ctl._call_attr(doc, "Import", str(with_points), ctl._pt((0, 0, 0)), 1.0)
            n1 = ctl._call(getattr, ms, "Count")
            for i in range(n1 - 1, n0 - 1, -1):
                ctl._call_attr(ctl._call_attr(ms, "Item", i), "Delete")
            ctl._call_attr(doc, "EndUndoMark")
            after = layers(ctl, doc)
            s.record("P1b_points_then_delete", {"imported": n1 - n0, "left": ctl._call(getattr, ms, "Count") - n0,
                                                "layers": {name: after.get(name) for name in LAYERS}})

        # P2 고른 객체
        doc = s.copy_and_open(WORK / "synthetic_base.dwg", "t7_p2.dwg")
        picked = s.lisp('(progn (setq ss (ssget "_X" \'((8 . "CEN")))) (sssetfirst nil ss) (sslength ss))')
        result = {"lisp_count": picked}
        for prop in ("PickfirstSelectionSet", "ActiveSelectionSet"):
            try:
                sel = ctl._call(getattr, doc, prop)
                n = ctl._call(getattr, sel, "Count")
                result[prop] = {"count": n, "handles": [str(ctl._safe_attr(ctl._call_attr(sel, "Item", i), "Handle"))
                                                        for i in range(min(n, 5))]}
            except Exception as e:  # noqa: BLE001
                result[prop] = f"실패: {e}"
        s.record("P2_selection", result)
        ctl.cancel_command()

        # P3 출력
        doc = s.copy_and_open(WORK / "synthetic_base.dwg", "t7_p3.dwg")
        saved0 = bool(ctl._safe_attr(doc, "Saved"))
        layout = ctl._call(getattr, doc, "ActiveLayout")
        props0 = {k: ctl._safe_attr(layout, k) for k in LAYOUT_PROPS}
        custom0 = None
        try:
            custom0 = ctl._call_attr(layout, "GetCustomScale")
        except Exception as e:  # noqa: BLE001
            custom0 = f"실패: {e}"
        bg0 = ctl._call_attr(doc, "GetVariable", "BACKGROUNDPLOT")
        zoom = {}
        try:
            ctl._call_attr(ctl.app, "ZoomWindow", ctl._pt((-3000, -4000)), ctl._pt((15000, 9000)))
            zoom["ZoomWindow"] = "ok"
        except Exception as e:  # noqa: BLE001
            zoom["ZoomWindow"] = f"실패: {e}"
        ctl._call_attr(doc, "SetVariable", "BACKGROUNDPLOT", 0)
        pdf = OUT / "t7_plot.pdf"
        if pdf.exists():
            pdf.unlink()
        t0 = time.perf_counter()
        try:
            ctl._call(setattr, layout, "ConfigName", "DWG To PDF.pc3")
            ctl._call_attr(layout, "RefreshPlotDeviceInfo")
            ctl._call(setattr, layout, "StyleSheet", "HEO(2020)_A3.ctb")
            ctl._call(setattr, layout, "PlotWithPlotStyles", True)
            medias = list(ctl._call_attr(layout, "GetCanonicalMediaNames") or [])
            a3 = [m for m in medias if "A3" in m.upper()]
            media = next((m for m in a3 if "FULL_BLEED" in m.upper() and "297" in m), a3[0] if a3 else None)
            ctl._call(setattr, layout, "CanonicalMediaName", media)
            ctl._call(setattr, layout, "PaperUnits", 1)
            ctl._call(setattr, layout, "PlotType", 0)
            ctl._call(setattr, layout, "UseStandardScale", False)
            ctl._call_attr(layout, "SetCustomScale", 1.0, 100.0)
            ctl._call(setattr, layout, "CenterPlot", True)
            plot = ctl._call(getattr, doc, "Plot")
            ok = ctl._call_attr(plot, "PlotToFile", str(pdf), "DWG To PDF.pc3")
        finally:
            restore = {}
            for k, v in props0.items():
                if v is None:
                    continue
                try:
                    ctl._call(setattr, layout, k, v)
                    restore[k] = "ok"
                except Exception as e:  # noqa: BLE001
                    restore[k] = f"실패: {e}"
            if isinstance(custom0, tuple):
                ctl._call_attr(layout, "SetCustomScale", *custom0)
            ctl._call_attr(doc, "SetVariable", "BACKGROUNDPLOT", bg0)
            try:
                ctl._call_attr(ctl.app, "ZoomPrevious")
                zoom["ZoomPrevious"] = "ok"
            except Exception as e:  # noqa: BLE001
                zoom["ZoomPrevious"] = f"실패: {e}"
        props1 = {k: ctl._safe_attr(layout, k) for k in LAYOUT_PROPS}
        s.record("P3_plot", {"returned": ok, "ms": round((time.perf_counter() - t0) * 1000), "media": media,
                             "exists": pdf.exists(), "media_box_mm": media_box(pdf) if pdf.exists() else None,
                             "zoom": zoom, "restore": restore, "props_same": props0 == props1,
                             "props_diff": {k: (props0[k], props1[k]) for k in props0 if props0[k] != props1[k]},
                             "custom_scale0": custom0, "saved_before": saved0,
                             "saved_after": bool(ctl._safe_attr(doc, "Saved")),
                             "bg_restored": ctl._call_attr(doc, "GetVariable", "BACKGROUNDPLOT") == bg0})


def plot_copy():
    """P3b 원본 도면 설정을 건드리지 않게, Export한 DXF 사본을 읽기 전용으로 열어 출력하고 닫는다"""
    OUT.mkdir(parents=True, exist_ok=True)
    with Spike("T7_probe_plot_copy") as s:
        ctl = s.ctl
        doc = s.copy_and_open(WORK / "synthetic_base.dwg", "t7_p3b.dwg")
        ctl.draw_line((0, -2000), (1000, -2000), layer="WAL")      # 저장 안 한 변경
        layout0 = ctl._call(getattr, doc, "ActiveLayout")
        props0 = {k: ctl._safe_attr(layout0, k) for k in LAYOUT_PROPS}
        state0 = ctl.doc_state(doc)
        t0 = time.perf_counter()
        snap = ctl.export_dxf(str(OUT / "p3b_copy"))
        docs = ctl._call(getattr, ctl.app, "Documents")
        copy = ctl._call_attr(docs, "Open", snap["path"], True)
        ms_open = round((time.perf_counter() - t0) * 1000)
        bg0 = ctl._call_attr(copy, "GetVariable", "BACKGROUNDPLOT")
        pdf = OUT / "t7_plot_copy.pdf"
        if pdf.exists():
            pdf.unlink()
        try:
            ctl._call_attr(copy, "Activate")
            ctl._call_attr(ctl.app, "ZoomWindow", ctl._pt((-3000, -4000)), ctl._pt((15000, 9000)))
            ctl._call_attr(copy, "SetVariable", "BACKGROUNDPLOT", 0)
            layout = ctl._call(getattr, copy, "ActiveLayout")
            ctl._call(setattr, layout, "ConfigName", "DWG To PDF.pc3")
            ctl._call_attr(layout, "RefreshPlotDeviceInfo")
            ctl._call(setattr, layout, "StyleSheet", "HEO(2020)_A3.ctb")
            ctl._call(setattr, layout, "PlotWithPlotStyles", True)
            ctl._call(setattr, layout, "CanonicalMediaName", "A3")
            ctl._call(setattr, layout, "PaperUnits", 1)
            ctl._call(setattr, layout, "PlotType", 0)
            ctl._call(setattr, layout, "UseStandardScale", False)
            ctl._call_attr(layout, "SetCustomScale", 1.0, 100.0)
            ctl._call(setattr, layout, "CenterPlot", True)
            ok = ctl._call_attr(ctl._call(getattr, copy, "Plot"), "PlotToFile", str(pdf), "DWG To PDF.pc3")
        finally:
            ctl._call_attr(copy, "SetVariable", "BACKGROUNDPLOT", bg0)
            ctl._call_attr(copy, "Close", False)
            ctl._call_attr(doc, "Activate")
        props1 = {k: ctl._safe_attr(ctl._call(getattr, doc, "ActiveLayout"), k) for k in LAYOUT_PROPS}
        s.record("P3b_plot_copy", {"returned": ok, "ms_total": round((time.perf_counter() - t0) * 1000),
                                   "ms_export_open": ms_open, "exists": pdf.exists(),
                                   "media_box_mm": media_box(pdf) if pdf.exists() else None,
                                   "original_layout_same": props0 == props1,
                                   "original_state_same": ctl.doc_state(doc) == state0,
                                   "copy_closed": all(d["full_name"] != snap["path"] for d in ctl.list_drawings())})


if __name__ == "__main__":
    import sys
    plot_copy() if "plot_copy" in sys.argv else main()
