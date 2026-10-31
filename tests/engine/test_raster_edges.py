"""빗금으로 채운 벽(띠)의 두 면선을 profile 가장자리로 재고 기록한다(tools/raster/measure.py --names=바깥/안쪽).

정답은 합성기 형상: 면선 y −150·+150(굵은선), 그 사이 45° 빗금. 기준: 가장자리 1 px 이내, 빗금 때문에 중심이 흔들려도 기록 가능.
"""
import json

import pytest

from tests.engine import raster_synth as rs
from tools.raster import measure as m
from tools.raster import record as rec
from tools.review.render import Frame

S = rs.S
PX = 1 / S
I = Frame(0, 0, 0)


def _hatched_wall(face=3.0, noise=0.03, blur=1.3):
    """사진 같은 빗금 벽: 굵은 면선 둘, 촘촘한 45° 빗금, 연필 번짐(블러)으로 한 덩어리가 된다"""
    from PIL import ImageFilter
    sc = rs.Scene((400, 200), S, origin=(60.0, 100.0))
    for y in (-150.0, 150.0):
        sc.line((-500, y), (5500, y), face)
    sc.hatch((-500, -150, 5500, 150), 45.0, 40.0, 1.5, 0.7)
    return sc.image(noise=noise, seed=2).filter(ImageFilter.GaussianBlur(blur)), sc


def _cal(sc):
    return m.Calib(S, 0.0, *sc.px(0, 0))


def test_a_hatched_wall_is_one_wide_run_whose_center_is_unreliable():
    img, sc = _hatched_wall()
    runs = m.profile(rs.dark_of(img), _cal(sc), I, "v", 2500, -500, 500, band=300)["runs"]
    assert len(runs) == 1 and runs[0]["width_px"] > 15


def test_profile_reports_band_edges_at_the_face_lines():
    img, sc = _hatched_wall()
    [run] = m.profile(rs.dark_of(img), _cal(sc), I, "v", 2500, -500, 500, band=300)["runs"]
    lo, hi = run["edges"]["start"], run["edges"]["end"]
    assert abs(lo["coord"] - (-150)) <= PX and abs(hi["coord"] - 150) <= PX
    assert lo["status"] == "ok" and hi["status"] == "ok"


def test_edges_are_recorded_as_two_face_observations_and_reverify(tmp_path, capsys):
    img, sc = _hatched_wall()
    image = tmp_path / "wall.png"
    img.save(image)
    calib = tmp_path / "calib.json"
    pts = []
    for x, y in ((0, -1000), (5000, -1000), (0, 1000)):
        u, v = sc.px(x, y)
        pts += ["--point", f"{u},{v}={x},{y}"]
    assert m.main(["calib", str(image), "-o", str(calib), *pts]) == 0
    measure = tmp_path / "measure.yaml"
    assert rec.main(["init", "--image", str(image), "--calib", str(calib), "--standard", "heo-2026@2026.1",
                     "--k", "100", "-o", str(measure)]) == 0
    capsys.readouterr()
    assert m.main(["profile", str(image), "--calib", str(calib), "--axis", "v", "--at", "2500", "--range", "-500",
                   "500", "--band", "300", "--record", str(measure), "--names=W-f1/W-f2"]) == 0
    obs = json.loads(json.dumps(rec.load(measure)["observations"]))
    assert obs["W-f1"]["edge"] == "start" and obs["W-f2"]["edge"] == "end"
    assert abs(obs["W-f1"]["coord"] + 150) <= PX and abs(obs["W-f2"]["coord"] - 150) <= PX
    capsys.readouterr()
    assert rec.main(["validate", str(measure), "--reverify"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == [] and report["reverified"] == 2


def test_a_single_edge_can_be_recorded_and_bad_names_are_refused(tmp_path, capsys):
    img, sc = _hatched_wall()
    D = rs.dark_of(img)
    res = m.profile(D, _cal(sc), I, "v", 2500, -500, 500, band=300)
    base = {"kind": "line", "frame": [0.0, 0.0, 0.0], "command": ["profile"], "image_sha256": "0" * 64,
            "calib_sha256": "0" * 64}
    items = m.profile_observations(res, I, ["_/W-f2"], base)
    assert list(items) == ["W-f2"] and items["W-f2"]["edge"] == "end"
    with pytest.raises(ValueError):
        m.profile_observations(res, I, ["a/b/c"], base)


@pytest.mark.parametrize("face", [2.0, 3.0, 4.0])
def test_edges_stay_finite_and_near_the_faces_for_any_line_weight(face):
    img, sc = _hatched_wall(face=face)
    runs = m.profile(rs.dark_of(img), _cal(sc), I, "v", 2500, -500, 500, band=300)["runs"]
    for r in runs:
        for e in r["edges"].values():
            assert e["coord"] is None or -500 - PX <= e["coord"] <= 500 + PX
    assert abs(runs[0]["edges"]["start"]["coord"] + 150) <= PX and abs(runs[-1]["edges"]["end"]["coord"] - 150) <= PX


def test_edges_do_not_reach_into_a_neighbouring_line():
    from PIL import ImageFilter
    sc = rs.Scene((400, 200), S, origin=(60.0, 100.0))
    for y in (-150.0, 150.0):
        sc.line((-500, y), (5500, y), 3.0)
    sc.hatch((-500, -150, 5500, 150), 45.0, 40.0, 1.5, 0.7)
    sc.line((-500, 280.0), (5500, 280.0), 2.0)                 # 바로 옆(130 mm) 다른 선
    img = sc.image(noise=0.03, seed=2).filter(ImageFilter.GaussianBlur(1.3))
    runs = m.profile(rs.dark_of(img), m.Calib(S, 0.0, *sc.px(0, 0)), I, "v", 2500, -500, 500, band=300)["runs"]
    wall = next(r for r in runs if r["start"] < 0 < r["end"])
    assert abs(wall["edges"]["end"]["coord"] - 150) <= PX
