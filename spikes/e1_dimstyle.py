"""E1 치수 스타일: 템플릿 DIM330·DIM270의 주석 여부, 비주석 전환 방법, 1/100·1/200 치수 문자 크기

결과: WORK/template.dxf(R2013, 이후 실험의 바탕), results/E1.json
"""
import pythoncom
import win32com.client

from _harness import TEMPLATE, WORK, Spike

STYLES = ("DIM330", "DIM270")
ANNO = "AcadAnnotative"


def dimstyle(ctl, doc, name):
    styles = ctl._call(getattr, doc, "DimStyles")
    return ctl._call_attr(styles, "Item", name)


def xdata(ctl, obj, app):
    try:
        types, values = obj.GetXData(app)
    except Exception as e:  # noqa: BLE001
        return f"오류: {e}"
    return list(zip(types or (), values or ()))


def dim_report(path):
    """DXF의 치수마다 스타일, 주석 여부, 실제 문자 높이(치수 블록 안 MTEXT)"""
    import ezdxf
    d = ezdxf.readfile(str(path))
    out = []
    for e in d.modelspace().query("DIMENSION"):
        h = None
        blk = e.get_geometry_block()
        if blk is not None:
            hs = [m.dxf.char_height for m in blk.query("MTEXT")] + [t.dxf.height for t in blk.query("TEXT")]
            h = round(hs[0], 2) if hs else None
        ov = e.get_dim_style().dxf.name if e.dxf.hasattr("dimstyle") else None
        out.append({"handle": e.dxf.handle, "dimstyle": e.dxf.dimstyle, "annotative": e.has_xdata(ANNO),
                    "text_height": h, "has_override": e.has_xdata("ACAD")})
    ds = {}
    for s in d.dimstyles:
        if s.dxf.name in STYLES:
            ds[s.dxf.name] = {"annotative_xdata": s.get_xdata(ANNO) if s.has_xdata(ANNO) else None,
                              "dimscale": s.dxf.get("dimscale"), "dimtxt": s.dxf.get("dimtxt"),
                              "dimasz": s.dxf.get("dimasz"), "dimexo": s.dxf.get("dimexo"),
                              "dimexe": s.dxf.get("dimexe"), "dimdec": s.dxf.get("dimdec"),
                              "dimblk_handle": s.dxf.get("dimblk_handle"),
                              "dimtxsty_handle": s.dxf.get("dimtxsty_handle")}
    return {"dxfversion": d.dxfversion, "dims": out, "dimstyles": ds}


def add_dims(s, y0, label):
    """현재 치수 스타일·DIMSCALE로 6000 치수 하나를 넣는다"""
    s.lisp('(progn (setvar "OSMODE" 0) (setvar "CLAYER" "DIM") '
           f'(command "_.DIMLINEAR" (list 0 {y0}) (list 6000 {y0}) "_H" (list 3000 {y0 + 900})) T)')
    s.record(f"dim_{label}", {"y": y0})


def set_xdata(obj, app, pairs):
    types = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, [1001] + [c for c, _ in pairs])
    values = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, [app] + [v for _, v in pairs])
    obj.SetXData(types, values)


with Spike("E1") as s:
    ctl = s.ctl
    # 1) 템플릿 사본의 DIM330·DIM270 주석 여부(COM)
    doc = s.copy_and_open(TEMPLATE, "e1_template.dwg")
    s.record("template_com_xdata", {n: xdata(ctl, dimstyle(ctl, doc, n), ANNO) for n in STYLES})
    s.record("template_sysvars", {v: ctl._call_attr(doc, "GetVariable", v)
                                  for v in ("DIMSTYLE", "DIMSCALE", "CANNOSCALE", "LTSCALE", "INSUNITS", "DIMANNO")})
    # 2) R2013 DXF로 저장(이 사본은 버릴 문서라 SaveAs로 이름이 바뀌어도 된다) → template.dxf
    name_before = ctl._safe_attr(doc, "FullName")
    ctl._call_attr(doc, "SaveAs", str(WORK / "template.dxf"), 61)
    s.record("saveas_dxf_renames_doc", {"before": name_before, "after": ctl._safe_attr(doc, "FullName")})
    ctl._call_attr(doc, "Close", False)
    s.record("template_dxf", dim_report(WORK / "template.dxf"))

    # 3) 주석 상태 그대로 1/100(CANNOSCALE 1:100)에서 치수를 넣어 본다
    doc = s.copy_and_open(TEMPLATE, "e1_dims.dwg")
    s.cmd("_.-DIMSTYLE\n_R\nDIM330\n")
    s.cmd("_.DIMSCALE\n50\n")
    add_dims(s, 0, "annotative_k100")

    # 4) 비주석 전환: ① COM SetXData(플래그 0)
    attempts = {}
    for n in STYLES:
        try:
            set_xdata(dimstyle(ctl, doc, n), ANNO,
                      [(1000, "AnnotativeData"), (1002, "{"), (1070, 1), (1070, 0), (1002, "}")])
            attempts[f"{n}_setxdata"] = xdata(ctl, dimstyle(ctl, doc, n), ANNO)
        except Exception as e:  # noqa: BLE001
            attempts[f"{n}_setxdata"] = f"실패: {e}"
    # ② LISP entmod로 xdata 제거(앱 이름만 주면 그 앱의 xdata가 지워진다)
    for n in STYLES:
        try:
            v = s.lisp(f'(progn (entmod (append (entget (tblobjname "DIMSTYLE" "{n}")) '
                       f'\'((-3 ("{ANNO}"))))) (cdr (assoc -3 (entget (tblobjname "DIMSTYLE" "{n}") \'("{ANNO}")))))')
            attempts[f"{n}_entmod_after"] = v
        except Exception as e:  # noqa: BLE001
            attempts[f"{n}_entmod_after"] = f"실패: {e}"
    s.record("conversion_attempts", attempts)

    # 5) 전환 뒤 1/100(DIMSCALE 50)·1/200(DIMSCALE 100) 치수
    s.cmd("_.-DIMSTYLE\n_R\nDIM330\n")
    s.cmd("_.DIMSCALE\n50\n")
    add_dims(s, -3000, "nonanno_k100")
    s.cmd("_.-DIMSTYLE\n_R\nDIM330\n")
    s.cmd("_.DIMSCALE\n100\n")
    add_dims(s, -6000, "nonanno_k200")
    s.record("dimanno_after", ctl._call_attr(doc, "GetVariable", "DIMANNO"))
    ctl._call_attr(doc, "SaveAs", str(WORK / "e1_dims.dxf"), 61)
    ctl._call_attr(doc, "Close", False)
    s.record("dims_dxf", dim_report(WORK / "e1_dims.dxf"))
