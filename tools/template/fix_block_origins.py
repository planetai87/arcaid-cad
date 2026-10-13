"""템플릿에서 기준점이 형상과 멀리 떨어진 블록의 기준점을 고친다.

  방위         : 형상 중심
  ARR, ARR2    : 형상 좌하단
  입단면층레벨 : 안에 넣은 '레벨' 기호의 중심(레벨선 왼쪽 끝)
속성이 있는 블록(입단면층레벨)은 기준점(Origin)을 옮기면 GstarCAD -INSERT가 속성 문자를
엉뚱한 곳에 놓는다. 그래서 기준점은 (0,0)으로 두고 블록 안 객체를 옮긴다.
이미 고친 블록은 이동량이 0이라 여러 번 실행해도 결과가 같다.
사용법: python fix_block_origins.py [템플릿 경로]  (기본: 스킬 템플릿)
"""
import argparse
import time
from pathlib import Path

DEFAULT_TPL = Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard" / "assets" / "회사표준_템플릿.dwg"

parser = argparse.ArgumentParser(description="템플릿 블록의 기준점을 형상에 맞게 고친다")
parser.add_argument("template", nargs="?", default=str(DEFAULT_TPL), help="템플릿 경로(기본: 스킬 템플릿)")
TPL = str(Path(parser.parse_args().template).resolve())

import pythoncom  # noqa: E402 (--help는 COM 없이 끝나게 인자를 먼저 읽는다)
import win32com.client  # noqa: E402
from win32com.client import VARIANT  # noqa: E402

TARGETS = {"방위": "center", "ARR": "lowerleft", "ARR2": "lowerleft", "입단면층레벨": "symbol"}

# 블록 <NAME> 안의 첫 중첩 블록 삽입점이 (0,0)에 오도록 선·블록 참조·속성 정의의 10/11 좌표를 옮긴다
SHIFT_TO_SYMBOL = " ".join("""
(progn
 (setq e (entnext (tblobjname "BLOCK" "<NAME>")))
 (while (/= (cdr (assoc 0 (entget e))) "INSERT") (setq e (entnext e)))
 (setq ip (cdr (assoc 10 (entget e))))
 (setq e (entnext (tblobjname "BLOCK" "<NAME>")))
 (while (/= (cdr (assoc 0 (setq d (entget e)))) "ENDBLK")
  (if (member (cdr (assoc 0 d)) '("LINE" "INSERT" "ATTDEF"))
   (progn
    (foreach k '(10 11)
     (if (assoc k d)
      (setq d (subst (list k (- (cadr (assoc k d)) (car ip)) (- (caddr (assoc k d)) (cadr ip)) (cadddr (assoc k d)))
                     (assoc k d) d))))
    (entmod d)))
  (setq e (entnext e)))
 (princ))
""".split())


def pt(x, y):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), 0.0))


app = win32com.client.Dispatch("GCAD.Application")
doc = app.Documents.Open(TPL)
time.sleep(1)


def bbox(name):
    ref = doc.ModelSpace.InsertBlock(pt(0, 0), name, 1.0, 1.0, 1.0, 0.0)
    mn, mx = ref.GetBoundingBox()
    ref.Delete()
    return mn, mx


for name, mode in TARGETS.items():
    blk = doc.Blocks.Item(name)
    mn, mx = bbox(name)
    o = blk.Origin
    if mode == "symbol":
        # 기준점은 (0,0)에 두고, 중첩 블록 삽입점이 (0,0)에 오도록 객체를 옮긴다.
        # GstarCAD COM은 블록 정의 안의 선·블록 참조에 Move도 좌표 속성 변경도 무시하므로(오류 없음)
        # AutoLISP entmod로 옮긴다
        blk.Origin = pt(0, 0)
        doc.SendCommand(SHIFT_TO_SYMBOL.replace("<NAME>", name) + "\n")
        while doc.GetVariable("CMDACTIVE"):
            time.sleep(0.2)
        mn2, mx2 = bbox(name)
        print(f"{name}: ({mn[0]:.0f},{mn[1]:.0f})-({mx[0]:.0f},{mx[1]:.0f}) → ({mn2[0]:.0f},{mn2[1]:.0f})-({mx2[0]:.0f},{mx2[1]:.0f})")
        continue
    if mode == "lowerleft":
        dx, dy = mn[0], mn[1]
    else:
        dx, dy = (mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2
    blk.Origin = pt(o[0] + dx, o[1] + dy)
    mn2, mx2 = bbox(name)
    print(f"{name}: ({mn[0]:.0f},{mn[1]:.0f})-({mx[0]:.0f},{mx[1]:.0f}) → ({mn2[0]:.0f},{mn2[1]:.0f})-({mx2[0]:.0f},{mx2[1]:.0f})")

doc.Save()
doc.Close(False)
