"""E8 단위·경로

단위: $INSUNITS 4(mm)·6(m)·0(미정) 대상에 생성 DXF(4)를 넣었을 때 6000mm 선의 결과 길이
     (Import, -INSERT DWG 두 방법), 배율 1000 블록 참조가 스냅샷에서 어떻게 보이는지
경로: 한글·공백이 든 폴더에서 COM Open/SaveAs, LISP open 쓰기, -INSERT(따옴표)·InsertBlock, Export, Import
"""
import shutil

import ezdxf

from _harness import WORK, Spike

KO = WORK / "한글 경로"
GEN = WORK / "gen.dxf"
CONV = WORK / "gen_conv.dwg"   # E4 (나)에서 만든 DWG


def cen_line_length(ctl, doc):
    ms = ctl._call(getattr, doc, "ModelSpace")
    for i in range(ctl._call(getattr, ms, "Count")):
        e = ctl._call_attr(ms, "Item", i)
        try:
            _, v = e.GetXData("ARCAID")
        except Exception:  # noqa: BLE001
            continue
        if v and "oid=e4-0001" in v:
            return round(ctl._safe_attr(e, "Length"), 3)
    return None


def try_(s, label, fn):
    try:
        s.record(label, {"ok": True, "value": fn()})
    except Exception as e:  # noqa: BLE001
        s.record(label, {"ok": False, "error": repr(e)})


with Spike("E8") as s:
    ctl = s.ctl
    # ---------- 단위 ----------
    for units in (4, 6, 0):
        for method in ("import", "insert_dwg"):
            doc = s.copy_and_open(WORK / "synthetic_base.dwg", f"e8_u{units}_{method}.dwg")
            ctl._call_attr(doc, "SetVariable", "INSUNITS", units)
            info = {"src": ctl._call_attr(doc, "GetVariable", "INSUNITSDEFSOURCE"),
                    "tgt": ctl._call_attr(doc, "GetVariable", "INSUNITSDEFTARGET")}
            if method == "import":
                ctl._call_attr(doc, "Import", str(GEN), ctl._pt((0, 0, 0)), 1.0)
            else:
                r = s.cmd(f"_.-INSERT\n{CONV}\n0,0\n1\n1\n0\n_.EXPLODE\n_L\n\n", timeout=30)
                info["cmd"] = r
            info["cen_6000_line_length"] = cen_line_length(ctl, doc)
            s.record(f"units_{units}_{method}", info)

    # 배율 1000 블록 참조와 스냅샷
    doc = s.copy_and_open(WORK / "synthetic_base.dwg", "e8_blockscale.dwg")
    ms = ctl._call(getattr, doc, "ModelSpace")
    ctl._call_attr(ms, "InsertBlock", ctl._pt((50000, 0, 0)), "계단화살표", 1000.0, 1000.0, 1000.0, 0.0)
    ss = ctl._call_attr(ctl._call(getattr, doc, "SelectionSets"), "Add", "ARCAID_E8")
    ctl._call_attr(doc, "Export", str(WORK / "e8_blockscale"), "DXF", ss)
    d = ezdxf.readfile(str(WORK / "e8_blockscale.dxf"))
    s.record("blockscale_snapshot", [(e.dxf.name, e.dxf.xscale, e.dxf.yscale) for e in d.modelspace().query("INSERT")
                                     if e.dxf.xscale != 1])

    # ---------- 한글·공백 경로 ----------
    KO.mkdir(exist_ok=True)
    src = KO / "합성 도면.dwg"
    shutil.copyfile(WORK / "synthetic_base.dwg", src)
    try_(s, "path_com_open", lambda: str(s.open(src) and ctl._safe_attr(ctl.doc, "FullName")))
    try_(s, "path_com_saveas", lambda: (ctl._call_attr(ctl.doc, "SaveAs", str(KO / "저장 시험.dwg")),
                                        ctl._safe_attr(ctl.doc, "FullName"))[1])
    lisp_txt = KO / "lisp 쓰기.txt"
    try_(s, "path_lisp_open_write", lambda: (s.lisp(
        f'(progn (setq f (open "{str(lisp_txt).replace(chr(92), "/")}" "w")) (write-line "한글 쓰기" f) (close f) T)'),
        lisp_txt.exists() and lisp_txt.read_bytes().decode("utf-8", "replace").strip())[1])
    ko_conv = KO / "생성 변환.dwg"
    shutil.copyfile(CONV, ko_conv)
    try_(s, "path_insert_cmd_quoted", lambda: s.cmd(f'_.-INSERT\n"{ko_conv}"\n60000,0\n1\n1\n0\n', timeout=30))
    try_(s, "path_insertblock_com", lambda: ctl._safe_attr(ctl._call_attr(
        ctl._call(getattr, ctl.doc, "ModelSpace"), "InsertBlock", ctl._pt((70000, 0, 0)), str(ko_conv),
        1.0, 1.0, 1.0, 0.0), "Name"))
    ko_gen = KO / "생성 객체.dxf"
    shutil.copyfile(GEN, ko_gen)
    try_(s, "path_import", lambda: ctl._call_attr(ctl.doc, "Import", str(ko_gen), ctl._pt((80000, 0, 0)), 1.0) or "ok")

    def export_ko():
        ss2 = ctl._call_attr(ctl._call(getattr, ctl.doc, "SelectionSets"), "Add", "ARCAID_E8KO")
        ctl._call_attr(ctl.doc, "Export", str(KO / "스냅샷 사본"), "DXF", ss2)
        return (KO / "스냅샷 사본.dxf").exists()
    try_(s, "path_export", export_ko)
