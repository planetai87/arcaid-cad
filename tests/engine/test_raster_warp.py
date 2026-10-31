"""사진용 보정(휨·원근, tools/raster/measure.py calib --vline·--hline·--warp)을 합성 왜곡 장면으로 확인한다.

정답 변환은 합성기 Scene.px(닮음 + 사다리꼴 + 휨)이다. 기준: 다항식 보정의 검증점·격자점 오차 1 px 이하, 왕복 0.01 px 이하.
"""
import json
import math

import numpy as np
import pytest

from tests.engine import raster_synth as rs
from tools.raster import measure as m

S = rs.S
WARP = (0.024, 0.01)
XS, YS = (0.0, 4000.0, 8000.0, 12000.0), (0.0, 5000.0, 10000.0)


def _scene(warp=WARP):
    """축선 격자(세로 4·가로 3)와 벽 면선 한 쌍(y 2400·2600)"""
    sc = rs.Scene((1000, 760), S, 0.8, (90.0, 680.0), warp=warp)
    for x in XS:
        sc.line((x, -800), (x, 10600), rs.THIN, 0.8)
    for y in YS:
        sc.line((-800, y), (12600, y), rs.THIN, 0.8)
    for y in (2400.0, 2600.0):
        sc.line((1000, y), (11000, y), rs.THICK)
    return sc.image(noise=0.02, seed=3), sc


def _segments(sc):
    """사람이 준다고 보는 대략의 선 구간(px 끝점)"""
    v = [(sc.px(x, -700) + sc.px(x, 10500), x) for x in XS]
    h = [(sc.px(-700, y) + sc.px(12500, y), y) for y in YS]
    return v, h


def _poly(img, sc, degree=2):
    D = rs.dark_of(img)
    v, h = _segments(sc)
    pairs = [(sc.px(x, y), (x, y)) for x, y in ((0, 0), (12000, 0), (0, 10000))]
    checks = [(sc.px(x, y), (x, y)) for x, y in ((12000, 10000), (8000, 5000))]
    vlines = [(m.line_samples(D, seg, 12), x) for seg, x in v]
    hlines = [(m.line_samples(D, seg, 12), y) for seg, y in h]
    return D, m.fit_calib(pairs, checks, vlines=vlines, hlines=hlines, degree=degree)


def test_a_similarity_calibration_misses_a_warped_photo():
    img, sc = _scene()
    pairs = [(sc.px(x, y), (x, y)) for x, y in ((0, 0), (12000, 0), (0, 10000))]
    res = m.fit_calib(pairs, [(sc.px(12000, 10000), (12000, 10000))])
    assert res["checks"][0]["err_px"] > 3.0


def test_line_samples_follow_a_bent_line():
    img, sc = _scene()
    D = rs.dark_of(img)
    seg = sc.px(4000, -700) + sc.px(4000, 10500)
    pts = m.line_samples(D, seg, 12)
    assert len(pts) >= 10
    for u, v in pts:                                      # 표본점은 휜 선 위(정답 X 4000 근처)
        ys = np.linspace(-700, 10500, 2000)
        d = min(math.dist((u, v), sc.px(4000, y)) for y in ys)
        assert d <= 1.0


@pytest.mark.parametrize("warp", [WARP, None])
def test_a_poly_calibration_from_lines_recovers_the_photo_within_a_pixel(warp):
    img, sc = _scene(warp)
    D, res = _poly(img, sc)
    assert res["status"] == "ok", res
    assert res["model"] == "poly" and all(c["err_px"] <= 1.0 for c in res["checks"])
    cal = m.Calib.from_result(res)
    worst = max(math.dist([float(c) for c in cal.to_px(x, y)], sc.px(x, y))
                for x in np.linspace(-500, 12500, 9) for y in np.linspace(-500, 10500, 9))
    assert worst <= 1.0
    x, y = cal.to_wcs(*cal.to_px(np.array([123.4, 11000.0]), np.array([-300.0, 9800.0])))
    assert np.allclose(x, [123.4, 11000.0], atol=0.01 / S) and np.allclose(y, [-300.0, 9800.0], atol=0.01 / S)


def test_profile_finds_wall_faces_in_a_warped_corner_with_the_poly_calibration():
    img, sc = _scene()
    D, res = _poly(img, sc)
    cal = m.Calib.from_result(res)
    from tools.review.render import Frame
    runs = m.profile(D, cal, Frame(0, 0, 0), "v", 10500, 2200, 2800, band=300)["runs"]
    faces = [r["center"] for r in runs if r["width_px"] > 2.4]
    assert len(faces) == 2 and abs(faces[0] - 2400) <= 1 / S and abs(faces[1] - 2600) <= 1 / S


def test_a_poly_calibration_needs_both_directions_constrained():
    img, sc = _scene()
    D = rs.dark_of(img)
    v, _ = _segments(sc)
    pairs = [(sc.px(x, y), (x, y)) for x, y in ((0, 0), (12000, 0), (0, 10000))]
    res = m.fit_calib(pairs, vlines=[(m.line_samples(D, seg, 12), x) for seg, x in v], degree=3)
    assert res["status"] == "rejected" and any("가로" in r for r in res["reasons"])


def test_cli_writes_a_poly_calibration_that_profile_reads(tmp_path, capsys):
    img, sc = _scene()
    path = tmp_path / "photo.png"
    img.save(path)
    v, h = _segments(sc)
    args = ["calib", str(path), "-o", str(tmp_path / "calib.json"), "--warp", "2", "--band", "12"]
    for x, y in ((0, 0), (12000, 0), (0, 10000)):
        u, vv = sc.px(x, y)
        args += ["--point", f"{u},{vv}={x},{y}"]
    u, vv = sc.px(12000, 10000)
    args += ["--check", f"{u},{vv}=12000,10000"]
    for seg, x in v:
        args += ["--vline", ",".join(f"{c:.2f}" for c in seg) + f"={x}"]
    for seg, y in h:
        args += ["--hline", ",".join(f"{c:.2f}" for c in seg) + f"={y}"]
    assert m.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["model"] == "poly"
    saved = json.loads((tmp_path / "calib.json").read_text(encoding="utf-8"))
    assert saved["warp"]["degree"] == 2
    assert m.main(["profile", str(path), "--calib", str(tmp_path / "calib.json"), "--axis", "v", "--at", "10500",
                   "--range", "2200", "2800", "--band", "300"]) == 0
    runs = json.loads(capsys.readouterr().out)["runs"]
    assert any(abs(r["center"] - 2600) <= 1 / S for r in runs)


def test_overlay_warps_a_poly_calibrated_photo_onto_the_view(tmp_path):
    from PIL import Image
    from tools.review import render as rv
    img, sc = _scene()
    D, res = _poly(img, sc)
    path = tmp_path / "photo.png"
    img.save(path)
    from tools.raster.measure import load_image
    res = {**res, "schema": m.CALIB_SCHEMA, "sha256": load_image(path)[1]}
    (tmp_path / "calib.json").write_text(json.dumps(res), encoding="utf-8")
    cal = rv.load_calib(tmp_path / "calib.json", path)
    for x, y in ((0, 0), (12000, 10000), (11000, 2600), (-500, 10500)):
        assert math.dist(cal.to_px(x, y), sc.px(x, y)) <= 1.0
    ramp = Image.new("F", (1000, 760))
    ramp.putdata([i % 1000 + 0.5 for i in range(1000 * 760)])
    view = rv.View([0.0, 0.0, 12000.0, 10000.0], scale=0.05)
    out = rv.warp(ramp, view, cal, fill=-1.0).load()
    for i, j in ((5, 5), (300, 100), (590, 490), (100, 400)):
        x, y = view.to_wcs((i + 0.5, j + 0.5))
        assert abs(out[i, j] - sc.px(x, y)[0]) <= 0.6


def test_a_cubic_needs_four_distinct_lines_each_way():
    img, sc = _scene()
    D, res = _poly(img, sc, degree=3)                     # 가로선이 Y 0·5000·10000 세 줄뿐
    assert res["status"] == "rejected" and any("가로" in r and "4개" in r for r in res["reasons"])


def test_fitline_follows_a_long_wall_face_on_a_bent_photo():
    img, sc = _scene()
    D, res = _poly(img, sc)
    cal = m.Calib.from_result(res)
    from tools.review.render import Frame
    r = m.fit_line(D, (1500, 2600), (10500, 2600), 100, cal, Frame(0, 0, 0))
    assert r["status"] == "ok"
    assert abs(r["angle_local_deg"]) <= 0.05 and abs(r["point_local"][1] - 2600) <= 1 / S


def test_angles_reads_directions_in_the_corrected_frame():
    img, sc = _scene()
    D, res = _poly(img, sc)
    cal = m.Calib.from_result(res)
    from tools.review.render import Frame
    peaks = [p["angle_wcs_deg"] for p in m.angles(D, cal, Frame(0, 0, 0), (1000, 6000, 12500, 10500))["peaks"][:2]]
    assert any(min(a, 180 - a) <= 0.1 for a in peaks) and any(abs(a - 90) <= 0.1 for a in peaks)


def test_mesh_warp_matches_the_photo_everywhere_including_cell_seams_and_rotated_views():
    from PIL import Image
    from tools.review import render as rv
    img, sc = _scene()
    D, res = _poly(img, sc)
    cal = rv.ImageCalib(res)
    w, h = 1000, 760
    ramps = [Image.fromarray(np.tile(np.arange(w) + 0.5, (h, 1)).astype(np.float32), "F"),
             Image.fromarray(np.tile((np.arange(h) + 0.5)[:, None], (1, w)).astype(np.float32), "F")]
    for frame, window, scale in ((None, [0.0, 0.0, 12000.0, 10000.0], 0.05),
                                 (rv.Frame(3000, 2000, 23.76), [0.0, 0.0, 6000.0, 4000.0], 0.11)):
        view = rv.View(window, frame=frame, scale=scale)
        ou, ov = (np.asarray(rv.warp(r, view, cal, fill=-1.0)) for r in ramps)
        worst = 0.0
        for j in range(0, view.size[1], 7):
            for i in range(0, view.size[0], 7):
                tu, tv = sc.px(*view.to_wcs((i + 0.5, j + 0.5)))
                if 1 < tu < w - 1 and 1 < tv < h - 1:
                    worst = max(worst, math.hypot(ou[j, i] - tu, ov[j, i] - tv))
        assert worst <= 0.2


def test_overlay_compare_finds_nothing_on_a_matching_warped_photo(tmp_path):
    import ezdxf
    from tools.raster.measure import load_image
    from tools.review import render as rv
    img, sc = _scene()
    D, res = _poly(img, sc)
    path = tmp_path / "photo.png"
    img.save(path)
    (tmp_path / "calib.json").write_text(json.dumps({**res, "schema": m.CALIB_SCHEMA, "sha256": load_image(path)[1]}),
                                         encoding="utf-8")
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    for x in XS:
        msp.add_line((x, -800), (x, 10600))
    for y in YS:
        msp.add_line((-800, y), (12600, y))
    for y in (2400.0, 2600.0):
        msp.add_line((1000, y), (11000, y))
    doc.saveas(tmp_path / "same.dxf")
    out = rv.overlay(tmp_path / "same.dxf", path, tmp_path / "calib.json", tmp_path / "o.png",
                     [-500.0, -500.0, 12500.0, 10500.0], scale=0.12, grid=0, compare=True)
    assert out["only_image"] == [] and out["only_dxf"] == []


def test_a_poly_calibration_supports_record_and_reverify(tmp_path, capsys):
    from tools.raster import record as rec
    img, sc = _scene()
    D, res = _poly(img, sc)
    path = tmp_path / "photo.png"
    img.save(path)
    from tools.raster.measure import load_image
    (tmp_path / "calib.json").write_text(json.dumps({**res, "schema": m.CALIB_SCHEMA, "sha256": load_image(path)[1]}),
                                         encoding="utf-8")
    measure = tmp_path / "measure.yaml"
    assert rec.main(["init", "--image", str(path), "--calib", str(tmp_path / "calib.json"), "--standard",
                     "heo-2026@2026.1", "--k", "100", "-o", str(measure)]) == 0
    assert m.main(["profile", str(path), "--calib", str(tmp_path / "calib.json"), "--axis", "v", "--at", "10500",
                   "--range", "2200", "2800", "--band", "300", "--record", str(measure), "--names=f1,f2"]) == 0
    capsys.readouterr()
    assert rec.main(["validate", str(measure), "--reverify"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == [] and report["reverified"] == 2
