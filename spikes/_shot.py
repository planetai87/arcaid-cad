"""시험 도면을 열어 전체 줌 후 화면을 PNG로 저장한다: python _shot.py <도면 이름> <png 이름>"""
import sys

from _harness import WORK, Spike

with Spike("shot") as s:
    s.open(WORK / sys.argv[1])
    s.cmd("_.ZOOM\n_E\n")
    png = s.ctl.screenshot()
    (WORK / sys.argv[2]).write_bytes(png)
    s.record("png", str(WORK / sys.argv[2]))
