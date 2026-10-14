"""회사 템플릿의 출력 설정(모든 레이아웃)을 회사 CTB·정식 출력기·A3로 맞춘다

2026-10-15 사용자 결정: CTB HEO(2020)_A3.ctb, 출력기 DWG To PDF.pc3(E9 뒤 정식 출력기), 용지 A3.
템플릿에는 이 PC에 없는 출력기("AutoCAD PDF (General Documentation).pc3", 옛 DWF ePlot)와 JUNGLIM-d.ctb가 들어 있었다.
출력 범위·축척은 레이아웃마다 그대로 둔다(plot 툴은 출력할 때 범위·축척을 따로 정한다).

순서(GstarCAD 실행 중이어야 함):
  1. 원본을 <원본>.bak으로 백업하고, 작업 폴더(기본 C:\\arcaid\\work\\t7)의 사본에서 고친다
  2. 고친 사본을 Export한 DXF에서 모든 레이아웃의 출력기·CTB·용지를 확인한다
  3. 통과하면 고친 사본을 원본 경로로 복사한다. 사용자가 연 도면은 건드리지 않고 끝나면 다시 활성화한다
  4. 이어서 export_template_dxf.py로 template.dxf와 해시를 다시 만든다
사용법: python set_plot_setup.py [템플릿 경로] [--work 작업 폴더]
"""
import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_TPL = REPO / "skills" / "cad-drawing-standard" / "assets" / "회사표준_템플릿.dwg"
DEVICE = "DWG To PDF.pc3"
CTB = "HEO(2020)_A3.ctb"
MEDIA = "A3"

parser = argparse.ArgumentParser(description="템플릿 출력 설정을 회사 CTB·DWG To PDF·A3로 맞춘다")
parser.add_argument("template", nargs="?", default=str(DEFAULT_TPL), help="템플릿 경로(기본: 스킬 템플릿)")
parser.add_argument("--work", default=r"C:\arcaid\work\t7", help="작업 폴더(한글·공백 없는 경로 권장)")
args = parser.parse_args()

import ezdxf  # noqa: E402
from _cadutil import close_work_docs, connect, restore_user  # noqa: E402

TPL = Path(args.template).resolve()
WORK = Path(args.work)
WORK.mkdir(parents=True, exist_ok=True)
EDIT = WORK / "template_plot.dwg"
CHECK = WORK / "template_plot_check"


def pick_media(ctl, layout):
    names = list(ctl._call_attr(layout, "GetCanonicalMediaNames") or [])
    if MEDIA in names:
        return MEDIA
    return next((m for m in names if MEDIA in m.upper() and "FULL_BLEED" in m.upper()),
                next((m for m in names if MEDIA in m.upper()), None))


def main():
    ctl, user = connect()
    close_work_docs(ctl, WORK)
    shutil.copy2(TPL, str(TPL) + ".bak")  # *.bak은 git이 무시한다
    if EDIT.exists():
        EDIT.unlink()
    shutil.copyfile(TPL, EDIT)
    try:
        if not ctl.open_drawing(str(EDIT)):
            raise RuntimeError(f"열기 실패: {EDIT}")
        doc = ctl.doc
        layouts = ctl._call(getattr, doc, "Layouts")
        for i in range(ctl._call(getattr, layouts, "Count")):
            layout = ctl._call_attr(layouts, "Item", i)
            name = ctl._safe_attr(layout, "Name")
            ctl._call(setattr, layout, "ConfigName", DEVICE)
            ctl._call_attr(layout, "RefreshPlotDeviceInfo")
            ctl._call(setattr, layout, "StyleSheet", CTB)
            ctl._call(setattr, layout, "PlotWithPlotStyles", True)
            media = pick_media(ctl, layout)
            if media is None:
                raise RuntimeError(f"{DEVICE}에 {MEDIA} 용지가 없음")
            ctl._call(setattr, layout, "CanonicalMediaName", media)
            ctl._call(setattr, layout, "PaperUnits", 1)
            print(f"{name}: {DEVICE} / {CTB} / {media}")
        ctl._call_attr(doc, "Save")
        r = ctl.export_dxf(str(CHECK))
        if not r["ok"]:
            raise RuntimeError(f"확인용 Export 실패: {r['error']}")
        ctl._call_attr(doc, "Close", False)
        d = ezdxf.readfile(r["path"])
        problems = []
        for layout in d.layouts:
            p = layout.dxf_layout.dxf
            got = (p.get("plot_configuration_file"), p.get("current_style_sheet"), p.get("paper_size"))
            print(f"확인 {layout.name}: {got}")
            if got[0] != DEVICE or str(got[1]).casefold() != CTB.casefold() or MEDIA not in str(got[2]).upper():
                problems.append(f"{layout.name}: {got}")
        if problems:
            raise RuntimeError("출력 설정이 맞지 않음: " + "; ".join(problems))
        shutil.copyfile(EDIT, TPL)
        print(f"템플릿 갱신: {TPL}")
    finally:
        close_work_docs(ctl, WORK)
        restore_user(ctl, user)


if __name__ == "__main__":
    main()
