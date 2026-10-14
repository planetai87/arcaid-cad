"""E4 보조: 생성 해치(섬)가 CAD에서 몇 개 루프·어떤 스타일로 들어갔는지"""
import ezdxf
from _harness import WORK, Spike

d = ezdxf.readfile(str(WORK / "gen.dxf"))
for h in d.modelspace().query("HATCH"):
    print("DXF", h.dxf.layer, "style", h.dxf.hatch_style, "solid", h.dxf.solid_fill, "assoc", h.dxf.associative,
          [(p.path_type_flags, len(p.vertices)) for p in h.paths], "seeds", h.seeds)
with Spike("E4_hatch") as s:
    doc = s.open(WORK / "e4_c_import.dwg")
    ms = s.ctl._call(getattr, doc, "ModelSpace")
    for i in range(s.ctl._call(getattr, ms, "Count")):
        e = s.ctl._call_attr(ms, "Item", i)
        if str(s.ctl._safe_attr(e, "ObjectName")) == "AcDbHatch":
            try:
                t, v = e.GetXData("ARCAID")
            except Exception:
                v = None
            s.record(f"hatch_{s.ctl._safe_attr(e, 'Handle')}",
                     {"layer": s.ctl._safe_attr(e, "Layer"), "loops": s.ctl._safe_attr(e, "NumberOfLoops"),
                      "style": s.ctl._safe_attr(e, "HatchStyle"), "pattern": s.ctl._safe_attr(e, "PatternName"),
                      "area": s.ctl._safe_attr(e, "Area"), "xdata": list(v) if v else None})
