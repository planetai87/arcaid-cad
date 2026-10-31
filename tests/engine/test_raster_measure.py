"""이미지 측정 도구(tools/raster/measure.py)를 합성 평면 이미지(tests/engine/raster_synth.py)로 확인한다.

정답은 합성기의 형상 정의와 별도 변환에서 온다. 기준: 이미지 전체 위치 오차 1 px 이하, fitline 각도 0.05° 이하,
angles 0.1° 이하, 면선 1 px 이하, 독립 검증점 1 px 이하. 빈 띠·간섭·기울어짐·거부 사례는 상태로 확인한다.
"""
import json
import math

import numpy as np
from PIL import Image
import pytest

from tests.engine import raster_synth as rs
from tools.raster import measure as m
from tools.review.render import Frame

PX_MM = 1 / rs.S            # 1 px = 16.7 mm
IDENT = Frame(0.0, 0.0, 0.0)
WING = Frame(*rs.WING)


def _axis(D, sc, p1, p2, band=8):
    """사람이 펴 본 그림에서 축선 근처를 고른 것처럼, 정답 px 근처 구간으로 축선을 맞춘다"""
    r = m.fit_line(D, sc.px(*p1), sc.px(*p2), band)
    assert r["status"] == "ok"
    return r["line_px"]


def _calib(beta, aniso=0.0):
    img, sc = rs.plan(beta=beta, aniso=aniso)
    D = rs.dark_of(img)
    X1 = _axis(D, sc, (0, -2500), (0, -900))
    X3 = _axis(D, sc, (9850, -2500), (9850, -900))
    Y1 = _axis(D, sc, (-2500, 0), (-900, 0))
    Y2 = _axis(D, sc, (-2500, 5700), (-900, 5700))
    pt = lambda a, b: tuple(m.cross(a, b)["point_px"])  # noqa: E731
    pairs = [(pt(X1, Y1), (0.0, 0.0)), (pt(X3, Y1), (9850.0, 0.0)), (pt(X1, Y2), (0.0, 5700.0))]
    checks = [(pt(X3, Y2), (9850.0, 5700.0))]
    return D, sc, m.fit_calib(pairs, checks)


def _cal(res):
    return m.Calib(res["px_per_mm"], res["beta_deg"], *res["origin_px"], res["err_mm"])


@pytest.mark.parametrize("beta", [0.0, 0.8])
def test_calib_recovers_transform_within_one_px(beta):
    D, sc, res = _calib(beta)
    assert res["status"] == "ok", res
    cal = _cal(res)
    worst = max(math.dist([float(c) for c in cal.to_px(x, y)], sc.px(x, y))
                for x in (-3000, 7000, 19000) for y in (-3000, 6000, 16000))
    assert worst <= 1.0
    assert abs(res["beta_deg"] - beta) <= 0.05
    assert res["checks"][0]["err_px"] <= 1.0
    assert res["err_mm"] >= res["checks"][0]["err_mm"] - 0.01      # 보정 오차에 검증점 오차가 들어간다
    assert res["affine"]["anisotropy_pct"] < 0.3
    # 왕복
    u, v = cal.to_px(1234.5, -678.9)
    assert np.allclose(cal.to_wcs(u, v), (1234.5, -678.9))


def test_calib_rejects_bad_inputs():
    img, sc = rs.plan()
    p = lambda x, y: (sc.px(x, y), (x, y))  # noqa: E731
    two = m.fit_calib([p(0, 0), p(9850, 0)])
    assert two["status"] == "rejected" and "3개 이상" in two["reasons"][0]
    close = m.fit_calib([p(0, 0), p(300, 0), p(0, 5700)])
    assert close["status"] == "rejected" and any("대응점 사이" in r for r in close["reasons"])
    line = m.fit_calib([p(0, 0), p(5000, 0), p(9850, 0)])
    assert line["status"] == "rejected" and any("한 직선" in r for r in line["reasons"])
    assert "독립 검증점" in m.fit_calib([p(0, 0), p(9850, 0), p(0, 5700)])["warnings"][0]


def test_calib_rejects_non_uniform_scaling():
    _, _, res = _calib(0.0, aniso=0.01)            # 가로만 1% 늘린 이미지
    assert res["status"] == "rejected"
    assert any("비균등" in r for r in res["reasons"])


@pytest.mark.parametrize("beta", [0.0, 0.8])
def test_angles_find_wing_and_main_directions(beta):
    D, sc, res = _calib(beta)
    cal = _cal(res)
    wing = m.angles(D, cal, WING, (-300, -300, 9500, 5500))["peaks"]
    assert abs(wing[0]["angle_wcs_deg"] - 23.76) <= 0.1
    assert abs(wing[0]["angle_local_deg"] % 180) <= 0.1 or abs(wing[0]["angle_local_deg"] - 180) <= 0.1
    assert any(abs(p["angle_wcs_deg"] - 113.76) <= 0.1 for p in wing[:3])
    main = [p["angle_wcs_deg"] for p in m.angles(D, cal, IDENT, (-1000, -1000, 10500, 6500))["peaks"][:2]]
    assert any(min(a, 180 - a) <= 0.1 for a in main) and any(abs(a - 90) <= 0.1 for a in main)


@pytest.mark.parametrize("beta", [0.0, 0.8])
def test_fitline_wing_face_angle_and_offset(beta):
    D, sc, res = _calib(beta)
    cal = _cal(res)
    r = m.fit_line(D, (1000, 0), (8000, 0), 100, cal, WING)
    assert r["status"] == "ok"
    assert abs(r["angle_wcs_deg"] - 23.76) <= 0.05
    assert abs(r["angle_local_deg"]) <= 0.05
    assert abs(r["point_local"][1]) <= PX_MM
    assert r["tol_mm"] == pytest.approx(cal.err_mm + 0.5 * PX_MM, abs=0.02)


@pytest.mark.parametrize("beta", [0.0, 0.8])
def test_profile_wall_faces_and_window_line_widths(beta):
    D, sc, res = _calib(beta)
    cal = _cal(res)
    wall = m.profile(D, cal, IDENT, "v", 5000, -400, 400, band=300)
    assert wall["status"] == "ok"
    centers = [r["center"] for r in wall["runs"]]
    assert len(centers) == 3                       # 남측 벽 면선 2개 + 축 Y1
    for got, want in zip(centers, (-100, 0, 100)):
        assert abs(got - want) <= PX_MM
    assert wall["runs"][0]["width_px"] > wall["runs"][1]["width_px"]
    win = m.profile(D, cal, IDENT, "v", 2500, 5500, 5950, band=300)
    by_center = {round(r["center"] / 50) * 50: r for r in win["runs"]}
    thin, thick = by_center[5650], by_center[5800]
    assert abs(thin["center"] - 5650) <= PX_MM and abs(thick["center"] - 5800) <= PX_MM
    assert thick["width_px"] >= 2.5 and thick["width_px"] > thin["width_px"]
    wing = m.profile(D, cal, WING, "v", 4500, -300, 500, band=300)
    faces = [r["center"] for r in wing["runs"] if r["width_px"] > 2.4]
    assert len(faces) == 2 and abs(faces[0]) <= PX_MM and abs(faces[1] - 200) <= PX_MM


def test_profile_flags_tilt_interference_double_and_empty():
    D, sc, res = _calib(0.0)
    cal = _cal(res)
    tilted = m.profile(D, cal, IDENT, "v", 3000, 7000, 9000, band=300)     # 회전동 벽을 정방향 단면으로
    assert tilted["status"] == "partial"
    assert "tilted" in tilted["runs"][0]["flags"] and abs(tilted["runs"][0]["tilt_deg"] - 23.76) < 1.0
    text = m.profile(D, cal, IDENT, "u", 2100, -400, 400, band=400)        # 문자 획이 띠 일부에 걸침
    assert text["status"] == "partial"
    assert any("inconsistent" in r["flags"] for r in text["runs"])
    pair = m.profile(D, cal, IDENT, "u", 1500, 12900, 13200, band=300)    # 중심 간격 4 px → 두 선
    assert [abs(r["center"] - c) <= PX_MM for r, c in zip(pair["runs"], (13000, 13000 + 4 * PX_MM))] == [True, True]
    close = m.profile(D, cal, IDENT, "u", 1500, 11900, 12150, band=300)   # 중심 간격 2 px
    assert len(close["runs"]) == 2 or close["runs"][0]["width_px"] >= 3.5 or "double" in close["runs"][0]["flags"]
    empty = m.profile(D, cal, IDENT, "u", 1500, 15000, 17000, band=300)
    assert empty["status"] == "empty" and empty["runs"] == []
    budget = m.profile(D, cal, IDENT, "v", 5000, -400, 400, band=300, max_tol=1.0)
    assert all("budget" in r["flags"] and r["status"] == "unreadable" for r in budget["runs"])


def test_profile_runs_never_overlap_when_lines_touch():
    sc = rs.Scene((200, 120), rs.S, origin=(100.0, 100.0))
    sc.line((0, -1000), (0, 1000), rs.THICK, 1.0)                 # 진한 굵은선
    sc.line((3 * PX_MM, -1000), (3 * PX_MM, 1000), 2.0, 0.45)       # 맞닿은 옅은 선(지상6층 코어 벽에서 겹침이 났던 경우)
    D = rs.dark_of(sc.image())
    cal = m.Calib(rs.S, 0.0, *sc.px(0, 0))
    runs = m.profile(D, cal, IDENT, "u", 0, -300, 300, band=200)["runs"]
    for a, b in zip(runs, runs[1:]):
        assert a["end"] <= b["start"] + 1e-6
    assert abs(runs[0]["center"]) <= PX_MM
    assert len(runs) == 1 or "double" in runs[1]["flags"]


def test_view_unwarps_local_frame(tmp_path):
    D, sc, res = _calib(0.8)
    cal = _cal(res)
    out = m.view(D, cal, WING, (0, -1000, 9000, 1000), tmp_path / "wing.png", mmpp=10)
    assert out["size"] == [900, 200]
    g = np.asarray(Image.open(out["png"]).convert("L"), dtype=float)
    row = lambda v: int((1000 - v) / 10)  # noqa: E731
    col = slice(100, 800)
    assert g[row(0), col].mean() < 120 and g[row(200), col].mean() < 120      # 면선 v 0·200이 수평으로 펴짐
    assert g[row(-500), col].mean() > 200


def test_cli_calib_file_profile_plot_and_sha_guard(tmp_path, capsys):
    img, sc = rs.plan(beta=0.8)
    path = tmp_path / "plan.png"
    img.save(path)
    _, _, res = _calib(0.8)
    args = ["calib", str(path), "-o", str(tmp_path / "calib.json")]
    for r in res["residuals"]:
        args += ["--point", f"{r['px'][0]},{r['px'][1]}={r['wcs'][0]},{r['wcs'][1]}"]
    for c in res["checks"]:
        args += ["--check", f"{c['px'][0]},{c['px'][1]}={c['wcs'][0]},{c['wcs'][1]}"]
    assert m.main(args) == 0
    calib = json.loads(capsys.readouterr().out)
    assert calib["status"] == "ok" and calib["written"].endswith("calib.json")
    rc = m.main(["profile", str(path), "--calib", str(tmp_path / "calib.json"), "--axis", "v", "--at", "5000",
                 "--range", "-400", "400", "--band", "300", "--plot", str(tmp_path / "p.png")])
    out = capsys.readouterr().out
    assert rc == 0 and out.count("\n") == 1
    prof = json.loads(out)
    assert prof["status"] == "ok" and len(prof["runs"]) == 3 and (tmp_path / "p.png").exists()
    assert "_curve" not in prof
    other = tmp_path / "other.png"
    Image.new("L", (50, 50), 255).save(other)
    assert m.main(["view", str(other), "--calib", str(tmp_path / "calib.json"), "--window", "0", "0", "100", "100",
                   "-o", str(tmp_path / "v.png")]) == 1
    assert "sha256" in json.loads(capsys.readouterr().out)["message"]
    assert m.main(["calib", str(path), "-o", str(tmp_path / "bad.json"), "--point", "1,1=0,0"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "rejected" and not (tmp_path / "bad.json").exists()


def test_cross_rejects_parallel_lines():
    with pytest.raises(ValueError):
        m.cross([0, 0, 1, 0], [0, 5, 1, 0.01])


# ---------- 점 관측(blob) ----------

def _near(got, want, tol):
    return all(abs(g - w) <= tol for g, w in zip(got, want))


@pytest.mark.parametrize("beta, shift", [(0.0, 0.0), (0.8, 0.0), (0.0, 0.37), (0.8, 0.71)])
def test_blob_finds_circle_center_and_box_extent(beta, shift):
    D, sc, res = _calib(beta)
    cal = _cal(res)
    tol = PX_MM + res["err_mm"]
    cx, cy, r = rs.CIRCLE
    cx_w, cy_w = cx + shift * PX_MM, cy + shift * PX_MM       # 표본 격자를 서브픽셀만큼 옮긴다
    ring = m.blob(D, cal, IDENT, (cx_w - 700, cy_w - 700, cx_w + 700, cy_w + 700))
    assert ring["status"] == "ok" and ring["flags"] == []
    assert _near(ring["point_local"], (cx, cy), tol)
    half = r + rs.THICK / 2 * PX_MM
    assert _near(ring["bbox_local"], (cx - half, cy - half, cx + half, cy + half), tol)
    x0, y0, x1, y1 = rs.BOX
    d = shift * PX_MM
    box = m.blob(D, cal, IDENT, (x0 - 300 + d, y0 - 300 + d, x1 + 300 + d, y1 + 300 + d))
    h = rs.THIN / 2 * PX_MM
    assert _near(box["bbox_local"], (x0 - h, y0 - h, x1 + h, y1 + h), tol)
    assert _near(box["point_local"], ((x0 + x1) / 2, (y0 + y1) / 2), tol)
    assert _near(box["point_wcs"], box["point_local"], 1e-6)
    assert box["tol_mm"] == pytest.approx(cal.err_mm + 1 / cal.s, abs=0.02)


def test_blob_in_rotated_frame_reports_local_box():
    D, sc, res = _calib(0.8)
    cal = _cal(res)
    x0, y0, x1, y1 = rs.WING_BOX
    r = m.blob(D, cal, WING, (x0 - 200, y0 - 200, x1 + 200, y1 + 200))
    h = rs.THIN / 2 * PX_MM
    tol = PX_MM + res["err_mm"]
    assert r["status"] == "ok"
    assert _near(r["bbox_local"], (x0 - h, y0 - h, x1 + h, y1 + h), tol)
    assert _near(r["point_wcs"], WING.to_wcs((x0 + x1) / 2, (y0 + y1) / 2), tol)


def test_blob_rejects_empty_truncated_outside_and_over_budget():
    D, sc, res = _calib(0.0)
    cal = _cal(res)
    assert m.blob(D, cal, IDENT, (17000, 12000, 18000, 13000))["status"] == "empty"
    cut = m.blob(D, cal, IDENT, (14800, 10800, 15200, 11800))      # 사각형 가운데만
    assert cut["status"] == "unreadable" and "truncated" in cut["flags"]
    assert m.blob(D, cal, IDENT, (18000, 12000, 21000, 13000))["status"] == "outside"
    x0, y0, x1, y1 = rs.BOX
    budget = m.blob(D, cal, IDENT, (x0 - 300, y0 - 300, x1 + 300, y1 + 300), max_tol=1.0)
    assert budget["status"] == "unreadable" and "budget" in budget["flags"]
