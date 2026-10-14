"""E4 삽입·분해: 생성 DXF를 열린 도면에 넣는 방법 비교

(가) -INSERT로 DXF 직접 (나) DWG로 바꿔 InsertBlock → Explode (다) Document.Import
(라) DXF를 별도 문서로 열어 CopyObjects로 대상 모형 공간에 복사
판정: 개수·종류, 레이어·색·선종류, 한글 문자, 블록 속성, 좌표(0.01mm), XDATA 유지, 남는 블록 정의, 시간, 해치 표시
"""
import json
import math
import time

import pythoncom
import win32com.client

from _harness import WORK, Spike
from _inspect import dxf_summary, live_summary

GEN = WORK / "gen.dxf"
GEN100 = WORK / "gen100.dxf"


def handle_set(ctl, doc):
    ms = ctl._call(getattr, doc, "ModelSpace")
    return {str(ctl._safe_attr(ctl._call_attr(ms, "Item", i), "Handle")) for i in range(ctl._call(getattr, ms, "Count"))}


def block_names(ctl, doc):
    blocks = ctl._call(getattr, doc, "Blocks")
    return {str(ctl._safe_attr(ctl._call_attr(blocks, "Item", i), "Name")) for i in range(ctl._call(getattr, blocks, "Count"))}


def oid_of(rec):
    for v in rec.get("xdata") or []:
        if isinstance(v, str) and v.startswith("oid="):
            return v[4:]
    return None


def close_pt(a, b, tol=0.01):
    if a is None or b is None:
        return a == b
    return len(a) == len(b) and all(math.isclose(x, y, abs_tol=tol) for x, y in zip(a, b))


def compare(gen, new_entities):
    want = {oid_of(r): r for r in gen["entities"].values()}
    got = {}
    untagged = []
    for h, r in new_entities.items():
        o = oid_of(r)
        if o:
            got[o] = r
        else:
            untagged.append((h, r["type"], r["layer"]))
    rows = {}
    for o, w in want.items():
        g = got.get(o)
        if g is None:
            rows[o] = {"type": w["type"], "missing_or_untagged": True}
            continue
        row = {"type": w["type"]}
        for k in ("type", "layer", "linetype"):
            if str(w.get(k)).upper() != str(g.get(k)).upper():
                row[k] = [w.get(k), g.get(k)]
        if (w.get("color") or 256) != (g.get("color") or 256):
            row["color"] = [w.get("color"), g.get("color")]
        if w.get("text") != g.get("text") and "text" in w:
            row["text"] = [w.get("text"), g.get("text")]
        if w.get("attribs") != g.get("attribs") and "attribs" in w:
            row["attribs"] = [w.get("attribs"), g.get("attribs")]
        if "pt" in w and not close_pt(w["pt"][:len(g.get("pt") or [])], g.get("pt")):
            row["pt"] = [w.get("pt"), g.get("pt")]
        rows[o] = row
    problems = {o: r for o, r in rows.items() if len(r) > 1}
    return {"expected": len(want), "tagged_found": len(got), "untagged_new": untagged, "problems": problems}


def run_method(s, label, target_name, apply, gen_path):
    ctl = s.ctl
    doc = s.copy_and_open(WORK / "synthetic_base.dwg", target_name) if target_name else s.ctl.doc
    before_h = handle_set(ctl, doc)
    before_b = block_names(ctl, doc)
    t0 = time.perf_counter()
    err = None
    try:
        apply(s, doc, gen_path)
    except Exception as e:  # noqa: BLE001
        err = repr(e)
    ms = round((time.perf_counter() - t0) * 1000)
    ctl.switch_drawing(ctl._safe_attr(doc, "FullName"))
    ctl.doc = doc
    after = live_summary(ctl, doc)
    new = {h: r for h, r in after["entities"].items() if h not in before_h}
    removed = sorted(before_h - set(after["entities"]))
    res = {"ms": ms, "error": err, "new_count": len(new), "removed_existing": removed,
           "new_blocks": sorted(block_names(ctl, doc) - before_b),
           "compare": compare(dxf_summary(gen_path), new) if not err or new else None,
           "cmdactive": ctl._read_cmdactive(retry=False)}
    s.record(label, res)
    return doc, res


def m_insert_dxf(s, doc, gen_path):
    r = s.cmd(f"_.-INSERT\n{gen_path}\n0,0\n1\n1\n0\n", timeout=30)
    s.record("insert_dxf_cmd", r)
    if r.get("cmdactive") or r.get("pending"):
        s.ctl.cancel_command()
        raise RuntimeError(f"-INSERT가 끝나지 않음: {r}")
    r2 = s.cmd("_.EXPLODE\n_L\n\n", timeout=30)
    s.record("insert_dxf_explode", r2)


def m_dwg_insert_explode(s, doc, gen_path):
    ctl = s.ctl
    dwg = WORK / (gen_path.stem + "_conv.dwg")
    if dwg.exists():
        dwg.unlink()
    src = s.open(gen_path)
    ctl._call_attr(src, "SaveAs", str(dwg), 60)
    ctl._call_attr(src, "Close", False)
    ctl.switch_drawing(ctl._safe_attr(doc, "FullName"))
    ctl.doc = doc
    ms = ctl._call(getattr, doc, "ModelSpace")
    ref = ctl._call_attr(ms, "InsertBlock", ctl._pt((0, 0, 0)), str(dwg), 1.0, 1.0, 1.0, 0.0)
    parts = ctl._call_attr(ref, "Explode")
    s.record(f"explode_parts_{gen_path.stem}", len(parts) if parts else 0)
    ctl._call_attr(ref, "Delete")
    try:
        blk = ctl._call_attr(ctl._call(getattr, doc, "Blocks"), "Item", dwg.stem)
        ctl._call_attr(blk, "Delete")
    except Exception as e:  # noqa: BLE001
        s.record(f"block_def_delete_{gen_path.stem}", repr(e))


def m_import(s, doc, gen_path):
    s.ctl._call_attr(doc, "Import", str(gen_path), s.ctl._pt((0, 0, 0)), 1.0)


def m_copyobjects(s, doc, gen_path):
    ctl = s.ctl
    target_ms = ctl._call(getattr, doc, "ModelSpace")
    src = s.open(gen_path)
    ms = ctl._call(getattr, src, "ModelSpace")
    objs = [ctl._call_attr(ms, "Item", i) for i in range(ctl._call(getattr, ms, "Count"))]
    arr = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, objs)
    copied = ctl._call_attr(src, "CopyObjects", arr, target_ms)
    s.record(f"copyobjects_returned_{gen_path.stem}", len(copied) if copied else 0)
    ctl._call_attr(src, "Close", False)


METHODS = [("a_insert_dxf", m_insert_dxf), ("b_dwg_insert_explode", m_dwg_insert_explode),
           ("c_import", m_import), ("d_copyobjects", m_copyobjects)]

with Spike("E4") as s:
    for label, fn in METHODS:
        doc, res = run_method(s, label, f"e4_{label}.dwg", fn, GEN)
        # 같은 도면에 태그 선 100개로 시간 측정
        run_method(s, label + "_100", None, fn, GEN100)
        s.cmd("_.ZOOM\n_W\n19000,-4000\n27000,10000\n")
        (WORK / f"e4_{label}.png").write_bytes(s.ctl.screenshot())
        s.ctl._call_attr(doc, "Save")
    print(json.dumps({k: {x: v.get(x) for x in ("ms", "error", "new_count", "new_blocks")}
                      for k, v in s.data["steps"].items() if isinstance(v, dict) and "ms" in v},
                     ensure_ascii=False, indent=1))
