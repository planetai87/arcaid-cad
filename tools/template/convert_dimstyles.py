"""회사 템플릿의 치수 스타일 DIM330·DIM270을 비주석으로 바꾸고 스타일 DIMSCALE을 50(A3 1/100)으로 맞춘다.

구조 결정 D3(주석 축척 미사용)과 T0 E1 결과를 따른다. 주석 스타일이면 1/100(주석 축척 1:100)에서
치수 문자가 표준의 2배(330)가 된다. LISP entmod로 치수 스타일의 AcadAnnotative XDATA를 지우면 비주석이 된다.

순서(GstarCAD 실행 중이어야 함):
  1. 원본을 <원본>.bak으로 백업하고, 작업 폴더(기본 C:\\arcaid\\work\\t2)의 사본에서 고친다
  2. 고친 사본을 R2013 DXF로 내보내 두 스타일의 주석 XDATA 없음·DIMSCALE 50을 확인한다
  3. 고친 사본의 또 다른 사본에 -DIMSTYLE로 DIM330만 고르고(DIMSCALE 덮어쓰기 없이) 6000 치수를 넣어
     치수 문자 높이가 165(3.3 x 50)인지 확인한다
  4. 모두 통과하면 고친 사본을 원본 경로로 복사한다. 사용자가 연 도면은 건드리지 않고 끝나면 다시 활성화한다
사용법: python convert_dimstyles.py [템플릿 경로] [--work 작업 폴더]
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_TPL = REPO / "skills" / "cad-drawing-standard" / "assets" / "회사표준_템플릿.dwg"
STYLES = ("DIM330", "DIM270")
DIMSCALE = 50.0

parser = argparse.ArgumentParser(description="템플릿 DIM330·DIM270을 비주석(DIMSCALE 50)으로 바꾼다")
parser.add_argument("template", nargs="?", default=str(DEFAULT_TPL), help="템플릿 경로(기본: 스킬 템플릿)")
parser.add_argument("--work", default=r"C:\arcaid\work\t2", help="작업 폴더(한글·공백 없는 경로 권장)")
args = parser.parse_args()

sys.path.insert(0, str(REPO / "src"))
import ezdxf  # noqa: E402
from cad_controller import CADController  # noqa: E402 (--help는 COM 없이 끝나게 인자를 먼저 읽는다)

TPL = Path(args.template).resolve()
WORK = Path(args.work)
WORK.mkdir(parents=True, exist_ok=True)
EDIT = WORK / "template_edit.dwg"
CHECK_DXF = WORK / "template_edit_check.dxf"
DIM_TEST = WORK / "template_dimtest.dwg"
DIM_TEST_DXF = WORK / "template_dimtest.dxf"


def ensure_active(ctl, doc, timeout=10.0):
    """SendCommand·LISP는 활성 문서로 가므로, 보내기 전에 대상 문서가 활성인지 확인한다

    문서를 닫거나 연 직후에는 활성 문서가 바로 바뀌지 않아 명령이 사라진 적이 있다(결과 파일 없음).
    """
    import time
    name = ctl._safe_attr(doc, "Name")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = ctl._safe_attr(ctl._safe_attr(ctl.app, "ActiveDocument"), "Name")
        if active == name and ctl._read_cmdactive() == 0:
            return
        try:
            ctl._call_attr(doc, "Activate")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise RuntimeError(f"대상 문서가 활성화되지 않음: {name}")


def lisp(ctl, expr):
    ensure_active(ctl, ctl.doc)
    r = ctl.run_lisp(expr)
    if not r["ok"]:
        raise RuntimeError(f"LISP 실패: {r['error']} :: {expr[:100]}")
    return r["value"]


def convert_style(name):
    return (f'(progn (setq e (entget (tblobjname "DIMSTYLE" "{name}"))) '
            f'(setq e (subst (cons 40 {DIMSCALE}) (assoc 40 e) e)) '
            f'(entmod (append e \'((-3 ("AcadAnnotative"))))) '
            f'(list (cdr (assoc 40 (entget (tblobjname "DIMSTYLE" "{name}")))) '
            f'(assoc -3 (entget (tblobjname "DIMSTYLE" "{name}") \'("AcadAnnotative")))))')


def close_doc(ctl, doc):
    ctl._call_attr(doc, "Close", False)


def close_work_docs(ctl):
    """작업 폴더에서 연 문서가 남아 있으면 저장하지 않고 닫는다(실패했을 때 대비)"""
    work = os.path.normcase(str(WORK.resolve())) + os.sep
    docs = ctl._call(getattr, ctl.app, "Documents")
    for i in range(ctl._call(getattr, docs, "Count") - 1, -1, -1):
        doc = ctl._call_attr(docs, "Item", i)
        full = os.path.normcase(str(ctl._safe_attr(doc, "FullName") or ""))
        if full.startswith(work):
            ctl._call_attr(doc, "Close", False)
            print("작업 사본 닫음:", full)


def main():
    ctl = CADController()
    if not ctl.start_cad():
        raise SystemExit("CAD에 연결하지 못함")
    user = next((d for d in ctl.list_drawings() if d["is_active"]), None)
    close_work_docs(ctl)  # 앞선 실행이 남긴 작업 사본(파일 잠금) 정리

    shutil.copy2(TPL, str(TPL) + ".bak")  # *.bak은 git이 무시한다
    for p in (EDIT, CHECK_DXF, DIM_TEST, DIM_TEST_DXF):
        if p.exists():
            p.unlink()
    shutil.copyfile(TPL, EDIT)
    try:
        # 1) 사본에서 두 스타일 전환 + 헤더 DIMSCALE
        if not ctl.open_drawing(str(EDIT)):
            raise RuntimeError(f"열기 실패: {EDIT}")
        doc = ctl.doc
        for name in STYLES:
            value = lisp(ctl, convert_style(name))
            print(f"{name}: {value}")
            # GstarCAD의 prin1은 50.0을 50으로 쓴다
            if not re.fullmatch(r"\(50(\.0+)? nil\)", value.strip()):
                raise RuntimeError(f"{name} 전환 결과가 예상과 다름: {value}")
        ctl._call_attr(doc, "SetVariable", "DIMSCALE", DIMSCALE)
        ctl._call_attr(doc, "Save")
        # 2) DXF로 내보내 확인(이 문서는 버릴 사본이라 SaveAs로 이름이 바뀌어도 된다)
        shutil.copyfile(EDIT, DIM_TEST)
        ctl._call_attr(doc, "SaveAs", str(CHECK_DXF), 61)
        close_doc(ctl, doc)
        d = ezdxf.readfile(str(CHECK_DXF))
        for name in STYLES:
            ds = d.dimstyles.get(name)
            if ds.has_xdata("AcadAnnotative") or abs(ds.dxf.dimscale - DIMSCALE) > 1e-9:
                raise RuntimeError(f"{name} 확인 실패: 주석={ds.has_xdata('AcadAnnotative')}, DIMSCALE={ds.dxf.dimscale}")
        print("DXF 확인: 두 스타일 비주석, DIMSCALE", DIMSCALE, "| 헤더 DIMSCALE", d.header.get("$DIMSCALE"))
        # 3) DIMSCALE 덮어쓰기 없이 DIM330으로 치수를 넣어 문자 높이 확인
        if not ctl.open_drawing(str(DIM_TEST)):
            raise RuntimeError(f"열기 실패: {DIM_TEST}")
        doc = ctl.doc
        ensure_active(ctl, doc)
        ctl._send_and_wait("_.-DIMSTYLE\n_R\nDIM330\n")
        lisp(ctl, '(progn (setvar "OSMODE" 0) (setvar "CLAYER" "DIM") '
                  '(command "_.DIMLINEAR" (list 0 0) (list 6000 0) "_H" (list 3000 900)) T)')
        ctl._call_attr(doc, "SaveAs", str(DIM_TEST_DXF), 61)
        close_doc(ctl, doc)
        d = ezdxf.readfile(str(DIM_TEST_DXF))
        dims = list(d.modelspace().query("DIMENSION"))
        heights = [m.dxf.char_height for dim in dims for m in dim.get_geometry_block().query("MTEXT")]
        print("치수 문자 높이:", heights, "| 주석 치수:", [dim.has_xdata("AcadAnnotative") for dim in dims])
        if not heights or abs(heights[0] - 165.0) > 1e-6 or any(dim.has_xdata("AcadAnnotative") for dim in dims):
            raise RuntimeError("1/100 치수 문자 높이가 165가 아님")
        # 4) 원본 교체
        shutil.copyfile(EDIT, TPL)
        print("원본 교체:", TPL)
    finally:
        close_work_docs(ctl)
        if user:
            ctl.switch_drawing(user["full_name"] or user["name"])
            print("사용자 도면 재활성화:", user["name"])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
