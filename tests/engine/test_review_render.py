"""검토 도구(tools/review/render.py): 비교·목록·렌더를 합성 DXF로 확인한다(CAD 없음)"""
import json
import math

import ezdxf
from PIL import Image

from tools.review import render as rv


def _doc(path, moved=False, extra=False):
    doc = ezdxf.new("R2013")
    doc.layers.add("WAL")
    doc.layers.add("DOR")
    msp = doc.modelspace()
    msp.add_line((0, 0), (4000, 0), dxfattribs={"layer": "WAL"})
    msp.add_line((0, 0), (4000, 0), dxfattribs={"layer": "WAL"})          # 같은 형상 두 개(개수로 비교)
    msp.add_lwpolyline([(0, 1000), (2000, 1000), (2000, 1150), (0, 1150)], close=True, dxfattribs={"layer": "WAL"})
    msp.add_circle((3000 if moved else 1000, 2000), 300, dxfattribs={"layer": "DOR"})
    if extra:
        msp.add_text("객실", height=250, dxfattribs={"layer": "DOR"}).set_placement((500, 2500))
    doc.saveas(path)
    return path


def test_diff_counts_added_removed_and_regions(tmp_path):
    base = _doc(tmp_path / "base.dxf")
    new = _doc(tmp_path / "new.dxf", moved=True, extra=True)
    d = rv.diff(base, new, gap=100)
    assert (d["common"], d["added"], d["removed"]) == (3, 2, 1)
    assert d["added_by_layer"] == {"DOR": 2} and d["removed_by_layer"] == {"DOR": 1}
    assert [r["count"] for r in d["removed_regions"]] == [1]
    assert d["removed_regions"][0]["window"] == [700.0, 1700.0, 1300.0, 2300.0]
    assert sum(r["count"] for r in d["added_regions"]) == 2


def test_duplicate_shapes_are_counted_not_merged(tmp_path):
    base = _doc(tmp_path / "base.dxf")
    new = ezdxf.readfile(base)
    new.modelspace().add_line((0, 0), (4000, 0), dxfattribs={"layer": "WAL"})
    new.saveas(tmp_path / "new.dxf")
    d = rv.diff(base, tmp_path / "new.dxf")
    assert (d["common"], d["added"], d["removed"]) == (4, 1, 0)


def test_list_window_layers_and_local_frame(tmp_path):
    path = _doc(tmp_path / "a.dxf", extra=True)
    out = rv.list_entities(path, [-10, 900, 2100, 1200], frame=rv.Frame(0, 1000, 90), layers=["wal"])
    assert out["count"] == 1
    e = out["entities"][0]
    assert e["type"] == "LWPOLYLINE" and e["closed"]
    assert e["points"] == [[0.0, 1000.0], [2000.0, 1000.0], [2000.0, 1150.0], [0.0, 1150.0]]
    assert e["local"]["points"][1] == [0.0, -2000.0] and e["local"]["points"][2] == [150.0, -2000.0]
    text = rv.list_entities(path, [400, 2400, 1500, 2900])["entities"]
    assert [t["text"] for t in text if t["type"] == "TEXT"] == ["객실"]


def test_frame_round_trip():
    f = rv.Frame(2200, 6540, 23.76)
    u, v = f.to_local(f.to_wcs(8034.1, 3196.0))
    assert math.isclose(u, 8034.1, abs_tol=1e-6) and math.isclose(v, 3196.0, abs_tol=1e-6)


def test_render_colors_added_and_removed(tmp_path):
    base = _doc(tmp_path / "base.dxf")
    new = _doc(tmp_path / "new.dxf", moved=True)
    result = rv.render(new, tmp_path / "out.png", [-500, -500, 4500, 3000], base_path=base, scale=0.1, grid=1000,
                       frame=rv.Frame(0, 0, 30))
    assert result["size"] == [500, 350] and (result["added"], result["removed"]) == (1, 1)
    img = Image.open(tmp_path / "out.png").convert("RGB")
    px = img.load()

    def near(x, y, color, r=40):
        cx, cy = (x + 500) * 0.1, (3000 - y) * 0.1
        return any(px[int(cx + dx), int(cy + dy)] == color for dx in range(-r // 10, r // 10 + 1)
                   for dy in range(-r // 10, r // 10 + 1) if 0 <= cx + dx < 500 and 0 <= cy + dy < 350)
    assert near(3300, 2000, rv.RED[:3])       # 옮긴 원(추가)
    assert near(1300, 2000, rv.BLUE[:3])      # 원래 원(삭제)
    assert near(2000, 0, rv.GRAY[:3])         # 공통 벽선


def test_cli_outputs_one_json(tmp_path, capsys):
    base = _doc(tmp_path / "base.dxf")
    new = _doc(tmp_path / "new.dxf", extra=True)
    assert rv.main(["diff", str(base), str(new)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == 1 and out["removed"] == 0
