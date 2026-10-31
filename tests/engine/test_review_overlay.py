"""원본 겹침(tools/review/render.py overlay)과 공용 변환 View를 합성 이미지·합성 DXF로 확인한다(CAD 없음).

정답 좌표는 합성기(tests/engine/raster_synth.py Scene.px)와 시험 안의 공식에서 온다(도구의 변환을 쓰지 않음).
"""
import json
import math
import random

import ezdxf
from PIL import Image
import pytest

from tests.engine import raster_synth as rs
from tools.review import render as rv

WING = rv.Frame(*rs.WING)


# ---------- View ----------

def test_view_without_frame_matches_the_window_formula_exactly():
    view = rv.View([-500.0, -300.0, 4500.0, 3000.0], scale=0.137)
    rnd = random.Random(3)
    for _ in range(200):
        p = (rnd.uniform(-1000, 5000), rnd.uniform(-1000, 4000))
        assert view.to_px(p) == ((p[0] - -500.0) * 0.137, (3000.0 - p[1]) * 0.137)
    assert view.size == (round(5000 * 0.137), round(3300 * 0.137))


def test_view_with_a_frame_unwarps_local_coordinates():
    view = rv.View([0.0, -1000.0, 9000.0, 1000.0], frame=WING, scale=0.1)
    assert view.size == (900, 200)
    assert view.to_px(WING.to_wcs(0.0, 1000.0)) == pytest.approx((0.0, 0.0), abs=1e-9)
    assert view.to_px(WING.to_wcs(9000.0, -1000.0)) == pytest.approx((900.0, 200.0), abs=1e-9)
    for px in ((0.0, 0.0), (123.4, 56.7), (900.0, 200.0)):
        assert view.to_px(view.to_wcs(px)) == pytest.approx(px, abs=1e-9)


def test_view_default_scale_keeps_the_long_side_at_2400_px():
    assert max(rv.View([0.0, 0.0, 12000.0, 3000.0]).size) == 2400


# ---------- render 회귀(형상·색 마스크) ----------

def _shapes(path, moved=False):
    doc = ezdxf.new("R2013")
    for name in ("WAL", "DOR"):
        doc.layers.add(name)
    msp = doc.modelspace()
    msp.add_line((0, 0), (4000, 0), dxfattribs={"layer": "WAL"})
    msp.add_line((0, 2600), (4000, 2600), dxfattribs={"layer": "WAL"})
    msp.add_circle((3000 if moved else 1000, 1500), 300, dxfattribs={"layer": "DOR"})
    msp.add_line((20000, 0), (21000, 0), dxfattribs={"layer": "WAL"})             # 창 밖
    doc.saveas(path)
    return path


def _colored(img, x, y, color, view, r=2):
    u, v = view.to_px((x, y))
    px = img.load()
    return any(px[int(u) + dx, int(v) + dy] == color for dx in range(-r, r + 1) for dy in range(-r, r + 1)
               if 0 <= int(u) + dx < img.size[0] and 0 <= int(v) + dy < img.size[1])


def test_render_draws_shapes_where_the_view_puts_them(tmp_path):
    new = _shapes(tmp_path / "new.dxf")
    window = [-500.0, -500.0, 4500.4, 3000.0]
    out = rv.render(new, tmp_path / "a.png", window, scale=0.1, grid=0)
    view = rv.View(window, scale=0.1)
    assert out["size"] == list(view.size) == [500, 350]
    img = Image.open(tmp_path / "a.png").convert("RGB")
    for x, y in ((100, 0), (2000, 0), (3900, 2600), (1300, 1500), (1000, 1800)):
        assert _colored(img, x, y, rv.BLACK[:3], view), (x, y)
    assert not _colored(img, 2000, 1000, rv.BLACK[:3], view, r=5)                  # 빈 곳


def test_render_colors_common_added_and_removed_by_the_view(tmp_path):
    base, new = _shapes(tmp_path / "base.dxf"), _shapes(tmp_path / "new.dxf", moved=True)
    window = [-500.0, -500.0, 4500.0, 3000.0]
    rv.render(new, tmp_path / "b.png", window, base_path=base, scale=0.2, grid=0)
    view = rv.View(window, scale=0.2)
    img = Image.open(tmp_path / "b.png").convert("RGB")
    assert _colored(img, 2000, 0, rv.GRAY[:3], view)
    assert _colored(img, 3300, 1500, rv.RED[:3], view) and not _colored(img, 3300, 1500, rv.BLUE[:3], view)
    assert _colored(img, 700, 1500, rv.BLUE[:3], view) and not _colored(img, 700, 1500, rv.RED[:3], view)


# ---------- 보정 파일·이미지 변환 ----------

def _calib_file(tmp_path, sc, image_path, **over):
    from tools.raster.measure import CALIB_SCHEMA, load_image
    _, sha = load_image(image_path)
    data = {"schema": CALIB_SCHEMA, "status": "ok", "sha256": sha, "px_per_mm": sc.s, "beta_deg": sc.beta,
            "origin_px": [sc.u0, sc.v0], "err_mm": 2.0, **over}
    path = tmp_path / "calib.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_warp_samples_the_image_at_the_view_pixel_centers(tmp_path):
    """F 모드 램프(픽셀 i의 값 = 중심 좌표 i+0.5): 쌍선형 보간이 정확하므로 출력 값 = 대응 이미지 좌표"""
    sc = rs.Scene((400, 300), 0.05, beta_deg=0.8, origin=(37.3, 251.9))
    ramp_u = Image.new("F", (400, 300))
    ramp_u.putdata([i % 400 + 0.5 for i in range(400 * 300)])
    path = tmp_path / "img.png"
    Image.new("L", (400, 300), 255).save(path)
    cal = rv.load_calib(_calib_file(tmp_path, sc, path), path)
    view = rv.View([500.0, -1500.0, 3500.0, 1200.0], frame=rv.Frame(1000.0, 1000.0, 23.76), scale=0.031)
    out = rv.warp(ramp_u, view, cal, fill=-1.0)
    px = out.load()
    checked = 0
    for i, j in ((0, 0), (40, 30), (92, 5), (12, 80), (60, 60)):
        x, y = view.to_wcs((i + 0.5, j + 0.5))
        u, v = sc.px(x, y)                                     # 합성기의 정답 변환
        if 1 <= u <= 399 and 1 <= v <= 299:
            assert px[i, j] == pytest.approx(u, abs=1e-3)
            checked += 1
    assert checked >= 4


def test_load_calib_refuses_another_image_or_a_rejected_calibration(tmp_path):
    sc = rs.Scene((50, 50), 0.05)
    path = tmp_path / "img.png"
    Image.new("L", (50, 50), 255).save(path)
    other = tmp_path / "other.png"
    Image.new("L", (50, 50), 0).save(other)
    good = _calib_file(tmp_path, sc, path)
    with pytest.raises(ValueError, match="sha256"):
        rv.load_calib(good, other)
    bad = _calib_file(tmp_path, sc, path, status="rejected")
    with pytest.raises(ValueError, match="거부"):
        rv.load_calib(bad, path)


# ---------- overlay·compare ----------

def _plan_dxf(path, drop=None, move=None, extra=None):
    """rs.plan과 같은 축·벽 면선(남·서·동). drop: 뺄 남측 면선 y, move: (y, 새 y), extra: 더할 가로선 y"""
    doc = ezdxf.new("R2013")
    for name in ("CON", "CEN"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for x in rs.GRID_X.values():
        msp.add_line((x, -2500), (x, 14000), dxfattribs={"layer": "CEN"})
    for y in rs.GRID_Y.values():
        msp.add_line((-2500, y), (12500, y), dxfattribs={"layer": "CEN"})
    for y in (-100.0, 100.0):
        if y == drop:
            continue
        yy = move[1] if move and move[0] == y else y
        msp.add_line((-100, yy), (9950, yy), dxfattribs={"layer": "CON"})
    for x in (-100.0, 100.0, 9750.0, 9950.0):
        msp.add_line((x, -100), (x, 5800), dxfattribs={"layer": "CON"})
    if extra is not None:
        msp.add_line((5000, extra), (8500, extra), dxfattribs={"layer": "CON"})
    doc.saveas(path)
    return path


@pytest.fixture
def plan(tmp_path):
    img, sc = rs.plan(beta=0.8)
    path = tmp_path / "plan.png"
    img.save(path)
    return {"image": path, "calib": _calib_file(tmp_path, sc, path), "sc": sc, "dir": tmp_path}


ROI = [4000.0, -800.0, 9000.0, 800.0]          # 남측 벽 가운데(문자·해치 간섭 없음, 축 X2·Y1 지남)


def test_overlay_puts_dxf_lines_on_the_original_lines(plan):
    dxf = _plan_dxf(plan["dir"] / "same.dxf")
    out = rv.overlay(dxf, plan["image"], plan["calib"], plan["dir"] / "o.png", ROI, scale=0.24, grid=0)
    view = rv.View(ROI, scale=0.24)
    img = Image.open(out["png"]).convert("RGB")
    assert _colored(img, 6000, 100, rv.RED[:3], view) and _colored(img, 6000, -100, rv.RED[:3], view)
    bg = rv.warp(Image.open(plan["image"]).convert("L"), view, rv.load_calib(plan["calib"], plan["image"]), fill=255)
    u, v = view.to_px((6000, 100))
    assert bg.getpixel((int(u), int(v))) < 120                                    # 원본 면선이 같은 px
    assert out["outside_pct"] == 0


def test_overlay_unwarps_a_rotated_frame(plan):
    dxf = plan["dir"] / "wing.dxf"
    doc = ezdxf.new("R2013")
    doc.layers.add("CON")
    doc.layers.add("CEN")
    for x in rs.GRID_X.values():                                                  # 축 X2가 창을 지난다
        doc.modelspace().add_line((x, -2500), (x, 14000), dxfattribs={"layer": "CEN"})
    for v in (0.0, 200.0):
        doc.modelspace().add_line(WING.to_wcs(0, v), WING.to_wcs(9000, v), dxfattribs={"layer": "CON"})
    doc.saveas(dxf)
    window = [1000.0, -500.0, 8000.0, 700.0]
    out = rv.overlay(dxf, plan["image"], plan["calib"], plan["dir"] / "w.png", window, frame=WING, scale=0.2,
                     grid=0, compare=True)
    view = rv.View(window, frame=WING, scale=0.2)
    img = Image.open(out["png"]).convert("RGB")
    for u in (2000.0, 6000.0):
        _, row = view.to_px(WING.to_wcs(u, 200.0))
        assert row == pytest.approx((700.0 - 200.0) * 0.2)                         # 지역 v가 그림의 가로줄
        assert _colored(img, *WING.to_wcs(u, 200.0), rv.GRAY[:3], view)
    assert out["only_image"] == [] and out["only_dxf"] == []


@pytest.mark.parametrize("scale", [0.12, 0.48])
def test_compare_finds_nothing_when_the_dxf_matches(plan, scale):
    out = rv.overlay(_plan_dxf(plan["dir"] / "same.dxf"), plan["image"], plan["calib"], plan["dir"] / "c.png", ROI,
                     scale=scale, grid=0, compare=True)
    assert out["only_image"] == [] and out["only_dxf"] == []
    assert out["counts"]["dxf_px"] > 0 and out["counts"]["image_px"] > 0


def _covers(region, x0, y0, x1, y1):
    w = region["window"]
    return w[0] <= x0 and w[1] <= y0 and w[2] >= x1 and w[3] >= y1


@pytest.mark.parametrize("scale", [0.12, 0.48])
def test_compare_finds_a_missing_line(plan, scale):
    out = rv.overlay(_plan_dxf(plan["dir"] / "drop.dxf", drop=100.0), plan["image"], plan["calib"],
                     plan["dir"] / "d.png", ROI, scale=scale, grid=0, compare=True)
    assert out["only_dxf"] == []
    assert len(out["only_image"]) == 1 and _covers(out["only_image"][0], 4500, 90, 8500, 110)


@pytest.mark.parametrize("scale", [0.12, 0.48])
def test_compare_finds_an_extra_line(plan, scale):
    out = rv.overlay(_plan_dxf(plan["dir"] / "extra.dxf", extra=500.0), plan["image"], plan["calib"],
                     plan["dir"] / "e.png", ROI, scale=scale, grid=0, compare=True)
    assert out["only_image"] == []
    assert len(out["only_dxf"]) == 1 and _covers(out["only_dxf"][0], 5100, 500, 8400, 500)   # 1 px 선


def test_compare_finds_a_line_moved_by_150_mm_on_both_sides(plan):
    out = rv.overlay(_plan_dxf(plan["dir"] / "move.dxf", move=(100.0, 250.0)), plan["image"], plan["calib"],
                     plan["dir"] / "m.png", ROI, scale=0.24, grid=0, compare=True)
    assert any(_covers(r, 4500, 90, 8500, 110) for r in out["only_image"])
    assert any(_covers(r, 4500, 250, 8500, 250) for r in out["only_dxf"])
    assert math.isclose(out["tol_mm"], 2.0 + 2 / plan["sc"].s, abs_tol=0.01)


def test_compare_ignores_dxf_outside_the_image(plan):
    window = [17000.0, 10000.0, 21000.0, 13000.0]                                 # 이미지 오른쪽 끝에 걸침
    dxf = plan["dir"] / "out.dxf"
    doc = ezdxf.new("R2013")
    doc.layers.add("CON")
    doc.modelspace().add_line((20000, 11000), (20900, 11000), dxfattribs={"layer": "CON"})   # 이미지 밖
    doc.saveas(dxf)
    out = rv.overlay(dxf, plan["image"], plan["calib"], plan["dir"] / "x.png", window, scale=0.2, grid=0,
                     compare=True)
    assert 0 < out["outside_pct"] < 100 and out["only_dxf"] == []
    with pytest.raises(ValueError, match="밖"):
        rv.overlay(dxf, plan["image"], plan["calib"], plan["dir"] / "y.png", [30000.0, 30000.0, 31000.0, 31000.0])


def test_cli_overlay_prints_one_json(plan, capsys):
    dxf = _plan_dxf(plan["dir"] / "same.dxf")
    rc = rv.main(["overlay", str(dxf), "--image", str(plan["image"]), "--calib", str(plan["calib"]),
                  "-o", str(plan["dir"] / "cli.png"), "--window", *map(str, ROI), "--compare", "--layers", "CON,CEN"])
    out = capsys.readouterr().out
    assert rc == 0 and out.count("\n") == 1
    data = json.loads(out)
    assert data["only_image"] == [] and data["png"].endswith("cli.png")


def test_calib_schema_matches_the_measure_tool():
    from tools.raster.measure import CALIB_SCHEMA
    assert rv.CALIB_SCHEMA == CALIB_SCHEMA
