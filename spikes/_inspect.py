"""T0 실험용 도면 요약·비교 도구 (제품 코드 아님)

- dxf_summary(path): ezdxf로 DXF 사본을 읽어 요약
- live_summary(ctl, doc): COM으로 열린 도면의 모형 공간을 읽어 같은 형식으로 요약
- diff(a, b): 두 요약을 비교
"""
from collections import Counter

COM_TO_DXF = {
    "AcDbLine": "LINE", "AcDbPolyline": "LWPOLYLINE", "AcDb2dPolyline": "POLYLINE",
    "AcDbCircle": "CIRCLE", "AcDbArc": "ARC", "AcDbEllipse": "ELLIPSE",
    "AcDbText": "TEXT", "AcDbMText": "MTEXT", "AcDbHatch": "HATCH",
    "AcDbBlockReference": "INSERT", "AcDbRotatedDimension": "DIMENSION",
    "AcDbAlignedDimension": "DIMENSION", "AcDbLeader": "LEADER", "AcDbPoint": "POINT",
}


def _r(v, n=3):
    return [round(float(x), n) for x in v] if v is not None else None


# ---------- DXF ----------

def dxf_summary(path, app="ARCAID"):
    import ezdxf
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    ents = {}
    for e in msp:
        rec = {"type": e.dxftype(), "layer": e.dxf.layer,
               "color": e.dxf.get("color", 256), "linetype": e.dxf.get("linetype", "BYLAYER")}
        t = e.dxftype()
        if t == "TEXT":
            rec["text"] = e.dxf.text
            rec["pt"] = _r(e.dxf.insert)
            rec["height"] = round(e.dxf.height, 3)
        elif t == "MTEXT":
            rec["text"] = e.text
            rec["pt"] = _r(e.dxf.insert)
        elif t == "INSERT":
            rec["block"] = e.dxf.name
            rec["pt"] = _r(e.dxf.insert)
            rec["scale"] = [e.dxf.get("xscale", 1), e.dxf.get("yscale", 1)]
            rec["attribs"] = {a.dxf.tag: a.dxf.text for a in e.attribs}
        elif t == "LINE":
            rec["pt"] = _r(e.dxf.start) + _r(e.dxf.end)
        elif t == "DIMENSION":
            rec["dimstyle"] = e.dxf.get("dimstyle")
            rec["measurement"] = round(e.get_measurement(), 3) if hasattr(e, "get_measurement") else None
            rec["annotative"] = e.has_xdata("AcadAnnotative")
        elif t == "HATCH":
            rec["pattern"] = e.dxf.pattern_name
            rec["loops"] = len(e.paths)
        if e.has_xdata(app):
            rec["xdata"] = [v for _, v in e.get_xdata(app)]
        ents[e.dxf.handle] = rec
    dimstyles = {}
    for ds in doc.dimstyles:
        ann = None
        if ds.has_xdata("AcadAnnotative"):
            tags = ds.get_xdata("AcadAnnotative")
            ann = [v for c, v in tags if c == 1070]
        dimstyles[ds.dxf.name] = {"annotative": ann, "dimscale": ds.dxf.get("dimscale"),
                                  "dimtxt": ds.dxf.get("dimtxt"), "dimasz": ds.dxf.get("dimasz"),
                                  "dimexo": ds.dxf.get("dimexo")}
    return {
        "source": str(path),
        "dxfversion": doc.dxfversion,
        "insunits": doc.header.get("$INSUNITS"),
        "codepage": doc.header.get("$DWGCODEPAGE"),
        "entities": ents,
        "layers": {l.dxf.name: {"color": l.dxf.color, "linetype": l.dxf.linetype} for l in doc.layers},
        "dimstyles": dimstyles,
        "blocks": sorted(b.name for b in doc.blocks if not b.name.startswith("*")),
        "xrefs": sorted(b.name for b in doc.blocks if b.block and b.block.is_xref),
    }


# ---------- 열린 도면(COM) ----------

def _attr(ctl, obj, name, default=None):
    return ctl._safe_attr(obj, name, default)


def _xdata(ctl, ent, app):
    try:
        types, values = ent.GetXData(app)
    except Exception:  # noqa: BLE001
        return None
    if not types:
        return None
    return [v for t, v in zip(types, values) if t != 1001]


def live_summary(ctl, doc=None, app="ARCAID"):
    doc = doc if doc is not None else ctl.doc
    ms = ctl._call(getattr, doc, "ModelSpace")
    ents = {}
    for i in range(ctl._call(getattr, ms, "Count")):
        e = ctl._call_attr(ms, "Item", i)
        on = str(_attr(ctl, e, "ObjectName", ""))
        t = COM_TO_DXF.get(on, on)
        rec = {"type": t, "layer": _attr(ctl, e, "Layer"), "color": ctl._read_color(e),
               "linetype": _attr(ctl, e, "Linetype")}
        if t in ("TEXT", "MTEXT"):
            rec["text"] = _attr(ctl, e, "TextString")
            rec["pt"] = _r(_attr(ctl, e, "InsertionPoint"))
        elif t == "INSERT":
            rec["block"] = _attr(ctl, e, "Name")
            rec["pt"] = _r(_attr(ctl, e, "InsertionPoint"))
            attrs = {}
            if _attr(ctl, e, "HasAttributes"):
                try:
                    for a in ctl._call_attr(e, "GetAttributes"):
                        attrs[_attr(ctl, a, "TagString")] = _attr(ctl, a, "TextString")
                except Exception:  # noqa: BLE001
                    pass
            rec["attribs"] = attrs
        elif t == "LINE":
            rec["pt"] = _r(_attr(ctl, e, "StartPoint")) + _r(_attr(ctl, e, "EndPoint"))
        elif t == "DIMENSION":
            rec["dimstyle"] = _attr(ctl, e, "StyleName")
            m = _attr(ctl, e, "Measurement")
            rec["measurement"] = round(m, 3) if m is not None else None
        elif t == "HATCH":
            rec["pattern"] = _attr(ctl, e, "PatternName")
            rec["loops"] = _attr(ctl, e, "NumberOfLoops")
        xd = _xdata(ctl, e, app)
        if xd:
            rec["xdata"] = xd
        ents[str(_attr(ctl, e, "Handle"))] = rec
    return {
        "source": str(_attr(ctl, doc, "FullName") or _attr(ctl, doc, "Name")),
        "insunits": ctl._call_attr(doc, "GetVariable", "INSUNITS"),
        "entities": ents,
    }


# ---------- 비교 ----------

def counts(summary):
    return Counter((r["type"], r["layer"].upper() if r["layer"] else r["layer"])
                   for r in summary["entities"].values())


def texts(summary):
    return sorted(r["text"] for r in summary["entities"].values() if r.get("text") is not None)


def attribs(summary):
    return sorted((r["block"], tuple(sorted(r["attribs"].items())))
                  for r in summary["entities"].values() if r.get("block") is not None)


def diff(live, other):
    """live(열린 도면)와 other(DXF 사본 등)의 차이"""
    cl, co = counts(live), counts(other)
    hl, ho = set(live["entities"]), set(other["entities"])
    same_handle = hl & ho
    type_match = sum(1 for h in same_handle
                     if live["entities"][h]["type"] == other["entities"][h]["type"]
                     and (live["entities"][h]["layer"] or "").upper() == (other["entities"][h]["layer"] or "").upper())
    return {
        "count_live": sum(cl.values()),
        "count_other": sum(co.values()),
        "counts_equal": cl == co,
        "counts_only_live": {f"{k[0]}@{k[1]}": v for k, v in (cl - co).items()},
        "counts_only_other": {f"{k[0]}@{k[1]}": v for k, v in (co - cl).items()},
        "texts_equal": texts(live) == texts(other),
        "texts_missing": sorted(set(texts(live)) - set(texts(other))),
        "texts_extra": sorted(set(texts(other)) - set(texts(live))),
        "attribs_equal": attribs(live) == attribs(other),
        "handle_overlap": len(same_handle),
        "handle_match_pct": round(100 * type_match / max(1, len(hl)), 1),
    }
