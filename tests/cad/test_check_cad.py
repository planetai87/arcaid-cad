"""표준 검사 CAD 연동 테스트(GstarCAD 실행 중, ARCAID_CAD_TESTS=1)

GstarCAD로 표준대로 그린 도면의 스냅샷에 오탐이 없는지, 심은 위반만 정확히 잡는지 본다.
치수 점은 다른 객체에서 1000 이상 떨어뜨려 객체 스냅이 끌어가지 않게 한다.
"""
from pathlib import Path

from src.engine.check import check_standard
from src.engine.standards import load_standard
from src.snapshot_service import snapshot_active


def _dim330(ctl, y: float, dimscale: float) -> str:
    """DIM 레이어에 DIM330·DIMSCALE로 선형 치수 하나를 넣고 핸들을 돌려준다"""
    from _cadutil import ensure_active, lisp

    doc = ctl.doc
    ensure_active(ctl, doc)
    ctl._call_attr(doc, "SetVariable", "CLAYER", "DIM")
    for cmd in ("_.-DIMSTYLE\n_R\nDIM330\n", f"_.DIMSCALE\n{dimscale:g}\n",
                f"_.DIMLINEAR\n0,{y}\n6000,{y}\n3000,{y - 1500}\n"):
        ctl._send_and_wait(cmd)
    ctl._call_attr(doc, "SetVariable", "CLAYER", "0")
    return lisp(ctl, "(cdr (assoc 5 (entget (entlast))))").strip('"')


def _check(ctl, work: Path):
    meta, _ = snapshot_active(ctl, work)
    return check_standard(Path(meta.dxf_path), load_standard(), k=100, snapshot=meta)


def test_standard_drawing_has_no_false_positives_then_catches_planted(cad, synthetic_dwg, tmp_path):
    ctl, _ = cad
    assert ctl.open_drawing(str(synthetic_dwg))
    _dim330(ctl, -3000, 50)
    r = _check(ctl, tmp_path / "work")
    assert r.violations == [], [(v.code, v.handle, v.current) for v in r.violations]
    assert r.unchecked == [] and r.is_complete()
    assert r.meta.modelspace_count == 13   # 축선 5 + 버블 5 + 실명 2 + 치수 1

    red = ctl.draw_line((0, -12000), (1000, -12000), layer="WAL", color=1)
    red_handle = ctl._safe_attr(red, "Handle")
    dim_handle = _dim330(ctl, -8000, 100)
    r = _check(ctl, tmp_path / "work")
    assert sorted((v.code, v.handle) for v in r.violations) == sorted(
        [("COLOR_NOT_BYLAYER", red_handle), ("DIM_SCALE", dim_handle)])
    assert {(f.property, f.value) for f in r.fixes} == {("color", 256), ("dimscale", 50.0)}
