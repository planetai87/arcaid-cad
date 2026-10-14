"""T6 사전 확인(제품 코드 아님): 보호된 적용 경로가 기대는 GstarCAD 동작

P1 객체에 COM SetXData → Export 사본에서 그대로 읽히는가
P2 묶음 안에서 Import → Export → SetXData ×N → 묶음 닫기 → UNDO 1이 한 번에 되돌리는가
P3 블록 정의를 뺀 생성 DXF(skip_blocks)를 그 블록이 있는 도면에 Import하면 참조·속성이 들어가는가
P4 묶음 안 COM 속성 쓰기(Layer, color 256(트루컬러 포함), Linetype ByLayer, Lineweight -1) → UNDO 1 복귀
P5 시간: 107개·827개 Import, SetXData
"""
import copy
import json
import time

import ezdxf
import pythoncom
import win32com.client

from _harness import REPO, WORK, Spike
from engine.standards import load_standard
from engine.write import build_dxf

BASE = WORK / "synthetic_base.dwg"
OUT = WORK / "t6"
SPEC = json.loads((REPO / "tests" / "engine" / "specs" / "dangsan_typical.json").read_text(encoding="utf-8"))
APP = "ARCAID"


def snap(s, name):
    r = s.ctl.export_dxf(str(OUT / name))
    if not r["ok"]:
        raise RuntimeError(f"Export 실패: {r['error']}")
    return ezdxf.readfile(r["path"])


def handles(doc):
    return {e.dxf.handle for e in doc.modelspace()}


def tags(e):
    return [v for _, v in e.get_xdata(APP)] if e.has_xdata(APP) else None


def set_xdata(ctl, handle, strings):
    obj = ctl._call_attr(ctl.doc, "HandleToObject", handle)
    types = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, [1001] + [1000] * len(strings))
    values = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, [APP] + list(strings))
    ctl._call_attr(obj, "SetXData", types, values)


def undo1(s):
    return s.ctl._send_and_wait("UNDO 1\n")


def props(doc, h):
    e = doc.entitydb[h]
    return {"layer": e.dxf.layer, "color": e.dxf.get("color", 256), "true_color": e.dxf.get("true_color"),
            "linetype": e.dxf.get("linetype", "BYLAYER"), "lineweight": e.dxf.get("lineweight", -1)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    std = load_standard()
    gen = OUT / "gen.dxf"
    assert build_dxf(SPEC, std, gen).status.value == "success"
    gen_skip = OUT / "gen_skip.dxf"
    assert build_dxf(SPEC, std, gen_skip, skip_blocks=["축열버블-상"]).status.value == "success"
    big = copy.deepcopy(SPEC)
    for place in big["items"][2]["place"]:
        place["repeat"]["count"] = 50
    gen_big = OUT / "gen_big.dxf"
    assert build_dxf(big, std, gen_big).status.value == "success"

    with Spike("T6_probe") as s:
        ctl = s.ctl

        # P1 객체마다 SetXData
        s.copy_and_open(BASE, "t6_p1.dwg")
        d0 = snap(s, "p1_s0")
        picked = {}
        for e in d0.modelspace():
            picked.setdefault(e.dxftype(), e.dxf.handle)
        written = {}
        for kind, h in picked.items():
            strings = [f"op=p1", f"oid=p1:{kind}", "path=probe", "ver=3.0.0", "in=0123456789abcdef", "state=fedcba9876543210"]
            try:
                set_xdata(ctl, h, strings)
                written[kind] = strings
            except Exception as e:  # noqa: BLE001
                written[kind] = f"실패: {e}"
        d1 = snap(s, "p1_s1")
        s.record("P1_setxdata", {k: {"wrote": isinstance(v, list), "read_back_same": tags(d1.entitydb[picked[k]]) == v}
                                 for k, v in written.items()})

        # P2 묶음 안 Import + Export + SetXData → UNDO 1
        s.copy_and_open(BASE, "t6_p2.dwg")
        d0 = snap(s, "p2_s0")
        base = handles(d0)
        ctl._call_attr(ctl.doc, "StartUndoMark")
        t0 = time.perf_counter()
        ctl._call_attr(ctl.doc, "Import", str(gen), ctl._pt((0, 0, 0)), 1.0)
        import_ms = round((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        d1 = snap(s, "p2_s1")
        export_ms = round((time.perf_counter() - t0) * 1000)
        new = sorted(handles(d1) - base)
        t0 = time.perf_counter()
        for h in new:
            set_xdata(ctl, h, [v for v in tags(d1.entitydb[h])] + ["state=0000000000000000"])
        xdata_ms = round((time.perf_counter() - t0) * 1000)
        ctl._call_attr(ctl.doc, "EndUndoMark")
        d2 = snap(s, "p2_s2")
        with_state = sum(1 for h in new if any(v.startswith("state=") for v in (tags(d2.entitydb[h]) or [])))
        undo = undo1(s)
        d3 = snap(s, "p2_s3")
        s.record("P2_undo_after_export_and_setxdata", {
            "new": len(new), "with_state": with_state, "undo": undo, "restored": handles(d3) == base,
            "left": len(handles(d3) - base), "lost": len(base - handles(d3)),
            "ms": {"import_107": import_ms, "export": export_ms, "setxdata_107": xdata_ms}})

        # P3 skip_blocks
        s.copy_and_open(BASE, "t6_p3.dwg")
        d0 = snap(s, "p3_s0")
        ctl._call_attr(ctl.doc, "Import", str(gen_skip), ctl._pt((0, 0, 0)), 1.0)
        d1 = snap(s, "p3_s1")
        new = [d1.entitydb[h] for h in handles(d1) - handles(d0)]
        bubbles = [e for e in new if e.dxftype() == "INSERT" and e.dxf.name == "축열버블-상"]
        s.record("P3_skip_blocks", {"new": len(new), "bubbles": len(bubbles),
                                    "attribs": sorted(a.dxf.text for b in bubbles for a in b.attribs)})

        # P4 COM 속성 쓰기 → UNDO 1
        s.copy_and_open(BASE, "t6_p4.dwg")
        s.lisp('(progn (entmake \'((0 . "LINE") (8 . "WAL") (420 . 16711680) (10 0 -9000 0) (11 1000 -9000 0))) '
               '(entmake \'((0 . "LINE") (8 . "WAL") (6 . "HIDDEN") (370 . 50) (62 . 1) (10 0 -9500 0) (11 1000 -9500 0))) T)')
        d0 = snap(s, "p4_s0")
        targets = [e.dxf.handle for e in d0.modelspace() if e.dxftype() == "LINE" and e.dxf.start.y in (-9000, -9500)]
        before = {h: props(d0, h) for h in targets}
        ctl._call_attr(ctl.doc, "StartUndoMark")
        for h in targets:
            obj = ctl._call_attr(ctl.doc, "HandleToObject", h)
            ctl._call(setattr, obj, "Layer", "DIM")
            ctl._write_color(obj, 256)
            ctl._call(setattr, obj, "Linetype", "ByLayer")
            ctl._call(setattr, obj, "Lineweight", -1)
        ctl._call_attr(ctl.doc, "EndUndoMark")
        d1 = snap(s, "p4_s1")
        after = {h: props(d1, h) for h in targets}
        undo = undo1(s)
        d2 = snap(s, "p4_s2")
        s.record("P4_props_undo", {"before": before, "after": after, "undo": undo,
                                   "restored": {h: props(d2, h) == before[h] for h in targets}})

        # P5 큰 묶음 시간
        s.copy_and_open(BASE, "t6_p5.dwg")
        d0 = snap(s, "p5_s0")
        ctl._call_attr(ctl.doc, "StartUndoMark")
        t0 = time.perf_counter()
        ctl._call_attr(ctl.doc, "Import", str(gen_big), ctl._pt((0, 0, 0)), 1.0)
        import_ms = round((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        d1 = snap(s, "p5_s1")
        export_ms = round((time.perf_counter() - t0) * 1000)
        new = sorted(handles(d1) - handles(d0))
        t0 = time.perf_counter()
        for h in new:
            set_xdata(ctl, h, tags(d1.entitydb[h]) + ["state=0000000000000000"])
        xdata_ms = round((time.perf_counter() - t0) * 1000)
        ctl._call_attr(ctl.doc, "EndUndoMark")
        s.record("P5_timing", {"new": len(new), "ms": {"import": import_ms, "export_read": export_ms,
                                                       "setxdata": xdata_ms}})


if __name__ == "__main__":
    main()
