"""이미지 판독 시험용 합성 평면 이미지. 측정 도구(tools/raster/measure.py)와 별개로 구현한다.

좌표: WCS mm(y 위), 이미지 px(u 오른쪽, v 아래, 픽셀 (i, j)의 중심 (i+0.5, j+0.5)).
변환: X = x·cosβ − y·sinβ, Y = x·sinβ + y·cosβ, u = u0 + s·X·(1 + aniso), v = v0 − s·Y.
선은 픽셀마다 4×4 초과 표본이 선 안에 드는 비율로 칠한다(안티앨리어싱). 정답은 형상 정의에서 바로 계산한다.
"""
from functools import lru_cache
import math

import numpy as np
from PIL import Image

WING = (2200.0, 6540.0, 23.76)      # 회전동 지역 좌표계(원점 WCS, 각도)


class Scene:
    def __init__(self, size, s, beta_deg=0.0, origin=(0.0, 0.0), aniso=0.0, supersample=4, warp=None):
        self.w, self.h = size
        self.s, self.beta, self.aniso, self.F = s, beta_deg, aniso, supersample
        self.u0, self.v0 = origin
        self.warp = warp                      # (keystone, bend): 사진 원근·휨(px), 아래 px 참고
        self.ink = np.zeros((self.h, self.w), dtype=np.float32)

    def px(self, x, y):
        b = math.radians(self.beta)
        X = x * math.cos(b) - y * math.sin(b)
        Y = x * math.sin(b) + y * math.cos(b)
        u, v = self.u0 + self.s * X * (1 + self.aniso), self.v0 - self.s * Y
        if self.warp:                         # 그림 중심 기준 정규화 좌표로 사다리꼴(u += k·xn·yn·w)과 휨(v += b·xn²·h)
            xn, yn = u / self.w - 0.5, v / self.h - 0.5
            u, v = u + self.warp[0] * xn * yn * self.w, v + self.warp[1] * xn * xn * self.h
        return u, v

    @staticmethod
    def local(frame, u, v):
        ox, oy, deg = frame
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        return ox + u * c - v * s, oy + u * s + v * c

    def line(self, p1, p2, width_px=1.0, dark=1.0, frame=None):
        if frame:
            p1, p2 = self.local(frame, *p1), self.local(frame, *p2)
        if not self.warp:
            self.stroke(np.array(self.px(*p1)), np.array(self.px(*p2)), width_px, dark)
            return
        n = max(1, int(math.dist(self.px(*p1), self.px(*p2)) / 4))      # 왜곡이 있으면 4 px 조각으로 휘게
        pts = [self.px(p1[0] + (p2[0] - p1[0]) * i / n, p1[1] + (p2[1] - p1[1]) * i / n) for i in range(n + 1)]
        for a, b in zip(pts, pts[1:]):
            self.stroke(np.array(a), np.array(b), width_px, dark)

    def stroke(self, a, b, width, dark):
        """px 선분 a→b(맞댄 끝)를 폭 width px로 칠한다"""
        d = b - a
        length = float(np.hypot(*d))
        if length < 1e-6:
            return
        t = d / length
        n = np.array([-t[1], t[0]])
        hw = width / 2
        lo = np.floor(np.minimum(a, b) - hw - 1).astype(int)
        hi = np.ceil(np.maximum(a, b) + hw + 1).astype(int)
        x0, y0 = max(lo[0], 0), max(lo[1], 0)
        x1, y1 = min(hi[0], self.w), min(hi[1], self.h)
        if x1 <= x0 or y1 <= y0:
            return
        F = self.F
        sub = ((np.arange(F) + 0.5) / F).astype(np.float32)
        us = (np.arange(x0, x1, dtype=np.float32)[:, None] + sub[None, :]).ravel()
        for r0 in range(y0, y1, 32):
            r1 = min(r0 + 32, y1)
            vs = (np.arange(r0, r1, dtype=np.float32)[:, None] + sub[None, :]).ravel()
            U, V = np.meshgrid(us - a[0], vs - a[1])
            along = U * t[0] + V * t[1]
            perp = U * n[0] + V * n[1]
            inside = (along >= 0) & (along <= length) & (np.abs(perp) <= hw)
            cov = inside.reshape(r1 - r0, F, x1 - x0, F).mean(axis=(1, 3))
            region = self.ink[r0:r1, x0:x1]
            region[:] = 1 - (1 - region) * (1 - cov * dark)

    def ring(self, center, r, width_px=1.0, frame=None, n=72):
        """원(정 n각형 근사, 꼭짓점이 원 위)"""
        pts = [(center[0] + r * math.cos(2 * math.pi * k / n), center[1] + r * math.sin(2 * math.pi * k / n))
               for k in range(n)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            self.line(a, b, width_px, frame=frame)

    def box(self, rect, width_px=1.0, frame=None):
        x0, y0, x1, y1 = rect
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            self.line(a, b, width_px, frame=frame)

    def scribble(self, rect, count, seed, width_px=1.5):
        """문자 같은 짧은 획(간섭용)"""
        rng = np.random.default_rng(seed)
        x0, y0, x1, y1 = rect
        for _ in range(count):
            p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
            ang, ln = rng.uniform(0, math.pi), rng.uniform(60, 180)
            q = (p[0] + ln * math.cos(ang), p[1] + ln * math.sin(ang))
            self.line(p, q, width_px, 0.9)

    def hatch(self, rect, angle_deg, spacing, width_px=1.0, dark=0.7):
        """사각형(WCS) 안 평행선(해치)"""
        x0, y0, x1, y1 = rect
        d = (math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg)))
        n = (-d[1], d[0])
        corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        offs = [c[0] * n[0] + c[1] * n[1] for c in corners]
        k = math.ceil(min(offs) / spacing)
        while k * spacing <= max(offs):
            c = k * spacing
            p = (n[0] * c, n[1] * c)
            ts = []
            for axis, lo_, hi_ in ((0, x0, x1), (1, y0, y1)):
                if abs(d[axis]) > 1e-12:
                    ts += [(lo_ - p[axis]) / d[axis], (hi_ - p[axis]) / d[axis]]
            pts = [(p[0] + tt * d[0], p[1] + tt * d[1]) for tt in ts]
            pts = [q for q in pts if x0 - 1e-6 <= q[0] <= x1 + 1e-6 and y0 - 1e-6 <= q[1] <= y1 + 1e-6]
            if len(pts) >= 2:
                pts.sort()
                self.line(pts[0], pts[-1], width_px, dark)
            k += 1

    def image(self, noise=0.0, seed=0, paper=0.02, contrast=0.9):
        gray = 1.0 - (paper + contrast * self.ink)
        if noise:
            gray = gray + np.random.default_rng(seed).normal(0.0, noise, gray.shape)
        return Image.fromarray((np.clip(gray, 0, 1) * 255).round().astype(np.uint8), "L")


S = 0.06                      # px/mm(60 px/m, 지상6층 59.06 px/m와 비슷)
GRID_X = {"X1": 0.0, "X2": 6850.0, "X3": 9850.0}
GRID_Y = {"Y1": 0.0, "Y2": 5700.0}
THICK, THIN = 3.0, 1.0
CIRCLE = (16000.0, 9000.0, 400.0)            # 원 표식 중심·반지름
BOX = (14500.0, 11000.0, 15500.0, 11600.0)     # 사각형(WCS)
WING_BOX = (3000.0, 2000.0, 4000.0, 2600.0)    # 회전동 지역 좌표 사각형


@lru_cache(maxsize=8)
def plan(beta=0.0, aniso=0.0, noise=0.03, seed=1, warp=None):
    """시험 평면: 축, RC 벽(굵은선 두 줄), 창(굵은선+가는선), 23.76° 회전동, 문자·해치 간섭, 붙은 이중선, 빈 영역,
    원 표식·사각형(점 관측용).
    (PIL 이미지, Scene) — Scene.px가 정답 변환이다"""
    sc = Scene((1320, 1140), S, beta, (180.0, 960.0), aniso, warp=warp)
    for x in GRID_X.values():
        sc.line((x, -2500), (x, 14000), THIN, 0.8)
    for y in GRID_Y.values():
        sc.line((-2500, y), (12500, y), THIN, 0.8)
    for y in (-100.0, 100.0):                          # 남측 벽 면선
        sc.line((-100, y), (9950, y), THICK)
    for x in (-100.0, 100.0):                          # 서측 벽
        sc.line((x, -100), (x, 5800), THICK)
    for x in (9750.0, 9950.0):                         # 동측 벽
        sc.line((x, -100), (x, 5800), THICK)
    sc.line((1000, 5800), (4000, 5800), THICK)        # 북측 창: 바깥 굵은선 + 안쪽 가는선
    sc.line((1000, 5650), (4000, 5650), THIN)
    for v in (0.0, 200.0):                             # 회전동
        sc.line((0, v), (9000, v), THICK, frame=WING)
    for u in (9000.0, 9200.0):
        sc.line((u, 0), (u, 5000), THICK, frame=WING)
    for v in (5000.0, 5200.0):
        sc.line((0, v), (9200, v), THICK, frame=WING)
    sc.scribble((-300, 2000, 300, 2200), 25, seed=7)   # 서측 벽 위 문자 간섭(y 2000~2200)
    sc.hatch((3000, 1500, 5000, 3000), 45.0, 200.0)
    for x in (12000.0, 12000.0 + 2 / S):               # 중심 간격 2 px(사이 1 px) 이중선
        sc.line((x, 0), (x, 3000), THIN)
    for x in (13000.0, 13000.0 + 4 / S):               # 중심 간격 4 px
        sc.line((x, 0), (x, 3000), THIN)
    sc.ring(CIRCLE[:2], CIRCLE[2], THICK)              # 점 표식(원)
    sc.box(BOX, THIN)                                  # 문자 상자 같은 사각형
    sc.box(WING_BOX, THIN, frame=WING)                 # 회전동 안 사각형(지역 좌표)
    return sc.image(noise=noise, seed=seed), sc


def dark_of(img):
    return 1.0 - np.asarray(img, dtype=np.float64) / 255.0
