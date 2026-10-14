"""E7 XDATA 조회: 태그 객체 1000개(op 두 개)를 넣고 앱 이름·op로 찾는 정확도와 속도

- LISP: (ssget "_X" '((-3 ("ARCAID")))) 개수, op 값으로 거르는 반복
- 스냅샷 DXF(ezdxf)에서 같은 일
덤: E4에서 느렸던 원인 확인 — 템플릿 전체를 담은 DXF와 최소 DXF의 Import 시간 비교
"""
import time

import ezdxf

from _harness import WORK, Spike
from gen_dxf import build

N = 500


def minimal_dxf(name, op, n, x0):
    """템플릿 없이 레이어 하나만 정의한 최소 R2013 DXF"""
    d = ezdxf.new("R2013")
    d.header["$INSUNITS"] = 4
    d.layers.add("TXT1", color=7)
    d.appids.add("ARCAID")
    msp = d.modelspace()
    for i in range(n):
        y = -20000 - (i // 50) * 200
        x = x0 + (i % 50) * 120
        msp.add_line((x, y), (x + 100, y), dxfattribs={"layer": "TXT1"}).set_xdata(
            "ARCAID", [(1000, f"op={op}"), (1000, f"oid={op}-{i:04d}")])
    d.saveas(str(WORK / name))
    return WORK / name


def timed_import(s, path):
    t0 = time.perf_counter()
    s.ctl._call_attr(s.ctl.doc, "Import", str(path), s.ctl._pt((0, 0, 0)), 1.0)
    return round((time.perf_counter() - t0) * 1000)


with Spike("E7") as s:
    a, _ = build("gen_e7a.dxf", "e7a", N, 20000.0, basic=False)
    b, _ = build("gen_e7b.dxf", "e7b", N, 30000.0, basic=False)
    m = minimal_dxf("gen_e7m.dxf", "e7m", 2 * N, 40000.0)
    s.copy_and_open(WORK / "synthetic_base.dwg", "e7_target.dwg")
    s.record("import_ms", {"template_based_500_a": timed_import(s, a), "template_based_500_b": timed_import(s, b),
                           "minimal_1000": timed_import(s, m)})

    t0 = time.perf_counter()
    total = s.lisp('(progn (setq ss (ssget "_X" \'((-3 ("ARCAID"))))) (if ss (sslength ss) 0))')
    t_all = round((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    n_a = s.lisp('(progn (setq ss (ssget "_X" \'((-3 ("ARCAID")))) n 0 i 0) '
                 '(if ss (repeat (sslength ss) (setq e (ssname ss i) i (1+ i)) '
                 '(if (member \'(1000 . "op=e7a") (cdr (cadr (assoc -3 (entget e \'("ARCAID")))))) '
                 '(setq n (1+ n))))) n)')
    t_op = round((time.perf_counter() - t0) * 1000)
    s.record("lisp", {"all_tagged": total, "ms_all": t_all, "op_e7a": n_a, "ms_op": t_op})

    t0 = time.perf_counter()
    ss = s.ctl._call_attr(s.ctl._call(getattr, s.ctl.doc, "SelectionSets"), "Add", "ARCAID_E7")
    s.ctl._call_attr(s.ctl.doc, "Export", str(WORK / "e7_snap"), "DXF", ss)
    t_exp = round((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    d = ezdxf.readfile(str(WORK / "e7_snap.dxf"))
    t_read = round((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    tagged = [e for e in d.modelspace() if e.has_xdata("ARCAID")]
    by_op = sum(1 for e in tagged if (1000, "op=e7a") in [(c, v) for c, v in e.get_xdata("ARCAID")])
    t_filter = round((time.perf_counter() - t0) * 1000)
    s.record("snapshot", {"all_tagged": len(tagged), "op_e7a": by_op, "ms_export": t_exp, "ms_read": t_read,
                          "ms_filter": t_filter})
