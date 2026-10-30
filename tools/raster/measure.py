"""평면 이미지 측정 도구(CAD 없음): 좌표 보정, 직선 맞춤·교점, 선 각도, 단면 프로파일, 지역 좌표로 펴 보기.

사용(저장소 루트):
  python tools/raster/measure.py fitline IMG --px --from U,V --to U,V --band PX            # 이미지 px로 직선 맞춤
  python tools/raster/measure.py cross --line U,V,DU,DV --line U,V,DU,DV                   # 두 px 직선의 교점
  python tools/raster/measure.py calib IMG --point U,V=X,Y ... [--check U,V=X,Y ...] -o calib.json
  python tools/raster/measure.py angles IMG --calib calib.json --window U0 V0 U1 V1 [--frame X,Y,DEG]
  python tools/raster/measure.py fitline IMG --calib calib.json [--frame X,Y,DEG] --from U,V --to U,V --band MM
  python tools/raster/measure.py profile IMG --calib calib.json [--frame X,Y,DEG] --axis u|v --at C --range A B
                                 [--band MM] [--plot out.png] [--record FILE --names=a,_,b]
  python tools/raster/measure.py view IMG --calib calib.json [--frame X,Y,DEG] --window U0 V0 U1 V1 -o out.png
                                 [--mmpp MM] [--grid MM]

좌표 규약:
- 이미지 px (u, v): u는 오른쪽, v는 아래로 증가한다. 픽셀 (i, j)(열 i, 행 j)의 중심은 (i+0.5, j+0.5)다.
- WCS (x, y): mm, y는 위로 증가한다. 지역 좌표(--frame X,Y,DEG)는 tools/review/render.py의 Frame과 같다.
- 보정은 닮음 변환이다: u = u0 + s(cosβ·x − sinβ·y), v = v0 − s(sinβ·x + cosβ·y). β는 화면에서 반시계가 +다.
보정(calib)은 대응점 3개 이상을 최소제곱으로 맞추고, 아핀으로 다시 맞춰 x·y 축척 차(비균등 확대)를 확인한다.
보정에 쓰지 않은 검증점(--check)의 오차와 잔차 중 큰 값을 보정 오차(err_mm)로 둔다.
측정값의 허용오차(tol_mm)는 보정 오차 + 경계 보간 오차(0.5 px)다. --max-tol(기본 2 px)을 넘으면 판독 불가로 본다.
선폭(width_px)은 관측값일 뿐이고, 벽·창 같은 의미는 정하지 않는다.
표준 출력은 UTF-8 JSON 하나이고, 성공이면 종료 코드 0, 거부·오류면 1이다.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.review.render import Frame, _font  # noqa: E402

CALIB_SCHEMA = "arcaid-raster-calib/1"
MIN_POINTS = 3                 # 2개면 닮음 변환이 정확히 맞아 잔차로 검증할 수 없다
MIN_SEPARATION_PX = 50.0       # 대응점 사이 최소 거리
COLLINEAR_RATIO = 0.1          # 대응점 분포의 둘째/첫째 특잇값이 이보다 작으면 한 직선 위로 본다
MAX_ANISOTROPY = 0.003         # 아핀 맞춤의 x·y 축척 차 상한(0.3%)
MAX_RESIDUAL_PX = 1.0          # 대응점 잔차 RMS 상한
EDGE_ERR_PX = 0.5              # 반높이 경계 보간 오차(가정)
CONSISTENT_PX = 1.0            # 띠 안 줄마다 잰 중심이 이보다 흩어지면 판독 불가
TILT_DEG = 0.5                 # 선이 단면선과 직각에서 이만큼 벗어나면 '기울어짐'
MIN_CONTRAST = 0.2             # 바탕보다 이만큼 어두워야 선으로 본다(어두움 0~1)
MAX_TOL_PX = 2.0
MAX_VIEW_SIDE = 2400
GRID_COLOR, LABEL_COLOR = (0, 150, 0, 110), (0, 120, 0, 255)


def _r(v, digits=2):
    return round(float(v), digits) + 0.0


# ---------- 이미지·좌표 ----------

def load_image(path):
    """어두움 배열(0 흰색 ~ 1 검정, 행 v·열 u)과 파일 sha256. 투명 영역은 흰 바탕으로 본다"""
    data = Path(path).read_bytes()
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    gray = np.asarray(im.convert("L"), dtype=np.float64) / 255.0
    return 1.0 - gray, hashlib.sha256(data).hexdigest()


def sample(dark, u, v):
    """연속 이미지 좌표에서 쌍선형 보간한 어두움. 픽셀 중심 밖(가장자리 반 픽셀 포함)은 nan"""
    h, w = dark.shape
    x = np.asarray(u, dtype=np.float64) - 0.5
    y = np.asarray(v, dtype=np.float64) - 0.5
    inside = (x >= 0) & (y >= 0) & (x <= w - 1) & (y <= h - 1)
    xc, yc = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
    x0 = np.minimum(np.floor(xc).astype(int), w - 2)
    y0 = np.minimum(np.floor(yc).astype(int), h - 2)
    fx, fy = xc - x0, yc - y0
    val = (dark[y0, x0] * (1 - fx) * (1 - fy) + dark[y0, x0 + 1] * fx * (1 - fy)
           + dark[y0 + 1, x0] * (1 - fx) * fy + dark[y0 + 1, x0 + 1] * fx * fy)
    return np.where(inside, val, np.nan)


class Calib:
    """WCS(mm, y 위) ↔ 이미지(px, v 아래) 닮음 변환"""

    def __init__(self, s, beta_deg, u0, v0, err_mm=0.0, sha256=None):
        self.s, self.beta, self.u0, self.v0 = float(s), float(beta_deg), float(u0), float(v0)
        self.err_mm, self.sha256 = float(err_mm), sha256
        self.c, self.n = math.cos(math.radians(self.beta)), math.sin(math.radians(self.beta))

    def to_px(self, x, y):
        x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        return self.u0 + self.s * (self.c * x - self.n * y), self.v0 - self.s * (self.n * x + self.c * y)

    def to_wcs(self, u, v):
        a = (np.asarray(u, dtype=np.float64) - self.u0) / self.s
        b = -(np.asarray(v, dtype=np.float64) - self.v0) / self.s
        return self.c * a + self.n * b, -self.n * a + self.c * b

    def tol_mm(self):
        return self.err_mm + EDGE_ERR_PX / self.s

    @classmethod
    def load(cls, path, image_sha=None):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != CALIB_SCHEMA:
            raise ValueError(f"보정 파일 형식이 아님({data.get('schema')})")
        if data.get("status") != "ok":
            raise ValueError("거부된 보정 파일")
        if image_sha and data.get("sha256") and data["sha256"] != image_sha:
            raise ValueError("보정 파일의 이미지 sha256이 이 이미지와 다름")
        return cls(data["px_per_mm"], data["beta_deg"], *data["origin_px"], data.get("err_mm", 0.0),
                   data.get("sha256"))


def _frame(text):
    return Frame.parse(text) or Frame(0.0, 0.0, 0.0)


def _local_to_px(calib, frame, u, v):
    x, y = frame.to_wcs(np.asarray(u, dtype=np.float64), np.asarray(v, dtype=np.float64))
    return calib.to_px(x, y)


def _px_to_local(calib, frame, u, v):
    x, y = calib.to_wcs(u, v)
    lu, lv = frame.to_local((x, y))
    return lu, lv


def _angle180(deg):
    """방향각을 [−90, 90)으로"""
    return (deg + 90.0) % 180.0 - 90.0


# ---------- 보정 ----------

def fit_calib(pairs, checks=(), max_anisotropy=MAX_ANISOTROPY, min_separation=MIN_SEPARATION_PX,
              max_residual=MAX_RESIDUAL_PX):
    """pairs·checks: [((u, v), (x, y)), ...]. 거부 사유가 있으면 status rejected"""
    reasons, warnings = [], []
    P = np.array([p for p, _ in pairs], dtype=np.float64).reshape(-1, 2)
    W = np.array([w for _, w in pairs], dtype=np.float64).reshape(-1, 2)
    n = len(P)
    if n < MIN_POINTS:
        reasons.append(f"대응점이 {n}개(3개 이상 필요: 2개면 잔차로 검증할 수 없음)")
    if n >= 2:
        dist = min(math.dist(P[i], P[j]) for i in range(n) for j in range(i + 1, n))
        if dist < min_separation:
            reasons.append(f"대응점 사이가 {dist:.1f} px(최소 {min_separation:g} px)")
        sv = np.linalg.svd(W - W.mean(axis=0), compute_uv=False)
        if sv[0] == 0 or sv[-1] / sv[0] < COLLINEAR_RATIO:
            reasons.append("대응점이 한 직선 위에 있음(x·y 축척 차를 확인할 수 없음)")
    if reasons:
        return {"status": "rejected", "reasons": reasons}
    # 닮음: (u, −v) = (tu, tw) + [[a, −b], [b, a]]·(x, y)
    rows, rhs = [], []
    for (u, v), (x, y) in zip(P, W):
        rows += [[x, -y, 1.0, 0.0], [y, x, 0.0, 1.0]]
        rhs += [u, -v]
    a, b, tu, tw = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
    calib = Calib(math.hypot(a, b), math.degrees(math.atan2(b, a)), tu, -tw)
    # 아핀으로 다시 맞춰 비균등 배율·뒤집힘 확인
    A = np.column_stack([W, np.ones(n)])
    mu = np.linalg.lstsq(A, P[:, 0], rcond=None)[0]
    mw = np.linalg.lstsq(A, -P[:, 1], rcond=None)[0]
    M = np.array([[mu[0], mu[1]], [mw[0], mw[1]]])
    sv = np.linalg.svd(M, compute_uv=False)
    anisotropy = sv[0] / sv[1] - 1.0
    cols = M.T
    shear = 90.0 - math.degrees(math.acos(np.clip(np.dot(cols[0], cols[1]) /
                                                  (np.linalg.norm(cols[0]) * np.linalg.norm(cols[1])), -1, 1)))
    if np.linalg.det(M) <= 0:
        reasons.append("이미지가 뒤집혀 있음(아핀 행렬식 ≤ 0)")
    if anisotropy > max_anisotropy:
        reasons.append(f"x·y 축척 차 {anisotropy * 100:.2f}%(상한 {max_anisotropy * 100:g}%, 비균등 확대 이미지)")
    pu, pv = calib.to_px(W[:, 0], W[:, 1])
    res = np.hypot(pu - P[:, 0], pv - P[:, 1])
    rms = float(np.sqrt(np.mean(res ** 2)))
    if rms > max_residual:
        reasons.append(f"대응점 잔차 RMS {rms:.2f} px(상한 {max_residual:g} px)")
    check_out = []
    for (u, v), (x, y) in checks:
        cu, cv = calib.to_px(x, y)
        e = math.hypot(float(cu) - u, float(cv) - v)
        check_out.append({"px": [_r(u), _r(v)], "wcs": [_r(x), _r(y)], "err_px": _r(e, 3), "err_mm": _r(e / calib.s)})
    if not checks:
        warnings.append("독립 검증점이 없음(--check). 보정 오차를 잔차만으로 잡았다")
    worst_check = max([c["err_px"] for c in check_out], default=0.0)
    calib.err_mm = max(rms, worst_check) / calib.s
    if worst_check > max_residual:
        reasons.append(f"검증점 오차 {worst_check:.2f} px(상한 {max_residual:g} px)")
    return {
        "status": "rejected" if reasons else "ok", "reasons": reasons, "warnings": warnings,
        "px_per_mm": calib.s, "px_per_m": _r(calib.s * 1000, 4), "beta_deg": _r(calib.beta, 4),
        "origin_px": [calib.u0, calib.v0], "err_mm": _r(calib.err_mm),
        "residuals": [{"px": [_r(u), _r(v)], "wcs": [_r(x), _r(y)], "err_px": _r(e, 3)}
                      for (u, v), (x, y), e in zip(P, W, res)],
        "rms_px": _r(rms, 3), "checks": check_out,
        "affine": {"anisotropy_pct": _r(anisotropy * 100, 4), "shear_deg": _r(shear, 4)},
    }


# ---------- 직선 맞춤·교점 ----------

def fit_line(dark, p1, p2, band, calib=None, frame=None, min_contrast=MIN_CONTRAST):
    """p1→p2 둘레 띠(폭 band) 안의 어두운 픽셀에 직선을 맞춘다(어두움 가중 주성분).
    calib가 있으면 p1·p2·band는 지역 mm, 없으면 이미지 px"""
    if calib:
        frame = frame or Frame(0.0, 0.0, 0.0)
        a = np.array([float(c) for c in _local_to_px(calib, frame, p1[0], p1[1])])
        b = np.array([float(c) for c in _local_to_px(calib, frame, p2[0], p2[1])])
        band_px = band * calib.s
    else:
        a, b, band_px = np.array(p1, dtype=np.float64), np.array(p2, dtype=np.float64), float(band)
    length = float(np.linalg.norm(b - a))
    if length < 2:
        raise ValueError("직선 구간이 2 px보다 짧음")
    t = (b - a) / length
    nrm = np.array([-t[1], t[0]])
    hb = band_px / 2
    corners = np.array([a + nrm * hb, a - nrm * hb, b + nrm * hb, b - nrm * hb])
    h, w = dark.shape
    x0, y0 = np.maximum(np.floor(corners.min(axis=0)).astype(int), 0)
    x1, y1 = np.minimum(np.ceil(corners.max(axis=0)).astype(int) + 1, [w, h])
    jj, ii = np.mgrid[y0:y1, x0:x1]
    pu, pv = ii + 0.5, jj + 0.5
    ru, rv = pu - a[0], pv - a[1]
    along, perp = ru * t[0] + rv * t[1], ru * nrm[0] + rv * nrm[1]
    mask = (along >= 0) & (along <= length) & (np.abs(perp) <= hb)
    vals = dark[jj, ii][mask]
    out = {"band_px": _r(band_px), "length_px": _r(length)}
    if vals.size < 10:
        return {**out, "status": "empty", "message": "띠 안 픽셀이 너무 적음(이미지 밖?)"}
    bg, top = np.percentile(vals, 10), np.percentile(vals, 99.5)
    if top - bg < min_contrast:
        return {**out, "status": "empty", "message": f"띠 안에 선이 없음(대비 {top - bg:.2f})"}
    wts = vals - bg
    wts[wts < 0.2 * (top - bg)] = 0.0
    pts = np.column_stack([pu[mask], pv[mask]])
    total = wts.sum()
    mean = (wts[:, None] * pts).sum(axis=0) / total
    X = pts - mean
    cov = (X * wts[:, None]).T @ X / total
    evals, evecs = np.linalg.eigh(cov)
    d = evecs[:, int(np.argmax(evals))]
    if np.dot(d, t) < 0:
        d = -d
    perp_fit = X @ np.array([-d[1], d[0]])
    rms = float(np.sqrt((wts * perp_fit ** 2).sum() / total))
    al = along[mask][wts > 0]
    bins = np.unique(np.clip(np.floor(al).astype(int), 0, max(int(length) - 1, 0)))
    coverage = len(bins) / max(int(math.ceil(length)), 1)
    out.update({"status": "ok" if coverage >= 0.5 else "partial",
                "point_px": [_r(mean[0], 3), _r(mean[1], 3)], "dir_px": [_r(d[0], 6), _r(d[1], 6)],
                "line_px": [float(mean[0]), float(mean[1]), float(d[0]), float(d[1])],
                "rms_px": _r(rms, 3), "pixels": int((wts > 0).sum()), "coverage": _r(coverage, 3)})
    if coverage < 0.5:
        out["message"] = f"선이 구간의 {coverage * 100:.0f}%만 덮음"
    if calib:
        # 맞춘 직선과 '구간 중점을 지나는 수직선'의 교점을 지역 좌표로 낸다
        mid = (a + b) / 2
        s_par = np.dot(mid - mean, d)
        foot = mean + d * s_par
        q1, q2 = mean - d * 100, mean + d * 100
        w1, w2 = np.array(calib.to_wcs(*q1)), np.array(calib.to_wcs(*q2))
        ang_wcs = math.degrees(math.atan2(w2[1] - w1[1], w2[0] - w1[0]))
        lu, lv = _px_to_local(calib, frame, foot[0], foot[1])
        out.update({"angle_wcs_deg": _r(_angle180(ang_wcs), 4),
                    "angle_local_deg": _r(_angle180(ang_wcs - frame.deg), 4),
                    "point_local": [_r(lu), _r(lv)], "rms_mm": _r(rms / calib.s),
                    "tol_mm": _r(calib.tol_mm())})
    return out


def cross(line1, line2):
    """두 px 직선(u, v, du, dv)의 교점"""
    p, d = np.array(line1[:2], dtype=np.float64), np.array(line1[2:], dtype=np.float64)
    q, e = np.array(line2[:2], dtype=np.float64), np.array(line2[2:], dtype=np.float64)
    det = d[0] * (-e[1]) - d[1] * (-e[0])
    sin = abs(det) / (np.linalg.norm(d) * np.linalg.norm(e))
    if sin < math.sin(math.radians(5)):
        raise ValueError("두 직선이 거의 평행함(사이각 5° 미만)")
    k = ((q[0] - p[0]) * (-e[1]) - (q[1] - p[1]) * (-e[0])) / det
    x = p + k * d
    return {"status": "ok", "point_px": [float(x[0]), float(x[1])],
            "angle_between_deg": _r(math.degrees(math.asin(min(sin, 1.0))), 3)}


# ---------- 선 각도 ----------

def _sharpness(pu, pv, wts, thetas):
    """각 θ(라디안, px 법선 방향)로 투영한 어두움 분포의 날카로움 Σρ A(ρ)². 그 방향의 선이 많을수록 크다"""
    out = np.empty(len(thetas))
    for k, th in enumerate(thetas):
        rho = pu * math.cos(th) + pv * math.sin(th)
        acc = np.bincount(np.floor(rho - rho.min()).astype(int), weights=wts)
        out[k] = float((acc ** 2).sum())
    return out


def angles(dark, calib, frame, window, top=5):
    """창(지역 mm) 안 선의 주된 방향(WCS·지역 도, 0~180). 투영 날카로움(Hough 각도 탐색):
    0.25° 간격으로 찾고 ±0.5°를 0.01° 간격으로 다시 찾는다. 기울기 방식은 가는 사선에서 45° 쪽으로 치우친다"""
    u0, v0, u1, v1 = window
    gu = np.linspace(u0, u1, max(int((u1 - u0) * calib.s), 2))
    gv = np.linspace(v0, v1, max(int((v1 - v0) * calib.s), 2))
    GU, GV = np.meshgrid(gu, gv)
    cu, cv = _local_to_px(calib, frame, GU, GV)
    h, w = dark.shape
    x0, x1 = max(int(math.floor(cu.min())), 0), min(int(math.ceil(cu.max())), w)
    y0, y1 = max(int(math.floor(cv.min())), 0), min(int(math.ceil(cv.max())), h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError("창이 이미지 밖이거나 너무 작음")
    jj, ii = np.mgrid[y0:y1, x0:x1]
    pu, pv = ii + 0.5, jj + 0.5
    lu, lv = _px_to_local(calib, frame, pu, pv)
    inside = (lu >= u0) & (lu <= u1) & (lv >= v0) & (lv <= v1)
    vals = dark[jj, ii][inside]
    bg = np.percentile(vals, 50) if vals.size else 0.0
    wts = vals - bg
    keep = wts > MIN_CONTRAST
    if keep.sum() < 20:
        return {"status": "empty", "message": "창 안에 선이 없음", "peaks": []}
    pu, pv, wts = pu[inside][keep], pv[inside][keep], wts[keep]
    pu, pv = pu - pu.mean(), pv - pv.mean()
    coarse = np.radians(np.arange(0.0, 180.0, 0.25))
    score = _sharpness(pu, pv, wts, coarse)
    base = float(np.median(score))
    found = []
    for i in np.argsort(score)[::-1]:
        if score[i] < score[i - 1] or score[i] < score[(i + 1) % len(score)]:
            continue
        deg = math.degrees(coarse[i])
        if any(min(abs(deg - p), 180 - abs(deg - p)) < 3.0 for p, _ in found):
            continue
        fine = np.radians(np.arange(deg - 0.5, deg + 0.5 + 1e-9, 0.01))
        fs = _sharpness(pu, pv, wts, fine)
        found.append((math.degrees(fine[int(np.argmax(fs))]), float(fs.max())))
        if len(found) >= top:
            break
    best = max(s for _, s in found)
    out = []
    for th_deg, s in found:
        th = math.radians(th_deg)
        # 법선 θ의 선 방향(px) = (−sinθ, cosθ). 화면 위쪽 기준 각 = atan2(−cosθ, −sinθ), WCS 각 = 화면 각 − β
        wcs = (math.degrees(math.atan2(-math.cos(th), -math.sin(th))) - calib.beta) % 180.0
        out.append({"angle_wcs_deg": _r(wcs, 3) % 180.0, "angle_local_deg": _r((wcs - frame.deg) % 180.0, 3) % 180.0,
                    "strength": _r((s - base) / (best - base) if best > base else 1.0, 3)})
    return {"status": "ok", "peaks": out, "pixels": int(keep.sum())}


# ---------- 단면 프로파일 ----------

def _edge(t, h, i_in, i_out, half):
    """h[i_in] ≥ half > h[i_out]인 이웃 두 표본 사이에서 반높이 위치를 선형 보간"""
    d = h[i_in] - h[i_out]
    f = (h[i_in] - half) / d if d else 0.0
    return t[i_in] + (t[i_out] - t[i_in]) * f


def profile(dark, calib, frame, axis, at, start, end, band=0.0, step=None, min_contrast=MIN_CONTRAST,
            max_tol=None):
    """지역 좌표 단면선(axis 방향으로 start→end, 다른 축 값 at)을 따라 어두움을 재고, 선(어두운 구간)마다
    시작·끝·중심·폭을 낸다. band(mm)가 있으면 그 폭으로 평균하고, 줄마다 잰 중심으로 기울어짐·흩어짐을 본다.
    선 경계는 봉우리 높이의 반(바탕 기준)에서 잡는다. 중심 간격이 3 px 안팎보다 좁은 두 선은 하나의 넓은 구간으로
    합쳐질 수 있다(사이 골이 봉우리의 75%보다 얕으면 'double'). 폭(width_px)이 다른 선보다 넓으면 의심한다"""
    if axis not in ("u", "v"):
        raise ValueError("axis는 u 또는 v")
    if end <= start:
        raise ValueError("range는 A < B")
    s = calib.s
    step = step or 0.25 / s
    max_tol = max_tol if max_tol is not None else MAX_TOL_PX / s
    t = np.arange(start, end + step / 2, step)
    n_off = int(math.ceil(band * s / 0.5)) + 1 if band > 0 else 1
    offs = np.linspace(-band / 2, band / 2, n_off) if n_off > 1 else np.array([0.0])
    T, O = np.meshgrid(t, at + offs)
    lu, lv = (T, O) if axis == "u" else (O, T)
    pu, pv = _local_to_px(calib, frame, lu, lv)
    D = sample(dark, pu, pv)
    base = {"axis": axis, "at": at, "range": [start, end], "band": band, "step": _r(step, 4),
            "tol_mm": _r(calib.tol_mm()), "max_tol_mm": _r(max_tol)}
    if np.isnan(D).all():
        return {**base, "status": "outside", "message": "단면선이 이미지 밖", "runs": []}
    outside = bool(np.isnan(D).any())
    with np.errstate(all="ignore"):
        P = np.nanmean(D, axis=0)
    P = np.where(np.isnan(P), np.nanmin(P), P)
    bg = float(np.percentile(P, 25))
    h = P - bg
    n = len(t)
    cand = [i for i in range(n) if h[i] >= min_contrast and (i == 0 or h[i] >= h[i - 1])
            and (i == n - 1 or h[i] > h[i + 1])]
    cand.sort(key=lambda i: -h[i])
    assigned = np.zeros(n, dtype=bool)
    runs = []
    for i in cand:
        if assigned[i]:
            continue
        half = h[i] / 2
        lo = i
        while lo > 0 and h[lo - 1] >= half and not assigned[lo - 1]:
            lo -= 1
        hi = i
        while hi < n - 1 and h[hi + 1] >= half and not assigned[hi + 1]:
            hi += 1
        # 이미 잡은 더 진한 선에 맞닿아 멈췄으면 경계를 보간하지 않고 붙은 선으로 표시한다
        touch_l, touch_r = lo > 0 and assigned[lo - 1], hi < n - 1 and assigned[hi + 1]
        left = t[0] if lo == 0 else (t[lo] if touch_l else _edge(t, h, lo, lo - 1, half))
        right = t[-1] if hi == n - 1 else (t[hi] if touch_r else _edge(t, h, hi, hi + 1, half))
        absorbed = [j for j in cand if lo <= j <= hi and j != i and not assigned[j]]
        peaks = 1 + sum(1 for j in absorbed
                        if h[min(i, j):max(i, j) + 1].min() < 0.75 * h[j])   # 사이에 뚜렷한 골이 있으면 별개 봉우리
        if touch_l or touch_r:
            peaks = max(peaks, 2)
        assigned[lo:hi + 1] = True
        runs.append({"lo": lo, "hi": hi, "left": float(left), "right": float(right), "peak": float(h[i]),
                     "peaks": peaks, "truncated": lo == 0 or hi == n - 1})
    runs.sort(key=lambda r: r["left"])
    out_runs = []
    for k, r in enumerate(runs):
        width = r["right"] - r["left"]
        center = (r["left"] + r["right"]) / 2
        flags = []
        # 띠 안 줄마다 무게중심
        pad = max(width, 2 / s)
        win = (t >= r["left"] - pad) & (t <= r["right"] + pad)
        cs, os_ = [], []
        for row, o in zip(D, offs):
            seg = row[win] - bg
            if np.isnan(seg).any():
                continue
            wts = np.where(seg > 0.25 * r["peak"], seg, 0.0)
            if wts.sum() <= 0:
                continue
            cs.append(float((t[win] * wts).sum() / wts.sum()))
            os_.append(float(o))
        tilt, resid, spread = 0.0, 0.0, 0.0
        if len(cs) >= 3:
            m, c0 = np.polyfit(os_, cs, 1)
            tilt = math.degrees(math.atan(m))
            resid = float(np.max(np.abs(np.array(cs) - (c0 + m * np.array(os_)))))
            spread = float(np.ptp(cs))
            if len(cs) < 0.8 * len(offs):
                flags.append("rows_missing")
        if resid > CONSISTENT_PX / s:
            flags.append("inconsistent")
        elif abs(tilt) > TILT_DEG:
            flags.append("tilted")
        if r["peaks"] > 1:
            flags.append("double")
        if r["truncated"]:
            flags.append("truncated")
        if calib.tol_mm() > max_tol:
            flags.append("budget")
        cu, cv = (center, at) if axis == "u" else (at, center)
        wx, wy = frame.to_wcs(cu, cv)
        out_runs.append({
            "index": k, "start": _r(r["left"]), "end": _r(r["right"]), "center": _r(center),
            "width_mm": _r(width), "width_px": _r(width * s, 3), "peak": _r(r["peak"], 3), "peaks": r["peaks"],
            "center_local": [_r(cu), _r(cv)], "center_wcs": [_r(wx), _r(wy)],
            "tilt_deg": _r(tilt, 3), "spread_mm": _r(spread), "flags": flags,
            "status": "ok" if not flags else ("unreadable" if set(flags) & {"inconsistent", "budget", "truncated",
                                                                           "rows_missing"} else "check"),
        })
    if not out_runs:
        status = "empty"
    elif all(r["status"] == "ok" for r in out_runs):
        status = "ok"
    else:
        status = "partial"
    result = {**base, "status": status, "background": _r(bg, 3), "runs": out_runs,
              "_curve": (t, P, bg)}
    if outside:
        result["warning"] = "단면선 일부가 이미지 밖"
    messages = {"tilted": "단면선과 직각이 아님(frame 확인)", "inconsistent": "띠 안에서 위치가 흩어짐(문자·해치 간섭?)",
                "double": "붙은 이중선 의심", "truncated": "범위 끝에서 잘림", "budget": "오차 예산 초과",
                "rows_missing": "띠 일부에서 선이 끊김"}
    used = sorted({f for r in out_runs for f in r["flags"]})
    if used:
        result["flag_meaning"] = {f: messages[f] for f in used}
    return result


def plot_profile(result, out_path, size=(1000, 300)):
    """프로파일 곡선과 잰 구간을 그림으로(에이전트가 Read로 확인)"""
    t, P, bg = result["_curve"]
    w, h = size
    im = Image.new("RGB", size, "white")
    dr = ImageDraw.Draw(im)
    font = _font(12)
    t0, t1 = float(t[0]), float(t[-1])
    x = lambda v: 40 + (v - t0) / (t1 - t0) * (w - 60)  # noqa: E731
    y = lambda d: h - 30 - d * (h - 50)  # noqa: E731
    for r in result["runs"]:
        color = (210, 240, 210) if r["status"] == "ok" else (250, 220, 200)
        dr.rectangle([x(r["start"]), 10, x(r["end"]), h - 30], fill=color)
        dr.text((x(r["center"]) - 10, 12), str(r["index"]), fill="black", font=font)
    dr.line([(40, y(bg)), (w - 20, y(bg))], fill=(150, 150, 150))
    dr.line([(x(a), y(b)) for a, b in zip(t, P)], fill="black", width=1)
    step = _nice_step(t1 - t0, 10)
    for g in np.arange(math.ceil(t0 / step) * step, t1 + 1e-9, step):
        dr.line([(x(g), h - 30), (x(g), h - 25)], fill="black")
        dr.text((x(g) - 15, h - 22), f"{g:g}", fill="black", font=font)
    dr.text((44, h - 46), f"{result['axis']} along, at {result['at']:g} mm, band {result['band']:g}",
            fill=(80, 80, 80), font=font)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
    return str(out_path)


# ---------- 펴 보기 ----------

def _nice_step(span, lines):
    raw = span / max(lines, 1)
    for m in (1, 2, 5):
        for e in range(0, 7):
            if m * 10 ** e >= raw:
                return float(m * 10 ** e)
    return float(10 ** 7)


def view(dark, calib, frame, window, out_path, mmpp=None, grid=None, local_names=("u", "v")):
    """원본을 지역 좌표(창 U0 V0 U1 V1, mm)의 격자로 다시 표본해 눈금과 함께 PNG로 쓴다"""
    u0, v0, u1, v1 = window
    if u1 <= u0 or v1 <= v0:
        raise ValueError("window는 U0 V0 U1 V1(U0<U1, V0<V1)")
    mmpp = mmpp or max(max(u1 - u0, v1 - v0) / 1600, 0.25 / calib.s)
    W, H = int(math.ceil((u1 - u0) / mmpp)), int(math.ceil((v1 - v0) / mmpp))
    if max(W, H) > MAX_VIEW_SIDE:
        raise ValueError(f"그림이 너무 큼({W}×{H} px, 상한 {MAX_VIEW_SIDE}). --mmpp를 키울 것")
    ii, jj = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    pu, pv = _local_to_px(calib, frame, u0 + ii * mmpp, v1 - jj * mmpp)
    D = sample(dark, pu, pv)
    gray = np.where(np.isnan(D), 215.0, 255.0 * (1.0 - D))
    canvas = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8), "L").convert("RGBA")
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    dr, font = ImageDraw.Draw(layer), _font(13)
    step = grid or _nice_step(max(u1 - u0, v1 - v0), 10)
    nu, nv = local_names
    for g in np.arange(math.ceil(u0 / step) * step, u1 + 1e-9, step):
        px = (g - u0) / mmpp
        dr.line([(px, 0), (px, H)], fill=GRID_COLOR)
        dr.text((px + 2, 2), f"{nu} {g:g}", fill=LABEL_COLOR, font=font)
    for g in np.arange(math.ceil(v0 / step) * step, v1 + 1e-9, step):
        py = (v1 - g) / mmpp
        dr.line([(0, py), (W, py)], fill=GRID_COLOR)
        dr.text((2, py + 2), f"{nv} {g:g}", fill=LABEL_COLOR, font=font)
    canvas.alpha_composite(layer)
    dr = ImageDraw.Draw(canvas)
    legend = f"{nu} {u0:g}–{u1:g}, {nv} {v0:g}–{v1:g} mm  |  {mmpp:.2f} mm/px  |  grid {step:g}"
    if frame.deg or frame.x or frame.y:
        legend += f"  |  frame ({frame.x:g},{frame.y:g}) {frame.deg:g}°"
    dr.rectangle([0, H - 20, W, H], fill=(255, 255, 255, 230))
    dr.text((4, H - 18), legend, fill=(0, 0, 0, 255), font=_font(14))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path)
    return {"status": "ok", "png": str(out_path), "size": [W, H], "mm_per_px": _r(mmpp, 3), "grid": step}


# ---------- CLI ----------

def _pair(text):
    try:
        left, right = text.split("=")
        u, v = (float(c) for c in left.split(","))
        x, y = (float(c) for c in right.split(","))
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"U,V=X,Y 형식이 아님: {text}") from e
    return (u, v), (x, y)


def _xy(text):
    try:
        a, b = (float(c) for c in text.split(","))
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"A,B 형식이 아님: {text}") from e
    return a, b


def _line(text):
    try:
        vals = [float(c) for c in text.split(",")]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"U,V,DU,DV 형식이 아님: {text}") from e
    if len(vals) != 4:
        raise argparse.ArgumentTypeError(f"U,V,DU,DV 형식이 아님: {text}")
    return vals


def canonical_command(args):
    """기록용 명령(절대 경로, --record 빼고). record.py가 다시 실행해 기록값을 대조한다"""
    cmd = [args.command, str(Path(args.image).resolve()), "--calib", str(Path(args.calib).resolve())]
    if args.frame:
        cmd += ["--frame", args.frame]
    if args.command == "profile":
        cmd += ["--axis", args.axis, "--at", repr(args.at), "--range", repr(args.range[0]), repr(args.range[1]),
                "--band", repr(args.band), "--min-contrast", repr(args.min_contrast)]
        if args.max_tol is not None:
            cmd += ["--max-tol", repr(args.max_tol)]
    else:
        cmd += ["--from", f"{args.p1[0]!r},{args.p1[1]!r}", "--to", f"{args.p2[0]!r},{args.p2[1]!r}",
                "--band", repr(args.band)]
    return cmd


def profile_observations(result, frame, names, base):
    """프로파일 구간을 이름별 관측으로. names는 구간마다 하나(건너뛰기 '_' 또는 '-')"""
    runs = result["runs"]
    if len(names) != len(runs):
        raise ValueError(f"--names는 구간 {len(runs)}개에 하나씩(건너뛰기는 _), 받은 것 {len(names)}개")
    line_axis = "v" if result["axis"] == "u" else "u"
    ang_local = 0.0 if line_axis == "u" else 90.0
    items = {}
    for k, (name, r) in enumerate(zip(names, runs)):
        if name in ("_", "-"):
            continue
        if r["status"] == "unreadable":
            raise ValueError(f"구간 {k}({name})은 판독 불가({', '.join(r['flags'])})라 기록하지 않음")
        items[name] = {**base, "tool": "profile", "axis": line_axis, "coord": r["center"],
                       "angle_local_deg": ang_local, "angle_wcs_deg": _r(_angle180(frame.deg + ang_local), 4),
                       "point_wcs": r["center_wcs"], "width_px": r["width_px"], "tol_mm": result["tol_mm"],
                       "flags": r["flags"], "pick": k}
    return items


def fitline_observation(result, frame, name, base):
    if result["status"] not in ("ok", "partial"):
        raise ValueError(f"직선 맞춤이 {result['status']}라 기록하지 않음: {result.get('message', '')}")
    axis = "u" if abs(result["angle_local_deg"]) < 45 else "v"
    lu, lv = result["point_local"]
    wx, wy = frame.to_wcs(lu, lv)
    return {name: {**base, "tool": "fitline", "axis": axis, "coord": lv if axis == "u" else lu,
                   "angle_local_deg": result["angle_local_deg"], "angle_wcs_deg": result["angle_wcs_deg"],
                   "point_wcs": [_r(wx), _r(wy)], "width_px": None, "tol_mm": result["tol_mm"],
                   "flags": [] if result["status"] == "ok" else ["partial"], "pick": None}}


def _record(args, result, frame, sha):
    from tools.raster import record as rec
    calib_sha = hashlib.sha256(Path(args.calib).read_bytes()).hexdigest()
    base = {"kind": "line", "frame": [frame.x, frame.y, frame.deg], "command": canonical_command(args),
            "image_sha256": sha, "calib_sha256": calib_sha}
    if args.command == "profile":
        names = [n.strip() for n in (args.names or "").split(",")]
        items = profile_observations(result, frame, names, base)
    else:
        if not args.name:
            raise ValueError("--record에는 --name이 필요함")
        items = fitline_observation(result, frame, args.name, base)
    return rec.add_observations(args.record, items, sha, calib_sha)


def _load(args, need_calib=True):
    dark, sha = load_image(args.image)
    calib = Calib.load(args.calib, sha) if need_calib else None
    return dark, sha, calib


def rerun_observation(command, pick):
    """기록된 명령을 다시 실행해 같은 방식으로 관측을 만든다(record.py validate --reverify)"""
    args = _parser().parse_args(command)
    if args.command not in ("profile", "fitline") or getattr(args, "record", None):
        raise ValueError(f"재실행할 수 없는 명령: {command[:1]}")
    result = _run(args)
    frame = _frame(args.frame)
    base = {"kind": "line", "frame": [frame.x, frame.y, frame.deg], "command": list(command),
            "image_sha256": result["sha256"],
            "calib_sha256": hashlib.sha256(Path(args.calib).read_bytes()).hexdigest()}
    if args.command == "profile":
        names = ["_"] * len(result["runs"])
        if pick is None or not 0 <= pick < len(names):
            raise ValueError(f"재실행 결과에 구간 {pick}이 없음(구간 {len(names)}개)")
        names[pick] = "x"
        return profile_observations(result, frame, names, base)["x"]
    return fitline_observation(result, frame, "x", base)["x"]


def main(argv=None):
    args = _parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = _run(args)
    except (ValueError, OSError) as e:
        result = {"status": "error", "message": str(e)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") not in ("error", "rejected") else 1


def _parser():
    parser = argparse.ArgumentParser(description="평면 이미지 측정 도구(좌표 보정·직선·각도·프로파일·펴 보기)")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("calib", help="대응점으로 px↔mm 보정")
    c.add_argument("image")
    c.add_argument("--point", type=_pair, action="append", default=[], help="U,V=X,Y(이미지 px = WCS mm)")
    c.add_argument("--check", type=_pair, action="append", default=[], help="보정에 쓰지 않는 검증점")
    c.add_argument("-o", "--out", required=True)
    x = sub.add_parser("cross", help="두 px 직선의 교점")
    x.add_argument("--line", type=_line, action="append", required=True, help="U,V,DU,DV(fitline --px의 line_px)")
    f = sub.add_parser("fitline", help="띠 안 직선 맞춤")
    f.add_argument("image")
    f.add_argument("--calib")
    f.add_argument("--px", action="store_true", help="--from·--to·--band를 이미지 px로")
    f.add_argument("--frame")
    f.add_argument("--from", dest="p1", type=_xy, required=True)
    f.add_argument("--to", dest="p2", type=_xy, required=True)
    f.add_argument("--band", type=float, required=True)
    f.add_argument("--record", help="관측을 기록할 measure·영역 파일(--calib 필요)")
    f.add_argument("--name", help="기록할 관측 이름")
    a = sub.add_parser("angles", help="창 안 주된 선 각도")
    a.add_argument("image")
    a.add_argument("--calib", required=True)
    a.add_argument("--frame")
    a.add_argument("--window", nargs=4, type=float, required=True, metavar=("U0", "V0", "U1", "V1"))
    a.add_argument("--top", type=int, default=5)
    p = sub.add_parser("profile", help="단면선 어두움과 선 구간")
    p.add_argument("image")
    p.add_argument("--calib", required=True)
    p.add_argument("--frame")
    p.add_argument("--axis", choices=("u", "v"), required=True, help="단면선 진행 방향(지역 축)")
    p.add_argument("--at", type=float, required=True, help="다른 축의 값(mm)")
    p.add_argument("--range", nargs=2, type=float, required=True, metavar=("A", "B"))
    p.add_argument("--band", type=float, default=0.0, help="평균할 띠 폭(mm)")
    p.add_argument("--min-contrast", type=float, default=MIN_CONTRAST)
    p.add_argument("--max-tol", type=float, help="허용오차 상한 mm(기본 2 px)")
    p.add_argument("--plot", help="프로파일 그림 PNG")
    p.add_argument("--record", help="관측을 기록할 measure·영역 파일")
    p.add_argument("--names", help="구간마다 관측 이름(쉼표, 건너뛰기 _). 예: --names=s-out,_,s-in")
    v = sub.add_parser("view", help="지역 좌표로 펴 본 그림")
    v.add_argument("image")
    v.add_argument("--calib", required=True)
    v.add_argument("--frame")
    v.add_argument("--window", nargs=4, type=float, required=True, metavar=("U0", "V0", "U1", "V1"))
    v.add_argument("-o", "--out", required=True)
    v.add_argument("--mmpp", type=float)
    v.add_argument("--grid", type=float)
    return parser


def _run(args):
    if args.command == "cross":
        if len(args.line) != 2:
            raise ValueError("--line은 두 개")
        return cross(*args.line)
    if args.command == "calib":
        dark, sha = load_image(args.image)
        result = fit_calib(args.point, args.check)
        result = {"schema": CALIB_SCHEMA, "image": str(args.image), "sha256": sha,
                  "image_size": [dark.shape[1], dark.shape[0]], **result}
        if result["status"] == "ok":
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            result["written"] = str(args.out)
        return result
    if args.command == "fitline":
        if bool(args.px) == bool(args.calib):
            raise ValueError("--calib 또는 --px 중 하나")
        if args.record and args.px:
            raise ValueError("--record는 --calib(mm)일 때만")
        dark, sha, calib = _load(args, need_calib=not args.px)
        frame = _frame(args.frame)
        result = fit_line(dark, args.p1, args.p2, args.band, calib, frame)
        if args.record:
            result["recorded"] = _record(args, result, frame, sha)
        return {"image": str(args.image), "sha256": sha, **result}
    dark, sha, calib = _load(args)
    frame = _frame(args.frame)
    names = ("u", "v") if args.frame else ("x", "y")
    if args.command == "angles":
        result = angles(dark, calib, frame, args.window, args.top)
    elif args.command == "profile":
        result = profile(dark, calib, frame, args.axis, args.at, args.range[0], args.range[1], args.band,
                         min_contrast=args.min_contrast, max_tol=args.max_tol)
        if args.plot:
            result["plot"] = plot_profile(result, args.plot)
        result.pop("_curve", None)
        if args.record:
            result["recorded"] = _record(args, result, frame, sha)
    else:
        result = view(dark, calib, frame, args.window, args.out, args.mmpp, args.grid, names)
    return {"image": str(args.image), "sha256": sha, **result}


if __name__ == "__main__":
    sys.exit(main())
