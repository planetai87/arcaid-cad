"""생성 DXF 만들기: template.dxf(R2013) 위에 표준 레이어(ByLayer) 객체와 ARCAID XDATA를 넣는다

python gen_dxf.py <출력 이름> [--op OP] [--lines N] [--x X]
- 기본 객체 세트: 선, 닫힌 경량 폴리선, 원, 호, 한글 문자(MC 정렬), MTEXT, 축열 버블(속성 X9),
  섬이 있는 해치, SOLID 해치, 선형 치수(DIM330, DIMSCALE 50), LIM 레이어 PHANTOM 선
- --lines N: 태그 단 선을 N개 더 넣는다(E7·속도 시험용)
"""
import argparse

import ezdxf

from _harness import WORK

APP = "ARCAID"


def tag(e, op, oid, path):
    e.set_xdata(APP, [(1000, f"op={op}"), (1000, f"oid={oid}"), (1000, f"path={path}"), (1000, "ver=spike")])


def build(out_name, op="e4", n_lines=0, x0=20000.0, basic=True):
    doc = ezdxf.readfile(str(WORK / "template.dxf"))
    if APP not in doc.appids:
        doc.appids.add(APP)
    msp = doc.modelspace()
    for e in list(msp):
        msp.delete_entity(e)
    oid = 0

    def nxt():
        nonlocal oid
        oid += 1
        return f"{op}-{oid:04d}"

    if basic:
        tag(msp.add_line((x0, 0), (x0 + 6000, 0), dxfattribs={"layer": "CEN"}), op, nxt(), "grid/X")
        tag(msp.add_lwpolyline([(x0, 1000), (x0 + 3000, 1000), (x0 + 3000, 3000), (x0, 3000)], close=True,
                               dxfattribs={"layer": "WAL1"}), op, nxt(), "room/201/wall")
        tag(msp.add_circle((x0 + 4000, 2000), 300, dxfattribs={"layer": "DOR"}), op, nxt(), "misc/circle")
        tag(msp.add_arc((x0 + 5000, 2000), 900, 0, 90, dxfattribs={"layer": "DOR"}), op, nxt(), "room/201/door")
        t = msp.add_text("생성 객실 201", height=135,
                         dxfattribs={"layer": "ROM", "style": "돋움"})
        t.set_placement((x0 + 1500, 2000), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
        tag(t, op, nxt(), "room/201/name")
        tag(msp.add_mtext("생성 여러 줄\\P둘째 줄", dxfattribs={"layer": "TXT", "char_height": 135,
                                                       "insert": (x0, -1000), "style": "돋움"}),
            op, nxt(), "note/1")
        br = msp.add_blockref("축열버블-상", (x0 + 6000, 4000), dxfattribs={"layer": "DIM"})
        br.add_auto_attribs({"축열": "X9"})
        tag(br, op, nxt(), "grid/X9/bubble")
        h = msp.add_hatch(dxfattribs={"layer": "HAT"})
        h.set_pattern_fill("ANSI31", scale=50)
        h.paths.add_polyline_path([(x0, 5000), (x0 + 2000, 5000), (x0 + 2000, 7000), (x0, 7000)], is_closed=True,
                                  flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
        h.paths.add_polyline_path([(x0 + 500, 5500), (x0 + 1500, 5500), (x0 + 1500, 6500), (x0 + 500, 6500)],
                                  is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_DEFAULT)
        tag(h, op, nxt(), "hatch/island")
        hs = msp.add_hatch(dxfattribs={"layer": "HAT1"})
        hs.set_solid_fill(color=256)
        hs.paths.add_polyline_path([(x0 + 3000, 5000), (x0 + 4000, 5000), (x0 + 4000, 6000), (x0 + 3000, 6000)],
                                   is_closed=True)
        tag(hs, op, nxt(), "hatch/solid")
        dim = msp.add_linear_dim(base=(x0, 9000), p1=(x0, 8000), p2=(x0 + 6000, 8000), dimstyle="DIM330",
                                 override={"dimscale": 50}, dxfattribs={"layer": "DIM"})
        dim.render()
        tag(dim.dimension, op, nxt(), "dim/1")
        tag(msp.add_line((x0, -2500), (x0 + 6000, -2500), dxfattribs={"layer": "LIM", "linetype": "PHANTOM"}),
            op, nxt(), "site/boundary")
    for i in range(n_lines):
        y = -4000 - (i // 50) * 200
        x = x0 + (i % 50) * 120
        tag(msp.add_line((x, y), (x + 100, y), dxfattribs={"layer": "TXT1"}), op, nxt(), f"bulk/{i}")
    out = WORK / out_name
    doc.saveas(str(out))
    return out, oid


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--op", default="e4")
    ap.add_argument("--lines", type=int, default=0)
    ap.add_argument("--x", type=float, default=20000.0)
    ap.add_argument("--no-basic", action="store_true")
    a = ap.parse_args()
    path, n = build(a.out, a.op, a.lines, a.x, not a.no_basic)
    d = ezdxf.readfile(str(path))
    print(path, d.dxfversion, "tagged", n, "entities", len(d.modelspace()))
