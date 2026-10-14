"""E9 비교: 자동 출력(e9_auto.pdf)과 사용자 손 출력(manual.pdf)을 래스터로 바꿔 차이 비율을 잰다

실행: uv run --isolated --no-project --python 3.10 --with pymupdf -- python e9_compare.py
"""
from pathlib import Path

import pymupdf

WORK = Path(r"C:\arcaid\work\spike")


def raster(path, dpi=150):
    page = pymupdf.open(str(path))[0]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return page.rect, pix


a_rect, a = raster(WORK / "e9_auto.pdf")
m_rect, m = raster(WORK / "manual.pdf")
print("용지(mm):", [round(v / 72 * 25.4, 1) for v in (a_rect.width, a_rect.height)],
      [round(v / 72 * 25.4, 1) for v in (m_rect.width, m_rect.height)])
if (a.width, a.height) != (m.width, m.height):
    print("크기가 달라 픽셀 비교를 하지 않음:", (a.width, a.height), (m.width, m.height))
else:
    sa, sm = a.samples, m.samples
    diff = sum(1 for x, y in zip(sa, sm) if abs(x - y) > 32)
    ink_a = sum(1 for x in sa if x < 128)
    ink_m = sum(1 for x in sm if x < 128)
    print(f"다른 픽셀 {diff} / 전체 {len(sa)} ({100 * diff / len(sa):.3f}%), 잉크 픽셀 자동 {ink_a} 손 {ink_m}")
