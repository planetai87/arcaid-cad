"""E4 보조: 섬 해치 변형(스타일·내부 경로 플래그·방향)별로 GstarCAD가 구멍을 만드는지 면적으로 확인"""
import ezdxf
from ezdxf.lldxf import const as C
from _harness import WORK, Spike

doc = ezdxf.readfile(str(WORK / "template.dxf"))
doc.appids.add("ARCAID") if "ARCAID" not in doc.appids else None
msp = doc.modelspace()
for e in list(msp):
    msp.delete_entity(e)
variants = []
for i, (style, inner_flag, reverse) in enumerate([(0, C.BOUNDARY_PATH_DEFAULT, False), (1, C.BOUNDARY_PATH_DEFAULT, True),
                                                  (0, C.BOUNDARY_PATH_OUTERMOST, True), (2, C.BOUNDARY_PATH_DEFAULT, True)]):
    x = 30000 + i * 3000
    h = msp.add_hatch(dxfattribs={"layer": "HAT", "hatch_style": style})
    h.set_pattern_fill("ANSI31", scale=50)
    h.paths.add_polyline_path([(x, 0), (x + 2000, 0), (x + 2000, 2000), (x, 2000)], is_closed=True,
                              flags=C.BOUNDARY_PATH_EXTERNAL)
    inner = [(x + 500, 500), (x + 1500, 500), (x + 1500, 1500), (x + 500, 1500)]
    h.paths.add_polyline_path(list(reversed(inner)) if reverse else inner, is_closed=True, flags=inner_flag)
    h.set_xdata("ARCAID", [(1000, f"oid=v{i}")])
    variants.append({"i": i, "style": style, "inner_flag": inner_flag, "reverse": reverse})
doc.saveas(str(WORK / "gen_hatch.dxf"))

with Spike("E4_hatch_variants") as s:
    s.copy_and_open(WORK / "synthetic_base.dwg", "e4_hatch.dwg")
    s.ctl._call_attr(s.ctl.doc, "Import", str(WORK / "gen_hatch.dxf"), s.ctl._pt((0, 0, 0)), 1.0)
    ms = s.ctl._call(getattr, s.ctl.doc, "ModelSpace")
    for j in range(s.ctl._call(getattr, ms, "Count")):
        e = s.ctl._call_attr(ms, "Item", j)
        try:
            _, v = e.GetXData("ARCAID")
        except Exception:
            continue
        if v:
            i = int(v[1][-1])
            s.record(f"variant_{i}", {**variants[i], "loops": s.ctl._safe_attr(e, "NumberOfLoops"),
                                      "style_cad": s.ctl._safe_attr(e, "HatchStyle"), "area": s.ctl._safe_attr(e, "Area")})
    s.cmd("_.ZOOM\n_W\n29000,-500\n42000,2500\n")
    (WORK / "e4_hatch_variants.png").write_bytes(s.ctl.screenshot())
