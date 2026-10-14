"""E5 같은 이름 정의: 대상 도면에 표준과 다른 DIM330·레이어 CEN·블록 축열버블-상이 있을 때

- 생성 DXF(템플릿 정의)를 Import하면 어느 정의가 남는가
- 넣은 치수를 다시 계산시키면 대상의 DIM330(주석)을 따라가는가
- 적용 전에 스냅샷(Export DXF)과 template.dxf를 비교해 차이를 잡을 수 있는가, LISP tblsearch로도 되는가
"""
import ezdxf

from _harness import WORK, Spike


def snapshot(s, name):
    ctl = s.ctl
    ss = ctl._call_attr(ctl._call(getattr, ctl.doc, "SelectionSets"), "Add", f"ARCAID_{name}")
    ctl._call_attr(ctl.doc, "Export", str(WORK / name), "DXF", ss)
    ctl._call_attr(ss, "Delete")
    return WORK / (name + ".dxf")


def defs(path):
    d = ezdxf.readfile(str(path))
    ds = d.dimstyles.get("DIM330")
    lay = d.layers.get("CEN")
    blk = d.blocks.get("축열버블-상")
    sig = sorted((e.dxftype(), e.dxf.layer) for e in blk) if blk else None
    dims = []
    for e in d.modelspace().query("DIMENSION"):
        xd = [v for _, v in e.get_xdata("ARCAID")] if e.has_xdata("ARCAID") else None
        if xd:
            gb = e.get_geometry_block()
            hs = [m.dxf.char_height for m in gb.query("MTEXT")] if gb else []
            dims.append({"oid": xd[1], "annotative": e.has_xdata("AcadAnnotative"), "text_height": hs[:1]})
    return {"DIM330": {"dimtxt": ds.dxf.get("dimtxt"), "dimscale": ds.dxf.get("dimscale"),
                       "annotative": ds.has_xdata("AcadAnnotative")},
            "CEN": {"color": lay.dxf.color, "linetype": lay.dxf.linetype},
            "bubble_block": sig, "generated_dims": dims}


def compare_defs(a, b):
    return {k: (a[k], b[k]) for k in ("DIM330", "CEN", "bubble_block") if a[k] != b[k]}


with Spike("E5") as s:
    ctl = s.ctl
    doc = s.copy_and_open(WORK / "synthetic_base.dwg", "e5_target.dwg")
    # 대상에 다른 정의를 만든다: DIM330 DIMTXT 5, 레이어 CEN 색 3, 버블 블록에 원 하나 추가
    s.lisp('(progn (entmod (subst (cons 140 5.0) (assoc 140 (entget (tblobjname "DIMSTYLE" "DIM330"))) '
           '(entget (tblobjname "DIMSTYLE" "DIM330")))) T)')
    layer = ctl._call_attr(ctl._call(getattr, doc, "Layers"), "Item", "CEN")
    ctl._call(setattr, layer, "Color", 3)
    blk = ctl._call_attr(ctl._call(getattr, doc, "Blocks"), "Item", "축열버블-상")
    ctl._call_attr(blk, "AddCircle", ctl._pt((0, 0, 0)), 50)
    tpl = defs(WORK / "template.dxf")
    s.record("template_defs", tpl)

    # 적용 전 감지: 스냅샷 vs template.dxf, 그리고 LISP tblsearch
    before = defs(snapshot(s, "e5_before"))
    s.record("detect_snapshot_vs_template", compare_defs(tpl, before))
    s.record("detect_tblsearch_DIM330", s.lisp('(cdr (assoc 140 (tblsearch "DIMSTYLE" "DIM330")))'))

    # 생성 DXF를 Import
    ctl._call_attr(doc, "Import", str(WORK / "gen.dxf"), ctl._pt((0, 0, 0)), 1.0)
    after = defs(snapshot(s, "e5_after"))
    s.record("after_import_defs", {k: after[k] for k in ("DIM330", "CEN", "bubble_block")})
    s.record("after_import_target_kept", compare_defs(before, after) == {})
    s.record("generated_dims_after_import", after["generated_dims"])

    # 넣은 치수를 다시 계산시킨다(1mm 옮겼다가 되돌림)
    ms = ctl._call(getattr, doc, "ModelSpace")
    for i in range(ctl._call(getattr, ms, "Count")):
        e = ctl._call_attr(ms, "Item", i)
        if "Dimension" in str(ctl._safe_attr(e, "ObjectName")):
            try:
                _, v = e.GetXData("ARCAID")
            except Exception:  # noqa: BLE001
                v = None
            if v:
                ctl._call_attr(e, "Move", ctl._pt((0, 0)), ctl._pt((1, 0)))
                ctl._call_attr(e, "Move", ctl._pt((1, 0)), ctl._pt((0, 0)))
    regen = defs(snapshot(s, "e5_regen"))
    s.record("generated_dims_after_recompute", regen["generated_dims"])
