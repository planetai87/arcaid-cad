"""회사 템플릿에 '축렬, 계단.dwg' 예시와 같은 형상의 표준 블록을 추가한다.

블록 크기는 A3 1/100 기준(매뉴얼 예시 그대로). 다른 축척은 삽입 배율 s = k/100.
  축열버블-상 : 삽입점 = 가장 바깥 치수선 위 축 위치, 위로 지시선 + 버블
  축열버블-좌 : 삽입점 = 가장 바깥 치수선 위 축 위치, 왼쪽으로 지시선 + 버블
  계단화살표   : 꼭짓점이 삽입점, +y 방향을 가리키는 SOLID 쉐브론
  계단절단선Z  : 30° 절단선의 Z자 꺾임(중심이 삽입점). 양쪽 팔은 따로 30°로 긋는다
사용법: python add_standard_blocks.py [템플릿 경로]  (기본: 스킬 템플릿. 원본은 <템플릿>.bak으로 남긴다)
"""
import argparse
import shutil
import time
from pathlib import Path

DEFAULT_TPL = Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard" / "assets" / "회사표준_템플릿.dwg"

parser = argparse.ArgumentParser(description="회사 템플릿에 축열 버블·계단 기호 블록을 추가한다")
parser.add_argument("template", nargs="?", default=str(DEFAULT_TPL), help="템플릿 경로(기본: 스킬 템플릿)")
TPL = str(Path(parser.parse_args().template).resolve())

import pythoncom  # noqa: E402 (--help는 COM 없이 끝나게 인자를 먼저 읽는다)
import win32com.client  # noqa: E402
from win32com.client import VARIANT  # noqa: E402


def call(fn, *a, tries=60):
    for i in range(tries):
        try:
            return fn(*a)
        except pythoncom.com_error as e:
            if e.hresult in (-2147418111, -2147417846) and i < tries - 1:
                time.sleep(0.5)
                continue
            raise


def p3(x, y):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), 0.0))


def p2list(pts):
    flat = [float(v) for xy in pts for v in xy]
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, flat)


def new_block(doc, name):
    blocks = call(getattr, doc, "Blocks")
    for b in blocks:
        if b.Name == name:
            call(b.Delete)
            break
    return call(blocks.Add, p3(0, 0), name)


def bubble(doc, name, tick, center):
    blk = new_block(doc, name)
    ln = call(blk.AddLine, p3(*tick[0]), p3(*tick[1]))
    ln.Layer = "DIM"
    c = call(blk.AddCircle, p3(*center), 300.0)
    c.Layer = "DIM"
    # AddAttribute(높이, 모드, 프롬프트, 삽입점, 태그, 기본값)
    att = call(blk.AddAttribute, 225.0, 0, "축열 번호", p3(*center), "축열", "X1")
    att.Layer = "DIM"
    att.StyleName = "돋움"
    att.Alignment = 10  # acAlignmentMiddleCenter
    att.TextAlignmentPoint = p3(*center)


def stair_arrow(doc):
    blk = new_block(doc, "계단화살표")
    pts = [(0, 0), (-152.5, -87.9), (-130.6, -125.7), (0, -50.2), (130.6, -125.7), (152.5, -87.9)]
    pl = call(blk.AddLightWeightPolyline, p2list(pts))
    pl.Closed = True
    pl.Layer = "DIM"
    # 채움: GstarCAD는 블록 안 해치 Evaluate가 실패하므로 2D SOLID 두 개(좌·우 날개)로 채운다.
    # AddSolid 꼭짓점 순서는 사각형 ABCD일 때 A, B, D, C.
    for a, b, c, d in ((pts[0], pts[1], pts[2], pts[3]), (pts[0], pts[3], pts[4], pts[5])):
        s = call(blk.AddSolid, p3(*a), p3(*b), p3(*d), p3(*c))
        s.Layer = "DIM"


def cut_z(doc):
    blk = new_block(doc, "계단절단선Z")
    pts = [(-65.6, -11.8), (53.6, -110.0), (-53.6, 110.0), (65.6, 11.8)]
    pl = call(blk.AddLightWeightPolyline, p2list(pts))
    pl.Layer = "SYM1"


shutil.copy2(TPL, TPL + ".bak")  # *.bak은 git이 무시한다
app = win32com.client.Dispatch("GCAD.Application")
doc = call(call(getattr, app, "Documents").Open, TPL)
time.sleep(1)

bubble(doc, "축열버블-상", ((0, 350), (0, 500)), (0, 800))
bubble(doc, "축열버블-좌", ((-350, 0), (-500, 0)), (-800, 0))
stair_arrow(doc)
cut_z(doc)
for b in [b for b in call(getattr, doc, "Blocks")]:
    if b.Name == "축열":  # 형상·글꼴이 다른 예전 소형 블록
        call(b.Delete)

call(doc.Save)
print("blocks:", [b.Name for b in call(getattr, doc, "Blocks") if not b.Name.startswith("*")])
call(doc.Close, False)
